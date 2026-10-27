#!/usr/bin/env python3
"""Read-only Binance USD-M perpetual futures market snapshot. No API keys, no trading."""
import json, math, os, statistics, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode

BASE='https://fapi.binance.com'
SYMBOLS=['BTCUSDT','ETCUSDT','SOLUSDT','XRPUSDT','LTCUSDT','ADAUSDT','BCHUSDT','LINKUSDT']
EXTRAS=['ETHUSDT','DOGEUSDT','AVAXUSDT','SUIUSDT','BNBUSDT']
PERIODS=['5m','15m','1h','4h','1d']
LIMIT=240
TZ=timezone(timedelta(hours=8))

def get(path, params):
    url=BASE+path+'?'+urlencode(params)
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={'User-Agent':'read-only-hourly-market-research/1.0','Accept':'application/json'}),timeout=18) as response:
                return json.load(response)
        except Exception:
            if attempt==2: raise
            time.sleep(2*(attempt+1))

def ema(values,n):
    k=2/(n+1); v=values[0]
    for x in values[1:]: v=x*k+v*(1-k)
    return v

def pivots(bars, span=3):
    result=[]
    for i in range(span,len(bars)-span):
        window=bars[i-span:i+span+1]
        if bars[i]['high']==max(c['high'] for c in window) and sum(c['high']==bars[i]['high'] for c in window)==1:
            result.append({'type':'H','price':bars[i]['high'],'time':bars[i]['time']})
        if bars[i]['low']==min(c['low'] for c in window) and sum(c['low']==bars[i]['low'] for c in window)==1:
            result.append({'type':'L','price':bars[i]['low'],'time':bars[i]['time']})
    return result[-12:]

def calculate(b):
    closes=[x['close'] for x in b]; vols=[x['quote_volume'] for x in b]
    last=b[-1]; cur=closes[-1]
    mean20=statistics.mean(closes[-20:]); sd20=statistics.pstdev(closes[-20:]); prior20=statistics.mean(closes[-21:-1]); prior_sd=statistics.pstdev(closes[-21:-1])
    upper=mean20+2*sd20; lower=mean20-2*sd20
    prev_upper=prior20+2*prior_sd; prev_lower=prior20-2*prior_sd
    atrs=[max(b[i]['high']-b[i]['low'],abs(b[i]['high']-closes[i-1]),abs(b[i]['low']-closes[i-1])) for i in range(1,len(b))]
    ps=pivots(b)
    highs=[x for x in ps if x['type']=='H']; lows=[x for x in ps if x['type']=='L']
    high=max((x['high'] for x in b[-21:-1]),default=None)
    low=min((x['low'] for x in b[-21:-1]),default=None)
    bb_up=(upper-prev_upper)/cur*100; bb_down=(lower-prev_lower)/cur*100
    return {'bar_time_bj':last['time'],'closed_candle_close':cur,'ema20':round(ema(closes[-100:],20),10),'ema50':round(ema(closes[-150:],50),10),
        'atr14':round(statistics.mean(atrs[-14:]),10),'bb20_upper':round(upper,10),'bb20_middle':round(mean20,10),'bb20_lower':round(lower,10),
        'bb_width_pct':round((upper-lower)/mean20*100,4),'bb_upper_change_pct_of_price_per_bar':round(bb_up,6),'bb_lower_change_pct_of_price_per_bar':round(bb_down,6),
        'bb_expanding':bool(upper>prev_upper and lower<prev_lower),'bb_uptrend_expansion':bool(upper>prev_upper and lower>prev_lower),
        'bb_downtrend_expansion':bool(upper<prev_upper and lower<prev_lower),
        'volume_ratio_vs_prior20':round(vols[-1]/(statistics.mean(vols[-21:-1]) or 1),3),
        'last_pivots':ps,'recent_swing_high':high,'recent_swing_low':low,
        'close_above_prior20_high':bool(high is not None and cur>high),'close_below_prior20_low':bool(low is not None and cur<low),
        'latest_pivot_highs':highs[-2:],'latest_pivot_lows':lows[-2:],
        'structure_hint': ('HH' if len(highs)>1 and highs[-1]['price']>highs[-2]['price'] else 'LH' if len(highs)>1 else '?')+' / '+('HL' if len(lows)>1 and lows[-1]['price']>lows[-2]['price'] else 'LL' if len(lows)>1 else '?'),
        'warning':'BOS/CHoCH requires contextual confirmation. These flags are not executable signals. BB slopes are normalized price changes per candle, not chart angle degrees.'}

def bars_for(symbol,period,now_ms):
    data=get('/fapi/v1/klines',{'symbol':symbol,'interval':period,'limit':LIMIT})
    bars=[{'time':datetime.fromtimestamp(int(k[0])/1000,TZ).isoformat(timespec='seconds'), 'high':float(k[2]),'low':float(k[3]),'close':float(k[4]),'quote_volume':float(k[7])} for k in data if int(k[6])<now_ms]
    if len(bars)<151: raise ValueError(f'{symbol} {period} insufficient closed bars: {len(bars)}')
    return bars

def main():
    now=datetime.now(timezone.utc); now_ms=int(now.timestamp()*1000)
    snapshot={'source':'Binance USD-M Futures public REST API','source_url':BASE,'generated_utc':now.isoformat(timespec='seconds'),'generated_beijing':now.astimezone(TZ).isoformat(timespec='seconds'),
              'intervals':PERIODS,'symbols':{},'errors':{},'disclaimer':'Research data only; no direct account access or trade execution. Check actual exchange prices before acting.'}
    for symbol in SYMBOLS+EXTRAS:
        out={'intervals':{},'priority':'core' if symbol in SYMBOLS else 'extra'}
        try:
            ticker=get('/fapi/v1/ticker/24hr',{'symbol':symbol})
            out['last_price']=float(ticker['lastPrice']);out['price_change_pct_24h']=float(ticker['priceChangePercent'])
            out['high_24h']=float(ticker['highPrice']);out['low_24h']=float(ticker['lowPrice']);out['turnover_usdt_24h']=float(ticker['quoteVolume'])
            # Each interval uses only closed candles (no partially completed bar).
            for period in PERIODS:
                try: out['intervals'][period]=calculate(bars_for(symbol,period,now_ms))
                except Exception as e: snapshot['errors'][symbol+' '+period]=str(e)
            snapshot['symbols'][symbol]=out
        except Exception as e: snapshot['errors'][symbol]=str(e)
    if sum(len(snapshot['symbols'].get(s,{}).get('intervals',{}))==5 for s in SYMBOLS)<8:
        raise RuntimeError('Core symbols missing valid complete five-period data; refusing to overwrite previous snapshot. Errors: '+str(snapshot['errors']))
    Path('data').mkdir(exist_ok=True)
    Path('data/latest.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# 永续合约行情快照','',f"北京时间：{snapshot['generated_beijing']}",'', '数据：币安USD-M永续合约API；指标基于已收盘K线。不是交易建议。','',
          '| 币种 | 最新价 | 24h涨跌 | 15m结构提示 | 15m布林扩张 | 15m量比 |','|---|---:|---:|---|---|---:|']
    for symbol, x in snapshot['symbols'].items():
        t=x['intervals'].get('15m',{})
        lines.append(f"| {symbol} | {x.get('last_price','')} | {x.get('price_change_pct_24h','')}% | {t.get('structure_hint','')} | {t.get('bb_expanding','')} | {t.get('volume_ratio_vs_prior20','')} |")
    lines.extend(['','说明：当前价格由24小时行情接口取得，结构来自最近已收盘15分钟K线。布林扩张≠方向性交易信号。','',f"异常记录：{len(snapshot['errors'])} 条。详见 latest.json。"]) 
    Path('data/latest.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f"OK {snapshot['generated_beijing']}: {len(snapshot['symbols'])} symbols, {len(snapshot['errors'])} errors")
if __name__=='__main__': main()
