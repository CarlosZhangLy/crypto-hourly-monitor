"""Five-minute structural research signals from CLOSED OKX candles only."""
from datetime import datetime

def analyze(bars):
    # Confirmed fractal pivots require 2 bars on each side (no repaint from future candles).
    a=bars[-80:]
    highs=[]; lows=[]
    for i in range(2,len(a)-2):
        w=a[i-2:i+3]
        if a[i]["high"]==max(z["high"] for z in w) and sum(z["high"]==a[i]["high"] for z in w)==1:
            highs.append((i,a[i]["high"]))
        if a[i]["low"]==min(z["low"] for z in w) and sum(z["low"]==a[i]["low"] for z in w)==1:
            lows.append((i,a[i]["low"]))
    if not highs or not lows:
        return {"status":"insufficient_pivots","confirmed":False}
    last=a[-1]
    # Use only structural levels confirmed BEFORE the break candle.
    events=[]
    for i in range(max(22,len(a)-18),len(a)):
        prior_h=[p for p in highs if p[0]+2<i]
        prior_l=[p for p in lows if p[0]+2<i]
        if not prior_h or not prior_l: continue
        h=prior_h[-1][1]; l=prior_l[-1][1]
        close=a[i]["close"]; prev=a[i-1]["close"]
        if close>h and prev<=h:
            events.append(("long",i,h))
        if close<l and prev>=l:
            events.append(("short",i,l))
    atr=sum(max(a[j]["high"]-a[j]["low"],abs(a[j]["high"]-a[j-1]["close"]),abs(a[j]["low"]-a[j-1]["close"])) for j in range(len(a)-14,len(a)))/14
    out={"status":"no_recent_break","confirmed":False,"atr14":round(atr,10),
         "latest_closed_bar":last["time"],"last_pivot_high":highs[-1][1],
         "last_pivot_low":lows[-1][1]}
    if not events:return out
    side,i,level=events[-1]
    buffer=0.2*atr
    after=a[i+1:]
    # A touch after the breakout, then a completed candle returning to the correct side.
    touched=any((c["low"]<=level+buffer if side=="long" else c["high"]>=level-buffer) for c in after)
    regained=(last["close"]>level if side=="long" else last["close"]<level)
    piv=(lows if side=="long" else highs)
    post=[p for p in piv if p[0]>i and p[0]+2<len(a)]
    # A confirmed pivot formed after break, not merely price proximity.
    retest_pivot=bool(post and (post[-1][1]>=level-buffer if side=="long" else post[-1][1]<=level+buffer))
    prior=[p for p in piv if p[0]<i]
    higher_low=(bool(prior and post and post[-1][1]>prior[-1][1]) if side=="long"
                else bool(prior and post and post[-1][1]<prior[-1][1]))
    confirmed=touched and regained and retest_pivot and higher_low
    # avoid treating a many-bars-old break as a current setup
    fresh=len(a)-1-i<=12
    out.update({"status":"retest_confirmed" if confirmed and fresh else "break_watch",
                "side":side,"break_bar":a[i]["time"],"break_level":level,
                "bars_since_break":len(a)-1-i,
                "retest_touched":touched,"retest_pivot_confirmed":retest_pivot,
                "structure_hl_lh":higher_low,"regained_level":regained,
                "confirmed":bool(confirmed and fresh),
                "structure_label":"rule_confirmed_break_and_retest_not_BOS_CHoCH"})
    return out
