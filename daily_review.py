#!/usr/bin/env python3
"""Daily, auditable paper-trading replay of actually emailed OKX plans.

A: immediately place a normal price LIMIT for 120 min; if marketable, fill at
   the next 1m candle open (more favorable than the specified limit).
B: wait for CLOSED 5m breakout and >=1.2 volume ratio, retest and 5m HL/LH
   proxy confirmation; then activate a limit, all within the original 120 min.
Neither model submits real orders. No slippage, funding, or liquidation model.
Conservative double-taker fees: 0.05% entry + 0.05% exit.
"""
import argparse
import json
import math
import os
import smtplib
import ssl
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BJ=timezone(timedelta(hours=8))
UTC=timezone.utc
ROOT=Path("data/recommendations")
STATE=Path("data/daily_review_state.json")
FEE=0.0005
LEVELS=((0.004,0.0012,"40%→12%"),(0.01,0.006,"100%→60%"),
        (0.02,0.01,"200%→100%"),(0.04,0.03,"400%→300%"),
        (0.06,0.05,"600%→500%"),(0.08,None,"800%止盈"))
RISK_MAX=0.85
TOTAL_RISK_MAX=1.70
MAX_OPEN=2
ORDER_SECONDS=7200
EPS=1e-9

def parse_dt(s):
    d=datetime.fromisoformat(s)
    if d.tzinfo is None: raise ValueError("Missing timezone "+s)
    return d

def ms(d):
    return int(d.timestamp()*1000)

def fmt_dt(t):
    if not t: return "—"
    return datetime.fromtimestamp(t/1000,BJ).strftime("%m-%d %H:%M")

def as_float(x):
    v=float(x)
    if not math.isfinite(v): raise ValueError("nonfinite price/quantity")
    return v

def tick_round(value,tick,up):
    if tick<=0:return value
    d=Decimal(str(value))/Decimal(str(tick))
    return float(d.to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*Decimal(str(tick)))

def protect_stop(entry,side,profit_fraction,tick=0):
    # This first stage gives approximately +0.02% net notional after worst
    # standard 0.10% round-trip taker fees, assuming exact stop execution.
    px=entry*(1+profit_fraction if side=="多" else 1-profit_fraction)
    return tick_round(px,tick,side=="多")

def gross_net(trade,exit_price):
    sign=1 if trade["direction"]=="多" else -1
    units=trade["units"]
    gross=(exit_price-trade["fill_price"])*sign*units
    fees=(trade["fill_price"]+exit_price)*units*FEE
    return gross,fees,gross-fees

def maximum_favorable_fraction(trade,bar):
    entry=trade["fill_price"]
    return (bar["high"]/entry-1) if trade["direction"]=="多" else (1-bar["low"]/entry)

def close_trade(t,price,when,reason):
    gross,fees,net=gross_net(t,price)
    t.update(status="CLOSED",exit_price=price,exit_at=when,exit_reason=reason,
             gross_pnl=round(gross,8),fees=round(fees,8),net_pnl=round(net,8),
             net_roi_pct=round(100*net/t["margin_used"],4) if t["margin_used"] else None)

def stop_hit(t,bar,stop):
    return bar["low"]<=stop+EPS if t["direction"]=="多" else bar["high"]>=stop-EPS

def process_position(t,bar):
    if t["status"]!="OPEN":return
    current=t["protect_stop"]
    t["mark_price"]=bar["close"]
    t.pop("data_issue",None)
    # Conservative intraminute sequencing: old stop wins before new milestones.
    if stop_hit(t,bar,current):
        close_trade(t,current,bar["ts"],"原始止损" if t["highest_level"]==0 else "移动止损")
        return
    max_frac=max(0,maximum_favorable_fraction(t,bar))
    t["max_favorable_pct"]=round(max(t.get("max_favorable_pct",0),max_frac*100),6)
    for i,(trigger,protect,label) in enumerate(LEVELS,1):
        if max_frac+EPS<trigger or i<=t["highest_level"]:continue
        t["highest_level"]=i
        if protect is None:
            target=t["fill_price"]*(1.08 if t["direction"]=="多" else 0.92)
            close_trade(t,target,bar["ts"],"800%档位止盈")
            return
        newstop=protect_stop(t["fill_price"],t["direction"],protect,t.get("tick",0))
        if t["direction"]=="多":
            t["protect_stop"]=max(t["protect_stop"],newstop)
        else:
            t["protect_stop"]=min(t["protect_stop"],newstop)
    # Newly activated stops might be touched later within the same minute.
    # Unknown order of H/L -> pessimistically exit instead of claiming a hold.
    if t["highest_level"] and stop_hit(t,bar,t["protect_stop"]):
        close_trade(t,t["protect_stop"],bar["ts"],"同分钟触发及回撤（保守）")

def limit_fill(t,bar):
    px=t["limit"]
    if t["direction"]=="多":
        if bar["open"]<=px:return bar["open"]  # marketable buy LIMIT
        if bar["low"]<=px:return px
    else:
        if bar["open"]>=px:return bar["open"]  # marketable sell LIMIT
        if bar["high"]>=px:return px
    return None

def allowed_portfolio(trade,alltrades,model,fill):
    live=[v for v in alltrades.values() if v["model"]==model and v["status"]=="OPEN"]
    if len(live)>=MAX_OPEN:return False,"同时持仓已达2笔"
    risk=trade["units"]*(abs(fill-trade["stop"])+FEE*(fill+trade["stop"]))
    margin=trade["units"]*fill/trade["leverage"]
    if risk>RISK_MAX+EPS:return False,"单笔止损风险超出0.85U"
    if margin>25+EPS or trade["units"]*fill>75+EPS:return False,"保证金或名义仓位超限"
    # Exclude same-instrument same-side stacking to avoid duplicate exposure.
    if any(v["symbol"]==trade["symbol"] and v["direction"]==trade["direction"] for v in live):
        return False,"同币种同向仓位已存在"
    other_risk=sum(v["estimated_initial_risk"] for v in live)
    other_margin=sum(v["margin_used"] for v in live)
    if other_risk+risk>TOTAL_RISK_MAX+EPS:return False,"并持风险超过1.70U"
    if other_margin+margin>100+EPS:return False,"100U预算保证金不足"
    return True,(risk,margin)

def fill_trade(t,fill,bar,alltrades):
    ok,result=allowed_portfolio(t,alltrades,t["model"],fill)
    if not ok:
        t.update(status="SKIPPED",exit_reason=result)
        return
    risk,margin=result
    t.update(status="OPEN",fill_price=fill,fill_at=bar["ts"],
             protect_stop=t["stop"],highest_level=0,margin_used=margin,
             estimated_initial_risk=risk,max_favorable_pct=0)
    # Order can touch the initial stop in the entry minute; conservative.
    process_position(t,bar)

def input_records():
    records={}
    if not ROOT.exists():return records
    for file in ROOT.glob("*/*.json"):
        try:
            doc=json.loads(file.read_text(encoding="utf-8"))
            sent=parse_dt(doc["email_sent_bj"])
            for p in doc["plans"]:
                if p["id"] in records:raise ValueError("Duplicate recommendation ID")
                records[p["id"]]=(sent,p)
        except (KeyError,ValueError,TypeError,json.JSONDecodeError) as exc:
            raise RuntimeError("Invalid recommendation archive: "+str(file)) from exc
    return records

def initialize(rec,model):
    sent,p=rec
    try:
        direction=p["direction"]
        assert direction in ("多","空")
        size=as_float(p["contracts"])*as_float(p["ctval"])
        entry,stop=as_float(p["entry"]),as_float(p["stop"])
        lev=int(p["leverage"])
        if min(size,entry,stop)<=0 or lev not in (2,3,5,7,10):
            raise ValueError("invalid contracts/entry/stop")
        if (direction=="多" and stop>=entry) or (direction=="空" and stop<=entry):
            raise ValueError("stop direction mismatch")
        born=ms(sent)
        return {"id":p["id"],"model":model,"symbol":p["symbol"],"direction":direction,
                "score":p.get("score"),"sent_at":born,"expire_at":born+ORDER_SECONDS,
                "entry":entry,"limit":entry,"stop":stop,"units":size,"leverage":lev,
                "tick":as_float(p.get("tick_sz") or 0),
                "band":as_float(p.get("retest_band") or 0),
                "status":"WAIT" if model=="A" else "WAIT_SIGNAL",
                "last_bar":None,"highest_level":0,"max_favorable_pct":0}
    except (ValueError,KeyError,AssertionError) as exc:
        return {"id":p["id"],"model":model,"symbol":p.get("symbol","?"),
                "direction":p.get("direction","?"),"score":p.get("score"),
                "sent_at":ms(sent),"status":"UNVERIFIED","exit_reason":str(exc)}

def okx_1m(inst,start_ms,end_ms):
    """Get CLOSED minute candles, filter strictly to [start_ms,end_ms]."""
    cursor=end_ms+60000
    wanted_start=start_ms
    allbars={}
    for page in range(150):
        url="https://www.okx.com/api/v5/market/history-candles?"+urlencode(
            {"instId":inst,"bar":"1m","after":cursor,
             "before":max(0,wanted_start-60000),"limit":100})
        error=None
        for attempt in range(4):
            try:
                with urlopen(Request(url,headers={"User-Agent":"Mozilla/5.0 PaperReplay/1.0",
                    "Accept":"application/json"}),timeout=20) as r:
                    data=json.load(r)
                if str(data.get("code"))!="0":raise RuntimeError("OKX error "+str(data.get("msg")))
                rows=data.get("data",[])
                break
            except Exception as exc:
                error=exc
                time.sleep(0.6*(attempt+1))
        else:raise RuntimeError("OKX candles error for "+inst+": "+str(error))
        if not rows:break
        stamps=[]
        for r in rows:
            ts=int(r[0]); stamps.append(ts)
            if start_ms<=ts<=end_ms and r[8]=="1":
                allbars[ts]={"ts":ts,"open":float(r[1]),"high":float(r[2]),
                             "low":float(r[3]),"close":float(r[4]),"volume":float(r[7])}
        oldest=min(stamps)
        if oldest>=cursor:raise RuntimeError("Invalid pagination cursor "+inst)
        if oldest<=wanted_start:break
        cursor=oldest
        time.sleep(0.12)
    else:raise RuntimeError("Too many candle pages "+inst)
    out=sorted(allbars.values(),key=lambda x:x["ts"])
    # On missing minutes we must not silently infer fills, stop-outs or P&L.
    if out:
        missing=[t for t in range(max(start_ms,out[0]["ts"]),min(end_ms,out[-1]["ts"])+1,60000)
                 if t not in allbars]
        if missing:raise RuntimeError(inst+" missing "+str(len(missing))+" minute candles")
    return out

def five_minute_bars(bars):
    grouped=defaultdict(list)
    for b in bars:grouped[b["ts"]//300000].append(b)
    result={}
    done=[]
    for bucket,group in sorted(grouped.items()):
        group.sort(key=lambda x:x["ts"])
        if len(group)!=5:continue
        if any(group[i]["ts"]!=bucket*300000+60000*i for i in range(5)):continue
        vol=sum(x["volume"] for x in group)
        prior=[x["volume"] for x in done[-20:]]
        vratio=vol/statistics.mean(prior) if len(prior)==20 and statistics.mean(prior)>0 else None
        obj={"close":group[-1]["close"],"high":max(x["high"] for x in group),
             "low":min(x["low"] for x in group),"volume":vol,
             "ratio":vratio,"end":group[-1]["ts"]}
        done.append(obj)
        result[obj["end"]]=obj
    return result

def update_signal(t,bar,closed5,prev5):
    if t["status"]=="WAIT_SIGNAL":
        if not closed5 or closed5["ratio"] is None:return
        directional=(closed5["close"]>t["entry"] if t["direction"]=="多"
                     else closed5["close"]<t["entry"])
        if directional and closed5["ratio"]>=1.2:
            t["status"]="WAIT_RETEST"
            t["break_at"]=bar["ts"]
            t["break_swing"]=closed5["low"] if t["direction"]=="多" else closed5["high"]
        return
    if t["status"]=="WAIT_RETEST":
        band=t["band"]
        if (bar["low"]<=t["entry"]+band if t["direction"]=="多"
            else bar["high"]>=t["entry"]-band):
            t["retest_seen"]=True
        if not closed5 or not t.get("retest_seen") or not prev5:return
        favorable=(closed5["close"]>=t["entry"] if t["direction"]=="多" else closed5["close"]<=t["entry"])
        hl_lh=(closed5["low"]>t["break_swing"] if t["direction"]=="多"
               else closed5["high"]<t["break_swing"])
        if favorable and hl_lh:
            t["status"]="WAIT_LIMIT"
            t["limit_eligible_at"]=bar["ts"]+60000
        return

def replay(trades,records,cutoff_ms,fetcher=okx_1m):
    newids=[]
    for uid,rec in records.items():
        for model in ("A","B"):
            key=model+"|"+uid
            if key not in trades:
                trades[key]=initialize(rec,model)
                newids.append(key)
    active=[t for t in trades.values() if t["status"] not in
            ("CLOSED","EXPIRED","SKIPPED","UNVERIFIED","DATA_GAP")]
    relevant=[t for t in active if t["sent_at"]<=cutoff_ms+60000]
    if not relevant:return {}
    symbols={t["symbol"] for t in relevant}
    feeds={}
    failures={}
    for symbol in sorted(symbols):
        this=[t for t in relevant if t["symbol"]==symbol]
        starts=[]
        for t in this:
            # Need 100 minutes preceding the newly reviewed trades for 5m volume ratio.
            start=(t["last_bar"]+60000 if t.get("last_bar") is not None
                   else t["sent_at"]//60000*60000+60000)
            starts.append(start)
        start_ms=max(0,min(starts)-120*60000)
        try:
            bars=fetcher(symbol+"-USDT-SWAP",start_ms,cutoff_ms)
            if not bars or bars[-1]["ts"]<cutoff_ms-120000:
                raise RuntimeError("Missing latest confirmed minute bars")
            feeds[symbol]=bars
        except Exception as ex:
            failures[symbol]=str(ex)
            for t in this:
                t["data_issue"]=str(ex)
                t.pop("mark_price",None)
                t.pop("unrealized_net",None)
            continue
    # Cross-symbol chronological replay is needed to enforce total capital/risk.
    events=[]
    five_data={}
    for symbol,bars in feeds.items():
        fb=five_minute_bars(bars)
        five_data[symbol]=fb
        for b in bars:events.append((b["ts"],symbol,b))
    events.sort(key=lambda e:(e[0],e[1]))
    prev5_cache={}
    for ts,sym,bar in events:
        group=[t for t in trades.values() if t.get("symbol")==sym and
               t["status"] not in ("CLOSED","EXPIRED","SKIPPED","UNVERIFIED","DATA_GAP")
               and t["sent_at"]//60000*60000+60000<=ts
               and (t.get("last_bar") is None or t["last_bar"]<ts)]
        if not group:continue
        fb=five_data[sym].get(ts)
        prev5=prev5_cache.get(sym)
        for t in sorted(group,key=lambda x:(x["status"]!="OPEN",x["sent_at"],x["model"])):
            if t["status"]!="OPEN" and ts>=t["expire_at"]:
                t["status"]="EXPIRED"
                t["exit_reason"]="2小时未成交撤单"
                t["last_bar"]=ts
                continue
            if t["status"]=="OPEN":
                process_position(t,bar)
            elif t["model"]=="B" and t["status"] in ("WAIT_SIGNAL","WAIT_RETEST"):
                update_signal(t,bar,fb,prev5)
            elif t["status"] in ("WAIT","WAIT_LIMIT"):
                if t["status"]=="WAIT_LIMIT" and ts<t.get("limit_eligible_at",ts+1):pass
                else:
                    fill=limit_fill(t,bar)
                    if fill is not None:fill_trade(t,fill,bar,trades)
            t["last_bar"]=ts
        if fb:prev5_cache[sym]=fb
    # End-of-day unfilled orders are not assumed expired unless 120min elapsed.
    for t in relevant:
        if t["status"] in ("WAIT","WAIT_SIGNAL","WAIT_RETEST","WAIT_LIMIT") and cutoff_ms>=t["expire_at"]:
            t["status"]="EXPIRED"
            t["exit_reason"]="2小时未成交撤单"
    # Mark pending unknown data; do not misrepresent no-fill as proven.
    return failures

def mark_unrealized(t,candles):
    if t["status"]!="OPEN" or not candles:return 0.0
    px=candles[-1]["close"]
    gross,fees,net=gross_net(t,px)
    t["mark_price"]=px
    t["unrealized_net"]=round(net,8)
    t["unrealized_roi_pct"]=round(net/t["margin_used"]*100,4)
    return net

def tabulate(trades,records,day,now,failures):
    by_model=defaultdict(list)
    day_ids={uid for uid,(sent,p) in records.items() if sent.astimezone(BJ).date().isoformat()==day}
    for t in trades.values():
        if t["id"] in day_ids:
            by_model[t["model"]].append(t)
    lines=[f"# OKX模拟交易日报｜{day}", "",
           "模型A：邮件成功发送后从下一完整1m开始，普通限价单挂120分钟；买价高于市场价或卖价低于市场价时，可在模拟开始时立即成交。",
           "模型B：先满足已收盘5m突破、量比≥1.2、后续回踩和HL/LH代理结构确认，才激活限价；同样在邮件后120分钟到期。",
           "两者都是纸面模拟，B的HL/LH是简化代理判定，非严格BOS/CHoCH。每分钟最高最低价路径未知，止损优先作保守假设。",
           "手续费按双边吃单0.05%+0.05%计算，不含滑点、资金费、额外费用或强平；第一档+40%（100x口径）后，止损移至价格有利0.12%（100x口径+12%）；此前一直使用原始止损。",
           f"复盘截至：{now.astimezone(BJ):%Y-%m-%d %H:%M}，仅使用截至此时已收盘的OKX 1m K线。", ""]
    if failures:
        lines.extend(["## 数据质量警告",
                      "部分品种1m历史数据不可完整验证，因此相关交易状态可能保留为待确认："+
                      "；".join(f"{k}:{v[:110]}" for k,v in failures.items()),""])
    model_summaries={}
    for model in ("A","B"):
        rows=sorted(by_model[model],key=lambda x:(x["sent_at"],x["symbol"]))
        closed=[x for x in rows if x["status"]=="CLOSED"]
        opens=[x for x in rows if x["status"]=="OPEN"]
        nfill=len(closed)+len(opens)
        unfilled=sum(x["status"]=="EXPIRED" for x in rows)
        net=sum(x.get("net_pnl",0) for x in closed)
        floating=sum(x.get("unrealized_net",0) for x in opens)
        wins=sum(x.get("net_pnl",0)>0 for x in closed)
        losses=sum(x.get("net_pnl",0)<0 for x in closed)
        historical=[t for t in trades.values() if t["model"]==model]
        all_realized=sum(t.get("net_pnl",0) for t in historical if t["status"]=="CLOSED")
        all_floating=sum(t.get("unrealized_net",0) for t in historical if t["status"]=="OPEN")
        unknown_floating=any(t["status"]=="OPEN" and ("unrealized_net" not in t or t.get("data_issue")) for t in historical)
        model_summaries[model]={"plans":len(rows),"filled":nfill,"expired":unfilled,
                                "wins":wins,"losses":losses,"closed":len(closed),
                                "open":len(opens),"realized":net,"unrealized":floating,
                                "cumulative_realized":all_realized,"cumulative_floating":all_floating,
                                "equity_estimate":None if unknown_floating else 100+all_realized+all_floating}
        lines.extend([f"## 模型{model}｜{'收到即挂2小时' if model=='A' else '突破回踩确认后挂单'}",
            f"推荐{len(rows)}笔；模拟成交{nfill}笔；未成交撤单{unfilled}笔；"
            f"已平仓{len(closed)}笔（盈利{wins}、亏损{losses}）；仍持仓{len(opens)}笔；"
            f"已实现净盈亏 **{net:+.4f}U**，浮动净盈亏 **{floating:+.4f}U**。",
            f"已平仓胜率：{(wins/len(closed)*100):.1f}%" if closed else "已平仓胜率：暂无可计算样本",
            ("累计参考权益：无法可靠估值（存在未取得1m行情的持仓）" if unknown_floating else
             f"累计参考权益：**{100+all_realized+all_floating:.4f}U**（起始100U＋历史已实现＋未平仓浮动）"),
            "",
            "| 推荐时间 | 币种/方向 | 杠杆 | 限价/成交价 | 平仓价 | 状态/最高浮盈 | 净盈亏U |",
            "|---|---|---:|---:|---|---|---:|"])
        for t in rows:
            state=t["status"]
            labels={"WAIT":"待挂单结果","WAIT_SIGNAL":"未确认突破",
                    "WAIT_RETEST":"未确认回踩","WAIT_LIMIT":"等待限价成交",
                    "OPEN":"仍持仓","CLOSED":"已平仓","EXPIRED":"两小时未成交",
                    "SKIPPED":"风控跳过","UNVERIFIED":"数据/计划异常","DATA_GAP":"K线缺失"}
            label=labels.get(state,state)
            if t.get("fill_at"):label+=f"；进场{fmt_dt(t['fill_at'])}"
            if state=="CLOSED":label+=f"；平仓{fmt_dt(t['exit_at'])}"
            if state in ("CLOSED","OPEN"):
                label+=f"；最高{t.get('max_favorable_pct',0)*t['leverage']:.1f}%ROI"
            if t.get("exit_reason"):label+="；"+t["exit_reason"]
            if t.get("data_issue"):label+="；数据缺口（不保证完整）"
            pnl=(f"{t['net_pnl']:+.4f}" if state=="CLOSED" else
                 f"浮动{t.get('unrealized_net',0):+.4f}" if state=="OPEN" else "—")
            price_label=str(t.get("entry","—"))+" / "+str(round(t["fill_price"],8) if "fill_price" in t else "—")
            exit_label=str(round(t["exit_price"],8)) if "exit_price" in t else "—"
            lines.append(f"| {fmt_dt(t['sent_at'])} | {t['symbol']} {t['direction']} | "
                         f"{t.get('leverage','—')} | {price_label} | "
                         f"{exit_label} | {label} | {pnl} |")
        if not rows:lines.append("| — | — | — | — | 本日无可审计推荐记录 | — | — |")
        lines.extend(["","**说明：** 平仓盈亏已包含双边0.10%吃单费用；未满2小时或仍持仓的推荐不强行结算。"])
        if model=="B":
            lines.append("回踩和HL/LH取自1m聚合5m的代理识别，不能代表完全复刻每小时报告中的人工策略判断。")
        lines.append("")
    lines.extend(["## 当日市场/策略解读",
      "重点比较两套执行方式的成交率、已平仓净盈亏与最高浮盈回撤。推荐度是条件质量评分，不是已验证胜率。",
      "不能仅按行情后续涨跌判定历史推荐已成交；无成交的推荐净盈亏为0，实时未结算持仓独立列示。",
      "模拟同时持仓最多2笔、每笔初始风险≤0.85U、同时初始风险≤1.70U、单笔保证金≤25U、名义≤75U；"
      "两种模型各按独立100U参考本金计算，不代表真实账户权益。",
      "历史K线无法重建真实订单簿排队、分钟内事件顺序及强平机制；实际交易结果可能不同。"])
    return "\n".join(lines)+"\n",model_summaries

def send_email(title,text):
    needed=["QQ_SMTP_USER","QQ_SMTP_AUTH_CODE","ALERT_TO"]
    if not all(os.getenv(x,"").strip() for x in needed):
        raise RuntimeError("QQ SMTP secrets missing; cannot deliver daily review")
    from email_templates import render_markdown, shell
    html=shell(title,"OKX DAILY PAPER-TRADING REVIEW",render_markdown(text))
    msg=EmailMessage()
    msg["From"]=os.environ["QQ_SMTP_USER"].strip()
    msg["To"]=os.environ["ALERT_TO"].strip()
    msg["Subject"]=title
    msg.set_content(text)
    msg.add_alternative(html,subtype="html")
    with smtplib.SMTP_SSL("smtp.qq.com",465,context=ssl.create_default_context(),timeout=25) as smtp:
        smtp.login(os.environ["QQ_SMTP_USER"].strip(),os.environ["QQ_SMTP_AUTH_CODE"].strip())
        smtp.send_message(msg)

def run(day=None,dry_run=False):
    now=datetime.now(BJ)
    day=day or (now-timedelta(hours=2)).date().isoformat()
    if day>now.date().isoformat():
        raise ValueError("Cannot review a future day")
    if day==now.date().isoformat():
        cutoff=(int(now.timestamp())//60-1)*60000
    else:
        midnight=datetime.fromisoformat(day+"T00:00:00+08:00")
        cutoff=ms(midnight+timedelta(days=1))-60000
    prior=json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if prior.get("last_review_date") and day<prior["last_review_date"]:
        raise ValueError("Old date review would corrupt incremental state")
    if not dry_run and prior.get("last_review_date")==day:
        print("Daily review already delivered for",day)
        return
    trades=prior.get("trades",{})
    records=input_records()
    failures=replay(trades,records,cutoff)
    # For a still-open position, the last closed minute's close is the notional
    # mark. Fetching explicit marks is avoided; latest processed candle close
    # is stored by replay per position below.
    # All realized results stay in state for transparent future carry-forward.
    for t in trades.values():
        if t["status"]=="OPEN" and t.get("mark_price") and t.get("margin_used") and not t.get("data_issue"):
            gross,fees,net=gross_net(t,t["mark_price"])
            t["unrealized_net"]=net
            t["unrealized_roi_pct"]=100*net/t["margin_used"]
    report,summaries=tabulate(trades,records,day,now,failures)
    output=Path("data/daily_reviews")/(day+".md")
    detail=Path("data/daily_reviews")/(day+".json")
    if dry_run:
        print(report)
        return report
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(report,encoding="utf-8")
    detail.write_text(json.dumps({"day":day,"generated_beijing":now.isoformat(),"summaries":summaries,
        "failures":failures,"records":[t for t in trades.values()
        if datetime.fromtimestamp(t["sent_at"]/1000,BJ).date().isoformat()==day]},
        ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    STATE.write_text(json.dumps({"schema_version":1,"last_review_date":day,"trades":trades},
        ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    title="【OKX每日模拟复盘】%s｜A/B限价策略与阶梯止损" % day
    send_email(title,report)
    print("Daily review accepted by SMTP; results saved",day)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--day",help="Beijing review day YYYY-MM-DD")
    p.add_argument("--dry-run",action="store_true",help="calculate without writing/sending")
    args=p.parse_args()
    run(args.day,args.dry_run)

if __name__=="__main__":main()
