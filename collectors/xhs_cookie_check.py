#!/usr/bin/env python3
"""
xhs_cookie_check.py — 小红书 Cookie 有效性验证 + 自动续期

从 ~/.config/mom-index/env 读取 XHS_COOKIE（或环境变量），用 Playwright
带 cookie 访问小红书首页验证登录态；验证通过后把服务端最新下发的
cookie（含 HttpOnly 的 web_session，Playwright 可读到）序列化写回 env，
实现"刷新页面时 cookie-set 随时更新"的自动续期。

退出码：0=有效（写回失败仅告警，不影响结论）, 1=未设置, 2=无效/风控, 3=验证异常
stderr：简明原因
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

ENV_FILE = Path("~/.config/mom-index/env").expanduser()
VAR_NAME = "XHS_COOKIE"


def _notify(reason: str) -> None:
    """发 cookie 告警到飞书（通知失败静默，不影响退出码）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from notify_feishu import send_notice
        send_notice(f"**⚠️ 小红书 Cookie 问题**\n{reason}", summary="小红书 Cookie 告警")
    except Exception:
        pass


def get_cookie() -> str:
    """优先环境变量，其次 source env 文件。"""
    cookie = os.environ.get(VAR_NAME, "")
    if cookie:
        return cookie
    r = subprocess.run(
        ["bash", "-c", f"source {ENV_FILE} 2>/dev/null; printf '%s' \"${VAR_NAME}\""],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def parse_cookies(cookie_str: str) -> list[dict]:
    """header string（k=v; k2=v2）→ Playwright cookie 列表。"""
    cookies = []
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": ".xiaohongshu.com",
            "path": "/",
        })
    return cookies


def merge_cookies(original: str, ctx_cookies: list[dict]) -> str:
    """仅更新服务端变更的字段：保留原字段，覆盖变化项，追加新增项。

    不做全量覆盖——context 里注入后未变的字段保持原值，
    避免把页面新产生的指纹 cookie 混入 env 破坏与浏览器的一致性。
    """
    order: list[str] = []
    old: dict[str, str] = {}
    for pair in original.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        k = k.strip()
        if k not in old:
            order.append(k)
        old[k] = v.strip()
    new = {c["name"]: c["value"] for c in ctx_cookies}
    parts = []
    for k in order:
        parts.append(f"{k}={new.get(k, old[k])}")
    for k in new:
        if k not in old:
            parts.append(f"{k}={new[k]}")
    return "; ".join(parts)


def update_env_cookie(new_cookie: str) -> bool:
    """原子替换 env 文件中 XHS_COOKIE 值。"""
    if "'" in new_cookie:
        print("[xhs_check] cookie 含单引号，无法写入", file=sys.stderr)
        return False
    tmp = ENV_FILE.with_suffix(".env.tmp")
    try:
        lines = ENV_FILE.read_text().splitlines()
        replaced = False
        out = []
        for line in lines:
            if line.startswith(f"export {VAR_NAME}="):
                out.append(f"export {VAR_NAME}='{new_cookie}'")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            out.append(f"export {VAR_NAME}='{new_cookie}'")
        tmp.write_text("\n".join(out) + "\n")
        tmp.replace(ENV_FILE)
        return True
    except Exception as e:
        print(f"[xhs_check] 写入 env 失败: {e}", file=sys.stderr)
        return False


async def verify(cookie: str) -> tuple[bool, str, str | None]:
    """验证登录态。返回 (有效, 原因, 最新cookie或None)。"""
    from playwright.async_api import async_playwright
    try:
        from .anti_detection import get_anti_detection
    except ImportError:
        # 独立执行（cookie_server 直接跑本文件）时无包上下文
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from anti_detection import get_anti_detection

    _ad = get_anti_detection()
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
            await ctx.add_cookies(parse_cookies(cookie))

            page = await ctx.new_page()
            resp = await page.goto("https://www.xiaohongshu.com/",
                                   wait_until="domcontentloaded", timeout=30000)
            if resp is None or resp.status >= 400:
                return False, f"首页请求失败（HTTP {resp.status if resp else '无响应'}）", None
            await page.wait_for_timeout(6000)

            html = await page.content()
            if "安全限制" in html or "300012" in html:
                return False, "小红书风控拦截", None

            # 登录态判定：localStorage 登录令牌 或 侧边栏"我"入口
            token = await page.evaluate("() => localStorage.getItem('RWP_LOGIN_TOKEN')")
            me_el = await page.locator(".user.side-bar-component").count()
            if not token and me_el == 0:
                return False, "cookie 无登录态（未登录或已失效）", None

            refreshed = merge_cookies(cookie, await ctx.cookies("https://www.xiaohongshu.com/"))
            return True, "登录态有效", refreshed
        finally:
            await browser.close()


def main() -> int:
    cookie = get_cookie()
    if not cookie:
        print(f"[xhs_check] 未设置 {VAR_NAME}", file=sys.stderr)
        _notify(f"未设置 {VAR_NAME}，请更新 {ENV_FILE}")
        return 1

    try:
        ok, reason, refreshed = asyncio.run(verify(cookie))
    except Exception as e:
        print(f"[xhs_check] 验证异常: {e}", file=sys.stderr)
        _notify(f"验证异常: {e}")
        return 3

    if not ok:
        print(f"[xhs_check] {reason}", file=sys.stderr)
        _notify(f"{reason}\n请重新导出 cookie 写入 {ENV_FILE}")
        return 2

    # 验证通过：写回最新 cookie（服务端可能已更新/续期）
    if refreshed and refreshed != cookie:
        if update_env_cookie(refreshed):
            print(f"[xhs_check] {reason}，cookie 已续期写回 {ENV_FILE}", file=sys.stderr)
        else:
            print(f"[xhs_check] {reason}，但写回 env 失败", file=sys.stderr)
    else:
        print(f"[xhs_check] {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
