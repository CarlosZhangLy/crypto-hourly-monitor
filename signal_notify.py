#!/usr/bin/env python3
"""Send only changed 5m structural alerts. No order execution."""
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

CORE=("BTC","ETH","SOL","XRP","LTC","ADA","BCH","LINK")
BJ=timezone(timedelta(hours=8))
STATE=Path("data/signal_state.json")

def run():
    data=json.loads(Path("data/latest.json").read_text(encoding="utf-8"))
    stamp=datetime.fromisoformat(data["generated_beijing"])
    if stamp.tzinfo is None or not timedelta(0)<=datetime.now(BJ)-stamp<=timedelta(minutes=45):
        raise ValueError("Outdated snapshot")
    if data.get("errors"): raise ValueError("Collector errors")
    prev=json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    current={}
    alerts=[]
    for sym in CORE:
        x=data["symbols"][sym+"USDT"]
        q=x.get("five_minute_structure") or {}
        p=x["intervals"]
        side=q.get("side")
        if side not in ("long","short") or q.get("status") not in ("break_watch","retest_confirmed"):
            continue
        bars=q.get("bars_since_break",99)
        if bars>4: continue
        aligned=((p["15m"]["ema20"]>p["15m"]["ema50"] and p["1h"]["ema20"]>p["1h"]["ema50"]) if side=="long"
                 else (p["15m"]["ema20"]<p["15m"]["ema50"] and p["1h"]["ema20"]<p["1h"]["ema50"]))
        vol=float(p["15m"]["volume_ratio_vs_prior20"])
        if not aligned or vol<1.2:continue
        stage="回踩结构初步确认" if q.get("confirmed") else "突破待回踩"
        identity=str(q.get("break_bar"))+"|"+str(side)+"|"+stage
        current[sym]=identity
        if prev.get(sym)!=identity:
            direction="多" if side=="long" else "空"
            alerts.append(f"- **{sym} {direction}：{stage}**；结构位 {q.get('break_level')}，15m量比 {vol:.2f}，快照价 {x['last_price']}。")
    token=os.getenv("PUSHPLUS_TOKEN","").strip()
    if not token:raise ValueError("PUSHPLUS_TOKEN unavailable")
    if alerts:
        body="## OKX 15分钟结构状态变化\n"+f"快照北京时间 {stamp:%m-%d %H:%M}\n\n"+"\n".join(alerts[:3])
        body+="\n\n这是采集时点的观察线索，非实时下单信号。需核实最新5m结构、止损、费用和风险收益。"
        payload={"token":token,"title":f"OKX重要信号变化 {stamp:%H:%M}","content":body,"template":"markdown","channel":"wechat"}
        req=Request("https://www.pushplus.plus/send",data=json.dumps(payload,ensure_ascii=False).encode(),headers={"Content-Type":"application/json"},method="POST")
        with urlopen(req,timeout=20) as resp:result=json.load(resp)
        if result.get("code")!=200:raise RuntimeError("PushPlus rejected signal notification")
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(current,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Signal transition alerts sent: {len(alerts)}")

if __name__=="__main__":run()
