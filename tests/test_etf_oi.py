import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzer import etf_oi


CODES = ('510300.SH', '510310.SH', '510330.SH', '159919.SZ')


def inputs(days=('20261026', '20261027')):
    shares = {c: [{'ts_code': c, 'trade_date': d, 'fd_share': 100} for d in days] for c in CODES}
    navs = {c: [{'ts_code': c, 'nav_date': d, 'unit_nav': 2} for d in days] for c in CODES}
    vals = [{'ts_code': '000300.SH', 'trade_date': d, 'float_mv': 10_000_000_000} for d in days]
    prices = [{'ts_code': '000300.SH', 'trade_date': d, 'close': 4000,
               'pre_close': 3990, 'vol': 100} for d in days]
    return shares, navs, vals, prices


def test_同日四只净资产除普通流通市值并统一单位():
    records = etf_oi.build_records(*inputs())
    assert records[-1]['date'] == '2026-10-27'
    assert records[-1]['ratio_pct'] == pytest.approx(0.08)
    assert records[-1]['etf_assets_yuan'] == 8_000_000
    assert records[-1]['index_float_mv_yuan'] == 10_000_000_000
    assert records[-1]['volume_shares'] == 10_000
    assert records[-1]['index_pre_close'] == 3990
    assert len(records[-1]['funds']) == 4


@pytest.mark.parametrize('kind', ['missing_share', 'wrong_day', 'nan_nav', 'zero_mv', 'bad_close', 'bad_volume'])
def test_任何必需数据缺失不跨日补值或算零(kind):
    shares, navs, vals, prices = inputs()
    if kind == 'missing_share':
        shares[CODES[0]].pop()
    elif kind == 'wrong_day':
        navs[CODES[0]][-1]['nav_date'] = '20261028'
    elif kind == 'nan_nav':
        navs[CODES[0]][-1]['unit_nav'] = float('nan')
    elif kind == 'zero_mv':
        vals[-1]['float_mv'] = 0
    elif kind == 'bad_close':
        prices[-1]['close'] = -1
    else:
        prices[-1]['vol'] = float('inf')
    records = etf_oi.build_records(shares, navs, vals, prices)
    assert records[0]['ratio_pct'] == pytest.approx(0.08)
    assert records[-1]['ratio_pct'] is None
    assert records[-1]['missing']


def test_等值重复去重而冲突拒绝():
    shares, navs, vals, prices = inputs()
    navs[CODES[0]].append(copy.deepcopy(navs[CODES[0]][-1]))
    assert len(etf_oi.build_records(shares, navs, vals, prices)) == 2
    navs[CODES[0]][-1]['unit_nav'] = 3
    with pytest.raises(ValueError, match='重复|冲突'):
        etf_oi.build_records(shares, navs, vals, prices)


def test_行情漏中间一天仍保留缺口不跨日连线():
    shares, navs, vals, prices = inputs(('20261026', '20261027', '20261028'))
    prices.pop(1)
    records = etf_oi.build_records(shares, navs, vals, prices)
    assert [r['date'] for r in records] == ['2026-10-26', '2026-10-27', '2026-10-28']
    assert records[1]['ratio_pct'] is None
    assert records[1]['index_close'] is None
    assert '收盘点位' in records[1]['missing']


def backend(data):
    shares, navs, vals, prices = data
    return SimpleNamespace(
        fetch_etf_share_history=lambda code, start, end: shares[code],
        fetch_etf_nav_history=lambda code, start, end: navs[code],
        workflow_index_daily_valuation=lambda *args, **kwargs: vals,
        workflow_index_daily_prices=lambda *args, **kwargs: prices,
    )


def test_更新保留历史接受近期修订并按最后完整日期标注(tmp_path, monkeypatch):
    data = inputs()
    monkeypatch.setitem(sys.modules, 'stock_data', backend(data))
    path = tmp_path / 'etf_oi.json'
    old = {'records': [{'date': '2023-01-03', 'ratio_pct': 0.01}], 'as_of': '2023-01-03'}
    path.write_text(json.dumps(old))
    data[1][CODES[0]][0]['unit_nav'] = 3
    data[0][CODES[0]].pop()
    result = etf_oi.update_history(path, end_date='2026-10-28')
    assert result['as_of'] == '2026-10-26'
    assert result['records'][0]['date'] == '2023-01-03'
    assert result['records'][1]['ratio_pct'] == pytest.approx(0.09)
    assert result['records'][2]['ratio_pct'] is None
    assert json.loads(path.read_text()) == result
    assert list(tmp_path.iterdir()) == [path]


def test_空响应失败时不覆盖已存历史(tmp_path, monkeypatch):
    data = inputs()
    data[0][CODES[1]].clear()
    monkeypatch.setitem(sys.modules, 'stock_data', backend(data))
    path = tmp_path / 'etf_oi.json'
    original = '{"records": [{"date": "2026-10-23", "ratio_pct": 0.1}]}'
    path.write_text(original)
    with pytest.raises(ValueError, match='空|无数据'):
        etf_oi.update_history(path, end_date='2026-10-28')
    assert path.read_text() == original


def test_增量请求使用45日重叠窗口(tmp_path, monkeypatch):
    calls = []
    fake = backend(inputs())
    def shares(code, start, end):
        calls.append((start, end))
        return inputs()[0][code]
    fake.fetch_etf_share_history = shares
    monkeypatch.setitem(sys.modules, 'stock_data', fake)
    path = tmp_path / 'etf_oi.json'
    path.write_text(json.dumps({'records': [{'date': '2026-10-26', 'ratio_pct': 1,
                                           'index_pre_close': 3990}]}))
    etf_oi.update_history(path, end_date='2026-10-28')
    assert set(calls) == {('20260911', '20261028')}
