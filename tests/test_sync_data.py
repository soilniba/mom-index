import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sync_data


def test_发布ETF历史供网页读取(tmp_path):
    data, web = tmp_path / 'data', tmp_path / 'web'
    data.mkdir()
    content = '{"as_of":"2026-10-28","records":[]}'
    (data / 'etf_oi.json').write_text(content)
    assert sync_data.publish_etf_oi(data, web)
    assert (web / 'etf_oi.json').read_text() == content
    assert list(web.iterdir()) == [web / 'etf_oi.json']


def test_发布失败保留网页旧文件并清理临时文件(tmp_path, monkeypatch):
    data, web = tmp_path / 'data', tmp_path / 'web'
    data.mkdir()
    web.mkdir()
    (data / 'etf_oi.json').write_text('{"as_of":"2026-10-28"}')
    target = web / 'etf_oi.json'
    target.write_text('{"as_of":"2026-10-27"}')
    def failed_replace(*args):
        raise OSError('disk')
    monkeypatch.setattr(sync_data.os, 'replace', failed_replace)
    with pytest.raises(OSError):
        sync_data.publish_etf_oi(data, web)
    assert json.loads(target.read_text())['as_of'] == '2026-10-27'
    assert list(web.iterdir()) == [target]
