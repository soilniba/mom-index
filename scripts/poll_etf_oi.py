#!/usr/bin/env python3
"""北京时间收盘后每10分钟补查待齐ETF日期，完整后发布并停止该日查询。"""

import argparse
import copy
from datetime import date, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzer.etf_oi import HISTORY_FILENAME, _records, update_history
from sync_data import publish_etf_oi


BEIJING = ZoneInfo('Asia/Shanghai')
STATE_FILENAME = 'etf_poll_state.json'
LOCK_FILENAME = '.etf-poll.lock'
POLL_INTERVAL_SECONDS = 600


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('日期必须为ISO格式')
    return date.fromisoformat(value)


def _days(value):
    if not isinstance(value, list):
        raise ValueError('日期队列必须为列表')
    for item in value:
        _day(item)
    if value != sorted(set(value)):
        raise ValueError('日期队列必须升序且不重复')
    return value


def _complete_dates(data):
    complete = {row['date'] for row in _records(data) if row.get('ratio_pct') is not None}
    if not complete:
        raise ValueError('历史中没有完整v2数据，拒绝自动初始化')
    return complete


def _read_history(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    return data, _complete_dates(data)


def _read_state(path):
    state = json.loads(path.read_text(encoding='utf-8'))
    required = {'version', 'tracking_start', 'queued_through', 'pending_dates',
                'last_attempt_at', 'notify_pending', 'notify_dates'}
    if not isinstance(state, dict) or not required <= state.keys():
        raise ValueError('轮询状态缺少必要字段')
    if type(state['version']) is not int or state['version'] != 1:
        raise ValueError('轮询状态版本异常')
    start = _day(state['tracking_start'])
    through = _day(state['queued_through'])
    if through < start - timedelta(days=1):
        raise ValueError('轮询游标早于追踪起点')
    for field in ('pending_dates', 'notify_dates'):
        if any(not start <= _day(day) <= through for day in _days(state[field])):
            raise ValueError('轮询日期超出已登记范围')
    if set(state['pending_dates']) & set(state['notify_dates']):
        raise ValueError('待查日期与待发日期不能重叠')
    if type(state['notify_pending']) is not bool or state['notify_pending'] != bool(state['notify_dates']):
        raise ValueError('通知状态与待发日期不一致')
    attempt = state['last_attempt_at']
    if attempt is not None:
        if not isinstance(attempt, str) or datetime.fromisoformat(attempt).utcoffset() is None:
            raise ValueError('上次尝试时间必须包含时区')
    return state


def _write_state(path, state):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as output:
            temporary = output.name
            json.dump(state, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _finish_completed(state, complete):
    finished = set(state['pending_dates']) & complete
    state['pending_dates'] = sorted(set(state['pending_dates']) - finished)
    state['notify_dates'] = sorted(set(state['notify_dates']) | finished)
    state['notify_pending'] = bool(state['notify_dates'])


def _log_error(stage, exc):
    # 不输出上游异常正文，避免凭据或请求参数进入日志。
    print(f'[etf_poll] {stage}：{type(exc).__name__}', file=sys.stderr)


def _run_locked(data_dir, now):
    history_path = data_dir / HISTORY_FILENAME
    data, complete = _read_history(history_path)
    state_path = data_dir / STATE_FILENAME
    if state_path.exists():
        state = _read_state(state_path)
        saved = copy.deepcopy(state)
    else:
        latest = max(complete)
        state = {'version': 1, 'tracking_start': (_day(latest) + timedelta(days=1)).isoformat(),
                 'queued_through': latest, 'pending_dates': [], 'last_attempt_at': None,
                 'notify_pending': False, 'notify_dates': []}
        saved = None

    def save():
        nonlocal saved
        if state != saved:
            _write_state(state_path, state)
            saved = copy.deepcopy(state)

    # 更新器已保存、进程尚未保存队列就退出时，也能恢复发布而不重复取数。
    _finish_completed(state, complete)
    if not set(state['notify_dates']) <= complete:
        raise ValueError('待发日期缺少完整数据，拒绝发送陈旧图卡')
    save()
    cutoff = now.date() if now.hour >= 15 else now.date() - timedelta(days=1)
    registration_start = max(_day(state['tracking_start']),
                             _day(state['queued_through']) + timedelta(days=1))
    register = registration_start <= cutoff
    if not (register or state['pending_dates'] or state['notify_pending']):
        return 0
    attempt = state['last_attempt_at']
    if attempt and (now - datetime.fromisoformat(attempt)).total_seconds() < POLL_INTERVAL_SECONDS:
        return 0
    # 对齐timer的10分钟时段，避免相邻触发的秒级抖动将检查拖成20分钟一次。
    state['last_attempt_at'] = now.replace(minute=now.minute // 10 * 10,
                                          second=0, microsecond=0).isoformat()
    save()  # 在任何日历、行情或通知请求前保存限频时间。
    failed = False

    if register:
        try:
            import stock_data
            days = stock_data.fetch_trading_days('CN', registration_start.isoformat(), cutoff.isoformat())
            if not isinstance(days, list):
                raise ValueError('交易日历必须返回日期列表')
            if any(not registration_start <= _day(day) <= cutoff for day in days):
                raise ValueError('交易日历返回范围外日期')
        except Exception as exc:
            _log_error('交易日历失败', exc)
            failed = True
        else:
            state['pending_dates'] = sorted(set(state['pending_dates']) | set(days))
            state['queued_through'] = cutoff.isoformat()
            _finish_completed(state, complete)
            save()  # 新队列必须先落盘再查询行情。

    if state['pending_dates']:
        try:
            refreshed = update_history(history_path, end_date=max(state['pending_dates']),
                                       pending_dates=state['pending_dates'], request_interval_seconds=2)
            refreshed_complete = _complete_dates(refreshed)
            if not complete <= refreshed_complete:
                raise ValueError('更新使已有完整日期倒退')
        except Exception as exc:
            _log_error('行情补查失败', exc)
            failed = True
        else:
            data, complete = refreshed, refreshed_complete
            _finish_completed(state, complete)
            save()  # 完整日期移出查询队列，待发状态在副作用前持久化。

    if state['notify_pending']:
        try:
            from scripts.notify_etf_oi import send_etf_oi_card
            if not publish_etf_oi(data_dir):
                raise RuntimeError('网页发布未成功')
            if not send_etf_oi_card(data):
                raise RuntimeError('图卡发送未成功')
        except Exception as exc:
            _log_error('网页或图卡发布失败', exc)
            failed = True
        else:
            state['notify_pending'] = False
            state['notify_dates'] = []
            save()
    return int(failed)


def run_poll(data_dir, now=None) -> int:
    """一次处理已收盘日期；正常未齐/锁占用返回0，实际错误返回1。"""
    try:
        instant = now if now is not None else datetime.now(BEIJING)
        instant = (instant.replace(tzinfo=BEIJING) if instant.tzinfo is None
                   else instant.astimezone(BEIJING))
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / LOCK_FILENAME).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            return _run_locked(directory, instant)
    except Exception as exc:
        _log_error('状态或本地处理失败', exc)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data_dir', nargs='?', default='data')
    args = parser.parse_args()
    return run_poll(args.data_dir)


if __name__ == '__main__':
    sys.exit(main())
