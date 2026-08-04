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
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote

try:
    from .xhs_cookie_check import get_cookie, parse_cookies, update_env_cookie, merge_cookies
    from .anti_detection import get_anti_detection
except ImportError:
    # 独立执行（python3 collectors/xhs_playwright.py）时无包上下文
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from xhs_cookie_check import get_cookie, parse_cookies, update_env_cookie, merge_cookies
    from anti_detection import get_anti_detection

_ad = get_anti_detection()

SEARCH_KEYWORDS = {
    "nasdaq":     ["美股怎么买", "纳斯达克新手", "纳指还能买吗"],
    "gold":       ["黄金怎么买", "黄金亏了", "黄金还能涨吗"],
    "cpo":        ["CPO是什么", "CPO还能买吗"],
    "semiconductor": ["芯片还能上车吗", "半导体新手"],
}

OUTPUT_FILE = Path(__file__).resolve().parent.parent / "data" / "xhs_posts.json"

# 搜索页拦截类型识别（2026-08-03 实测：正文"请求太频繁"=限流；
# 滑块/扫码为预估类型，遇真实验证再校准关键词）
BLOCK_PATTERNS = {
    "rate_limit": ["请求太频繁", "请稍后再试", "请一分钟"],
    "slider":     ["拖动滑块", "向右滑动", "请完成验证", "滑块"],
    "scan_qr":    ["扫码登录", "请扫码", "二维码验证", "扫码验证身份"],
    "risk_limit": ["安全限制", "账号异常", "300012"],
}


class XhsBlocked(Exception):
    """搜索被小红书拦截（限流/滑块/扫码/风控），带类型。"""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def _detect_block(html: str):
    """识别拦截类型，未拦截返回 None。"""
    for kind, kws in BLOCK_PATTERNS.items():
        if any(k in html for k in kws):
            return kind
    return None


QR_WAIT_TIMEOUT = 300  # 等待用户扫码上限 5 分钟
_QR_IMG = Path("/tmp/xhs_qr_scan.png")


def _crop_blank(path: str, pad: int = 12) -> None:
    """裁掉图片四周的纯空白（保留提示文字+二维码，边缘留少量白）。"""
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        # 找非白内容区域（>240 视为空白背景置 0，其余置 255）
        bbox = img.convert("L").point(lambda p: 0 if p > 240 else 255).getbbox()
        if bbox:
            l, t, r, b = bbox
            w, h = img.size
            img.crop((max(0, l - pad), max(0, t - pad),
                      min(w, r + pad), min(h, b + pad))).save(path)
    except Exception:
        pass  # 无 Pillow 或裁切失败时保留原图


async def _send_qr_to_feishu(page) -> bool:
    """全页截图、裁掉四边空白后发到飞书「瞎报错」群。"""
    try:
        await page.screenshot(path=str(_QR_IMG))
        _crop_blank(str(_QR_IMG))
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from notify_feishu import send_qr_image
        return send_qr_image(str(_QR_IMG))
    except Exception as e:
        print(f"  ⚠️ 发送二维码失败: {e}")
        return False


async def _wait_qr_scan(page, search_url: str, captured: list) -> bool:
    """发二维码到飞书等用户扫码，轮询直到验证通过（captured 有数据）或超时。"""
    import time as _time
    deadline = _time.time() + QR_WAIT_TIMEOUT
    while _time.time() < deadline:
        if await _send_qr_to_feishu(page):
            print("  📱 二维码已发飞书「瞎报错」群，等待扫码...")
        # 等待扫码结果（二维码 1 分钟有效，期间轮询 captured）
        for _ in range(12):
            if captured:
                return True
            await asyncio.sleep(5)
        # 超时未过：重新加载搜索页刷新二维码/验证状态
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass
    return False


def _safe_int(v) -> int:
    """API 返回的计数可能是字符串/None/异常值，统一安全转 int。"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


_REL_TIME_RE = re.compile(r'^(?:刚刚|昨天|(\d+)分钟前|(\d+)小时前|(\d+)天前)$')
_MMDD_RE = re.compile(r'^(\d{2})-(\d{2})$')
_FULL_DATE_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')


def parse_publish_time(text, now=None) -> str:
    """搜索卡片角标时间 → 'YYYY-MM-DD HH:MM' 绝对时间戳。

    真实格式（2026-08-04 探测 v2/search/notes）：corner_tag_info 的
    publish_time 文本为 "刚刚/N分钟前/N小时前/N天前/昨天"（近期）或
    "MM-DD"（今年）/"YYYY-MM-DD"（往年）。MM-DD 落在未来 → 属去年
    （与股吧 _fmt_date 边界一致）。无法解析返回空串。
    """
    if not text:
        return ""
    now = now or datetime.now()
    m = _REL_TIME_RE.match(str(text))
    if m:
        if str(text) == "昨天":
            dt = now - timedelta(days=1)
        else:
            minutes, hours, days = (int(x) if x else 0 for x in m.groups())
            dt = now - timedelta(minutes=minutes, hours=hours, days=days)
        return dt.strftime("%Y-%m-%d %H:%M")
    m = _MMDD_RE.match(str(text))
    if m:
        mm, dd = int(m.group(1)), int(m.group(2))
        try:
            cand = datetime(now.year, mm, dd)
        except ValueError:
            return ""  # 非法日期（如 02-30）
        # 晚于今天超过 1 天 → 属去年（1 月初可见去年底帖子）
        if (cand - now).days > 1:
            try:
                cand = datetime(now.year - 1, mm, dd)
            except ValueError:
                pass  # 去年非闰年且为 02-29，保持当年构造结果
        return cand.strftime("%Y-%m-%d %H:%M")
    m = _FULL_DATE_RE.match(str(text))
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return ""
    return ""


def _parse_note_card(item: Dict) -> Dict:
    """标准化 note_card → 与 xhs_collector._parse_note 兼容的格式。"""
    note = item.get("note_card") or item
    user = note.get("user") or {}
    interact = note.get("interact_info") or {}
    title = note.get("display_title") or ""
    note_id = item.get("id") or note.get("id", "")
    token = item.get("xsec_token") or ""
    # 带 xsec_token 的分享链接：未登录可访问；explore 直链会被风控拦截
    url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={token}&xsec_source=pc_search" if note_id and token else ""
    # 角标时间（如 "2小时前"/"01-21"/"2025-02-06"）→ 绝对时间戳；无角标（新帖）留空
    pub_text = ""
    for tag in (note.get("corner_tag_info") or []):
        if isinstance(tag, dict) and tag.get("type") == "publish_time":
            pub_text = tag.get("text") or ""
            break
    return {
        "id": note_id,
        "title": title[:100],
        "content": title,  # 搜索卡片无正文，标题兜底
        "platform": "xiaohongshu",
        "author": user.get("nickname") or user.get("nick_name", "未知"),
        "author_followers": 0,  # 搜索卡片不含粉丝数
        "likes": _safe_int(interact.get("liked_count")),
        "comments_count": _safe_int(interact.get("comment_count")),
        "url": url,
        "collected_at": datetime.now().isoformat(),
        "publish_time": pub_text,
        "published_at": parse_publish_time(pub_text),
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
        block = _detect_block(html)
        if block == "scan_qr":
            # 扫码验证：发二维码到飞书等用户扫码，通过后继续搜索
            print(f"    📱 XHS 扫码验证: {keyword}，等待用户扫码...")
            if await _wait_qr_scan(page, url, captured):
                print(f"    ✅ 扫码通过，继续搜索: {keyword}")
            else:
                print(f"    ⛔ XHS 扫码超时未通过: {keyword}")
                raise XhsBlocked("scan_qr")
        elif block:
            print(f"    ⛔ XHS {block} 拦截: {keyword}")
            raise XhsBlocked(block)
        elif "登录" in html and "手机号" in html:
            print(f"    ⚠️ XHS 需要登录: {keyword}")
        else:
            print(f"    ⚠️ 未捕获到搜索响应: {keyword}")

    if not captured:
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
    if os.environ.get("MOM_INDEX_NO_XHS"):
        print("  ⚠️ MOM_INDEX_NO_XHS 已设置，跳过小红书")
        return {}
    cookie = get_cookie()
    if not cookie:
        print("  ⚠️ 未配置 XHS_COOKIE，跳过小红书（见 docs/xhs-cookie.md）")
        return {}

    from playwright.async_api import async_playwright

    async def _new_context(browser):
        """建 context：cloak 模式用源码级隐身指纹（不覆盖 UA/stealth），普通模式伪装。"""
        kw = {"locale": "zh-CN", "timezone_id": "Asia/Shanghai",
              "viewport": {"width": 1366, "height": 768}}
        if os.environ.get("XHS_BROWSER") != "cloak":
            kw["user_agent"] = _ad.get_random_ua()
        ctx = await browser.new_context(**kw)
        if os.environ.get("XHS_BROWSER") != "cloak":
            for s in _ad.get_stealth_scripts():
                await ctx.add_init_script(s)
        return ctx

    async def _run():
        result: Dict[str, List[Dict]] = {}
        blocked_kind = None
        async with async_playwright() as p:
            if os.environ.get("XHS_BROWSER") == "cloak":
                from cloakbrowser import launch_async
                browser = await launch_async()
            else:
                browser = await p.chromium.launch(headless=True, args=_ad.get_playwright_launch_args())
            try:
                ctx = await _new_context(browser)
                await ctx.add_cookies(parse_cookies(cookie))

                blocked_kind = None
                for sector_key, keywords in SEARCH_KEYWORDS.items():
                    all_notes: List[Dict] = []
                    for kw in keywords:
                        page = await ctx.new_page()
                        try:
                            notes = await search_keyword(page, kw)
                            all_notes.extend(notes)
                            _ad.sleep_like_human("search")
                        except XhsBlocked as e:
                            # 被拦截立即停止全部搜索，不再盲等剩余关键词
                            blocked_kind = e.kind
                            break
                        finally:
                            await page.close()
                    if blocked_kind:
                        break
                    # 去重
                    seen = set()
                    unique = []
                    for n in all_notes:
                        if n["id"] and n["id"] not in seen:
                            seen.add(n["id"])
                            unique.append(n)
                    result[sector_key] = unique
                    print(f"  [小红书-{sector_key}] 采集到 {len(unique)} 条")

                # 采集完成：有数据才写回服务端最新 cookie（仅更新变化的字段）。
                # 0 条 = 风控/失效，写回会用无效状态污染真实 cookie，必须跳过。
                if sum(len(v) for v in result.values()) > 0:
                    try:
                        refreshed = merge_cookies(cookie, await ctx.cookies("https://www.xiaohongshu.com/"))
                        if refreshed != cookie and update_env_cookie(refreshed):
                            print("  ✅ 小红书 cookie 已自动续期写回（仅更新变化字段）")
                    except Exception as e:
                        print(f"  ⚠️ cookie 续期写回失败: {e}")
                return result, blocked_kind
            finally:
                await browser.close()

    result, blocked_kind = asyncio.run(_run())
    if sum(len(v) for v in result.values()) == 0:
        # 有 cookie 但全板块 0 条：风控/失效/页面结构变更，告警到飞书
        hint = {
            "rate_limit": "搜索被限流，请停止探测等待冷却（至少 10 分钟）后重试",
            "slider": "触发滑块验证，需人工处理或实现自动拖拽后重试",
            "scan_qr": "触发扫码验证，需真人扫码确认后重试",
            "risk_limit": "账号安全限制，建议更换 Cookie 后重试",
        }.get(blocked_kind, "Cookie 失效 / 页面结构变更 / 其他拦截")
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
            from notify_feishu import send_notice
            send_notice(
                f"**⚠️ 小红书采集失败**\n所有板块 0 条\n原因：{hint}",
                summary="小红书采集告警",
            )
        except Exception:
            pass
    return result


if __name__ == "__main__":
    data = collect_all()
    total = sum(len(v) for v in data.values())
    if total == 0:
        print("\n采集 0 条，不覆盖现有数据文件")
    else:
        os.makedirs(OUTPUT_FILE.parent, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n共采集 {total} 条 XHS 帖子 → {OUTPUT_FILE}")
