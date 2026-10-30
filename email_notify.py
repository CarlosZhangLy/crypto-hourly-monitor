#!/usr/bin/env python3
"""QQ SMTP delivery: hourly rule digest and strictly validated trade plans. No order execution."""
import json
import os
import smtplib
import ssl
import hashlib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from risk_calc import fetch_specs, plan
from pushplus_notify import check, build

BJ=timezone(timedelta(hours=8))
CORE=("BTC","ETH","SOL","XRP","LTC","ADA","BCH","LINK")
STATE=Path("data/email_state.json")

def configured():
    return all(os.environ.get(k,"").strip() for k in ("QQ_SMTP_USER","QQ_SMTP_AUTH_CODE","ALERT_TO"))

def send(subject,content):
    sender=os.environ["QQ_SMTP_USER"].strip()
    password=os.environ["QQ_SMTP_AUTH_CODE"].strip()
    recipient=os.environ["ALERT_TO"].strip()
    msg=EmailMessage()
    msg["From"]=sender
    msg["To"]=recipient
    msg["Subject"]=subject
    msg.set_content(content)
    with smtplib.SMTP_SSL("smtp.qq.com",465,context=ssl.create_default_context(),timeout=20) as smtp:
        smtp.login(sender,password)
        smtp.send_message(msg)

def run():
    if not configured():
        print("QQ SMTP secrets missing: email notifications disabled; no false delivery claim.")
        return
    data=json.loads(Path("data/latest.json").read_text(encoding="utf-8"))
    stamp=check(data)
    now=datetime.now(BJ)
    if not 0<= (now-stamp).total_seconds()<=45*60:
        raise ValueError("Snapshot stale")
    previous=json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    state=dict(previous)
    # Email one digest per Beijing hour, on the :34 collection (not on 15-minute collections).
    if 34<=now.minute<=43:
        hour_key=stamp.strftime("%Y-%m-%d %H")
        if previous.get("digest_hour")!=hour_key:
            text=build(data,stamp)
            send("【OKX行情分析·规则版】%s｜8币行情与风控" % stamp.strftime("%m-%d %H:%M"),text+
                 "\n\n说明：此邮件由GitHub行情规则自动生成，并非ChatGPT大模型撰写的AI分析。")
            state["digest_hour"]=hour_key
            print("Rule digest email accepted by SMTP.")
    specs=fetch_specs([s+"-USDT-SWAP" for s in CORE])
    current={}
    candidates=[]
    for sym in CORE:
        x=data["symbols"][sym+"USDT"]
        spec=specs.get(sym+"-USDT-SWAP")
        if not spec: continue
        result=plan(x,spec)
        if not result: continue
        q=x.get("five_minute_structure") or {}
        identity="|".join((sym,str(q.get("break_bar")),str(q.get("side")),str(q.get("break_level"))))
        current[identity]={"symbol":sym,"side":result["direction"],"break_level":q.get("break_level")}
        if identity in previous.get("alerted",{}): continue
        candidates.append((sym,result,identity))
    candidates.sort(key=lambda v:v[1]["rr"],reverse=True)
    alerted=dict(previous.get("alerted",{}))
    # No more than 2 recommendations per snapshot; cap persistent identity set.
    for sym,r,key in candidates[:2]:
        direction="开多" if r["direction"]=="多" else "开空"
        subject="【OKX交易信号】%s %s｜限价%s｜有效10分钟" % (sym,direction,r["entry"])
        expiry=stamp+timedelta(minutes=10)
        # At collection + GitHub workflow latency this may already be expired; never send stale actionable plans.
        if now>=expiry: continue
        body=("\n".join([
            "%s USDT永续｜%s｜逐仓2倍" % (sym,direction),
            "限价：%s USDT" % r["entry"],
            "张数：%s 张（每张面值 %s）" % (r["contracts"],r["ctVal"]),
            "保证金：约 %.2f USDT" % r["margin"],
            "止损：%s" % r["stop"],
            "止盈：%s" % r["target"],
            "含估计手续费滑点最大损失：%.2f USDT" % r["risk"],
            "扣成本后盈亏比：%.2f:1" % r["rr"],
            "快照时间：%s（北京）" % stamp.strftime("%Y-%m-%d %H:%M:%S"),
            "失效时间：%s（北京）" % expiry.strftime("%Y-%m-%d %H:%M:%S"),
            "到期未成交请撤单；价格脱离计划区间、结构失效或实盘盈亏比低于1.8时取消。",
            "发送时的数据不等于即时可成交价，请在OKX确认价格及实际资金费率。系统不自动下单。",
        ]))
        send(subject,body)
        alerted[key]={"sym":sym,"direction":direction,"time":stamp.isoformat(),"expiry":expiry.isoformat()}
        state["alerted"]=alerted
        print("Trade email accepted by SMTP:",sym)
    # Do not automatically claim signals cancelled based only on a no-signal snapshot;
    # previously delivered limit orders need independent status checks on OKX.
    state["alerted"]=dict(list(alerted.items())[-100:])
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

if __name__=="__main__":
    run()
