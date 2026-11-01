"""确认重验证策略涵盖成功响应与条件请求，防止跨版本缓存。"""
from functools import partial
import http.client
from http.server import ThreadingHTTPServer
import importlib
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.mark.parametrize('filename', ['dashboard.html', 'etf_oi.js', 'data/example.json'])
def test_静态响应和304都要求缓存重验证(tmp_path, filename):
    handler = importlib.import_module('scripts.serve_frontend').RevalidatingHandler
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('version-2')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(handler, directory=tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
    try:
        conn.request('GET', '/' + filename)
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader('Cache-Control') == 'no-cache'
        modified = response.getheader('Last-Modified')
        assert response.read() == b'version-2'
        conn.request('GET', '/' + filename, headers={'If-Modified-Since': modified})
        response = conn.getresponse()
        assert response.status == 304
        assert response.getheader('Cache-Control') == 'no-cache'
        response.read()
    finally:
        conn.close()
        server.shutdown()
        server.server_close()
        thread.join()
