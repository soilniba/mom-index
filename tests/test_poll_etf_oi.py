import copy
from datetime import datetime
import fcntl
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

METHOD = 'csi300_etf_free_float_v2'
HISTORY = 'csi300_etf_position.json'
STATE = 'etf_poll_state.json'
BEIJING = ZoneInfo('Asia/Shanghai')


def at(value):
    return datetime.fromisoformat(value).replace(tzinfo=BEIJING)


def history(rows):
    return {
        'methodology_id': METHOD, 'coverage_start': '20240101',
        'coverage_end': '20261120', 'source_urls': [], 'limitations': [],
        'as_of': max(day for day, ratio in rows if ratio is not None),
        'records': [{'date': day, 'ratio_pct': ratio, 'missing': [] if ratio else ['share_or_nav']}
                    for day, ratio in rows],
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    poll = importlib.import_module('scripts.poll_etf_oi')
    from analyzer import etf_oi
    from scripts import notify_etf_oi
    import sync_data
    from sync_data import publish_etf_oi

    data = tmp_path / 'data'
    data.mkdir()
    (data / HISTORY).write_text(json.dumps(history([
        ('2024-01-02', None), ('2026-11-03', 1.8),
    ])))
    e = SimpleNamespace(poll=poll, data=data, frontend=tmp_path / 'frontend',
                        values={}, calendar_calls=[], quote_calls=[], sent=[],
                        publish_calls=[], calendar_error=None, quote_error=None,
                        publish_error=None, send_error=None, send_result=True)
    class BackendClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 30, tzinfo=BEIJING).astimezone(tz)

    monkeypatch.setattr(etf_oi, 'datetime', BackendClock)
    trading_days = ['2026-11-02', '2026-11-03', '2026-11-04', '2026-11-05',
                    '2026-11-06', '2026-11-09', '2026-11-10', '2026-11-11']

    def calendar(market, start, end):
        e.calendar_calls.append((market, start, end))
        assert read_state(e)['last_attempt_at']
        if e.calendar_error:
            raise e.calendar_error
        return [d for d in trading_days if start <= d <= end]

    def quotes(start, end, *, request_interval_seconds=0):
        e.quote_calls.append((start, end, request_interval_seconds))
        assert read_state(e)['pending_dates']
        assert read_state(e)['last_attempt_at']
        if e.quote_error:
            raise e.quote_error
        rows = [(d, e.values.get(d)) for d in trading_days
                if start <= d.replace('-', '') <= end]
        result = history([('2026-11-03', 1.8)])
        result['records'] = [{'date': d, 'ratio_pct': ratio,
                              'missing': [] if ratio is not None else ['share_or_nav']}
                             for d, ratio in rows]
        return result

    def publish(data_dir):
        e.publish_calls.append(str(data_dir))
        assert read_state(e)['notify_pending'] is True
        if e.publish_error:
            raise e.publish_error
        return publish_etf_oi(data_dir, e.frontend)

    def send(payload):
        assert read_state(e)['notify_pending'] is True
        if e.send_error:
            raise e.send_error
        e.sent.append(copy.deepcopy(payload))
        return e.send_result

    monkeypatch.setitem(sys.modules, 'stock_data', SimpleNamespace(
        fetch_trading_days=calendar, fetch_csi300_etf_position_history=quotes))
    monkeypatch.setattr(sync_data, 'FRONTEND_DATA_DIR', str(e.frontend))
    monkeypatch.setattr(poll, 'publish_etf_oi', publish)
    monkeypatch.setattr(notify_etf_oi, 'send_etf_oi_card', send, raising=False)
    return e


def read_state(e):
    return json.loads((e.data / STATE).read_text())


def run(e, now):
    return e.poll.run_poll(e.data, now=at(now))


def test_已完成日期被手动刷新退化后重新入队而不漏补(env):
    env.values['2026-11-04'] = 1.9
    assert run(env, '2026-11-05T08:00:00') == 0
    assert read_state(env)['pending_dates'] == []
    data = json.loads((env.data / HISTORY).read_text())
    data['records'][-1].update(ratio_pct=None, missing=['share_or_nav'])
    data['as_of'] = '2026-11-03'
    (env.data / HISTORY).write_text(json.dumps(data))
    env.values['2026-11-04'] = 2.0
    assert run(env, '2026-11-05T08:10:00') == 0
    assert len(env.quote_calls) == 2
    assert env.quote_calls[-1] == ('20261104', '20261104', 2)
    assert read_state(env)['pending_dates'] == []
    assert env.sent[-1]['records'][-1]['ratio_pct'] == 2.0


@pytest.mark.parametrize('remove_row', [False, True])
def test_待发送日期退化后撤回待发并继续补查(env, remove_row):
    env.values['2026-11-04'] = 1.9
    env.send_result = False
    assert run(env, '2026-11-05T08:00:00') == 1
    data = json.loads((env.data / HISTORY).read_text())
    if remove_row:
        data['records'].pop()
    else:
        data['records'][-1].update(ratio_pct=None, missing=['share_or_nav'])
    data['as_of'] = '2026-11-03'
    (env.data / HISTORY).write_text(json.dumps(data))
    env.values['2026-11-04'] = None
    assert run(env, '2026-11-05T08:10:00') == 0
    assert len(env.quote_calls) == 2
    state = read_state(env)
    assert state['pending_dates'] == ['2026-11-04']
    assert state['notify_pending'] is False and state['notify_dates'] == []
    assert len(env.sent) == 1


def test_北京时间8点前不登记不请求不写状态(env):
    assert run(env, '2026-11-05T07:59:59') == 0
    assert not (env.data / STATE).exists()
    assert env.calendar_calls == env.quote_calls == env.sent == []


def test_当天上午不登记当天且不请求行情(env):
    assert run(env, '2026-11-04T09:00:00') == 0
    state = read_state(env)
    assert state['tracking_start'] == '2026-11-04'
    assert state['queued_through'] == '2026-11-03'
    assert state['pending_dates'] == []
    assert env.calendar_calls == env.quote_calls == env.sent == []


def test_北京时间10点后不登记不请求不写状态(env):
    assert run(env, '2026-11-05T10:00:01') == 0
    assert not (env.data / STATE).exists()
    assert env.calendar_calls == env.quote_calls == env.sent == []


def test_北京时间8点后登记前一交易日并在联网前持久化(env):
    assert run(env, '2026-11-05T08:00:00') == 0
    assert read_state(env)['pending_dates'] == ['2026-11-04']
    assert env.calendar_calls == [('CN', '2026-11-04', '2026-11-04')]
    assert env.quote_calls == [('20261104', '20261104', 2)]
    assert env.sent == []


def test_按北京时间识别UTC输入的8点边界(env):
    assert env.poll.run_poll(env.data, now=datetime.fromisoformat('2026-11-04T23:59:59+00:00')) == 0
    assert not (env.data / STATE).exists()
    assert env.quote_calls == []
    assert env.poll.run_poll(env.data, now=datetime.fromisoformat('2026-11-05T00:00:00+00:00')) == 0
    assert read_state(env)['pending_dates'] == ['2026-11-04']


def test_周末推进自然日游标但不登记假交易日或请求行情(env):
    (env.data / HISTORY).write_text(json.dumps(history([('2026-11-06', 1.9)])))
    assert run(env, '2026-11-09T08:00:00') == 0
    assert read_state(env)['queued_through'] == '2026-11-08'
    assert read_state(env)['pending_dates'] == []
    assert env.calendar_calls == [('CN', '2026-11-07', '2026-11-08')]
    assert env.quote_calls == []
    before = (env.data / STATE).read_bytes()
    assert run(env, '2026-11-09T08:10:00') == 0
    assert (env.data / STATE).read_bytes() == before
    assert len(env.calendar_calls) == 1


def test_未齐每10分钟重试完整后停止且不发送陈旧卡(env):
    assert run(env, '2026-11-05T08:00:00') == 0
    original = (env.data / HISTORY).read_bytes()
    assert run(env, '2026-11-05T08:10:00') == 0
    assert (env.data / HISTORY).read_bytes() == original
    assert len(env.quote_calls) == 2
    assert env.sent == []
    env.values['2026-11-04'] = 1.9
    assert run(env, '2026-11-05T08:20:00') == 0
    assert read_state(env)['pending_dates'] == []
    assert read_state(env)['notify_pending'] is False
    assert len(env.sent) == 1
    assert json.loads((env.frontend / HISTORY).read_text())['as_of'] == '2026-11-04'
    before = (env.data / STATE).read_bytes()
    assert run(env, '2026-11-05T08:30:00') == 0
    assert len(env.quote_calls) == 3
    assert len(env.sent) == 1
    assert (env.data / STATE).read_bytes() == before


def test_十点后暂停且跨午夜重启后次日8点合并同一队列同批只发一张(env):
    assert run(env, '2026-11-05T08:00:00') == 0
    env.poll = importlib.reload(env.poll)
    assert run(env, '2026-11-05T10:10:00') == 0
    assert read_state(env)['pending_dates'] == ['2026-11-04']
    assert len(env.quote_calls) == 1
    assert run(env, '2026-11-06T00:00:00') == 0
    assert run(env, '2026-11-06T07:59:59') == 0
    assert len(env.quote_calls) == 1
    assert read_state(env)['pending_dates'] == ['2026-11-04']
    env.values.update({'2026-11-04': 1.9, '2026-11-05': 2.0})
    assert run(env, '2026-11-06T08:10:00') == 0
    assert env.quote_calls[-1] == ('20261104', '20261105', 2)
    assert read_state(env)['pending_dates'] == []
    assert len(env.sent) == 1
    assert env.sent[0]['as_of'] == '2026-11-05'


def test_周末仍重试旧待办(env):
    assert run(env, '2026-11-05T08:00:00') == 0
    assert run(env, '2026-11-09T08:00:00') == 0
    assert read_state(env)['pending_dates'] == ['2026-11-04', '2026-11-05', '2026-11-06']
    assert env.quote_calls[-1] == ('20261104', '20261106', 2)


def test_补齐早期缺口即使as_of未前进仍发布(env):
    assert run(env, '2026-11-05T08:00:00') == 0
    env.values['2026-11-05'] = 2.0
    assert run(env, '2026-11-06T08:00:00') == 0
    assert read_state(env)['pending_dates'] == ['2026-11-04']
    assert len(env.sent) == 1
    env.values['2026-11-04'] = 1.9
    assert run(env, '2026-11-06T08:10:00') == 0
    assert read_state(env)['pending_dates'] == []
    assert len(env.sent) == 2
    assert env.sent[0]['as_of'] == env.sent[1]['as_of'] == '2026-11-05'


@pytest.mark.parametrize('failure', ['send_false', 'send_exception', 'publish'])
def test_发布失败持久化待发且下次不重新查询已齐日期(env, failure):
    env.values['2026-11-04'] = 1.9
    if failure == 'send_false':
        env.send_result = False
    elif failure == 'send_exception':
        env.send_error = OSError('send')
    else:
        env.publish_error = OSError('web')
    assert run(env, '2026-11-05T08:00:00') == 1
    assert read_state(env)['pending_dates'] == []
    assert read_state(env)['notify_pending'] is True
    assert read_state(env)['notify_dates'] == ['2026-11-04']
    assert len(env.quote_calls) == 1
    env.send_result, env.send_error, env.publish_error = True, None, None
    assert run(env, '2026-11-05T08:10:00') == 0
    assert read_state(env)['notify_pending'] is False
    assert read_state(env)['notify_dates'] == []
    assert len(env.quote_calls) == 1


def test_行情异常仍处理上次已齐待发且保留本次待查(env):
    env.values['2026-11-04'] = 1.9
    env.send_error = OSError('send')
    assert run(env, '2026-11-05T08:00:00') == 1
    env.send_error = None
    env.quote_error = RuntimeError('upstream')
    assert run(env, '2026-11-06T08:00:00') == 1
    assert read_state(env)['pending_dates'] == ['2026-11-05']
    assert read_state(env)['notify_pending'] is False
    assert len(env.sent) == 1


def test_跨进程限频600秒内手动重跑不调用任何外部接口(env):
    assert run(env, '2026-11-05T08:00:00') == 0
    env.poll = importlib.reload(env.poll)
    before = (env.data / STATE).read_bytes()
    assert run(env, '2026-11-05T08:09:59') == 0
    assert len(env.calendar_calls) == len(env.quote_calls) == 1
    assert (env.data / STATE).read_bytes() == before
    assert run(env, '2026-11-05T08:10:00') == 0
    assert len(env.quote_calls) == 2


def test_相邻10分钟定时有秒级抖动仍各查询一次(env):
    assert run(env, '2026-11-05T08:00:01.200000') == 0
    assert run(env, '2026-11-05T08:10:00.400000') == 0
    assert len(env.quote_calls) == 2
    assert read_state(env)['last_attempt_at'] == '2026-11-05T08:10:00+08:00'


def test_锁占用立即退出且不改状态或联网(env):
    with (env.data / '.etf-poll.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(env, '2026-11-05T08:00:00') == 0
    assert not (env.data / STATE).exists()
    assert env.calendar_calls == env.quote_calls == []


@pytest.mark.parametrize('source', ['calendar', 'quote'])
def test_源异常不丢队列并持久化限频且记录异常类型(env, capsys, source):
    if source == 'calendar':
        env.calendar_error = OSError('upstream')
    else:
        env.quote_error = OSError('upstream')
    assert run(env, '2026-11-05T08:00:00') == 1
    state = read_state(env)
    assert state['last_attempt_at']
    assert state['pending_dates'] == ([] if source == 'calendar' else ['2026-11-04'])
    assert 'OSError' in capsys.readouterr().err
    calls = (len(env.calendar_calls), len(env.quote_calls))
    assert run(env, '2026-11-05T08:09:59') == 0
    assert (len(env.calendar_calls), len(env.quote_calls)) == calls
    env.calendar_error = env.quote_error = None
    assert run(env, '2026-11-05T08:10:00') == 0
    assert read_state(env)['pending_dates'] == ['2026-11-04']


@pytest.mark.parametrize('bad', ['{', '{}', '{"pending_dates": []}'])
def test_已有损坏或缺字段state拒绝联网且保持原文件(env, capsys, bad):
    (env.data / STATE).write_text(bad)
    assert run(env, '2026-11-05T08:00:00') == 1
    assert (env.data / STATE).read_text() == bad
    assert env.calendar_calls == env.quote_calls == []
    assert 'Error' in capsys.readouterr().err


@pytest.mark.parametrize('bad', [None, {}, {'methodology_id': METHOD, 'records': []}])
def test_首次缺失或无合法完整v2历史拒绝联网(env, bad):
    path = env.data / HISTORY
    if bad is None:
        path.unlink()
    else:
        path.write_text(json.dumps(bad))
    assert run(env, '2026-11-05T08:00:00') == 1
    assert env.calendar_calls == env.quote_calls == []


def test_数据已保存但队列尚未保存时重启恢复待发不重新查询(env):
    assert run(env, '2026-11-05T08:00:00') == 0
    (env.data / HISTORY).write_text(json.dumps(history([
        ('2026-11-03', 1.8), ('2026-11-04', 1.9),
    ])))
    assert run(env, '2026-11-05T08:10:00') == 0
    assert read_state(env)['pending_dates'] == []
    assert len(env.quote_calls) == 1
    assert len(env.sent) == 1


def test_原子状态替换失败不联网不损坏原队列且清理临时文件(env, monkeypatch):
    assert run(env, '2026-11-05T08:00:00') == 0
    before = (env.data / STATE).read_bytes()
    def fail(*args):
        raise OSError('disk')
    monkeypatch.setattr(env.poll.os, 'replace', fail)
    assert run(env, '2026-11-05T08:10:00') == 1
    assert (env.data / STATE).read_bytes() == before
    assert len(env.quote_calls) == 1
    assert not list(env.data.glob('*.tmp'))
