#!/usr/bin/env python3
"""Read-only OKX USDT perpetual market collector; no API credentials or trading."""
import json, statistics, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from five_minute import analyze as analyze_five_minute

BASE = 'https://www.okx.com'
SYMBOLS = ['BTC','ETH','SOL','XRP','LTC','ADA','BCH','LINK']
EXTRAS = ['DOGE','AVAX','SUI','BNB']
PERIODS = {'5m':'5m','15m':'15m','1h':'1H','4h':'4H','1d':'1D'}
TZ = timezone(timedelta(hours=8))
LIMIT = 100

def get(path, params):
    url = BASE + path + '?' + urlencode(params)
    for attempt in range(3):
        try:
            with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0 MarketResearchReadOnly/1.0','Accept':'application/json'}),timeout=15) as response:
                obj=json.load(response)
            if str(obj.get('code'))!='0':
                raise RuntimeError(f"OKX code={obj.get('code')} msg={obj.get('msg')}")
            return obj['data']
        except Exception as e:
            print(f"Request failure [{path} {params}] attempt {attempt+1}/3: {type(e).__name__}: {e}",flush=True)
            if attempt==2: raise
            time.sleep(1.5*(attempt+1))

def ema(values,n):
    alpha=2/(n+1); result=values[0]
    for v in values[1:]: result=v*alpha+result*(1-alpha)
    return result

def pivots(bars, span=3):
    out=[]
    for i in range(span,len(bars)-span):
        window=bars[i-span:i+span+1]
        if bars[i]['high']==max(c['high'] for c in window) and sum(c['high']==bars[i]['high'] for c in window)==1:
            out.append({'type':'H','price':bars[i]['high'],'time':bars[i]['time']})
        if bars[i]['low']==min(c['low'] for c in window) and sum(c['low']==bars[i]['low'] for c in window)==1:
            out.append({'type':'L','price':bars[i]['low'],'time':bars[i]['time']})
    return out[-12:]

def calculate(b):
    closes=[x['close'] for x in b]; vols=[x['quote_volume'] for x in b]
    cur=closes[-1]; mean=statistics.mean(closes[-20:]); sd=statistics.pstdev(closes[-20:])
    oldmean=statistics.mean(closes[-21:-1]); oldsd=statistics.pstdev(closes[-21:-1])
    upper=mean+2*sd;lower=mean-2*sd
    oldupper=oldmean+2*oldsd;oldlower=oldmean-2*oldsd
    trs=[max(b[i]['high']-b[i]['low'],abs(b[i]['high']-closes[i-1]),abs(b[i]['low']-closes[i-1])) for i in range(1,len(b))]
    ps=pivots(b); highs=[p for p in ps if p['type']=='H'];lows=[p for p in ps if p['type']=='L']
    rh=max(x['high'] for x in b[-21:-1]);rl=min(x['low'] for x in b[-21:-1])
    return {'bar_time_bj':b[-1]['time'],'closed_candle_close':cur,
      'ema20':round(ema(closes,20),10),'ema50':round(ema(closes,50),10),'atr14':round(statistics.mean(trs[-14:]),10),
      'bb20_upper':round(upper,10),'bb20_middle':round(mean,10),'bb20_lower':round(lower,10),
      'bb_width_pct':round((upper-lower)/mean*100,4),'bb_upper_change_pct_of_price_per_bar':round((upper-oldupper)/cur*100,6),
      'bb_lower_change_pct_of_price_per_bar':round((lower-oldlower)/cur*100,6),
      'bb_expanding':upper>oldupper and lower<oldlower,
      'bb_uptrend_expansion':upper>oldupper and lower>oldlower,
      'bb_downtrend_expansion':upper<oldupper and lower<oldlower,
      'volume_ratio_vs_prior20':round(vols[-1]/(statistics.mean(vols[-21:-1]) or 1),3),
      'last_pivots':ps,'recent_swing_high':rh,'recent_swing_low':rl,
      'close_above_prior20_high':cur>rh,'close_below_prior20_low':cur<rl,
      'latest_pivot_highs':highs[-2:],'latest_pivot_lows':lows[-2:],
      'structure_hint':('HH' if len(highs)>1 and highs[-1]['price']>highs[-2]['price'] else 'LH' if len(highs)>1 else '?')+' / '+('HL' if len(lows)>1 and lows[-1]['price']>lows[-2]['price'] else 'LL' if len(lows)>1 else '?'),
      'warning':'Structure is heuristic, not confirmed BOS/CHoCH or executable signals. BB values are price slopes, not visual angles.'}

def candles(inst, period, now_ms):
    raw=get('/api/v5/market/candles',{'instId':inst,'bar':PERIODS[period],'limit':LIMIT})
    # OKX returns newest-first rows: [ts,open,high,low,close,vol,volCcy,volCcyQuote,confirm]
    valid=[]
    for k in raw:
        stamp=int(k[0]); confirm=str(k[8])
        if confirm!='1' or stamp>now_ms: continue
        valid.append({'time':datetime.fromtimestamp(stamp/1000,TZ).isoformat(timespec='seconds'),
           'open':float(k[1]),'high':float(k[2]),'low':float(k[3]),'close':float(k[4]),'quote_volume':float(k[7]),'stamp':stamp})
    valid.sort(key=lambda x:x['stamp'])
    if len(valid)<60: raise ValueError(f'Only {len(valid)} confirmed candles for {inst} {period}')
    if len(set(x['stamp'] for x in valid))!=len(valid): raise ValueError('Duplicate candles')
    return valid

def main():
    now=datetime.now(timezone.utc); now_ms=int(now.timestamp()*1000)
    snapshot={'source':'OKX USDT-margined perpetual SWAP market REST API','source_url':BASE,
      'generated_utc':now.isoformat(timespec='seconds'),'generated_beijing':now.astimezone(TZ).isoformat(timespec='seconds'),
      'intervals':list(PERIODS),'symbols':{},'errors':{},
      'disclaimer':'No account or trading access. Verify quote and instrument at your actual order venue; OKX futures prices may differ from Binance.'}
    for symbol in SYMBOLS+EXTRAS:
        inst=f'{symbol}-USDT-SWAP'; out={'instrument':inst,'priority':'core' if symbol in SYMBOLS else 'extra','intervals':{}}
        try:
            ticker=get('/api/v5/market/ticker',{'instId':inst})[0]
            price=float(ticker['last']); opening=float(ticker['open24h'])
            out.update({'last_price':price,'price_change_pct_24h':round((price/opening-1)*100,4) if opening else None,
                        'high_24h':float(ticker['high24h']),'low_24h':float(ticker['low24h']),
                        'turnover_usdt_24h_approx':round(float(ticker['volCcy24h'])*price,2),
                        'ticker_time_bj':datetime.fromtimestamp(int(ticker['ts'])/1000,TZ).isoformat(timespec='seconds')})
            if now_ms-int(ticker['ts'])>15*60*1000: raise RuntimeError('Ticker older than 15 minutes')
            for period in PERIODS:
                try:
                    b=candles(inst,period,now_ms)
                    out['intervals'][period]=calculate(b)
                    if period=='5m':
                        out['five_minute_structure']=analyze_five_minute(b)
                        out['five_minute_candles']=[{key:c[key] for key in ('time','open','high','low','close','quote_volume')} for c in b[-60:]]
                    print(f'{inst} {period} OK: {len(b)} closed candles',flush=True)
                except Exception as e:
                    snapshot['errors'][f'{inst} {period}']=str(e)
            snapshot['symbols'][symbol+'USDT']=out
        except Exception as e:
            snapshot['errors'][inst]=str(e)
            print(f'{inst} FAILED: {type(e).__name__}: {e}',flush=True)
    complete=sum(len(snapshot['symbols'].get(s+'USDT',{}).get('intervals',{}))==5 for s in SYMBOLS)
    if complete!=8:
        raise RuntimeError(f'Core SWAP datasets complete {complete}/8; preserving last snapshot. Errors: {snapshot["errors"]}')
    Path('data').mkdir(exist_ok=True)
    Path('data/latest.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# OKX USDT永续合约行情快照','',f"北京时间：{snapshot['generated_beijing']}",'',
      '行情源：OKX-USDT-SWAP，技术指标仅基于已收盘K线。非直接交易指令。','',
      '| 币种 | 最新价 | 24h涨跌 | 15m结构提示 | 15m布林扩张 | 15m量比 |','|---|---:|---:|---|---|---:|']
    for symbol, x in snapshot['symbols'].items():
        t=x['intervals'].get('15m',{})
        lines.append(f"| {symbol} | {x.get('last_price','')} | {x.get('price_change_pct_24h','')}% | {t.get('structure_hint','')} | {t.get('bb_expanding','')} | {t.get('volume_ratio_vs_prior20','')} |")
    lines.extend(['','注意：OKX价格不能直接当作其他交易所的精确委托价。结构指标为提示，非确认信号。',
      '',f"异常记录：{len(snapshot['errors'])} 条，详见latest.json。"])
    Path('data/latest.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'OK: {complete}/8 core symbols completed; extras optional; {len(snapshot["errors"])} errors',flush=True)

if __name__=='__main__': main()
