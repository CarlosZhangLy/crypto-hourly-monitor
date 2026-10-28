"""Read-only indicative OKX USDT perpetual risk plans, sized in actual contracts."""
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import json

D=lambda x: Decimal(str(x))
def floor_step(v,s): return (v/s).to_integral_value(rounding=ROUND_DOWN)*s
def ceil_step(v,s): return (v/s).to_integral_value(rounding=ROUND_UP)*s

def fetch_specs(symbols):
    url="https://www.okx.com/api/v5/public/instruments?"+urlencode({"instType":"SWAP"})
    with urlopen(Request(url,headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"}),timeout=20) as r:
        obj=json.load(r)
    if str(obj.get("code"))!="0":raise RuntimeError("OKX instruments failed")
    return {x["instId"]:x for x in obj["data"] if x["instId"] in symbols}

def plan(x,spec):
    q=x.get("five_minute_structure") or {}
    if not q.get("confirmed") or q.get("bars_since_break",99)>6:return None
    side=q.get("side")
    if side not in ("long","short"):return None
    a=x["intervals"]; m=a["5m"]; t=a["15m"]; h=a["1h"]
    aligned=(t["ema20"]>t["ema50"] and h["ema20"]>h["ema50"]) if side=="long" else (t["ema20"]<t["ema50"] and h["ema20"]<h["ema50"])
    if not aligned or t["volume_ratio_vs_prior20"]<1.2:return None
    if spec.get("state")!="live" or spec.get("ctType")!="linear" or spec.get("settleCcy")!="USDT":return None
    px=D(x["last_price"]); atr=D(m["atr14"]); level=D(q["break_level"]); tick=D(spec["tickSz"])
    if not (px>0 and atr>0 and tick>0) or abs(px-level)>atr*D("1.5"):return None
    if side=="long":
        entry=floor_step(px,tick)
        stop=floor_step(min(D(m["recent_swing_low"]),level)-atr*D("0.25"),tick)
        target=floor_step(min(D(t["recent_swing_high"]),D(h["recent_swing_high"])),tick)
        if not stop<entry<target:return None
    else:
        entry=ceil_step(px,tick)
        stop=ceil_step(max(D(m["recent_swing_high"]),level)+atr*D("0.25"),tick)
        target=ceil_step(max(D(t["recent_swing_low"]),D(h["recent_swing_low"])),tick)
        if not target<entry<stop:return None
    ctval=D(spec["ctVal"]); lotsz=D(spec["lotSz"]); minsz=D(spec["minSz"])
    if not (ctval>0 and lotsz>0 and minsz>0):return None
    cost=D("0.002") # assumed round trip: 0.1% fees + 0.1% slippage
    loss_per_contract=(abs(entry-stop)+entry*cost)*ctval
    profit_per_contract=(abs(target-entry)-entry*cost)*ctval
    if profit_per_contract<=0 or profit_per_contract/loss_per_contract<D("1.8"):return None
    max_by_risk=D("1")/loss_per_contract
    max_by_margin=D("45")*D("2")/(entry*ctval)
    sz=floor_step(min(max_by_risk,max_by_margin),lotsz)
    if sz<minsz:return None
    notional=sz*ctval*entry; risk=sz*loss_per_contract
    return {"direction":"多" if side=="long" else "空","entry":str(entry),"stop":str(stop),
      "target":str(target),"contracts":str(sz),"ctVal":str(ctval),
      "notional":float(notional),"margin":float(notional/D("2")),"risk":float(risk),
      "gain":float(sz*profit_per_contract),"rr":float(profit_per_contract/loss_per_contract)}
