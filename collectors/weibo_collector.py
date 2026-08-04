"""
微博采集器 — TikHub API 版

从 ~/.config/mom-index/env 读取 TIKHUB_API_KEY（或环境变量），调用微博高级
搜索 fetch_advanced_search（$0.001/次，无折扣无免费额度，10/s 限流）：

    /api/v1/weibo/web_v2/fetch_advanced_search
    ?q=关键词&search_type=all&timescope=custom:当天0点:当天23点

timescope 限定当天（定时任务 23:30 跑，覆盖全天帖子），publish_time 形如
"今天07:32"/"08月03日 11:46"，解析为绝对时间戳。输出格式与 xhs 采集器
兼容（pipeline 下游无感）。返回完整正文（远超股吧/小红书搜索卡片的标题）。
"""
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import requests

ENV_FILE = Path("~/.config/mom-index/env").expanduser()
API_KEY_ENV = "TIKHUB_API_KEY"
API_BASE = "https://api.tikhub.io/api/v1/weibo/web_v2"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

SEARCH_KEYWORDS = {
    # 与 xhs/guba 一致的板块关键词（用小白的语言去搜，才能找到小白）
    "nasdaq":       ["美股怎么买", "纳斯达克新手", "纳指还能买吗"],
    "gold":         ["黄金怎么买", "黄金亏了", "黄金还能涨吗"],
    "cpo":          ["CPO是什么", "CPO还能买吗"],
    "semiconductor": ["芯片还能上车吗", "半导体新手"],
}

# "今天07:32" / "08月03日 11:46"（近几天）；"58分钟前"（近期）；
# "2026年08月04日 07:32" 等兜底
_REL_RE = re.compile(r'^(\d+)分钟前$|^(\d+)小时前$|^刚刚$|^昨天$')
_TODAY_RE = re.compile(r'^今天\s*(\d{1,2}):(\d{2})$')
_MD_RE = re.compile(r'^(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})$')
_FULL_RE = re.compile(r'^(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})$')


def parse_publish_time(text, now=None) -> str:
    """微博发布展示时间 → 'YYYY-MM-DD HH:MM' 绝对时间戳。

    真实格式（2026-08-04 实测 fetch_advanced_search）：
    "58分钟前"/"2小时前"（近期）、"今天07:32"（当天）、
    "08月03日 11:46"（今年内前几天）。MM-DD 落在未来 → 属去年
    （与股吧 _fmt_date 边界一致）。无法解析返回空串。
    """
    if not text:
        return ""
    now = now or datetime.now()
    s = str(text).strip()
    m = _REL_RE.match(s)
    if m:
        if s == "昨天":
            dt = now - timedelta(days=1)
        elif s == "刚刚":
            dt = now
        elif m.group(1):
            dt = now - timedelta(minutes=int(m.group(1)))
        else:
            dt = now - timedelta(hours=int(m.group(2)))
        return dt.strftime("%Y-%m-%d %H:%M")
    m = _TODAY_RE.match(s)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2))).strftime("%Y-%m-%d %H:%M")
    m = _MD_RE.match(s)
    if m:
        mm, dd = int(m.group(1)), int(m.group(2))
        try:
            cand = datetime(now.year, mm, dd, int(m.group(3)), int(m.group(4)))
        except ValueError:
            return ""
        if (cand - now).days > 1:  # 未来超 1 天 → 属去年
            try:
                cand = datetime(now.year - 1, mm, dd, int(m.group(3)), int(m.group(4)))
            except ValueError:
                pass
        return cand.strftime("%Y-%m-%d %H:%M")
    m = _FULL_RE.match(s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4)), int(m.group(5))).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return ""
    return ""


def get_api_key() -> str:
    """优先环境变量，其次 source env 文件（同 xhs_cookie_check.get_cookie）。"""
    key = os.environ.get(API_KEY_ENV, "")
    if key:
        return key
    r = subprocess.run(
        ["bash", "-c", f"source {ENV_FILE} 2>/dev/null; printf '%s' \"${API_KEY_ENV}\""],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def _today_timescope() -> str:
    """当天时间范围 custom:YYYY-MM-DD-0:YYYY-MM-DD-23"""
    d = datetime.now().strftime("%Y-%m-%d")
    return f"custom:{d}-0:{d}-23"


def search_keyword(keyword: str, api_key: str) -> List[Dict]:
    """高级搜索关键词 → 标准化 post 列表（失败返回 []）。"""
    try:
        resp = requests.get(
            f"{API_BASE}/fetch_advanced_search",
            params={
                "q": keyword,
                "search_type": "all",
                "timescope": _today_timescope(),
                "page": 1,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            proxies=PROXY,
            timeout=30,
        )
    except Exception as e:
        print(f"    ❌ {keyword}: 请求失败 {e}")
        return []
    if resp.status_code != 200:
        print(f"    ❌ {keyword}: HTTP {resp.status_code} {resp.text[:120]}")
        return []
    try:
        data = resp.json()
    except ValueError:
        print(f"    ❌ {keyword}: 响应非 JSON")
        return []
    if data.get("code") != 200:
        print(f"    ❌ {keyword}: {data.get('msg') or data.get('message')}")
        return []
    results = ((data.get("data") or {}).get("parsed_data") or {}).get("results") or []
    return [_parse_weibo(r) for r in results if isinstance(r, dict)]


def _parse_weibo(r: Dict) -> Dict:
    """高级搜索结果 → 与 xhs 采集器兼容的 post dict。"""
    interaction = r.get("interaction") or {}
    url = r.get("post_url") or ""
    if url.startswith("//"):
        url = "https:" + url
    pub_text = r.get("publish_time") or ""
    return {
        "id": str(r.get("weibo_id") or ""),
        "title": (r.get("content") or "")[:100],
        "content": r.get("content") or "",
        "platform": "weibo",
        "author": r.get("user_name") or r.get("user_nick", "未知"),
        "author_followers": 0,  # 高级搜索不返回粉丝数
        "likes": int(interaction.get("like_count") or 0),
        "comments_count": int(interaction.get("comment_count") or 0),
        "url": url,
        "collected_at": datetime.now().isoformat(),
        "publish_time": pub_text,
        "published_at": parse_publish_time(pub_text),
        "tags": [],
    }


def collect_all() -> Dict[str, List[Dict]]:
    """采集所有板块的微博。无 TIKHUB_API_KEY 时跳过（不产出数据）。"""
    api_key = get_api_key()
    if not api_key:
        print("  ⚠️ 未配置 TIKHUB_API_KEY，跳过微博（见 docs/weibo.md）")
        return {}
    result: Dict[str, List[Dict]] = {}
    for sector_key, keywords in SEARCH_KEYWORDS.items():
        posts: List[Dict] = []
        for kw in keywords:
            posts.extend(search_keyword(kw, api_key))
            time.sleep(0.3)  # 10/s 限流远够，微延迟防 429
        seen = set()
        unique = []
        for p in posts:
            if p["id"] and p["id"] not in seen:
                seen.add(p["id"])
                unique.append(p)
        result[sector_key] = unique
        print(f"  [微博-{sector_key}] 采集到 {len(unique)} 条")
    return result


if __name__ == "__main__":
    data = collect_all()
    total = sum(len(v) for v in data.values())
    print(f"\n共采集 {total} 条微博")
    for k, v in data.items():
        for p in v[:3]:
            print(f"  [{k}] {p['published_at']} {p['author']}: {p['title'][:40]}")
