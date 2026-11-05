"""慢手动请求与轮询相交时，旧响应不能抹去已发布的完整日。"""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzer import etf_oi


def test_slow_manual_response_cannot_replace_completed_poll_result(tmp_path, monkeypatch):
    path = tmp_path / etf_oi.HISTORY_FILENAME
    old = {'methodology_id': etf_oi.METHODOLOGY_ID, 'as_of': '2026-11-03', 'records': [
        {'date': '2026-11-03', 'ratio_pct': 2.1},
        {'date': '2026-11-04', 'ratio_pct': None},
    ]}
    path.write_text(json.dumps(old))
    calls = 0
    def fetch(begin, finish):
        nonlocal calls
        calls += 1
        if calls == 1:
            etf_oi.update_history(path, end_date='2026-11-04', pending_dates={'2026-11-04'})
            return copy.deepcopy(old)
        return {'methodology_id': etf_oi.METHODOLOGY_ID,
                'records': [{'date': '2026-11-04', 'ratio_pct': 2.2}]}
    monkeypatch.setitem(sys.modules, 'stock_data', SimpleNamespace(fetch_csi300_etf_position_history=fetch))
    with pytest.raises(ValueError, match='并发|其他更新'):
        etf_oi.update_history(path, end_date='2026-11-04')
    saved = json.loads(path.read_text())
    assert saved['as_of'] == '2026-11-04'
    assert saved['records'][-1]['ratio_pct'] == 2.2
    assert list(tmp_path.iterdir()) == [path]
