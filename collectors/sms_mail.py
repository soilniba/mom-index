#!/usr/bin/env python3
"""
sms_mail.py — 短信转发邮箱收件模块（POP3）

转发软件把手机短信转发到转发邮箱，本模块轮询 POP3 提取验证码。
平台无关，供 xhs_sms_login 等自动登录脚本复用。

配置（环境变量，与 xhs_sms_login 共用）：
- GET_SMS_MAIL：邮箱账号（如 sms-forward@126.com）
- GET_SMS_MAIL_KEY：邮箱授权码
- GET_SMS_POP_HOST：POP3 服务器地址（如 pop.126.com；换邮箱服务商时修改）

为什么 POP3 而不是 IMAP：网易对 IMAP 有 "Unsafe Login" 安全拦截
（2026-08-05 实测，连续多次均被拒），POP3 稳定可用且不分文件夹，
垃圾箱里的转发邮件也能收到。

验证码提取：只认正文 "内容：" 行后的 6 位数字（转发邮件正文格式），
避免误匹配邮件头/系统邮件里的数字。
"""

import email
import email.utils
import os
import poplib
import re
import time

MAIL_HOST = os.environ["GET_SMS_POP_HOST"]
MAIL_PORT = 995  # POP3-over-SSL 标准端口，各主流邮箱通用
LOOKBACK = 30  # 首轮回溯扫描的邮件数
FRESH_WINDOW = 200  # 只认该秒数内到达的邮件（验证码 3 分钟有效）


def _connect() -> poplib.POP3_SSL:
    """POP3 连接（账号与授权码从环境变量读取，不落盘）。"""
    p = poplib.POP3_SSL(MAIL_HOST, MAIL_PORT, timeout=20)
    try:
        p.user(os.environ["GET_SMS_MAIL"])
        p.pass_(os.environ["GET_SMS_MAIL_KEY"])
        return p
    except Exception:
        try:
            p.quit()
        except Exception:
            pass
        raise


def _body_of(em) -> str:
    """提取邮件正文（按 Content-Type charset 解码，兼容 GBK/utf-8）。"""
    parts = list(em.walk()) if em.is_multipart() else [em]
    for part in parts:
        if part.get_content_type() != "text/plain":
            continue
        data = part.get_payload(decode=True)
        if isinstance(data, str):  # 无编码头的邮件直接返回 str
            return data
        if data is None:
            return str(part.get_payload())
        try:
            return data.decode(part.get_content_charset() or "utf-8", "ignore")
        except LookupError:
            return data.decode("utf-8", "ignore")
    return ""


def _fresh(em, cutoff: float) -> bool:
    """邮件 Date 是否在 cutoff 之后（解析失败视为不新鲜，保守跳过）。"""
    try:
        dt = email.utils.parsedate_to_datetime(em.get("Date", ""))
    except Exception:
        return False
    return dt is not None and dt.timestamp() >= cutoff


def wait_for_code(pattern: str = r"内容：\s*(\d{6})",
                  timeout: int = 175, interval: int = 8) -> str | None:
    """轮询 POP3 等新邮件中的验证码，返回第一个匹配的 6 位数字。

    首轮回溯最近 LOOKBACK 封邮件并叠加时间窗过滤（只认 FRESH_WINDOW 秒内
    到达的），避免发码后至首次查询之间已到达的邮件被永久跳过；后续轮
    只查新增邮件（编号递增）。超时返回 None。
    """
    cutoff = time.time() - FRESH_WINDOW
    deadline = time.time() + timeout
    since = None
    while time.time() < deadline:
        p = None
        try:
            p = _connect()
            total = p.stat()[0]
            if since is None:
                since = total
                start = max(1, total - LOOKBACK + 1)
            else:
                start = since + 1
            for i in range(start, total + 1):
                _, lines, _ = p.top(i, 80)
                em = email.message_from_bytes(b"\r\n".join(lines))
                m = re.search(pattern, _body_of(em))
                if m and _fresh(em, cutoff):
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
