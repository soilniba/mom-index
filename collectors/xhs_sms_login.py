#!/usr/bin/env python3
"""
xhs_sms_login.py — 小红书短信验证码自动登录（替代手动扫码）

流程：Playwright 打开小红书登录弹窗 → 填手机号 → 勾选协议 → 点获取验证码 →
手机收短信后由转发软件转到 sms-forward@126.com 邮箱 → sms_mail 轮询 POP3 提取 →
自动填入验证码并登录 → 新 cookie 写回 ~/.config/mom-index/env 的 XHS_COOKIE。

手机号、邮箱账号、授权码均从环境变量读取（.bashrc 已设）：
GET_SMS_PHONE（手机号，转发软件已配置）、GET_SMS_MAIL（邮箱账号）、
GET_SMS_MAIL_KEY（邮箱授权码）。

触发方式：手动运行（python3 collectors/xhs_sms_login.py）或 feishu-bot 调用。
退出码：0=登录成功（cookie 已写回）、1=验证码超时未到、2=异常
"""

import asyncio
import os
import sys
from pathlib import Path

try:
    from .sms_mail import wait_for_code
    from .xhs_cookie_check import update_env_cookie
except ImportError:
    # 独立执行（python3 collectors/xhs_sms_login.py）时无包上下文
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sms_mail import wait_for_code
    from xhs_cookie_check import update_env_cookie

HOME_URL = "https://www.xiaohongshu.com/"
REQUIRED_ENV = ("GET_SMS_PHONE", "GET_SMS_MAIL", "GET_SMS_MAIL_KEY")
PHONE = os.environ.get("GET_SMS_PHONE", "")
CODE_TIMEOUT = 175  # 与按钮倒计时一致：验证码 3 分钟有效，175s 后可重发
MASKED_PHONE = PHONE[:3] + "***" + PHONE[-4:]


def _load_notify():
    """延迟导入 notify_feishu（独立执行时需把 scripts 目录加进 path）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from notify_feishu import send_notice
    return send_notice


def _cookies_to_header(ctx_cookies: list) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in ctx_cookies)


async def _ensure_dialog(page) -> bool:
    """等登录弹窗出现（无弹窗则点可见的 .login-btn 入口），上限 30s。"""
    for _ in range(15):
        if await page.locator(".login-container").count():
            return True
        btn = page.locator(".login-btn:visible")
        if await btn.count():
            try:
                await btn.first.click(timeout=5000)
            except Exception:
                pass
        await page.wait_for_timeout(2000)
    return False


async def _send_code(page) -> bool:
    """勾选协议并点获取验证码，返回是否真正发出（按钮进入倒计时）。

    未勾选协议时点击会被前端静默吞掉（无请求无报错，实测），因此必须先勾
    协议再点发码。协议状态无法从 class 判断，两轮覆盖两种初始状态：
    先勾再点；若协议本已勾选（第一轮误取消），第二轮补勾回再点。
    """
    btn = page.locator(".login-container .code-button").first
    agree = page.locator(".login-container .agree-icon")
    for _ in range(2):
        if await agree.count():
            try:
                await agree.first.click(timeout=3000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
        try:
            await btn.click(timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(4000)
        try:
            txt = (await btn.inner_text()).strip()
        except Exception:
            txt = ""
        if "重新发送" in txt or "s)" in txt:
            return True
    return False


async def _sms_login() -> int:
    from playwright.async_api import async_playwright

    send_notice = _load_notify()
    missing = [v for v in REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        send_notice(f"**❌ 小红书短信登录**\n环境变量未设置: {', '.join(missing)}",
                    summary="小红书登录异常")
        return 2
    send_notice(
        f"**📱 小红书短信验证码登录**\n已向 {MASKED_PHONE} 发送验证码，"
        f"短信将转发到邮箱自动回填（{CODE_TIMEOUT}s 内）",
        summary="小红书短信登录",
    )

    async with async_playwright() as p:
        if os.environ.get("XHS_BROWSER") == "plain":
            from anti_detection import get_anti_detection
            _ad = get_anti_detection()
            browser = await p.chromium.launch(
                headless=True, args=_ad.get_playwright_launch_args())
        else:
            # 默认 cloak：源码级隐身指纹，统一走 cloakbrowser
            from cloakbrowser import launch_async
            browser = await launch_async()
        try:
            kw = {"locale": "zh-CN", "timezone_id": "Asia/Shanghai",
                  "viewport": {"width": 1366, "height": 768}}
            if os.environ.get("XHS_BROWSER") == "plain":
                kw["user_agent"] = _ad.get_random_ua()
            ctx = await browser.new_context(**kw)
            if os.environ.get("XHS_BROWSER") == "plain":
                for s in _ad.get_stealth_scripts():
                    await ctx.add_init_script(s)
            page = await ctx.new_page()
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)

            if not await _ensure_dialog(page):
                send_notice("**⚠️ 小红书短信登录**\n未检测到登录弹窗，页面结构可能变化",
                            summary="小红书登录异常")
                return 2

            # 填手机号并发送验证码
            phone_el = page.locator(".login-container input[placeholder*='手机号']")
            if not await phone_el.count():
                send_notice("**⚠️ 小红书短信登录**\n未找到手机号输入框，页面结构可能变化",
                            summary="小红书登录异常")
                return 2
            await phone_el.first.fill(PHONE)
            await page.wait_for_timeout(500)
            if not await _send_code(page):
                send_notice("**⚠️ 小红书短信登录**\n点击获取验证码无响应（可能风控）",
                            summary="小红书登录异常")
                return 2

            # 轮询邮箱收验证码
            code = await asyncio.to_thread(
                wait_for_code, timeout=CODE_TIMEOUT, interval=8)
            if not code:
                send_notice(
                    f"**⏳ 小红书短信登录超时**\n{CODE_TIMEOUT}s 内邮箱未收到验证码，"
                    "请检查手机短信转发是否正常，可稍后重试",
                    summary="小红书登录超时",
                )
                return 1

            # 填验证码并登录
            code_el = page.locator(".login-container input[placeholder*='验证码']")
            if not await code_el.count():
                send_notice("**⚠️ 小红书短信登录**\n未找到验证码输入框，页面结构可能变化",
                            summary="小红书登录异常")
                return 2
            await code_el.first.fill(code)
            await page.wait_for_timeout(500)
            submit = page.locator(".login-container .submit")
            if not await submit.count():
                submit = page.locator(".login-container >> text=登录")
            await submit.first.click()

            # 轮询登录态（localStorage 登录令牌 / 侧边栏「我」入口）
            logged_in = False
            for _ in range(15):
                token = await page.evaluate(
                    "() => localStorage.getItem('RWP_LOGIN_TOKEN')")
                me_el = await page.locator(".user.side-bar-component").count()
                if token or me_el:
                    logged_in = True
                    break
                # 验证码错误/风控时弹窗会显示 err-msg，提前暴露；
                # 元素缺失时 inner_text 默认等 30s 会拖死轮询，必须设短超时容错
                try:
                    err = await page.locator(
                        ".login-container .err-msg").first.inner_text(timeout=2000)
                except Exception:
                    err = ""
                if err.strip() and "同意" not in err:
                    send_notice(f"**⚠️ 小红书短信登录**\n{err.strip()}",
                                summary="小红书登录异常")
                    return 2
                await asyncio.sleep(2)

            if not logged_in:
                send_notice("**⚠️ 小红书短信登录**\n验证码已填但未登录成功，请手动处理",
                            summary="小红书登录异常")
                return 2

            # 登录成功：写回新 cookie（仅覆盖 XHS_COOKIE 字段）
            header = _cookies_to_header(
                await ctx.cookies("https://www.xiaohongshu.com/"))
            if "web_session=" not in header:
                send_notice("**⚠️ 小红书短信登录**\n登录后未获取到登录 cookie，请重试",
                            summary="小红书登录异常")
                return 2
            if update_env_cookie(header):
                send_notice(
                    "**✅ 小红书短信登录成功**\n登录态已更新，采集将自动使用新 cookie",
                    summary="小红书登录成功",
                )
                return 0
            send_notice("**⚠️ 小红书短信登录**\n登录成功但 cookie 写回 env 失败，请手动更新",
                        summary="小红书登录异常")
            return 2
        finally:
            await browser.close()


def main() -> int:
    try:
        return asyncio.run(_sms_login())
    except Exception as e:
        print(f"[xhs_sms_login] 异常: {e}", file=sys.stderr)
        try:
            send_notice = _load_notify()
            send_notice(f"**❌ 小红书短信登录异常**\n{e}", summary="小红书登录异常")
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
