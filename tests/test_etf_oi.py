import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzer import etf_oi


METHOD = 'csi300_etf_free_float_v2'
FILENAME = 'csi300_etf_position.json'


def payload():
    return {
        'methodology_id': METHOD, 'coverage_start': '20240101', 'coverage_end': '20261030',
        'source_urls': ['https://www.csindex.com.cn/'],
        'limitations': ['不代表国家队实际持仓'],
        'records': [
            {'date': '2026-10-26', 'ratio_pct': 1.8, 'etf_assets_yuan': 3.6e11,
             'index_free_float_mv_yuan': 2e13, 'fund_count': 25, 'index_member_count': 300,
             'membership_effective_from': '20261019', 'missing': [], 'funds': [],
             'carried_members': [], 'carried_member_count': 0, 'carried_weight_pct': 0.0,
             'index_close': None, 'index_pre_close': None, 'volume_shares': None},
            {'date': '2026-10-27', 'ratio_pct': None, 'etf_assets_yuan': None,
             'index_free_float_mv_yuan': 2e13, 'fund_count': 25, 'index_member_count': 300,
             'membership_effective_from': '20261019', 'missing': ['ETF份额'], 'funds': [],
             'carried_members': [], 'carried_member_count': 0, 'carried_weight_pct': 0.0,
             'index_close': 4000, 'index_pre_close': 3990, 'volume_shares': 1e8},
        ],
    }


def backend(monkeypatch, data, calls):
    def fetch(begin, finish):
        calls.append((begin, finish))
        return copy.deepcopy(data)
    monkeypatch.setitem(sys.modules, 'stock_data', SimpleNamespace(fetch_csi300_etf_position_history=fetch))


def test_共享新口径原样保存且不因行情缺失抹掉有效比例(tmp_path, monkeypatch):
    calls = []
    backend(monkeypatch, payload(), calls)
    path = tmp_path / FILENAME
    old_path = tmp_path / 'etf_oi.json'
    old_path.write_text('{"records":[{"date":"2023-01-03","ratio_pct":0.1}]}')
    result = etf_oi.update_history(path, end_date='2026-10-28')
    assert calls == [('20240101', '20261028')]
    assert result['methodology_id'] == METHOD
    assert result['as_of'] == '2026-10-26'
    assert [r['date'] for r in result['records']] == ['2026-10-26', '2026-10-27']
    assert result['records'][0]['ratio_pct'] == 1.8
    assert result['records'][0]['fund_count'] == 25
    assert result['records'][0]['index_free_float_mv_yuan'] == 2e13
    assert result['records'][1]['ratio_pct'] is None
    assert json.loads(path.read_text()) == result
    assert json.loads(old_path.read_text())['records'][0]['date'] == '2023-01-03'
    assert sorted(p.name for p in tmp_path.iterdir()) == [FILENAME, 'etf_oi.json']


@pytest.mark.parametrize('wrong', [None, 'four_etfs_float_mv_v0'])
def test_旧版本拒绝增量拼接且不修改文件(tmp_path, monkeypatch, wrong):
    calls = []
    backend(monkeypatch, payload(), calls)
    path = tmp_path / FILENAME
    original = json.dumps({'methodology_id': wrong, 'records': [{'date': '2023-01-03', 'ratio_pct': .1}]})
    path.write_text(original)
    with pytest.raises(ValueError, match='版本|口径'):
        etf_oi.update_history(path, end_date='2026-10-28')
    assert path.read_text() == original
    assert not calls


def test_原始旧历史文件不得被新更新器覆盖(tmp_path, monkeypatch):
    calls = []
    backend(monkeypatch, payload(), calls)
    path = tmp_path / 'etf_oi.json'
    with pytest.raises(ValueError, match='旧|口径'):
        etf_oi.update_history(path, end_date='2026-10-28')
    assert not path.exists()
    assert not calls


@pytest.mark.parametrize('failure', ['empty', 'incomplete', 'wrong_version', 'network'])
def test_更新失败保留原文件并无临时残留(tmp_path, monkeypatch, failure):
    data = payload()
    if failure == 'empty':
        data['records'] = []
    elif failure == 'incomplete':
        for row in data['records']:
            row['ratio_pct'] = None
    elif failure == 'wrong_version':
        data['methodology_id'] = 'legacy'
    backend(monkeypatch, data, [])
    if failure == 'network':
        def failed(*args):
            raise OSError('upstream')
        sys.modules['stock_data'].fetch_csi300_etf_position_history = failed
    path = tmp_path / FILENAME
    original = json.dumps(payload())
    path.write_text(original)
    with pytest.raises((ValueError, OSError)):
        etf_oi.update_history(path, end_date='2026-10-28')
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]


def test_近期45日重拉接受修订且不跨缺口保留陈旧比例(tmp_path, monkeypatch):
    data = payload()
    original = copy.deepcopy(data)
    original['records'].insert(0, dict(data['records'][0], date='2024-01-02', ratio_pct=.3))
    original['records'][2]['ratio_pct'] = 1.9
    data['records'][0]['ratio_pct'] = 1.7
    calls = []
    backend(monkeypatch, data, calls)
    path = tmp_path / FILENAME
    path.write_text(json.dumps(original))
    result = etf_oi.update_history(path, end_date='2026-10-28')
    assert calls == [('20260912', '20261028')]
    assert result['records'][0]['ratio_pct'] == .3
    assert result['records'][1]['ratio_pct'] == 1.7
    assert result['records'][2]['ratio_pct'] is None
    assert result['as_of'] == '2026-10-26'


def migration_payload():
    data = payload()
    data['records'].insert(0, dict(data['records'][0], date='2024-01-02', ratio_pct=.4))
    data['records'][1]['carried_members'] = [{
        'code': '600001.SH', 'source_date': '20261023', 'price_source': 'last_valid_close',
        'close': 10.0, 'free_share': 1000.0, 'free_float_mv_yuan': 1e8,
    }]
    data['records'][1]['carried_member_count'] = 1
    data['records'][1]['carried_weight_pct'] = .0005
    return data


def v1_bytes():
    data = migration_payload()
    data['methodology_id'] = 'csi300_etf_free_float_v1'
    data['records'][0]['ratio_pct'] = .3
    data['records'][1]['ratio_pct'] = None
    for row in data['records']:
        for key in ['carried_members', 'carried_member_count', 'carried_weight_pct']:
            row.pop(key)
    return json.dumps(data, ensure_ascii=False, indent=1).encode()


@pytest.mark.parametrize('prior_backup', [False, True])
def test_v1从2024完整重算不拼接且逐字节保留备份(tmp_path, monkeypatch, prior_backup):
    data = migration_payload()
    calls = []
    backend(monkeypatch, data, calls)
    path = tmp_path / FILENAME
    original = v1_bytes()
    path.write_bytes(original)
    backup = tmp_path / 'csi300_etf_position.v1.json'
    if prior_backup:
        backup.write_bytes(original)
    result = etf_oi.update_history(path, end_date='2026-10-28')
    assert calls == [('20240101', '20261028')]
    assert result['methodology_id'] == 'csi300_etf_free_float_v2'
    assert result['records'] == data['records']
    assert result['records'][0]['ratio_pct'] == .4
    assert result['records'][1]['ratio_pct'] == 1.8
    assert result['records'][1]['missing'] == []
    assert result['records'][1]['carried_member_count'] == 1
    assert backup.read_bytes() == original
    assert json.loads(path.read_bytes()) == result
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize('failure', ['network', 'wrong_version', 'incomplete', 'truncated'])
def test_v1迁移请求失败或不完整保持原文件且不新建备份(tmp_path, monkeypatch, failure):
    data = migration_payload()
    if failure == 'wrong_version':
        data['methodology_id'] = 'csi300_etf_free_float_v1'
    elif failure == 'incomplete':
        for row in data['records']:
            row['ratio_pct'] = None
    elif failure == 'truncated':
        data['records'].pop(0)
    backend(monkeypatch, data, [])
    if failure == 'network':
        def failed(*args):
            raise OSError('upstream')
        sys.modules['stock_data'].fetch_csi300_etf_position_history = failed
    path = tmp_path / FILENAME
    original = v1_bytes()
    path.write_bytes(original)
    with pytest.raises((ValueError, OSError)):
        etf_oi.update_history(path, end_date='2026-10-28')
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_已有不同v1备份时拒绝覆盖两个历史文件(tmp_path, monkeypatch):
    backend(monkeypatch, migration_payload(), [])
    path = tmp_path / FILENAME
    original = v1_bytes()
    path.write_bytes(original)
    backup = tmp_path / 'csi300_etf_position.v1.json'
    backup.write_bytes(b'prior archive')
    with pytest.raises(ValueError, match='备份'):
        etf_oi.update_history(path, end_date='2026-10-28')
    assert path.read_bytes() == original
    assert backup.read_bytes() == b'prior archive'
    assert len(list(tmp_path.iterdir())) == 2


def test_迁移替换失败保留当前v1和完整备份且清理临时文件(tmp_path, monkeypatch):
    backend(monkeypatch, migration_payload(), [])
    path = tmp_path / FILENAME
    original = v1_bytes()
    path.write_bytes(original)
    def fail_replace(*args):
        raise OSError('disk')
    monkeypatch.setattr(etf_oi.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        etf_oi.update_history(path, end_date='2026-10-28')
    assert path.read_bytes() == original
    assert (tmp_path / 'csi300_etf_position.v1.json').read_bytes() == original
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.parametrize('empty', [False, True])
def test_待补日期全未齐保留原字节并返回旧数据(tmp_path, monkeypatch, empty):
    old = payload()
    old['as_of'] = '2026-10-26'
    old['records'].append(dict(old['records'][1], date='2026-10-28'))
    fresh = copy.deepcopy(old)
    fresh['records'][0]['ratio_pct'] = None
    fresh['records'][1]['ratio_pct'] = 9.0  # 范围内非待补日的完整值不能触发保存。
    if empty:
        fresh['records'] = []
    calls = []
    backend(monkeypatch, fresh, calls)
    path = tmp_path / FILENAME
    original = json.dumps(old, ensure_ascii=False, indent=1).encode()
    path.write_bytes(original)
    result = etf_oi.update_history(path, end_date='2026-10-28',
                                   pending_dates={'2026-10-26', '2026-10-28'})
    assert result == old
    assert path.read_bytes() == original
    assert calls == [('20261026', '20261028')]
    assert list(tmp_path.iterdir()) == [path]


def test_待补旧洞跨45日且只合并非连续待补日期的完整值(tmp_path, monkeypatch):
    old = payload()
    old['as_of'] = '2026-10-26'
    old['records'].insert(0, dict(old['records'][1], date='2026-07-01'))
    fresh = copy.deepcopy(old)
    fresh['records'][0]['ratio_pct'] = .8
    fresh['records'][1]['ratio_pct'] = None  # 不可抹去非待补日已有完整记录。
    fresh['records'][2]['ratio_pct'] = 9.0  # 非待补日原有缺口也不顺便写入。
    fresh['records'].append(dict(old['records'][2], date='2026-10-28'))
    calls = []
    backend(monkeypatch, fresh, calls)
    path = tmp_path / FILENAME
    path.write_text(json.dumps(old))
    result = etf_oi.update_history(path, end_date='2026-10-28',
                                   pending_dates={'2026-07-01', '2026-10-28'})
    assert calls == [('20260701', '20261028')]
    assert result['as_of'] == '2026-10-26'
    assert [r['ratio_pct'] for r in result['records']] == [.8, 1.8, None]
    assert result['records'][1:] == old['records'][1:]
    assert json.loads(path.read_bytes()) == result
    assert list(tmp_path.iterdir()) == [path]


def test_待补日期按指定最小范围且只透传非零请求间隔(tmp_path, monkeypatch):
    calls = []
    def fetch(begin, finish, *, request_interval_seconds):
        calls.append((begin, finish, request_interval_seconds))
        data = payload()
        data['records'] = [dict(data['records'][0], date='2026-10-27')]
        return data
    monkeypatch.setitem(sys.modules, 'stock_data', SimpleNamespace(fetch_csi300_etf_position_history=fetch))
    path = tmp_path / FILENAME
    path.write_text(json.dumps(payload()))
    result = etf_oi.update_history(path, end_date='2026-10-28', pending_dates={'2026-10-27'},
                                   request_interval_seconds=2)
    assert calls == [('20261027', '20261027', 2)]
    assert result['as_of'] == '2026-10-27'
    assert result['records'][1]['ratio_pct'] == 1.8


@pytest.mark.parametrize('pending', [set(), '2026-10-27', {'bad'}, {'20261027'},
                                     {'2023-12-29'}, {'2026-10-29'}, {'9999-12-31'}, {True}])
def test_非法待补日期联网前拒绝并保护旧历史(tmp_path, monkeypatch, pending):
    calls = []
    backend(monkeypatch, payload(), calls)
    path = tmp_path / FILENAME
    original = json.dumps(payload()).encode()
    path.write_bytes(original)
    with pytest.raises(ValueError, match='日期|待补|范围'):
        etf_oi.update_history(path, end_date='2026-10-28', pending_dates=pending)
    assert not calls
    assert path.read_bytes() == original


def test_待补未来日期即使截止日允许也拒绝(tmp_path, monkeypatch):
    calls = []
    backend(monkeypatch, payload(), calls)
    path = tmp_path / FILENAME
    path.write_text(json.dumps(payload()))
    with pytest.raises(ValueError, match='日期|未来'):
        etf_oi.update_history(path, end_date='9999-12-31', pending_dates={'9999-12-31'})
    assert not calls


@pytest.mark.parametrize('failure', ['wrong_version', 'outside', 'conflict', 'network'])
def test_待补源错误或未知日期冲突不得静默当作未齐(tmp_path, monkeypatch, failure):
    data = payload()
    data['records'].append(dict(data['records'][1], date='2026-10-28'))
    if failure == 'wrong_version':
        data['methodology_id'] = 'legacy'
    elif failure == 'outside':
        data['records'].append(dict(data['records'][1], date='2026-10-29'))
    elif failure == 'conflict':
        data['records'].append(dict(data['records'][1], ratio_pct=3.0))
    backend(monkeypatch, data, [])
    if failure == 'network':
        def failed(*args):
            raise OSError('upstream')
        sys.modules['stock_data'].fetch_csi300_etf_position_history = failed
    path = tmp_path / FILENAME
    original = json.dumps(payload()).encode()
    path.write_bytes(original)
    with pytest.raises((ValueError, OSError)):
        etf_oi.update_history(path, end_date='2026-10-28',
                              pending_dates={'2026-10-26', '2026-10-28'})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_v1禁止局部迁移且不联网不备份(tmp_path, monkeypatch):
    calls = []
    backend(monkeypatch, migration_payload(), calls)
    path = tmp_path / FILENAME
    original = v1_bytes()
    path.write_bytes(original)
    with pytest.raises(ValueError, match='v1|迁移'):
        etf_oi.update_history(path, end_date='2026-10-28', pending_dates={'2026-10-27'})
    assert not calls
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_局部更新缺少历史文件时明确报错(tmp_path, monkeypatch):
    calls = []
    backend(monkeypatch, payload(), calls)
    path = tmp_path / FILENAME
    with pytest.raises(FileNotFoundError):
        etf_oi.update_history(path, end_date='2026-10-28', pending_dates={'2026-10-27'})
    assert not calls
    assert not path.exists()


@pytest.mark.parametrize('interval', [True, False, -1, float('nan'), float('inf'), '2', None])
def test_非法请求间隔在读取历史与联网前拒绝(tmp_path, monkeypatch, interval):
    calls = []
    backend(monkeypatch, payload(), calls)
    with pytest.raises(ValueError, match='interval|间隔'):
        etf_oi.update_history(tmp_path / FILENAME, pending_dates={'2026-10-27'},
                              request_interval_seconds=interval)
    assert not calls
