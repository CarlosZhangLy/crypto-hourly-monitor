#!/usr/bin/env python3
"""Rule-based OKX research digest for PushPlus. Read only, no order execution."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

CORE = ("BTC", "ETH", "SOL", "XRP", "LTC", "ADA", "BCH", "LINK")
PERIODS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
BJ = timezone(timedelta(hours=8))

def check(snapshot, now=None):
    now = now or datetime.now(BJ)
    stamp = datetime.fromisoformat(snapshot["generated_beijing"])
    if stamp.tzinfo is None or not timedelta(0) <= now - stamp <= timedelta(minutes=45):
        raise ValueError("Invalid or expired GitHub snapshot")
    if snapshot.get("errors"):
        raise ValueError("Collector has errors; no trade-oriented summary")
    if not str(snapshot.get("source", "")).startswith("OKX"):
        raise ValueError("Unexpected data source")
    for sym in CORE:
        x = snapshot["symbols"][sym + "USDT"]
        tick = datetime.fromisoformat(x["ticker_time_bj"])
        if tick.tzinfo is None or abs((stamp - tick).total_seconds()) > 900:
            raise ValueError(sym + " stale ticker")
        for p, length in PERIODS.items():
            c = x["intervals"][p]
            start = datetime.fromisoformat(c["bar_time_bj"])
            if start.tzinfo is None:
                raise ValueError(sym + " missing candle timezone")
            end = start + timedelta(minutes=length)
            if end > stamp or stamp - end > timedelta(minutes=max(30, length)):
                raise ValueError(sym + " " + p + " missing recent closed candle")
            for k in ("ema20", "ema50", "atr14", "volume_ratio_vs_prior20", "recent_swing_high", "recent_swing_low"):
                if k not in c:
                    raise ValueError(sym + " " + p + " missing " + k)
    return stamp

def evaluate(sym, x):
    a = x["intervals"]
    m5, m15, h1, h4, day = [a[k] for k in ("5m", "15m", "1h", "4h", "1d")]
    price = float(x["last_price"])
    trend = [h1["ema20"] > h1["ema50"], h4["ema20"] > h4["ema50"], day["ema20"] > day["ema50"]]
    trend_count = sum(trend)
    direction = "偏多" if trend_count == 3 else "偏空" if trend_count == 0 else "震荡/分歧"
    atr_pct = 100 * m15["atr14"] / max(price, 1e-12)
    vol = m15["volume_ratio_vs_prior20"]
    bull_break = bool(m15["close_above_prior20_high"] or h1["close_above_prior20_high"])
    bear_break = bool(m15["close_below_prior20_low"] or h1["close_below_prior20_low"])
    bb_bull = bool(m15["bb_expanding"] and price >= m15["bb20_middle"])
    bb_bear = bool(m15["bb_expanding"] and price < m15["bb20_middle"])
    recent_support = float(m15["recent_swing_low"])
    recent_resistance = float(m15["recent_swing_high"])
    # Approximate proximity is a WATCH clue, not evidence of a completed retest.
    support_near = 0 <= price - recent_support <= 0.8 * m15["atr14"]
    resistance_near = 0 <= recent_resistance - price <= 0.8 * m15["atr14"]
    # Independent factors: higher timeframe direction, trigger, liquidity and volatility.
    long_score = sum((trend_count >= 2, m15["ema20"] > m15["ema50"], bull_break, bb_bull, vol >= 1.3, support_near))
    short_score = sum((trend_count <= 1, m15["ema20"] < m15["ema50"], bear_break, bb_bear, vol >= 1.3, resistance_near))
    if long_score > short_score:
        side, score = "多头观察", long_score
    elif short_score > long_score:
        side, score = "空头观察", short_score
    else:
        side, score = "方向未定", long_score
    flags = []
    if bull_break: flags.append("收盘突破前20根高点")
    if bear_break: flags.append("收盘跌破前20根低点")
    if m15["bb_expanding"]: flags.append("15m布林外扩")
    if vol >= 1.3: flags.append("15m量比%.2f" % vol)
    if support_near: flags.append("接近15m支撑")
    if resistance_near: flags.append("接近15m阻力")
    if atr_pct >= 1.8: flags.append("短线波动偏高")
    # Never assert BOS/CHoCH or a confirmed pullback from heuristic pivot arrays.
    quality = "重点观察" if score >= 4 and vol >= 1.2 and atr_pct < 1.8 else "一般观察" if score >= 3 else "暂不交易"
    return dict(sym=sym, price=price, pct=float(x["price_change_pct_24h"]),
                direction=direction, side=side, score=score, quality=quality,
                flags=flags, support=recent_support, resistance=recent_resistance,
                atr_pct=atr_pct, hint=m15["structure_hint"])

def digest_status(snapshot):
    ranked = []
    for sym in CORE:
        x = snapshot["symbols"][sym + "USDT"]
        r = evaluate(sym, x)
        q = x.get("five_minute_structure", {})
        p = x["intervals"]
        if q.get("confirmed") and q.get("bars_since_break", 100) <= 3:
            side = q.get("side")
            aligned = (side == "long" and p["15m"]["ema20"] > p["15m"]["ema50"] and p["1h"]["ema20"] > p["1h"]["ema50"]) or (side == "short" and p["15m"]["ema20"] < p["15m"]["ema50"] and p["1h"]["ema20"] < p["1h"]["ema50"])
            if aligned and p["15m"]["volume_ratio_vs_prior20"] >= 1.2:
                ranked.append((3, sym, "5m回踩初步确认，需即时复核价格和风险"))
        if r["quality"] == "重点观察":
            ranked.append((2, sym, "多因素共振，等待5m确认"))
        elif r["quality"] == "一般观察":
            ranked.append((1, sym, "有初步线索，条件尚不完整"))
    ranked.sort(reverse=True)
    unique = []
    for priority, sym, explanation in ranked:
        if sym not in [z[0] for z in unique]:
            unique.append((sym, explanation, priority))
    if unique:
        top = unique[0]
        heading = "结构确认待复核" if top[2] == 3 else "重点观察" if top[2] == 2 else "有机会线索"
        return heading + "：" + "、".join(v[0] for v in unique[:2]), unique[:2]
    return "暂无合格机会", []

def build(snapshot, stamp):
    records = [evaluate(s, snapshot["symbols"][s+"USDT"]) for s in CORE]
    out = ["## OKX多指标综合监测 %s" % stamp.strftime("%m-%d %H:%M"),
           "行情：OKX USDT永续；仅已收盘K线。规则筛选，非AI回复。",
           "", "|币种|快照价|24h|趋势|观察方向|",
           "|---|---:|---:|---|---|"]
    for r in records:
        out.append("|%s|%s|%+.2f%%|%s|%s|" %
                   (r["sym"], format(r["price"], ".5g"), r["pct"], r["direction"], r["side"]))
    watch = sorted((r for r in records if r["quality"] != "暂不交易"),
                   key=lambda r: (r["quality"] == "重点观察", r["score"]), reverse=True)[:2]
    out += ["", "### 综合筛选优先观察（最多2个）"]
    if not watch:
        out.append("无满足多因素条件的候选，暂不交易。")
    for r in watch:
        details = "、".join(r["flags"][:4]) or "尚缺少成交量/突破确认"
        out.append("- **%s %s｜%s**：综合因子%d/6；%s；15m波动ATR/价约%.2f%%。" %
                   (r["sym"], r["side"], r["quality"], r["score"], details, r["atr_pct"]))
        out.append("  15m参考高/低：%s / %s USDT。仅作观察；尚未证实BOS/CHoCH及突破回踩。" %
                   (r["resistance"], r["support"]))
    out += ["", "### 5分钟突破与回踩确认（仅限采集时点）"]
    found = False
    for sym in CORE:
        q = snapshot["symbols"][sym + "USDT"].get("five_minute_structure")
        if not q or q.get("status") == "insufficient_pivots":
            continue
        if q.get("status") not in ("break_watch", "retest_confirmed"):
            continue
        found = True
        side = "做多观察" if q.get("side") == "long" else "做空观察"
        label = "突破回踩结构初步确认" if q.get("confirmed") else "突破后等待回踩确认"
        out.append("- **%s %s**：%s；5m关键结构位 %s，最近已收盘K线 %s。" %
                   (sym, side, label, q.get("break_level"), q.get("latest_closed_bar")))
        if q.get("confirmed"):
            out.append("  已满足程序定义的触及回踩区、局部HL/LH确认及重返结构位；仍需15m趋势、量能及扣费后盈亏比验证，不是实时买卖指令。")
    if not found:
        out.append("最近5分钟K线没有形成符合程序定义的突破回踩观察线索。")
    status, top = digest_status(snapshot)
    out += ["", "**本期机会状态：" + status + "。**", "本报告是历史快照规则筛选，观察/初步确认均不代表现在可挂单；需要最新报价、结构有效性和扣费后盈亏比复核。",
            "布林外扩不是视觉20°；结构标签仅为程序提示。ATR用于过滤异常波动，不能保证风险。",
            "本金参考100U；单笔预估损失≤1U，同时总风险≤2U（含手续费及滑点）；不自动交易。",
            "[GitHub行情数据](https://github.com/CarlosZhangly/crypto-hourly-monitor/blob/main/data/latest.md)"]
    return "\n".join(out)

def main():
    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token:
        raise ValueError("Missing PUSHPLUS_TOKEN secret")
    snapshot = json.loads(Path("data/latest.json").read_text(encoding="utf-8"))
    stamp = check(snapshot)
    status, _ = digest_status(snapshot)
    body = {"token": token, "title": "OKX综合监测 %s｜%s" % (stamp.strftime("%H:%M"), status),
            "content": build(snapshot, stamp), "template": "markdown", "channel": "wechat"}
    req = Request("https://www.pushplus.plus/send",
                  data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=20) as response:
        result = json.load(response)
    if result.get("code") != 200:
        raise RuntimeError("PushPlus API error: %s" % result.get("msg"))
    print("PushPlus accepted rule-based digest; receipt should be checked in WeChat.")

if __name__ == "__main__":
    main()
