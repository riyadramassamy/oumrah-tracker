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


def fetch_leg_month(o, d, month):
    """Prix le plus bas par jour (par adulte, aller simple) pour une ligne et un mois."""
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
        params = {
            "origin": o, "destination": d, "departure_at": month, "one_way": "true",
            "currency": CFG["currency"], "sorting": "price", "limit": 1000,
            "unique": "false", "token": TOKEN,
        }
        url = API + "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                payload = json.loads(r.read().decode())
            for t in payload.get("data", []):
                day = t["departure_at"][:10]
                p = t.get("price")
                if p is None:
                    continue
                if day not in days or p < days[day]["price"]:
                    days[day] = {
                        "price": p, "airline": t.get("airline", ""),
                        "transfers": t.get("transfers", t.get("number_of_changes", 0)),
                        "link": ("https://www.aviasales.com" + t["link"]) if t.get("link") else "",
                    }
        except (urllib.error.URLError, ValueError, KeyError) as e:
            print(f"  ! {o}->{d} {month}: {e}", file=sys.stderr)
        time.sleep(0.3)
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


def scan():
    o, dest, hubs = CFG["origin"], CFG["destination"], CFG["hubs"]
    y = CFG["year"]
    n_lo, n_hi = CFG["nights_min"], CFG["nights_max"]
    s_lo, s_hi = CFG["stopover_min_days"], CFG["stopover_max_days"]
    one = dt.timedelta(days=1)
    start, end = dt.date(y, 1, 1), dt.date(y, 12, 31)

    results = {"direct": []}
    for h in hubs:
        results[f"stop_{h}"] = []

    day = start
    while day <= end:
        # Direct : o -> dest, séjour, dest -> o
        out = leg(o, dest, day)
        if out:
            back = best_return(dest, o, day + n_lo * one, day + n_hi * one)
            if back:
                results["direct"].append([out, back])
        # Avec stopover
        for h in hubs:
            key = f"stop_{h}"
            cands = []
            # Stop à l'aller : o -> h, (s jours), h -> dest, séjour, dest -> o
            a1 = leg(o, h, day)
            if a1:
                for s in range(s_lo, s_hi + 1):
                    a2 = leg(h, dest, day + s * one)
                    if not a2:
                        continue
                    base = day + s * one
                    back = best_return(dest, o, base + n_lo * one, base + n_hi * one)
                    if back:
                        cands.append([a1, a2, back])
            # Stop au retour : o -> dest, séjour, dest -> h, (s jours), h -> o
            b1 = leg(o, dest, day)
            if b1:
                for n in range(n_lo, n_hi + 1):
                    b2 = leg(dest, h, day + n * one)
                    if not b2:
                        continue
                    for s in range(s_lo, s_hi + 1):
                        b3 = leg(h, o, day + (n + s) * one)
                        if b3:
                            cands.append([b1, b2, b3])
            if cands:
                results[key].append(min(cands, key=total_of))
        day += one
    return results


LABELS = {"direct": "Direct"}


def label(key):
    return "Direct" if key == "direct" else f"Stopover {key.split('_')[1]}"


def summarize(results):
    scen, month_best, bests = {}, {}, {}
    for key, combos in results.items():
        rows = []
        for legs in combos:
            rows.append({"total": total_of(legs), "depart": legs[0]["date"], "legs": legs})
        rows.sort(key=lambda r: r["total"])
        top, seen = [], set()
        for r in rows:
            if r["depart"][:7] in seen and len(top) >= 3:
                continue
            seen.add(r["depart"][:7])
            top.append(r)
            if len(top) >= 8:
                break
        mb = {}
        for r in rows:
            m = r["depart"][:7]
            if m not in mb or r["total"] < mb[m]:
                mb[m] = r["total"]
        scen[key] = {"label": label(key), "top": top}
        month_best[key] = dict(sorted(mb.items()))
        if rows:
            bests[key] = rows[0]["total"]
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
                        f"Départ {top['depart']} · {route}")
    if msgs:
        telegram("Oumrah — baisse de prix\n\n" + "\n\n".join(msgs))


def main():
    if not MOCK and not TOKEN:
        sys.exit("TRAVELPAYOUTS_TOKEN manquant (ou lance avec --mock).")
    os.makedirs(DATA, exist_ok=True)
    print("Scan en cours…")
    scen, month_best, bests = summarize(scan())
    if not bests:
        sys.exit("Aucun prix trouvé : vérifie le token et les routes.")
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
