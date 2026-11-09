#!/usr/bin/env python3
"""Relève les prix des vols (Travelpayouts / Aviasales), calcule les meilleurs
combinaisons, met à jour l'historique et envoie les alertes Telegram.

Usage :
  python fetch_prices.py          # mode réel (TRAVELPAYOUTS_TOKEN requis)
  python fetch_prices.py --mock   # données factices pour tester
"""
import os, sys, json, time, random, datetime as dt
import urllib.request, urllib.parse, urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
MOCK = "--mock" in sys.argv
TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN", "")

_cache = {}


def months(year, start_month=1, count=13):
    out, y, m = [], year, start_month
    for _ in range(count):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


ENDPOINTS = [
    ("prices_for_dates", "https://api.travelpayouts.com/aviasales/v3/prices_for_dates",
     lambda o, d, m: {"origin": o, "destination": d, "departure_at": m, "one_way": "true",
                      "sorting": "price", "limit": 1000, "unique": "false"}),
    ("calendar", "https://api.travelpayouts.com/v1/prices/calendar",
     lambda o, d, m: {"origin": o, "destination": d, "depart_date": m,
                      "calendar_type": "departure_date"}),
    ("grouped_prices", "https://api.travelpayouts.com/aviasales/v3/grouped_prices",
     lambda o, d, m: {"origin": o, "destination": d, "departure_at": m,
                      "group_by": "departure_at"}),
]
_shown = 0
route_days = {}


def _extract(data):
    """Accepte une liste de billets ou un dict date -> billet."""
    items = data.values() if isinstance(data, dict) else (data or [])
    days = {}
    for t in items:
        if not isinstance(t, dict):
            continue
        day, p = (t.get("departure_at") or "")[:10], t.get("price")
        if not day or not p:
            continue
        if day not in days or p < days[day]["price"]:
            link = t.get("link") or ""
            days[day] = {"price": p, "airline": t.get("airline", ""),
                         "transfers": t.get("transfers", t.get("number_of_changes", 0)) or 0,
                         "link": ("https://www.aviasales.com" + link) if link.startswith("/") else link}
    return days


def fetch_leg_month(o, d, month):
    """Prix le plus bas par jour (par adulte, aller simple) pour une ligne et un mois."""
    global _shown
    key = (o, d, month)
    if key in _cache:
        return _cache[key]
    days = {}
    if MOCK:
        rnd = random.Random(f"{o}{d}{month}")
        y, m = map(int, month.split("-"))
        base = 180 + (hash_route(o, d) % 220)
        for day in range(1, 29):
            season = 1.35 if m in (3, 4, 7, 8, 12) else 1.0
            price = base * season * rnd.uniform(0.8, 1.35)
            days[f"{month}-{day:02d}"] = {
                "price": round(price), "airline": rnd.choice(["QR", "EK", "EY", "SV", "AF"]),
                "transfers": rnd.choice([0, 1, 1, 2]), "link": "",
            }
    else:
        for name, url, build in ENDPOINTS:
            params = build(o, d, month)
            params.update({"currency": CFG["currency"], "token": TOKEN})
            req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                         headers={"X-Access-Token": TOKEN})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw = r.read().decode()
                payload = json.loads(raw)
            except urllib.error.HTTPError as e:
                print(f"  ! {name} {o}->{d} {month}: HTTP {e.code} {e.read()[:150]!r}", flush=True)
                continue
            except (urllib.error.URLError, ValueError) as e:
                print(f"  ! {name} {o}->{d} {month}: {e}", flush=True)
                continue
            finally:
                time.sleep(0.3)
            days = _extract(payload.get("data")) if isinstance(payload, dict) else {}
            if _shown < 6:  # aperçu des premières réponses brutes (sans le token)
                _shown += 1
                print(f"  [diag] {name} {o}->{d} {month}: {len(days)} jours · {raw[:220]!r}", flush=True)
            if days:
                break
    route_days[(o, d)] = route_days.get((o, d), 0) + len(days)
    _cache[key] = days
    return days


def hash_route(o, d):
    return sum(ord(c) * (i + 3) for i, c in enumerate(o + d))


def price_on(o, d, day):
    return fetch_leg_month(o, d, day.strftime("%Y-%m")).get(day.isoformat())


def leg(o, d, day):
    p = price_on(o, d, day)
    if not p:
        return None
    return {"from": o, "to": d, "date": day.isoformat(), "price": p["price"],
            "airline": p["airline"], "transfers": p["transfers"], "link": p["link"]}


def best_return(o, d, earliest, latest):
    best, day = None, earliest
    while day <= latest:
        l = leg(o, d, day)
        if l and (best is None or l["price"] < best["price"]):
            best = l
        day += dt.timedelta(days=1)
    return best


def pax_factor():
    return CFG["adults"] + CFG["children"] * CFG["child_price_factor"]


def total_of(legs):
    return round(sum(l["price"] for l in legs) * pax_factor())


def scan_origin(o, prefix, hop_home=None):
    """Combinaisons de vols depuis l'aéroport o (direct + stopovers).
    Si hop_home est donné (ex. RUN), on ajoute le petit vol hop_home -> o à l'aller
    et o -> hop_home au retour, avec une nuit de marge (gateway_buffer_days)."""
    dest, hubs = CFG["destination"], CFG["hubs"]
    y = CFG["year"]
    n_lo, n_hi = CFG["nights_min"], CFG["nights_max"]
    s_lo, s_hi = CFG["stopover_min_days"], CFG["stopover_max_days"]
    buf = CFG.get("gateway_buffer_days", 1)
    one = dt.timedelta(days=1)
    start, end = dt.date(y, 1, 1), dt.date(y, 12, 31)

    results = {f"{prefix}direct": []}
    for h in hubs:
        results[f"{prefix}stop_{h}"] = []

    def add(key, legs):
        if hop_home:
            first = dt.date.fromisoformat(legs[0]["date"])
            last = dt.date.fromisoformat(legs[-1]["date"])
            h_out = leg(hop_home, o, first - buf * one)
            h_back = leg(o, hop_home, last + buf * one)
            if not (h_out and h_back):
                return
            legs = [h_out] + legs + [h_back]
        results[key].append(legs)

    day = start
    while day <= end:
        out = leg(o, dest, day)
        if out:
            back = best_return(dest, o, day + n_lo * one, day + n_hi * one)
            if back:
                add(f"{prefix}direct", [out, back])
        for h in hubs:
            cands = []
            a1 = leg(o, h, day)
            if a1:
                for s_ in range(s_lo, s_hi + 1):
                    a2 = leg(h, dest, day + s_ * one)
                    if not a2:
                        continue
                    base = day + s_ * one
                    back = best_return(dest, o, base + n_lo * one, base + n_hi * one)
                    if back:
                        cands.append([a1, a2, back])
            b1 = leg(o, dest, day)
            if b1:
                for n in range(n_lo, n_hi + 1):
                    b2 = leg(dest, h, day + n * one)
                    if not b2:
                        continue
                    for s_ in range(s_lo, s_hi + 1):
                        b3 = leg(h, o, day + (n + s_) * one)
                        if b3:
                            cands.append([b1, b2, b3])
            if cands:
                add(f"{prefix}stop_{h}", min(cands, key=total_of))
        day += one
    return results


def month_min_leg(o, d, ym):
    """Billet le moins cher repéré pour ce trajet dans ce mois (ou None)."""
    best = None
    for day, p in fetch_leg_month(o, d, ym).items():
        if best is None or p["price"] < best["price"]:
            best = {"from": o, "to": d, "date": day, "price": p["price"], "airline": p["airline"],
                    "transfers": p["transfers"], "link": p["link"]}
    return best


def nominal_templates(o, hop_home, ym):
    """Parcours type pour un mois de départ : liste de variantes [(de, vers, date), ...]."""
    y, m = map(int, ym.split("-"))
    d0, one = dt.date(y, m, 10), dt.timedelta(days=1)
    n = (CFG["nights_min"] + CFG["nights_max"]) // 2
    s_ = (CFG["stopover_min_days"] + CFG["stopover_max_days"]) // 2
    buf, dest = CFG.get("gateway_buffer_days", 1), CFG["destination"]

    def wrap(t):
        if hop_home:
            return [(hop_home, o, t[0][2] - buf * one)] + t + [(o, hop_home, t[-1][2] + buf * one)]
        return t

    out = {"direct": [wrap([(o, dest, d0), (dest, o, d0 + n * one)])]}
    for h in CFG["hubs"]:
        out[f"stop_{h}"] = [
            wrap([(o, h, d0), (h, dest, d0 + s_ * one), (dest, o, d0 + (s_ + n) * one)]),
            wrap([(o, dest, d0), (dest, h, d0 + n * one), (h, o, d0 + (n + s_) * one)]),
        ]
    return out


def estimate_origin(o, prefix, hop_home=None):
    """Indice mensuel : somme des billets les moins chers du mois pour chaque tronçon.
    Les dates ne sont pas forcément alignées : c'est un plancher de prix, pas un itinéraire."""
    est = {}
    for ym in months(CFG["year"], 1, 12):
        for key, variants in nominal_templates(o, hop_home, ym).items():
            best = None
            for tpl in variants:
                legs = []
                for a_, b_, day in tpl:
                    l = month_min_leg(a_, b_, day.strftime("%Y-%m"))
                    if not l:
                        legs = None
                        break
                    legs.append(l)
                if legs and (best is None or total_of(legs) < total_of(best)):
                    best = legs
            if best:
                est.setdefault(prefix + key, {})[ym] = best
    return est


def scan():
    results = scan_origin(CFG["origin"], "")
    est = estimate_origin(CFG["origin"], "")
    gw = CFG.get("gateway")
    if gw:
        results.update(scan_origin(gw, "via_" + gw + "_", hop_home=CFG["origin"]))
        est.update(estimate_origin(gw, "via_" + gw + "_", hop_home=CFG["origin"]))
    return results, est


def label(key):
    via = ""
    if key.startswith("via_"):
        _, gw, key = key.split("_", 2)
        via = {"MRU": "Maurice"}.get(gw, gw) + " · "
    if key == "direct":
        return via + "Direct" if via else "Direct"
    return f"{via}Stopover {key.split('_')[1]}"


def summarize(results, estimates):
    scen, month_best, bests = {}, {}, {}
    for key in sorted(set(results) | set(estimates)):
        exact = []
        for legs in results.get(key, []):
            exact.append({"total": total_of(legs), "depart": legs[0]["date"], "kind": "exact", "legs": legs})
        exact.sort(key=lambda r: r["total"])
        ex_top, seen = [], set()
        for r in exact:
            if r["depart"][:7] in seen and len(ex_top) >= 3:
                continue
            seen.add(r["depart"][:7])
            ex_top.append(r)
            if len(ex_top) >= 5:
                break
        indic = [{"total": total_of(legs), "depart": ym + "-01", "month": ym, "kind": "indicatif", "legs": legs}
                 for ym, legs in estimates.get(key, {}).items()]
        indic.sort(key=lambda r: r["total"])
        top = ex_top + indic[:max(0, 8 - len(ex_top))]
        mb = {}
        for r in exact:
            m = r["depart"][:7]
            mb[m] = min(r["total"], mb.get(m, r["total"]))
        for r in indic:
            mb[r["month"]] = min(r["total"], mb.get(r["month"], r["total"]))
        if not mb:
            continue
        scen[key] = {"label": label(key), "top": top}
        month_best[key] = dict(sorted(mb.items()))
        bests[key] = min(mb.values())
    return scen, month_best, bests


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def telegram(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("[alerte non envoyée : Telegram non configuré]\n" + text)
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=30)
    except urllib.error.URLError as e:
        print(f"! Telegram: {e}", file=sys.stderr)


def alerts(bests, scen, history):
    cur = CFG["currency"].upper()
    prev_min = {}
    for h in history:
        for k, v in h["best"].items():
            prev_min[k] = min(v, prev_min.get(k, v))
    msgs = []
    for key, price in bests.items():
        thr = (CFG.get("alert_below") or {}).get(key)
        reasons = []
        if thr and price <= thr:
            reasons.append(f"sous ton seuil de {thr} {cur}")
        pm = prev_min.get(key)
        if pm and price <= pm * (1 - CFG["alert_min_drop_pct"] / 100):
            reasons.append(f"nouveau plus bas (avant : {pm} {cur})")
        if reasons:
            top = scen[key]["top"][0]
            route = " → ".join([top["legs"][0]["from"]] + [l["to"] for l in top["legs"]])
            msgs.append(f"✈️ {scen[key]['label']} : {price} {cur} pour 3 ({' ; '.join(reasons)})\n"
                        + (f"Estimation pour {top['month']} (billets les moins chers du mois) · {route}"
                           if top.get("kind") == "indicatif" else f"Départ {top['depart']} · {route}"))
    if msgs:
        telegram("Oumrah — baisse de prix\n\n" + "\n\n".join(msgs))


def probe():
    """Trajets témoins : permet de savoir si l'API répond pour d'autres routes."""
    tests = [("PAR", "LON"), ("PAR", "JED"), ("PAR", "DXB"), ("MRU", "JED"),
             ("DXB", "JED"), ("DOH", "JED"), ("RUN", "PAR"), ("RUN", "MRU"), ("JED", "RUN")]
    print("Test de contrôle (autres trajets) :", flush=True)
    for a, b in tests:
        for m in ("2027-03", "2027-07"):
            days = fetch_leg_month(a, b, m)
            lo = min((v["price"] for v in days.values()), default=None)
            print(f"  [probe] {a}->{b} {m}: {len(days)} jours" + (f", dès {lo}" if lo else ""), flush=True)


def main():
    if not MOCK and not TOKEN:
        sys.exit("TRAVELPAYOUTS_TOKEN manquant (ou lance avec --mock).")
    os.makedirs(DATA, exist_ok=True)
    print("Scan en cours…", flush=True)
    scen, month_best, bests = summarize(*scan())
    print("Jours avec un prix, par trajet :", flush=True)
    for (a, b), n in sorted(route_days.items()):
        print(f"  {a}->{b}: {n}", flush=True)
    if not bests:
        if not MOCK:
            probe()
        sys.exit("Aucun prix exploitable (voir le détail par trajet ci-dessus).")
    today = dt.date.today().isoformat()
    hist_path = os.path.join(DATA, "history.json")
    history = load(hist_path, [])
    history = [h for h in history if h["date"] != today]
    if not MOCK:
        alerts(bests, scen, history)
    history.append({"date": today, "best": bests})
    history.sort(key=lambda h: h["date"])
    latest = {
        "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "demo": MOCK, "currency": CFG["currency"].upper(),
        "pax": {"adults": CFG["adults"], "children": CFG["children"],
                "child_price_factor": CFG["child_price_factor"]},
        "trip": {"origin": CFG["origin"], "destination": CFG["destination"],
                 "nights": [CFG["nights_min"], CFG["nights_max"]],
                 "stopover_days": [CFG["stopover_min_days"], CFG["stopover_max_days"]]},
        "scenarios": scen, "months": month_best,
    }
    json.dump(latest, open(os.path.join(DATA, "latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(history, open(hist_path, "w", encoding="utf-8"), ensure_ascii=False)
    print("OK :", bests)


if __name__ == "__main__":
    main()
