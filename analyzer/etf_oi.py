"""消费stock_data统一计算的沪深300 ETF配置强度代理并原子保存独立历史。

流程：核对新口径版本 → 重拉近期窗口 → 保留缺口合并同口径历史 → 原子替换。
计算、ETF分类和逐日有效成分由stock_data负责；旧四只历史保留且不读取。
"""

from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo


METHODOLOGY_ID = 'csi300_etf_free_float_v1'
HISTORY_FILENAME = 'csi300_etf_position.json'
START_DATE = date(2024, 1, 1)


def _records(data):
    if not isinstance(data, dict) or data.get('methodology_id') != METHODOLOGY_ID:
        raise ValueError('指标口径版本不匹配，禁止与旧历史拼接')
    records = data.get('records')
    if not isinstance(records, list):
        raise ValueError('指标历史格式异常')
    for row in records:
        day = date.fromisoformat(row['date']).isoformat()
        value = row.get('ratio_pct')
        if day != row['date'] or (value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        )):
            raise ValueError('指标日期或比例异常')
    return records


def update_history(path, end_date=None) -> dict:
    """调用共享计算入口；请求失败或无完整值时保留旧文件，新旧口径永不合并。"""
    import stock_data

    path = Path(path)
    if path.name == 'etf_oi.json':
        raise ValueError('旧口径历史只保留，请使用新的独立历史文件')
    old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    previous = _records(old) if old is not None else []
    end = date.fromisoformat(end_date) if end_date else datetime.now(ZoneInfo('Asia/Shanghai')).date()
    last = max((date.fromisoformat(r['date']) for r in previous), default=START_DATE)
    start = max(START_DATE, last - timedelta(days=45)) if previous else START_DATE
    if start > end:
        raise ValueError('更新日期早于现有历史')
    fresh = stock_data.fetch_csi300_etf_position_history(start.strftime('%Y%m%d'), end.strftime('%Y%m%d'))
    rows = _records(fresh)
    if not any(r.get('ratio_pct') is not None for r in rows):
        raise ValueError('请求范围内无完整数据，保留旧历史')
    merged = {r['date']: r for r in previous}
    seen = {}
    for row in rows:
        day = row['date']
        if not start.isoformat() <= day <= end.isoformat():
            raise ValueError('共享接口返回请求范围外日期')
        if day in seen and seen[day] != row:
            raise ValueError('同日指标数据冲突')
        seen[day] = row
        merged[day] = row
    records = [merged[d] for d in sorted(merged)]
    result = dict(fresh, updated_at=datetime.now(timezone.utc).isoformat(),
                  as_of=max(r['date'] for r in records if r.get('ratio_pct') is not None), records=records)
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
