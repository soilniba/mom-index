"""captcha_solver 打码平台封装测试（mock 网络层，不发真实请求）"""
import hashlib
import json
import urllib.parse
from unittest import mock

import pytest

from collectors.captcha_solver import (
    CaptchaError, ConfigError, point_click, report_error, slider_gap,
)

IMG = b"\xff\xd8fake-jpeg"


def _fake_resp(data: dict):
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.__enter__.return_value = resp  # with urlopen(...) as resp 取回自身
    return resp


def _mock_upload(monkeypatch, data: dict):
    resp = _fake_resp(data)
    m = mock.Mock(return_value=resp)
    monkeypatch.setattr("collectors.captcha_solver.urllib.request.urlopen", m)
    return m


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CAPTCHA_USER", "test_user")
    monkeypatch.setenv("CAPTCHA_PASS", "secret")
    monkeypatch.setenv("CAPTCHA_SOFTID", "1")


def test_slider_gap(monkeypatch):
    m = _mock_upload(monkeypatch, {"err_no": 0, "err_str": "OK", "pic_id": "p1",
                                   "pic_str": "100,50|220,60"})
    gap, pic_id = slider_gap(IMG)
    assert gap == 120
    assert pic_id == "p1"
    # 密码传 md5（pass2），不传明文；类型号 9602
    body = urllib.parse.parse_qs(m.call_args.args[0].data.decode())
    assert "pass" not in body
    assert body["pass2"] == [hashlib.md5(b"secret").hexdigest()]
    assert body["codetype"] == ["9602"]


def test_slider_gap_comma_format(monkeypatch):
    """兼容 "x1,y1,x2,y2" 无分隔符格式。"""
    _mock_upload(monkeypatch, {"err_no": 0, "err_str": "OK", "pic_id": "p1",
                               "pic_str": "100,50,220,60"})
    gap, _ = slider_gap(IMG)
    assert gap == 120


def test_point_click(monkeypatch):
    _mock_upload(monkeypatch, {"err_no": 0, "err_str": "OK", "pic_id": "p2",
                               "pic_str": "10,20|30,40"})
    coords, pic_id = point_click(IMG)
    assert coords == [(10, 20), (30, 40)]
    assert pic_id == "p2"


def test_api_error_raises(monkeypatch):
    _mock_upload(monkeypatch, {"err_no": -100, "err_str": "密码错误",
                               "pic_id": "", "pic_str": ""})
    with pytest.raises(CaptchaError, match="密码错误"):
        slider_gap(IMG)


def test_non_dict_response_raises(monkeypatch):
    resp = mock.MagicMock()
    resp.read.return_value = b"[1,2]"
    resp.__enter__.return_value = resp
    monkeypatch.setattr(
        "collectors.captcha_solver.urllib.request.urlopen",
        mock.Mock(return_value=resp))
    with pytest.raises(CaptchaError, match="非 JSON 对象"):
        slider_gap(IMG)


def test_bad_coords_raises(monkeypatch):
    _mock_upload(monkeypatch, {"err_no": 0, "err_str": "OK", "pic_id": "p",
                               "pic_str": "abc"})
    with pytest.raises(CaptchaError, match="坐标解析失败"):
        slider_gap(IMG)


def test_missing_config_raises(monkeypatch):
    monkeypatch.delenv("CAPTCHA_USER")
    with pytest.raises(ConfigError):
        slider_gap(IMG)


def test_report_error_swallows_network_error(monkeypatch):
    monkeypatch.setattr(
        "collectors.captcha_solver.urllib.request.urlopen",
        mock.Mock(side_effect=RuntimeError("network down")))
    report_error("p1")  # 不抛异常


def test_report_error_sends_pic_id(monkeypatch):
    m = _mock_upload(monkeypatch, {"err_no": 0, "err_str": "OK", "pic_id": "",
                                   "pic_str": ""})
    report_error("p123")
    body = urllib.parse.parse_qs(m.call_args.args[0].data.decode())
    assert body["id"] == ["p123"]
