"""Génère un historique factice (90 jours) pour prévisualiser le dashboard."""
import json, random, datetime as dt, os
d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
latest = json.load(open(os.path.join(d, "latest.json"), encoding="utf-8"))
end = {k: min(r["total"] for r in v["top"]) for k, v in latest["scenarios"].items()}
rnd = random.Random(7)
hist, today = [], dt.date.today()
vals = {k: v * rnd.uniform(1.1, 1.3) for k, v in end.items()}
for i in range(90, -1, -1):
    for k in vals:
        target = end[k]
        vals[k] += (target - vals[k]) * 0.04 + rnd.gauss(0, end[k] * 0.018)
    hist.append({"date": (today - dt.timedelta(days=i)).isoformat(),
                 "best": {k: round(v) for k, v in vals.items()}})
hist[-1]["best"] = end
json.dump(hist, open(os.path.join(d, "history.json"), "w"), ensure_ascii=False)
print("historique démo :", len(hist), "jours")
