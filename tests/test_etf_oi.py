import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzer import etf_oi


METHOD = 'csi300_etf_free_float_v1'
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
             'index_close': None, 'index_pre_close': None, 'volume_shares': None},
            {'date': '2026-10-27', 'ratio_pct': None, 'etf_assets_yuan': None,
             'index_free_float_mv_yuan': 2e13, 'fund_count': 25, 'index_member_count': 300,
             'membership_effective_from': '20261019', 'missing': ['ETF份额'], 'funds': [],
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
