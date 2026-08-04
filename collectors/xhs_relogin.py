#!/usr/bin/env python3
"""
xhs_relogin.py — 小红书扫码重新登录，二维码发到飞书「瞎报错」群

feishu-bot 的 xhs_relogin 工具调用入口。无 cookie 上下文打开小红书，
出现登录二维码后截图发到飞书等用户扫码；扫码成功（localStorage 出现
登录令牌）后把新 cookie 写回 ~/.config/mom-index/env 的 XHS_COOKIE。

成功前不触碰 env 中旧 cookie，只有扫码成功才写回。

退出码：0=扫码成功（cookie 已写回）, 1=超时未扫码, 2=异常
"""

import asyncio
import sys
from pathlib import Path

try:
    from .xhs_cookie_check import update_env_cookie
    from .xhs_playwright import QR_WAIT_TIMEOUT, _QR_IMG, _crop_blank
    from .anti_detection import get_anti_detection
except ImportError:
    # 独立执行（python3 collectors/xhs_relogin.py）时无包上下文
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from xhs_cookie_check import update_env_cookie
    from xhs_playwright import QR_WAIT_TIMEOUT, _QR_IMG, _crop_blank
    from anti_detection import get_anti_detection

_ad = get_anti_detection()

HOME_URL = "https://www.xiaohongshu.com/"
# 登录弹窗关键词（与采集器 scan_qr 判定一致）
QR_TEXT_KEYWORDS = ("扫码登录", "请扫码", "二维码验证", "扫码验证身份")
# 登录按钮候选选择器（首页未自动弹窗时依次尝试）
LOGIN_BTN_SELECTORS = (
    ".login-btn", "[data-testid='login-button']", "span:has-text('登录')",
)
REFRESH_INTERVAL = 60  # 二维码 1 分钟有效，到点重载刷新并重发
POLL_INTERVAL = 5


def _load_notify():
    """延迟导入 notify_feishu（独立执行时需把 scripts 目录加进 path）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from notify_feishu import send_notice, send_qr_image
    return send_notice, send_qr_image


def _cookies_to_header(ctx_cookies: list) -> str:
    return "; ".join(f"{c['name']}={c['value']}" for c in ctx_cookies)


async def _relogin() -> int:
    import time as _time

    from playwright.async_api import async_playwright

    send_notice, send_qr_image = _load_notify()
    send_notice(
        "**📱 小红书重新登录**\\n已开始登录流程，二维码将发送到本群，请用手机扫码（5 分钟内有效，过期可再喊我重发）",
        summary="小红书重新登录",
    )

    logged_in = False
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=_ad.get_playwright_launch_args())
        try:
            ctx = await browser.new_context(
                locale="zh-CN", timezone_id="Asia/Shanghai",
                viewport={"width": 1366, "height": 768},
                user_agent=_ad.get_random_ua(),
            )
            for s in _ad.get_stealth_scripts():
                await ctx.add_init_script(s)
            page = await ctx.new_page()
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)

            deadline = _time.time() + QR_WAIT_TIMEOUT
            while _time.time() < deadline:
                # 登录弹窗未出现则尝试点击登录按钮
                await page.wait_for_timeout(3000)
                html = await page.content()
                if not any(kw in html for kw in QR_TEXT_KEYWORDS):
                    clicked = False
                    for sel in LOGIN_BTN_SELECTORS:
                        if await page.locator(sel).count():
                            try:
                                await page.locator(sel).first.click()
                                clicked = True
                                break
                            except Exception:
                                continue
                    if not clicked:
                        send_notice(
                            "**⚠️ 小红书重新登录**\\n未检测到登录二维码，页面结构可能变化，请手动处理",
                            summary="小红书登录异常",
                        )
                        return 2

                await page.wait_for_timeout(2000)
                await page.screenshot(path=str(_QR_IMG))
                _crop_blank(str(_QR_IMG))
                if not send_qr_image(str(_QR_IMG)):
                    send_notice("**⚠️ 小红书重新登录**\\n二维码发送失败", summary="小红书登录异常")

                # 轮询登录态（localStorage 登录令牌 / 侧边栏「我」入口）
                for _ in range(REFRESH_INTERVAL // POLL_INTERVAL):
                    token = await page.evaluate(
                        "() => localStorage.getItem('RWP_LOGIN_TOKEN')")
                    me_el = await page.locator(".user.side-bar-component").count()
                    if token or me_el:
                        logged_in = True
                        break
                    await asyncio.sleep(POLL_INTERVAL)
                if logged_in:
                    break
                # 刷新二维码：重载页面，下一轮重发截图
                try:
                    await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=20000)
                except Exception:
                    pass

            if not logged_in:
                send_notice(
                    "**⏳ 小红书重新登录超时**\\n未在 5 分钟内完成扫码，可再喊 bot 重发",
                    summary="小红书登录超时",
                )
                return 1

            # 扫码成功：写回新 cookie（仅覆盖 XHS_COOKIE 字段）
            header = _cookies_to_header(
                await ctx.cookies("https://www.xiaohongshu.com/"))
            if "web_session=" not in header:
                send_notice(
                    "**⚠️ 小红书重新登录**\\n扫码后未获取到登录 cookie，请重试",
                    summary="小红书登录异常",
                )
                return 2
            if update_env_cookie(header):
                send_notice(
                    "**✅ 小红书登录成功**\\n登录态已更新，采集将自动使用新 cookie",
                    summary="小红书登录成功",
                )
                return 0
            send_notice(
                "**⚠️ 小红书重新登录**\\n扫码成功但 cookie 写回 env 失败，请手动更新",
                summary="小红书登录异常",
            )
            return 2
        finally:
            await browser.close()


def main() -> int:
    try:
        return asyncio.run(_relogin())
    except Exception as e:
        print(f"[xhs_relogin] 异常: {e}", file=sys.stderr)
        try:
            send_notice, _ = _load_notify()
            send_notice(f"**❌ 小红书重新登录异常**\\n{e}", summary="小红书登录异常")
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
