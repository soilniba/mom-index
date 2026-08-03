"""
小红书 Playwright 采集器 — 登录态版

从 ~/.config/mom-index/env 读取 XHS_COOKIE（含 web_session），Playwright
隐身访问搜索页，截获前端自己的 search/notes API 响应（签名由前端生成，
无需自行实现 x-s 签名）。采集完成后把服务端最新 cookie 写回 env 续期。

输出格式与 rnote 版 xhs_collector 兼容（pipeline 下游无感）。
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote

try:
    from .xhs_cookie_check import get_cookie, parse_cookies, update_env_cookie, _ctx_cookies
    from .anti_detection import get_anti_detection
except ImportError:
    # 独立执行（python3 collectors/xhs_playwright.py）时无包上下文
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from xhs_cookie_check import get_cookie, parse_cookies, update_env_cookie, _ctx_cookies
    from anti_detection import get_anti_detection

_ad = get_anti_detection()

SEARCH_KEYWORDS = {
    "nasdaq":     ["美股怎么买", "纳斯达克新手", "纳指还能买吗"],
    "gold":       ["黄金怎么买", "黄金亏了", "黄金还能涨吗"],
    "cpo":        ["CPO是什么", "CPO还能买吗"],
    "semiconductor": ["芯片还能上车吗", "半导体新手"],
}

OUTPUT_FILE = Path(__file__).resolve().parent.parent / "data" / "xhs_posts.json"


def _safe_int(v) -> int:
    """API 返回的计数可能是字符串/None/异常值，统一安全转 int。"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _parse_note_card(item: Dict) -> Dict:
    """标准化 note_card → 与 xhs_collector._parse_note 兼容的格式。"""
    note = item.get("note_card") or item
    user = note.get("user") or {}
    interact = note.get("interact_info") or {}
    title = note.get("display_title") or ""
    return {
        "id": item.get("id") or note.get("id", ""),
        "title": title[:100],
        "content": title,  # 搜索卡片无正文，标题兜底
        "platform": "xiaohongshu",
        "author": user.get("nickname") or user.get("nick_name", "未知"),
        "author_followers": 0,  # 搜索卡片不含粉丝数
        "likes": _safe_int(interact.get("liked_count")),
        "comments_count": _safe_int(interact.get("comment_count")),
        "collected_at": datetime.now().isoformat(),
        "tags": [],
    }


async def search_keyword(page, keyword: str, limit: int = 8) -> List[Dict]:
    """打开搜索页，截获 search/notes API 响应解析笔记。"""
    captured = []

    async def on_response(resp):
        if "search/notes" in resp.url:
            try:
                captured.append(json.loads(await resp.text()))
            except Exception:
                pass

    page.on("response", on_response)
    url = f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}&type=51"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # 等待 API 响应 + 渲染（最多 12s）
        for _ in range(12):
            if captured:
                break
            await asyncio.sleep(1)
    except Exception as e:
        print(f"    ❌ {keyword}: {e}")
        return []

    if not captured:
        html = await page.content()
        if "安全限制" in html or "300012" in html:
            print(f"    ⚠️ XHS 风控拦截: {keyword}")
        elif "登录" in html and "手机号" in html:
            print(f"    ⚠️ XHS 需要登录: {keyword}")
        else:
            print(f"    ⚠️ 未捕获到搜索响应: {keyword}")
        return []

    payload = captured[0] if isinstance(captured[0], dict) else {}
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    items = inner.get("items", []) or []
    posts = []
    for item in items:
        if isinstance(item, dict) and item.get("model_type") == "note":
            posts.append(_parse_note_card(item))
    print(f"    '{keyword}' → {len(posts)}条")
    return posts[:limit]


def collect_all() -> Dict[str, List[Dict]]:
    """采集所有板块 — 单个浏览器实例复用登录态，搜索间加人类延迟。"""
    cookie = get_cookie()
    if not cookie:
        print("  ⚠️ 未配置 XHS_COOKIE，跳过小红书（见 docs/xhs-cookie.md）")
        return {}

    from playwright.async_api import async_playwright

    async def _run() -> Dict[str, List[Dict]]:
        result: Dict[str, List[Dict]] = {}
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

                for sector_key, keywords in SEARCH_KEYWORDS.items():
                    all_notes: List[Dict] = []
                    for kw in keywords:
                        page = await ctx.new_page()
                        try:
                            notes = await search_keyword(page, kw)
                            all_notes.extend(notes)
                            _ad.sleep_like_human("search")
                        finally:
                            await page.close()
                    # 去重
                    seen = set()
                    unique = []
                    for n in all_notes:
                        if n["id"] and n["id"] not in seen:
                            seen.add(n["id"])
                            unique.append(n)
                    result[sector_key] = unique
                    print(f"  [小红书-{sector_key}] 采集到 {len(unique)} 条")

                # 采集完成：写回服务端最新 cookie（自动续期）
                try:
                    refreshed = await _ctx_cookies(ctx)
                    if refreshed and refreshed != cookie and update_env_cookie(refreshed):
                        print("  ✅ 小红书 cookie 已自动续期写回")
                except Exception as e:
                    print(f"  ⚠️ cookie 续期写回失败: {e}")
                return result
            finally:
                await browser.close()

    return asyncio.run(_run())


if __name__ == "__main__":
    data = collect_all()
    total = sum(len(v) for v in data.values())
    if total == 0:
        print("\n采集 0 条，不覆盖现有数据文件")
    else:
        os.makedirs(OUTPUT_FILE.parent, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n共采集 {total} 条 XHS 帖子 → {OUTPUT_FILE}")
