"""Compact, mobile-friendly HTML email templates without external dependencies."""
import html
import re

def esc(value):
    return html.escape(str(value), quote=True)

def inline(text):
    text=esc(text)
    text=re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    return text

def shell(title,eyebrow,body,color="#2563eb"):
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',Arial,sans-serif;color:#1f2937">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background:#f3f5f9"><tr><td align="center" style="padding:20px 10px">
<table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:660px;background:#fff;border-radius:14px;overflow:hidden">
<tr><td style="background:#17243b;padding:24px 26px"><div style="font-size:12px;color:#b9c8e5;letter-spacing:1px">'''+esc(eyebrow)+'''</div><div style="font-size:23px;line-height:1.4;color:#fff;font-weight:700;margin-top:8px">'''+esc(title)+'''</div></td></tr>
<tr><td style="padding:24px 24px 18px">'''+body+'''</td></tr>
<tr><td style="background:#f8fafc;padding:16px 24px;color:#64748b;font-size:12px;line-height:1.8;border-top:1px solid #e5e7eb">OKX USDT永续 · GitHub采集快照 · 非自动下单。实际成交价格、手续费与持仓以OKX为准。</td></tr>
</table></td></tr></table></body></html>'''

def section(title):
    return '<div style="font-size:16px;font-weight:700;color:#17243b;margin:24px 0 10px;border-left:4px solid #2563eb;padding-left:10px">'+inline(title)+'</div>'

def _cells(line):
    s=line.strip()
    if s.startswith("|"):
        s=s[1:]
    if s.endswith("|"):
        s=s[:-1]
    return [x.strip() for x in s.split("|")]

def _divider(line):
    cells=_cells(line)
    return len(cells)>=2 and all(re.fullmatch(r":?-{3,}:?",x.strip()) for x in cells)

def _is_row(line, n):
    return "|" in line and len(_cells(line))==n

def render_markdown(text):
    out=[]
    lines=text.splitlines()
    i=0
    while i<len(lines):
        s=lines[i].strip()
        if not s:
            i+=1
            continue
        # Hide redundant methodology disclaimers; keep market conclusions and risk limits.
        if s.startswith(("说明：","说明:")) and any(x in s for x in
            ("structure_hint","本文所有数值","仅反映已收盘","不包含实时")):
            i+=1
            continue
        if s in ("---","***","___"):
            i+=1
            continue
        head=_cells(s) if "|" in s else []
        if len(head)>=2 and i+1<len(lines) and _divider(lines[i+1]) and len(_cells(lines[i+1]))==len(head):
            rows=[]
            i+=2
            while i<len(lines) and _is_row(lines[i].strip(),len(head)):
                rows.append(_cells(lines[i]))
                i+=1
            out.append('<div style="width:100%;overflow-x:auto;margin:12px 0 18px"><table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;font-size:13px;line-height:1.5">')
            out.append('<tr>'+''.join('<th style="text-align:left;background:#eaf0f7;color:#25364b;padding:10px 8px;border-bottom:1px solid #d6dfeb;white-space:nowrap">'+inline(x)+'</th>' for x in head)+'</tr>')
            for n,row in enumerate(rows):
                out.append('<tr style="background:%s">' % ('#f5f8fc' if n%2 else '#ffffff')+
                    ''.join('<td style="padding:9px 8px;border-bottom:1px solid #e8edf3;vertical-align:top;word-break:break-word">'+inline(x)+'</td>' for x in row)+'</tr>')
            out.append('</table></div>')
            continue
        if s.startswith("#"):
            out.append(section(s.lstrip("# ").strip()))
        elif s.startswith("- ") or s.startswith("* "):
            out.append('<div style="padding:4px 0 4px 10px;line-height:1.75;font-size:14px">• '+inline(s[2:])+'</div>')
        else:
            out.append('<div style="font-size:14px;line-height:1.85;margin:9px 0;color:#334155">'+inline(s)+'</div>')
        i+=1
    return "".join(out)

def analysis(text, title, plan_count=0):
    if plan_count:
        label="本期%d套条件性交易预案｜等待触发，非当前委托" % plan_count
        color="#eaf0fb"
    else:
        label="合约规格未核验或无符合风险预算的预案｜勿直接委托"
        color="#fff7ed"
    status='<div style="background:'+color+';border-radius:10px;padding:15px;margin-bottom:14px;font-weight:700;color:#27374d">'+esc(label)+'</div>'
    return shell(title,"DEEPSEEK AI · CONDITIONAL PLANS",status+render_markdown(text))

def field(label,value,accent=False):
    return '<td valign="top" width="50%" style="padding:10px 8px;border-bottom:1px solid #e8edf3"><div style="font-size:12px;color:#64748b">'+esc(label)+'</div><div style="font-size:%s;font-weight:700;color:%s;margin-top:4px;word-break:break-word">%s</div></td>'%("21px" if accent else "17px","#166534" if accent else "#17243b",esc(value))

def trade(sym,direction,r,stamp,expiry):
    top='<div style="padding:13px 16px;border-radius:10px;background:#eaf6ee;color:#166534;font-weight:700;font-size:18px;margin-bottom:14px">交易计划：'+esc(sym)+' '+esc(direction)+'</div>'
    grid='<table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>'+field('限价入场',r['entry']+' USDT',True)+field('逐仓杠杆','2倍')+'</tr><tr>'+field('止损 SL',r['stop'])+field('止盈 TP',r['target'])+'</tr><tr>'+field('OKX合约张数',r['contracts']+' 张')+field('预估保证金','%.2f U'%r['margin'])+'</tr><tr>'+field('估算最大损失','%.2f U'%r['risk'])+field('扣成本盈亏比','%.2f:1'%r['rr'])+'</tr></table>'
    foot='<div style="background:#fff7ed;border-radius:10px;padding:13px 16px;margin-top:16px;line-height:1.75;font-size:13px"><strong>有效期至 '+esc(expiry.strftime('%Y-%m-%d %H:%M:%S'))+'（北京）</strong><div>快照：'+esc(stamp.strftime('%Y-%m-%d %H:%M:%S'))+'；到期未成交请检查并撤销；价格偏离、结构失效或实际净盈亏比低于1.8时不下单。</div><div>预估费用含手续费与滑点，不保证最终损失上限。请核对OKX实时报价、账户风险及合约规格。</div></div>'
    return shell(sym+" "+direction+"｜限价 "+r['entry'],"TRADE SIGNAL · 人工确认后下单",top+grid+foot)

def expiry(sym,direction,when):
    body='<div style="background:#fff1f2;border-radius:12px;padding:18px;color:#9f1239;font-size:19px;font-weight:700;margin-bottom:14px">信号已到期 · 请检查挂单</div><div style="font-size:15px;line-height:1.8">币种：<strong>'+esc(sym)+'</strong>　方向：<strong>'+esc(direction)+'</strong></div><div style="font-size:14px;line-height:1.8">失效时间（北京）：'+esc(when.strftime('%Y-%m-%d %H:%M:%S'))+'</div><div style="padding:14px 0;line-height:1.85;font-size:14px">如有未成交限价单，请自行核对并撤销。<strong>已成交的仓位不要因此取消保护性止损。</strong>系统未连接OKX订单账户，不知道是否已成交，也不会自动撤单。</div>'
    return shell("交易信号过期提醒","EXPIRY NOTICE",body)

def test():
    return shell("QQ邮箱HTML显示测试","EMAIL DELIVERY TEST",'<div style="font-size:16px;line-height:1.8">如果你看到深蓝标题栏、白色卡片和整齐排版，说明HTML模板正常显示。</div>')
