#!/usr/bin/env python3
"""
notify_feishu.py — 发送告警到飞书「瞎报错」群

通过本机 feishu-bot relay API（127.0.0.1:8410）发送 markdown 卡片。
token 从环境变量 FEISHU_BOT_TOKEN 或 ~/.config/mom-index/env 读取。

用法：
  python3 scripts/notify_feishu.py --text "告警内容" [--summary "摘要"]
  或 import send_notice(markdown, summary="")

通知失败不抛异常（静默 stderr），不中断采集主流程。
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

RELAY_URL = "http://127.0.0.1:8410/relay/send/markdown"
ERROR_CHAT_ID = "oc_899a02f5430d2fe335a117f15db84ff8"  # 瞎报错群
ENV_FILE = Path("~/.config/mom-index/env").expanduser()
TOKEN_VAR = "FEISHU_BOT_TOKEN"


def get_token() -> str:
    """优先环境变量，其次 source env 文件。"""
    token = os.environ.get(TOKEN_VAR, "")
    if token:
        return token
    r = subprocess.run(
        ["bash", "-c", f"source {ENV_FILE} 2>/dev/null; printf '%s' \"${TOKEN_VAR}\""],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def send_notice(markdown: str, summary: str = "") -> bool:
    """发送告警到瞎报错群。返回是否发送成功（不抛异常）。"""
    body = json.dumps({
        "chat_id": ERROR_CHAT_ID,
        "markdown": markdown,
        "summary": summary or "mom-index 告警",
        "source": "mom-index",
    }).encode()
    headers = {"Content-Type": "application/json"}
    token = get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(RELAY_URL, data=body, method="POST", headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
            if not ok:
                print(f"[notify_feishu] relay 返回 HTTP {resp.status}", file=sys.stderr)
            return ok
    except Exception as e:
        print(f"[notify_feishu] 发送失败: {e}", file=sys.stderr)
        return False


def send_qr_image(file_path: str) -> bool:
    """上传图片并发到瞎报错群（用于小红书扫码验证）。不抛异常。"""
    def _post(url: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"}
        token = get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = Request(url, data=body, method="POST", headers=headers)
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    try:
        up = _post("http://127.0.0.1:8410/relay/upload/image", {"file_path": file_path})
        if not up.get("ok") or not up.get("image_key"):
            print(f"[notify_feishu] 图片上传失败: {up}", file=sys.stderr)
            return False
        sn = _post("http://127.0.0.1:8410/relay/send/image",
                   {"chat_id": ERROR_CHAT_ID, "image_key": up["image_key"]})
        return bool(sn.get("ok"))
    except Exception as e:
        print(f"[notify_feishu] 发送二维码失败: {e}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="发送告警到飞书瞎报错群")
    parser.add_argument("--text", required=True, help="markdown 告警内容")
    parser.add_argument("--summary", default="", help="通知摘要")
    args = parser.parse_args()
    return 0 if send_notice(args.text, args.summary) else 1


if __name__ == "__main__":
    sys.exit(main())
