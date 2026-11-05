"""消费stock_data统一计算的沪深300 ETF配置强度代理并原子保存独立历史。

流程：核对版本 → v1全量重建并备份 / v2重拉近期窗口 → 保留真实缺口 → 原子替换。
计算、ETF分类和逐日有效成分由stock_data负责；旧四只历史保留且不读取。
"""

from datetime import date, datetime, timedelta, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo


METHODOLOGY_ID = 'csi300_etf_free_float_v2'
PREVIOUS_METHODOLOGY_ID = 'csi300_etf_free_float_v1'
HISTORY_FILENAME = 'csi300_etf_position.json'
START_DATE = date(2024, 1, 1)


def _records(data, methodology_id=METHODOLOGY_ID):
    if not isinstance(data, dict) or data.get('methodology_id') != methodology_id:
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


def _preserve_v1(path, original):
    """完整写入临时副本后无覆盖地建立备份；已有不同内容时拒绝迁移。"""
    backup = path.with_name(f'{path.stem}.v1{path.suffix}')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.tmp', delete=False) as output:
            temporary = output.name
            output.write(original)
        try:
            os.link(temporary, backup)
        except FileExistsError:
            if backup.read_bytes() != original:
                raise ValueError('已有不同内容的v1备份，禁止覆盖')
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def update_history(path, end_date=None, *, pending_dates=None, request_interval_seconds=0) -> dict:
    """默认近期重拉或v1全量迁移；指定待补日期时仅合并完整值，未齐不写文件。"""
    if (isinstance(request_interval_seconds, bool)
            or not isinstance(request_interval_seconds, (int, float))
            or not math.isfinite(request_interval_seconds) or request_interval_seconds < 0):
        raise ValueError('request_interval_seconds请求间隔必须是有限非负数')
    import stock_data

    path = Path(path)
    if path.name == 'etf_oi.json':
        raise ValueError('旧口径历史只保留，请使用新的独立历史文件')
    original = path.read_bytes() if path.exists() else None
    old = json.loads(original) if original is not None else None
    migrating = isinstance(old, dict) and old.get('methodology_id') == PREVIOUS_METHODOLOGY_ID
    if pending_dates is not None:
        if migrating:
            raise ValueError('v1历史必须全量迁移，禁止局部更新')
        if old is None:
            raise FileNotFoundError(f'局部更新需要已有v2历史文件：{path}')
    old_rows = _records(old, PREVIOUS_METHODOLOGY_ID if migrating else METHODOLOGY_ID) if old is not None else []
    previous = [] if migrating else old_rows
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    end = date.fromisoformat(end_date) if end_date else today
    if pending_dates is not None:
        if isinstance(pending_dates, (str, bytes, dict)):
            raise ValueError('待补日期必须是非空ISO日期集合')
        try:
            pending_dates = set(pending_dates)
            if not pending_dates:
                raise ValueError('待补日期不能为空')
            for day in pending_dates:
                parsed = date.fromisoformat(day)
                if parsed.isoformat() != day or not START_DATE <= parsed <= min(end, today):
                    raise ValueError('待补日期超出历史、截止日或未来日期范围')
        except (TypeError, ValueError) as exc:
            raise ValueError('待补日期必须是有效范围内的非空ISO日期集合') from exc
        start, end = date.fromisoformat(min(pending_dates)), date.fromisoformat(max(pending_dates))
    else:
        last = max((date.fromisoformat(r['date']) for r in previous), default=START_DATE)
        start = max(START_DATE, last - timedelta(days=45)) if previous else START_DATE
    if start > end:
        raise ValueError('更新日期早于现有历史')
    options = {'request_interval_seconds': request_interval_seconds} if request_interval_seconds else {}
    fresh = stock_data.fetch_csi300_etf_position_history(
        start.strftime('%Y%m%d'), end.strftime('%Y%m%d'), **options)
    rows = _records(fresh)
    seen = {}
    for row in rows:
        day = row['date']
        if not start.isoformat() <= day <= end.isoformat():
            raise ValueError('共享接口返回请求范围外日期')
        if day in seen and seen[day] != row:
            raise ValueError('同日指标数据冲突')
        seen[day] = row
    if pending_dates is not None:
        rows = [r for r in seen.values() if r['date'] in pending_dates and r.get('ratio_pct') is not None]
        if not rows:
            return old
    elif not any(r.get('ratio_pct') is not None for r in rows):
        raise ValueError('请求范围内无完整数据，保留旧历史')
    merged = {r['date']: r for r in previous}
    merged.update((r['date'], r) for r in rows)
    if migrating and not {r['date'] for r in old_rows} <= seen.keys():
        raise ValueError('v2全量迁移响应缺少已有历史日期，保留v1原文件')
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
        # 所有入口共用目录锁；比较源快照与原子替换必须在同一临界区。
        # 锁不覆盖联网阶段，慢请求返回旧快照时拒绝覆盖后来已保存的数据。
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            fcntl.flock(directory, fcntl.LOCK_EX)
            current = path.read_bytes() if path.exists() else None
            if current != original:
                raise ValueError('历史已被其他更新修改，拒绝并发旧响应覆盖')
            if migrating:
                _preserve_v1(path, original)
            os.replace(temporary, path)
        finally:
            os.close(directory)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return result
