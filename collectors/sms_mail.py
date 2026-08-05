#!/usr/bin/env python3
"""
sms_mail.py — 短信转发邮箱收件模块（POP3）

转发软件把手机短信转发到转发邮箱，本模块轮询 POP3 提取验证码。
平台无关，供 xhs_sms_login 等自动登录脚本复用。

配置（环境变量，与 xhs_sms_login 共用）：
- GET_SMS_MAIL：邮箱账号（如 sms-forward@126.com）
- GET_SMS_MAIL_KEY：邮箱授权码

为什么 POP3 而不是 IMAP：网易对 IMAP 有 "Unsafe Login" 安全拦截
（2026-08-05 实测，连续多次均被拒），POP3 稳定可用且不分文件夹，
垃圾箱里的转发邮件也能收到。

验证码提取：只认正文 "内容：" 行后的 6 位数字（转发邮件正文格式），
避免误匹配邮件头/系统邮件里的数字。
"""

import email
import os
import poplib
import re
import time

MAIL_HOST = "pop.126.com"
MAIL_PORT = 995


def _connect() -> poplib.POP3_SSL:
    """POP3 连接（账号与授权码从环境变量读取，不落盘）。"""
    p = poplib.POP3_SSL(MAIL_HOST, MAIL_PORT, timeout=20)
    p.user(os.environ["GET_SMS_MAIL"])
    p.pass_(os.environ["GET_SMS_MAIL_KEY"])
    return p


def _body_of(em) -> str:
    if em.is_multipart():
        for part in em.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode("utf-8", "ignore")
    return em.get_payload(decode=True).decode("utf-8", "ignore")


def wait_for_code(pattern: str = r"内容：\s*(\d{6})",
                  timeout: int = 175, interval: int = 8) -> str | None:
    """轮询 POP3 等新邮件中的验证码，返回第一个匹配的 6 位数字。

    只认轮询开始后到达的新邮件（POP3 编号递增），避免把历史邮件里的
    数字当验证码。超时返回 None。
    """
    deadline = time.time() + timeout
    since = None
    while time.time() < deadline:
        p = None
        try:
            p = _connect()
            total = p.stat()[0]
            if since is None:
                since = total
            start = max(since + 1, total - 10 + 1)
            for i in range(start, total + 1):
                _, lines, _ = p.top(i, 80)
                em = email.message_from_bytes(b"\r\n".join(lines))
                m = re.search(pattern, _body_of(em))
                if m:
                    return m.group(1)
        except Exception:
            pass  # 网络抖动，下一轮重试
        finally:
            if p:
                try:
                    p.quit()
                except Exception:
                    pass
        time.sleep(interval)
    return None
