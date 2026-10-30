#!/usr/bin/env python3
"""QQ SMTP delivery: hourly rule digest and strictly validated trade plans. No order execution."""
import json
import os
import smtplib
import ssl
from ai_report import generate as ai_generate
import email_templates as templates
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

def send(subject,content,html_body=None):
    sender=os.environ["QQ_SMTP_USER"].strip()
    password=os.environ["QQ_SMTP_AUTH_CODE"].strip()
    recipient=os.environ["ALERT_TO"].strip()
    msg=EmailMessage()
    msg["From"]=sender
    msg["To"]=recipient
    msg["Subject"]=subject
    msg.set_content(content)
    if html_body:
        msg.add_alternative(html_body,subtype="html")
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
    if os.getenv("TEST_EMAIL","") == "1":
        send("【OKX邮件测试】GitHub Actions → QQ邮箱连接成功",
             "这是一封由GitHub Actions主动发送的测试邮件。\n\n收到了这封邮件，说明QQ SMTP登录及邮箱投递已基本接通。\n正式交易信号仍需结构、仓位、盈亏比和有效期全部通过验证。\n",templates.test())
        print("SMTP test email accepted by server.")
        return
    previous=json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    state=dict(previous)
    # Email one digest per Beijing hour, on the :34 collection (not on 15-minute collections).
    if 34<=now.minute<=48:
        hour_key=stamp.strftime("%Y-%m-%d %H")
        if previous.get("digest_hour")!=hour_key:
            try:
                text=ai_generate(data)
                title="【OKX AI行情分析】%s｜8币行情与风控" % stamp.strftime("%m-%d %H:%M")
            except Exception as ex:
                print("AI analysis unavailable; sending explicitly labelled rule report:",type(ex).__name__,str(ex)[:120])
                text=build(data,stamp)
                title="【OKX规则分析·AI未生成】%s｜8币行情与风控" % stamp.strftime("%m-%d %H:%M")
            send(title,text,templates.analysis(text,title))
            state["digest_hour"]=hour_key
            STATE.parent.mkdir(exist_ok=True)
            STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            print("Hourly report accepted by SMTP.")
    # Expiration notices are advisory only: no OKX order-status/position API is connected.
    alerted=dict(previous.get("alerted",{}))
    for key,rec in list(alerted.items()):
        if rec.get("notice_sent"): continue
        expiry_s=rec.get("expiry")
        if not expiry_s: continue
        expiry=datetime.fromisoformat(expiry_s)
        if now>=expiry:
            name=rec.get("sym","未知"); direction=rec.get("direction","")
            send("【OKX信号过期】%s %s｜未成交挂单请撤销" % (name,direction),
                 "该交易信号已超过有效期：%s（北京时间）。\n如你在OKX挂了未成交的限价单，请自行检查并取消；"
                 "如果已经成交，不要据此取消保护性止损。系统未连接你的订单或持仓，无法确认是否成交，也不会自动撤单。"
                 % expiry.strftime("%Y-%m-%d %H:%M:%S"),templates.expiry(name,direction,expiry))
            rec["notice_sent"]=True
            state["alerted"]=alerted
            STATE.parent.mkdir(exist_ok=True)
            STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            print("Expiry advisory accepted by SMTP:",name)
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
    # No more than 2 recommendations per snapshot; cap persistent identity set.
    for sym,r,key in candidates[:2]:
        direction="开多" if r["direction"]=="多" else "开空"
        subject="【OKX交易信号】%s %s｜限价%s｜有效10分钟" % (sym,direction,r["entry"])
        expiry=stamp+timedelta(minutes=10)
        # At collection + GitHub workflow latency this may already be expired; never send stale actionable plans.
        if now>=expiry or (expiry-now).total_seconds()<120: continue
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
        send(subject,body,templates.trade(sym,direction,r,stamp,expiry))
        alerted[key]={"sym":sym,"direction":direction,"time":stamp.isoformat(),"expiry":expiry.isoformat()}
        state["alerted"]=alerted
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("Trade email accepted by SMTP:",sym)
    # Never claim orders were cancelled: no account connection is present.
    # previously delivered limit orders need independent status checks on OKX.
    state["alerted"]=dict(list(alerted.items())[-100:])
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

if __name__=="__main__":
    run()
