"""Immutable hourly email recommendation archive for hypothetical backtesting.

Stores the *actually emailed* plan numbers after SMTP accepts the digest.
No account or order access. Timestamp is email-send time (Beijing), not snapshot time.
"""
import hashlib
import json
from datetime import timedelta
from pathlib import Path

ROOT = Path("data/recommendations")

def save(sent_at, snapshot_at, title, plans):
    if sent_at.tzinfo is None or snapshot_at.tzinfo is None:
        raise ValueError("timezone-aware timestamps required")
    key = sent_at.strftime("%Y%m%dT%H%M%S%f")
    path = ROOT / sent_at.strftime("%Y-%m-%d") / (key + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, p in enumerate(plans):
        source = ("%s|%d|%s|%s|%s" % (sent_at.isoformat(),index,p["symbol"],p["direction"],p["entry"]))
        uid = hashlib.sha256(source.encode()).hexdigest()[:20]
        rows.append({
            "id": uid, "email_sent_bj":sent_at.isoformat(), "snapshot_bj":snapshot_at.isoformat(),
            "symbol":p["symbol"],"direction":p["direction"],"entry":p["entry"],
            "stop":p["stop"],"tp1":p["tp1"],"tp2":p["tp2"],
            "leverage":p["leverage"],"contracts":p["contracts"],"ctval":p["ctval"],
            "notional":p["notional"],"margin":p["margin"],"score":p["score"],
            "retest_band":p.get("retest_band"),"structure_hint":p.get("structure_hint"),
            "program_confirmed":p.get("program_confirmed",False),
            "plan_expires_bj":p.get("expires_bj"),
            "simulation_order_expires_bj":(sent_at+timedelta(hours=2)).isoformat(),
            "source":"hourly_email_conditional_plan"
        })
    doc={"schema_version":1,"email_sent_bj":sent_at.isoformat(),
         "snapshot_bj":snapshot_at.isoformat(),"subject":title,
         "number_of_plans":len(rows),"plans":rows,
         "note":"Simulation assumes 120-minute hypothetical orders; this DOES NOT extend the original 60-minute live plan expiry."}
    path.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return path
