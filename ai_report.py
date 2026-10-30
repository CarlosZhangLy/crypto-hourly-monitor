#!/usr/bin/env python3
"""Generate an evidence-grounded Chinese hourly analysis through DeepSeek Chat Completions."""
import json
import os
from urllib.request import Request, urlopen

CORE=("BTC","ETH","SOL","XRP","LTC","ADA","BCH","LINK")
PERIODS=("5m","15m","1h","4h","1d")

def report_payload(data):
    rows={}
    for sym in CORE:
        x=data["symbols"][sym+"USDT"]
        a=x["intervals"]
        rows[sym]={
          "price":x["last_price"],"ticker_time_bj":x["ticker_time_bj"],
          "pct24h":x.get("price_change_pct_24h"),"structure":x.get("five_minute_structure"),
          "periods":{p:{k:a[p].get(k) for k in
            ("bar_time_bj","closed_candle_close","ema20","ema50","atr14","bb_expanding",
             "bb_uptrend_expansion","bb_downtrend_expansion","volume_ratio_vs_prior20",
             "recent_swing_high","recent_swing_low","structure_hint")} for p in PERIODS}}
    return {"source":data.get("source"),"snapshot":data["generated_beijing"],"errors":data.get("errors"),"symbols":rows}

def generate(data):
    key=os.getenv("DEEPSEEK_API_KEY","").strip()
    if not key:raise RuntimeError("DEEPSEEK_API_KEY not configured")
    model=os.getenv("DEEPSEEK_MODEL","deepseek-flash").strip()
    prompt=("你是一名严格依照OKX已收盘行情数据分析的合约市场研究助手。"
      "使用简体中文写每小时完整但易读的AI行情报告。必须包含北京时间和OKX来源、"
      "八币行情表、五周期均线趋势、15m量比及ATR/布林/结构分析、相对上一周期可见信息的趋势结论、"
      "最多两个观察标的和风险控制总结。不能虚构前期快照、订单、实时行情、胜率或成交。"
      "structure_hint仅启发式，confirmed仅程序规则确认非真实BOS/CHoCH。"
      "布林价差不得冒充视觉20度。没有完整可下单风险计划时必须写暂不交易。"
      "这是纯分析邮件，不得生成可执行委托或声称实时价格。"
      "所有引用数值只来自输入JSON，合理精简，不输出其他来源信息。")
    body={"model":model,
      "messages":[{"role":"system","content":prompt},
        {"role":"user","content":"请分析如下经采集程序生成的OKX结构化快照：\\n"+
          json.dumps(report_payload(data),ensure_ascii=False,separators=(",",":"))}],
      "thinking":{"type":"disabled"},
      "max_tokens":3500,
      "stream":False}
    req=Request("https://api.deepseek.com/chat/completions",
      data=json.dumps(body,ensure_ascii=False).encode("utf-8"),
      headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},method="POST")
    with urlopen(req,timeout=100) as resp: result=json.load(resp)
    choices=result.get("choices") or []
    if not choices:raise RuntimeError("DeepSeek response has no choices")
    ans=(choices[0].get("message") or {}).get("content") or ""
    ans=ans.strip()
    if len(ans)<200:raise RuntimeError("DeepSeek response empty or too short")
    print("DeepSeek AI analysis generated; usage tokens:",(result.get("usage") or {}).get("total_tokens","unknown"))
    return ans
