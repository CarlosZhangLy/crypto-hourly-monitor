#!/usr/bin/env python3
"""Generate an evidence-grounded Chinese hourly analysis through DeepSeek Chat Completions."""
import json
import os
from urllib.request import Request, urlopen

CORE=("BTC","ETH","SOL","XRP","LTC","ADA","BCH","LINK")
PERIODS=("5m","15m","1h","4h","1d")

def report_payload(data, plans=None):
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
    return {"source":data.get("source"),"snapshot":data["generated_beijing"],"errors":data.get("errors"),"symbols":rows, "conditional_plans":plans or []}

def generate(data, plans=None):
    key=os.getenv("DEEPSEEK_API_KEY","").strip()
    if not key:raise RuntimeError("DEEPSEEK_API_KEY not configured")
    model=os.getenv("DEEPSEEK_MODEL","deepseek-flash").strip()
    prompt=(
      "你是OKX USDT永续合约行情研究员，依据提供的已收盘5m、15m、1h、4h、日线数据解释市场。"
      "邮件最前面的【条件性交易预案】表格和具体入场、止盈止损及仓位已由Python按照OKX合约规格与0.20%估算成本计算，"
      "你收到的JSON conditional_plans就是那些预案。你的工作是独立分析交易条件的优缺点，不可修改或编造那些价格/评分/数量。"
      "特别要逐条点评预案：适合顺势还是逆势，支持与阻碍，需什么5m已收盘突破和回踩HL/LH，为什么目前只是待触发，"
      "推荐度是方案优先级评分不是胜率；不要把待触发方案说成已可挂单。"
      "不要重复完整预案表和计算过的合约数字：程序会将其放在报告最前面，你只负责紧接着写分析正文。"
      "正文按顺序：①一句本期结论；②各预案为什么进入榜单以及何时放弃；③八币紧凑行情表"
      "（币种、快照价、15m量比、15m/1h方向及结构、状态）；④5m到日线的关键矛盾及扩展观察；⑤风险总结。"
      "表格必须是严格Markdown表格：表头、分隔行、每行相同列数、每张表最多6列且单元格不换行。"
      "仅使用输入JSON数值，不虚构其他交易所数据、未给的历史行情、真实订单、账户余额或概率。"
      "程序confirmed和structure_hint是启发式规则，不等于严格BOS或CHoCH；"
      "布林轨道价格斜率不代表图表上的视觉20度夹角。"
      "如条件不足明确写现在不可直接委托，但不要只重复‘暂不交易’而省略交易条件分析。"
      "中文简练自然，禁止冗余免责声明、程序字段堆砌，正文建议600—1100汉字。")
    body={"model":model,
      "messages":[{"role":"system","content":prompt},
        {"role":"user","content":"请分析如下经采集程序生成的OKX结构化快照：\\n"+
          json.dumps(report_payload(data, plans),ensure_ascii=False,separators=(",",":"))}],
      "thinking":{"type":"disabled"},
      "max_tokens":3800,
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
