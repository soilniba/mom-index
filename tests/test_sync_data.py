import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sync_data


def test_发布ETF历史供网页读取(tmp_path):
    data, web = tmp_path / 'data', tmp_path / 'web'
    data.mkdir()
    content = '{"methodology_id":"csi300_etf_free_float_v1","as_of":"2026-10-28","records":[]}'
    (data / 'csi300_etf_position.json').write_text(content)
    assert sync_data.publish_etf_oi(data, web)
    assert (web / 'csi300_etf_position.json').read_text() == content
    assert list(web.iterdir()) == [web / 'csi300_etf_position.json']


def test_发布失败保留网页旧文件并清理临时文件(tmp_path, monkeypatch):
    data, web = tmp_path / 'data', tmp_path / 'web'
    data.mkdir()
    web.mkdir()
    (data / 'csi300_etf_position.json').write_text('{"as_of":"2026-10-28"}')
    target = web / 'csi300_etf_position.json'
    target.write_text('{"as_of":"2026-10-27"}')
    def failed_replace(*args):
        raise OSError('disk')
    monkeypatch.setattr(sync_data.os, 'replace', failed_replace)
    with pytest.raises(OSError):
        sync_data.publish_etf_oi(data, web)
    assert json.loads(target.read_text())['as_of'] == '2026-10-27'
    assert list(web.iterdir()) == [target]


def test_旧指标文件不发布为新口径(tmp_path):
    data, web = tmp_path / 'data', tmp_path / 'web'
    data.mkdir()
    (data / 'etf_oi.json').write_text('{"records":[]}')
    assert not sync_data.publish_etf_oi(data, web)
    assert not web.exists()
