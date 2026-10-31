"""Deterministic, conditional OKX plans for hourly research email; never places orders.

Prices/contract counts come from the OKX snapshot and live instrument specs.
A plan is not a signal: entry is only considered after future closed-candle confirmation.
"""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP

CORE = ("BTC", "ETH", "SOL", "XRP", "LTC", "ADA", "BCH", "LINK")
D = lambda v: Decimal(str(v))
FEE_SLIP = D("0.002")  # illustrative 0.10% round-trip fees + 0.10% slippage
RISK_CAP = D("0.85")
MARGIN_CAP = D("35")
LEVERAGE = D("2")


def round_to(v, tick, rounding):
    return (v / tick).to_integral_value(rounding=rounding) * tick


def fmt(v):
    return format(v.normalize(), "f")


def side_for(x):
    p = x["intervals"]
    t, h = p["15m"], p["1h"]
    # Prefer the hourly direction; allow a 15m reversal only if 1h close also
    # crosses its EMA20. These are heuristics, NOT confirmed BOS/CHoCH.
    if t["ema20"] > t["ema50"] and t["closed_candle_close"] > t["ema20"] and h["closed_candle_close"] > h["ema20"]:
        return "多"
    if t["ema20"] < t["ema50"] and t["closed_candle_close"] < t["ema20"] and h["closed_candle_close"] < h["ema20"]:
        return "空"
    return "多" if h["ema20"] > h["ema50"] else "空"


def candidate(symbol, x, spec, stamp):
    if spec.get("state") != "live" or spec.get("ctType") != "linear" or spec.get("settleCcy") != "USDT":
        return None
    if spec.get("instId") != symbol + "-USDT-SWAP":
        return None
    tick, ct, step, minimum = (D(spec[k]) for k in ("tickSz", "ctVal", "lotSz", "minSz"))
    if min(tick, ct, step, minimum) <= 0:
        return None

    p = x["intervals"]
    m, t, h, four, day = (p[k] for k in ("5m", "15m", "1h", "4h", "1d"))
    side = side_for(x)
    last = D(x["last_price"])
    atr = D(m["atr14"])
    hi, lo = D(m["recent_swing_high"]), D(m["recent_swing_low"])
    if not (last > 0 and atr > 0 and hi > lo > 0):
        return None

    # A future breakout followed by a retest, not a live limit order at last_price.
    pad = max(tick * 2, atr * D("0.12"))
    if side == "多":
        entry = round_to(max(last, hi, D(m["closed_candle_close"])) + pad, tick, ROUND_UP)
        stop = round_to(min(lo, entry - atr * D("1.6")) - atr * D("0.25"), tick, ROUND_DOWN)
    else:
        entry = round_to(min(last, lo, D(m["closed_candle_close"])) - pad, tick, ROUND_DOWN)
        stop = round_to(max(hi, entry + atr * D("1.6")) + atr * D("0.25"), tick, ROUND_UP)

    risk_distance = abs(entry - stop)
    if not (risk_distance > tick and entry > tick and stop > 0):
        return None
    # Estimate entry+exit fee/slippage per unit notional at entry.
    cost_distance = entry * FEE_SLIP
    # TP1/TP2 guarantee 1.9R / 2.9R NET under the cost assumption,
    # before fills, funding and any exceptional volatility.
    tp1_dist = risk_distance * D("1.9") + cost_distance * D("2.9")
    tp2_dist = risk_distance * D("2.9") + cost_distance * D("3.9")
    if side == "多":
        tp1 = round_to(entry + tp1_dist, tick, ROUND_UP)
        tp2 = round_to(entry + tp2_dist, tick, ROUND_UP)
    else:
        tp1 = round_to(entry - tp1_dist, tick, ROUND_DOWN)
        tp2 = round_to(entry - tp2_dist, tick, ROUND_DOWN)
    if min(tp1, tp2) <= 0:
        return None

    loss_per_contract = (risk_distance + cost_distance) * ct
    sz_risk = RISK_CAP / loss_per_contract
    sz_margin = MARGIN_CAP * LEVERAGE / (entry * ct)
    amount = round_to(min(sz_risk, sz_margin), step, ROUND_DOWN)
    if amount < minimum:
        return None
    notional = amount * ct * entry
    margin = notional / LEVERAGE
    risk = amount * loss_per_contract
    rr1 = ((abs(tp1 - entry) - cost_distance) * ct * amount) / risk
    rr2 = ((abs(tp2 - entry) - cost_distance) * ct * amount) / risk
    if risk > D("1") or margin > MARGIN_CAP or rr1 < D("1.8"):
        return None

    dir_sign = 1 if side == "多" else -1
    trend15 = (D(t["ema20"]) - D(t["ema50"])) * dir_sign > 0
    trend1h = (D(h["ema20"]) - D(h["ema50"])) * dir_sign > 0
    trend4h = (D(four["closed_candle_close"]) - D(four["ema20"])) * dir_sign > 0
    trendday = (D(day["closed_candle_close"]) - D(day["ema20"])) * dir_sign > 0
    vol5, vol15 = D(m["volume_ratio_vs_prior20"]), D(t["volume_ratio_vs_prior20"])
    hint = str(t.get("structure_hint", ""))
    favorable_hint = ("HH" in hint and "HL" in hint) if side == "多" else ("LH" in hint and "LL" in hint)
    score = (39 + (15 if trend15 else 0) + (14 if trend1h else 0)
             + (6 if trend4h else 0) + (3 if trendday else 0)
             + (7 if vol15 >= D("1.0") else 3 if vol15 >= D("0.8") else 0)
             + (5 if vol5 >= D("1.0") else 0) + (4 if favorable_hint else 0))
    dist_atr = abs(entry - last) / atr
    if dist_atr > 3:
        score -= 9
    elif dist_atr > 1.5:
        score -= 4
    score = max(25, min(88, score))
    q = x.get("five_minute_structure") or {}
    # Never infer a strict BOS / CHoCH from a heuristic flag.
    confirmed = bool(q.get("confirmed")) and q.get("bars_since_break", 99) <= 12
    status = "待触发：当前不可直接委托"
    expires = stamp + timedelta(minutes=60)

    return {
        "symbol": symbol, "direction": side, "status": status,
        "last_price": fmt(last), "entry": fmt(entry), "stop": fmt(stop),
        "tp1": fmt(tp1), "tp2": fmt(tp2), "score": score,
        "leverage": 2, "contracts": fmt(amount), "ctval": fmt(ct),
        "notional": float(notional), "margin": float(margin),
        "risk": float(risk), "rr1": round(float(rr1), 2), "rr2": round(float(rr2), 2),
        "fee_slippage_assumption": "0.20%双边总成本假设",
        "breakout": fmt(entry), "retest_band": fmt(max(tick, atr * D("0.2"))),
        "invalid": fmt(stop),
        "expires_bj": expires.strftime("%m-%d %H:%M"),
        "atr5": float(atr), "vol5": float(vol5), "vol15": float(vol15),
        "trend15": trend15, "trend1h": trend1h, "trend4h": trend4h,
        "structure_hint": hint, "program_confirmed": confirmed,
        "reasons": [
            "15m/1h EMA同向" if trend15 and trend1h else "多周期趋势有分歧",
            "15m量比%.2f" % float(vol15),
            "程序回踩已确认但仍须重新验证" if confirmed else "程序回踩未完成",
        ],
    }


def build_plans(data, specs, count=2):
    stamp = datetime.fromisoformat(data["generated_beijing"])
    results = []
    for symbol in CORE:
        x = (data.get("symbols") or {}).get(symbol + "USDT")
        spec = specs.get(symbol + "-USDT-SWAP")
        if not x or not spec or any(k not in x.get("intervals", {}) for k in ("5m", "15m", "1h", "4h", "1d")):
            continue
        try:
            rec = candidate(symbol, x, spec, stamp)
            if rec:
                results.append(rec)
        except (ValueError, TypeError, ArithmeticError, KeyError, OverflowError):
            continue
    results.sort(key=lambda p: (-p["score"], -p["rr1"], p["symbol"]))
    # Keep 1-2 strongest candidates; never claim a candidate is already an order.
    return results[:count]


def markdown(plans, stamp):
    if not plans:
        return ("## 条件性交易预案\n"
                "本次未能按OKX合约规格与100U风险预算验证出可计算的合约数量；不发布未经验证的委托点位。")
    rows = [
        "## 条件性交易预案（AI分析前置，非直接委托）",
        "| 币种/方向 | 触发后限价入场 | 止损 | 止盈TP1 | 止盈TP2 | 推荐度 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for p in plans:
        rows.append("| {symbol} {direction} | {entry} | {stop} | {tp1} | {tp2} | {score}% |".format(**p))
    rows.append("")
    for p in plans:
        rows.extend([
            "### {symbol} {direction}｜{score}%｜{status}".format(**p),
            "OKX快照价：{last_price}；等待未来5m收盘有效突破{entry}，且15m量比恢复到0.8以上，之后回踩入场价±{retest_band}形成相应HL/LH，再考虑在{entry}附近限价；未触发严禁提前挂单。".format(**p),
            "逐仓{leverage}倍；{contracts}张，合约面值{ctval}；名义本金约{notional:.2f}U，保证金约{margin:.2f}U；含费用和滑点最大估计损失{risk:.2f}U（{fee_slippage_assumption}）；扣成本净盈亏比TP1 {rr1}:1、TP2 {rr2}:1。".format(**p),
            "止损{stop}，结构跌破/上破止损方向即失效。预案有效至北京时间{expires_bj}；到期未满足触发与回测条件直接作废；若已挂单未成交需手动撤销。".format(**p),
            "推荐度是条件质量评分而非胜率；依据：{reasons}。程序结构确认={program_confirmed}；structure_hint={structure_hint}仅为启发式，非严格BOS/CHoCH。".format(**{**p, "reasons": "、".join(p["reasons"])}),
            "",
        ])
    rows.append("两套预案均处于待触发状态，不代表已经满足下单条件或存在成交；需在OKX重新核对实时报价、最小张数、实际手续费及已有仓位，总风险不超过2U。")
    return "\n".join(rows)
