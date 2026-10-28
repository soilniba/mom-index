"""固定四只沪深300 ETF 的净资产规模占普通流通市值比例。"""

from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo


FUNDS = {
    '510300.SH': '华泰柏瑞', '510310.SH': '易方达',
    '510330.SH': '华夏', '159919.SZ': '嘉实',
}
START_DATE = date(2023, 1, 1)


def _number(value, *, zero=False):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0 or (not zero and value == 0):
        return None
    return value


def _rows(value):
    return value.to_dict('records') if hasattr(value, 'to_dict') else list(value)


def _by_date(rows, code, date_field, fields):
    result = {}
    for row in rows:
        if row.get('ts_code') != code:
            raise ValueError(f'证券代码不匹配：{code}')
        raw_date = row.get(date_field)
        if not isinstance(raw_date, str) or len(raw_date) != 8 or not raw_date.isdigit():
            raise ValueError(f'日期格式异常：{code}')
        day = date.fromisoformat(raw_date).isoformat()
        values = tuple(_number(row.get(f), zero=f == 'vol') for f in fields)
        if day in result and result[day] != values:
            raise ValueError(f'同日重复数据冲突：{code} {day}')
        result[day] = values
    return result


def build_records(shares, navs, valuations, prices) -> list[dict]:
    """保留指数交易日；必需数据不齐时 ratio_pct=None，便于图中断线。"""
    sm = {c: _by_date(shares.get(c, []), c, 'trade_date', ('fd_share',)) for c in FUNDS}
    nm = {c: _by_date(navs.get(c, []), c, 'nav_date', ('unit_nav',)) for c in FUNDS}
    vm = _by_date(valuations, '000300.SH', 'trade_date', ('float_mv',))
    pm = _by_date(prices, '000300.SH', 'trade_date', ('close', 'vol'))
    records = []
    for day, (close, volume) in sorted(pm.items()):
        missing, funds = [], []
        mv = vm.get(day, (None,))[0]
        for label, value in (('流通市值', mv), ('收盘点位', close), ('成交量', volume)):
            if value is None:
                missing.append(label)
        for code in FUNDS:
            share = sm[code].get(day, (None,))[0]
            nav = nm[code].get(day, (None,))[0]
            if share is None:
                missing.append(f'{code}份额')
            if nav is None:
                missing.append(f'{code}净值')
            if share is not None and nav is not None:
                assets = share * 10_000 * nav
                if not math.isfinite(assets):
                    missing.append(f'{code}规模')
                else:
                    funds.append({'code': code, 'shares': share * 10_000,
                                  'unit_nav': nav, 'assets_yuan': assets})
        assets = sum(f['assets_yuan'] for f in funds) if not missing else None
        ratio = assets / mv * 100 if assets is not None else None
        if ratio is not None and not math.isfinite(ratio):
            missing.append('比例溢出')
            assets = ratio = None
        records.append({
            'date': day, 'ratio_pct': ratio, 'etf_assets_yuan': assets,
            'index_float_mv_yuan': mv, 'index_close': close,
            'volume_shares': volume * 100 if volume is not None else None,
            'funds': funds, 'missing': missing,
        })
    return records


def update_history(path, end_date=None) -> dict:
    """通过 stock_data 更新独立历史；完整请求失败时保留原文件。"""
    import stock_data

    path = Path(path)
    old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    previous = old.get('records', [])
    end = date.fromisoformat(end_date) if end_date else datetime.now(ZoneInfo('Asia/Shanghai')).date()
    last = max((date.fromisoformat(r['date']) for r in previous), default=START_DATE)
    start = max(START_DATE, last - timedelta(days=45)) if previous else START_DATE
    if start > end:
        raise ValueError('更新日期早于现有历史')
    begin, finish = start.strftime('%Y%m%d'), end.strftime('%Y%m%d')
    shares, navs = {}, {}
    for code in FUNDS:
        shares[code] = _rows(stock_data.fetch_etf_share_history(code, begin, finish))
        navs[code] = _rows(stock_data.fetch_etf_nav_history(code, begin, finish))
        if not shares[code] or not navs[code]:
            raise ValueError(f'{code}份额或净值响应为空，保留旧历史')
    vals = _rows(stock_data.workflow_index_daily_valuation(
        '000300.SH', begin, finish, 'ts_code,trade_date,float_mv'))
    prices = _rows(stock_data.workflow_index_daily_prices(
        '000300.SH', begin, finish, 'ts_code,trade_date,close,vol'))
    if not vals or not prices:
        raise ValueError('指数市值或行情响应为空，保留旧历史')
    fresh = build_records(shares, navs, vals, prices)
    if not any(r['ratio_pct'] is not None for r in fresh):
        raise ValueError('请求范围内无数据可完整计算，保留旧历史')
    merged = {r['date']: r for r in previous}
    for row in fresh:
        if start.isoformat() <= row['date'] <= end.isoformat():
            # 修订期间个别源临时缺失时，保留过去已验证的有效点。
            if row['ratio_pct'] is not None or merged.get(row['date'], {}).get('ratio_pct') is None:
                merged[row['date']] = row
    records = [merged[d] for d in sorted(merged)]
    result = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'as_of': max(r['date'] for r in records if r['ratio_pct'] is not None),
        'records': records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as output:
            temporary = output.name
            json.dump(result, output, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return result
