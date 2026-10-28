import json, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

CORE = ("BTC", "ETH", "SOL", "XRP", "LTC", "ADA", "BCH", "LINK")
PERIODS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}

def main():
    data = json.loads(Path("data/latest.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone(timedelta(hours=8)))
    stamp = datetime.fromisoformat(data["generated_beijing"])
    if not timedelta(0) <= now - stamp <= timedelta(minutes=45) or data.get("errors"):
        raise ValueError("Snapshot too old or errors found")
    lines = [f"## OKX行情摘要 {stamp:%m-%d %H:%M}", "", "|币种|快照价|24h|15m结构|均线方向|", "|---|---:|---:|---|---|"]
    for sym in CORE:
        x = data["symbols"][sym + "USDT"]
        ticker = datetime.fromisoformat(x["ticker_time_bj"])
        if abs((stamp - ticker).total_seconds()) > 900:
            raise ValueError(sym + " ticker stale")
        periods = x["intervals"]
        for p, minutes in PERIODS.items():
            end = datetime.fromisoformat(periods[p]["bar_time_bj"]) + timedelta(minutes=minutes)
            if end > stamp or stamp - end > timedelta(minutes=max(minutes, 30)):
                raise ValueError(sym + " " + p + " candle invalid")
        t = periods["15m"]
        alignment = [periods[p]["ema20"] > periods[p]["ema50"] for p in ("15m", "1h", "4h")]
        direction = "偏强" if all(alignment) else "偏弱" if not any(alignment) else "分歧"
        lines.append(f"|{sym}|{x['last_price']}|{x['price_change_pct_24h']:+.2f}%|{t['structure_hint']}|{direction}|")
    lines += ["", "### 重点观察（规则筛选）"]
    for sym in CORE:
        t = data["symbols"][sym + "USDT"]["intervals"]["15m"]
        if t["bb_expanding"] and t["volume_ratio_vs_prior20"] >= 1.5:
            lines.append(f"- {sym}：15m布林扩张，量比{t['volume_ratio_vs_prior20']:.2f}")
    lines += ["", "**结论：暂不交易。** 程序结构提示非确认的BOS/CHoCH；不生成自动委托。", "参考本金100U，单笔风险≤1U、总风险≤2U。", "OKX历史快照，仅供观察。"]
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token:
        raise ValueError("PUSHPLUS_TOKEN missing")
    payload = {"token": token, "title": f"OKX每小时行情 {stamp:%H:%M}", "content": "\n".join(lines), "template": "markdown", "channel": "wechat"}
    req = Request("https://www.pushplus.plus/send", data=json.dumps(payload, ensure_ascii=False).encode(), headers={"Content-Type":"application/json"}, method="POST")
    with urlopen(req, timeout=20) as response:
        result = json.load(response)
    if result.get("code") != 200:
        raise RuntimeError(f"PushPlus rejected: {result.get('code')} {result.get('msg')}")
    print("PushPlus accepted the summary. Check WeChat delivery.")

if __name__ == "__main__":
    main()
