#!/usr/bin/env python3
"""
captcha_solver.py — 打码平台封装（超级鹰）

场景：小红书点「获取验证码」时偶发滑块/点选人机验证（自研盾 burdock），
自动化无法直接通过。把验证图片上传打码平台（人工打码兜底），返回坐标，
本地再模拟拖动/点击。

凭证走环境变量（.bashrc 已设）：
  CAPTCHA_USER    超级鹰用户名
  CAPTCHA_PASS    密码明文（仅内存中 md5 后传参，不落盘不打印）
  CAPTCHA_SOFTID  软件ID（用户中心生成，可空）

未配置凭证时模块可正常导入，调用时抛 ConfigError —— 不配置不影响原登录流程。

超级鹰 API（https://upload.chaojiying.net/Upload/Processing.php）：
  参数 user/pass2/softid/codetype/file_base64（pass2 为密码 32 位小写 md5），
  返回 {"err_no":0,"err_str":"OK","pic_id":..,"pic_str":结果,"md5":..}。
  滑块拼图类型 9602：返回两个坐标，缺口水平距离 = |x1-x2|；
  点选类型 9004：返回 1~4 个坐标。
"""

import base64
import hashlib
import json
import os
import urllib.parse
import urllib.request

API_URL = "https://upload.chaojiying.net/Upload/Processing.php"
REPORT_URL = "https://upload.chaojiying.net/Upload/ReportError.php"
TIMEOUT = 30

SLIDER = 9602   # 滑块拼图：两坐标 x 差绝对值 = 缺口水平距离
CLICK_N = 9004  # 点选：1~4 个坐标


class CaptchaError(Exception):
    """打码平台调用失败（凭证错误 / 识别失败 / 网络异常）。"""


class ConfigError(CaptchaError):
    """CAPTCHA_* 环境变量未配置。"""


def _credentials() -> tuple[str, str, str]:
    user = os.environ.get("CAPTCHA_USER", "")
    password = os.environ.get("CAPTCHA_PASS", "")
    softid = os.environ.get("CAPTCHA_SOFTID", "")
    if not user or not password:
        raise ConfigError("未配置打码平台凭证（CAPTCHA_USER/CAPTCHA_PASS）")
    return user, password, softid


def _post(url: str, params: dict) -> dict:
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(params).encode(),
        headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _upload(codetype: int, img_bytes: bytes) -> dict:
    """上传图片识别，返回超级鹰原始 JSON；err_no != 0 时抛 CaptchaError。"""
    user, password, softid = _credentials()
    data = _post(API_URL, {
        "user": user,
        "pass2": hashlib.md5(password.encode()).hexdigest(),
        "softid": softid,
        "codetype": str(codetype),
        "file_base64": base64.b64encode(img_bytes).decode(),
    })
    if data.get("err_no") != 0:
        raise CaptchaError(f"超级鹰 {data.get('err_no')}: {data.get('err_str')}")
    return data


def solve(codetype: int, img_bytes: bytes) -> tuple[str, str]:
    """识别验证码图片，返回 (pic_str, pic_id)。pic_id 用于识别错误时返分。"""
    data = _upload(codetype, img_bytes)
    return data["pic_str"], data["pic_id"]


def _coords(pic_str: str) -> list[tuple[int, int]]:
    """解析坐标结果，兼容 "x,y" / "x,y|x,y" / "x,y,x,y" 三种格式。"""
    tokens = [t for t in pic_str.replace("|", ",").split(",") if t.strip()]
    return [(int(tokens[i]), int(tokens[i + 1]))
            for i in range(0, len(tokens) - 1, 2)]


def slider_gap(img_bytes: bytes) -> tuple[int, str]:
    """滑块拼图缺口水平距离（9602），返回 (gap 像素, pic_id)。"""
    pic_str, pic_id = solve(SLIDER, img_bytes)
    coords = _coords(pic_str)
    if len(coords) < 2:
        raise CaptchaError(f"滑块坐标解析失败: {pic_str!r}")
    return abs(coords[0][0] - coords[1][0]), pic_id


def point_click(img_bytes: bytes) -> tuple[list[tuple[int, int]], str]:
    """点选验证（9004：1~4 个坐标），返回 (坐标列表, pic_id)。"""
    pic_str, pic_id = solve(CLICK_N, img_bytes)
    return _coords(pic_str), pic_id


def report_error(pic_id: str) -> None:
    """识别结果确实错误时返分。超级鹰限定 3 分钟内、确认识别错才可调用。"""
    user, password, softid = _credentials()
    try:
        _post(REPORT_URL, {
            "user": user,
            "pass2": hashlib.md5(password.encode()).hexdigest(),
            "softid": softid,
            "id": pic_id,
        })
    except Exception:
        pass  # 返分失败不影响主流程
