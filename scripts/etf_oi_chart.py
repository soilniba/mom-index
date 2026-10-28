"""沪深300 ETF 规模占比双轴折线和成交量图，PNG 供飞书使用。"""

import io

from PIL import Image, ImageDraw, ImageFont

from scripts.notify_daily import FONT_PATH, FONT_INDEX_SC, CHART_BG, CHART_TEXT, CHART_TICK

RATIO_COLOR = '#fbbf24'
PRICE_COLOR = '#60a5fa'
VOLUME_COLOR = '#475569'
SIZE = (1600, 1100)
WINDOW = 250


def _limits(values):
    low, high = min(values), max(values)
    pad = (high - low) * .12 if high != low else max(abs(high) * .05, .01)
    return max(0, low - pad), high + pad


def chart_png(records: list) -> bytes:
    records = records[-WINDOW:]
    ratios = [r['ratio_pct'] for r in records if r.get('ratio_pct') is not None]
    prices = [r['index_close'] for r in records if r.get('index_close') is not None]
    if not ratios or not prices:
        return b''
    image = Image.new('RGB', SIZE, CHART_BG)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT_PATH, 26, index=FONT_INDEX_SC)
    small = ImageFont.truetype(FONT_PATH, 23, index=FONT_INDEX_SC)
    title = ImageFont.truetype(FONT_PATH, 40, index=FONT_INDEX_SC)
    left, right, top, bottom = 145, 1430, 230, 730
    n = len(records)
    x = lambda i: left + ((right - left) * i / (n - 1) if n > 1 else (right - left) / 2)
    rlow, rhigh = _limits(ratios)
    plow, phigh = _limits(prices)
    yr = lambda v: bottom - (v - rlow) / (rhigh - rlow) * (bottom - top)
    yp = lambda v: bottom - (v - plow) / (phigh - plow) * (bottom - top)
    complete = [r for r in records if r.get('ratio_pct') is not None]
    last = complete[-1]
    draw.text((80, 40), '头部沪深300 ETF 规模占比', font=title, fill=CHART_TEXT)
    draw.text((80, 103), f"截至 {last['date']}   ·   占比 {last['ratio_pct']:.4f}%"
              f"   ·   最近 {n} 个交易日", font=font, fill=CHART_TEXT)
    draw.text((left, 160), '● ETF规模占比（左轴，%）', font=font, fill=RATIO_COLOR)
    draw.text((770, 160), '● 沪深300（右轴，点）', font=font, fill=PRICE_COLOR)
    for i in range(6):
        yy = bottom - (bottom - top) * i / 5
        draw.line([(left, yy), (right, yy)], fill='#253248', width=1)
        draw.text((left - 15, yy), f'{rlow + (rhigh - rlow) * i / 5:.2f}%',
                  font=small, fill=RATIO_COLOR, anchor='rm')
        draw.text((right + 15, yy), f'{plow + (phigh - plow) * i / 5:.0f}',
                  font=small, fill=PRICE_COLOR, anchor='lm')

    for field, transform, color in [('index_close', yp, PRICE_COLOR), ('ratio_pct', yr, RATIO_COLOR)]:
        previous = None
        for i, row in enumerate(records):
            value = row.get(field)
            if value is None:
                previous = None
                continue
            point = (x(i), transform(value))
            if previous is not None:
                draw.line([previous, point], fill=color, width=4)
            else:
                draw.ellipse((point[0]-3, point[1]-3, point[0]+3, point[1]+3), fill=color)
            previous = point

    vtop, vbottom = 830, 975
    vmax = max((r.get('volume_shares') or 0 for r in records), default=0) or 1
    draw.text((left, 780), '沪深300成分股成交量（亿股；涨红跌绿）', font=font, fill=CHART_TEXT)
    for i in range(3):
        yy = vbottom - (vbottom - vtop) * i / 2
        draw.line([(left, yy), (right, yy)], fill='#253248', width=1)
        draw.text((left - 15, yy), f'{vmax * i / 2 / 1e8:.0f}', font=small, fill=CHART_TICK, anchor='rm')
    half_width = max(1, min(25, (right-left) / max(n, 1) * .35))
    for i, row in enumerate(records):
        vol = row.get('volume_shares')
        if vol is None:
            continue
        yy = vbottom - vol / vmax * (vbottom-vtop)
        close, previous_close = row.get('index_close'), row.get('index_pre_close')
        color = VOLUME_COLOR
        if close is not None and previous_close is not None:
            if close > previous_close:
                color = '#ef4444'
            elif close < previous_close:
                color = '#22c55e'
        draw.rectangle((x(i)-half_width, yy, x(i)+half_width, vbottom), fill=color)
    ticks = sorted(set(round(i * (n-1) / 5) for i in range(6)))
    for i in ticks:
        draw.text((x(i), vbottom+16), records[i]['date'], font=small, fill=CHART_TICK, anchor='mt')
    draw.text((80, 1050), '510300 · 510310 · 510330 · 159919   |   份额 × 单位净值 / 指数流通市值   |   stock_data',
              font=small, fill=CHART_TICK)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()
