"""
小红书数据采集器（rnote.dev API）
需要配置 RNODE_API_KEY 环境变量或直接填入
"""
import os
import requests
from datetime import datetime
from typing import List, Dict, Optional

# 配置: 在 https://rnote.dev/auth/register 注册后获取
# 设置环境变量 RNODE_API_KEY 或直接填入
API_KEY = os.environ.get("RNODE_API_KEY", "")
API_BASE = "https://rnote.dev/api/v2"
PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

SEARCH_KEYWORDS = {
    # 用小白的语言去搜，才能找到小白
    "nasdaq":     ["美股怎么买", "纳斯达克新手", "纳指还能买吗", "买美股"],
    "gold":       ["黄金怎么买", "买黄金亏了", "黄金新手", "黄金还能涨吗"],
    "cpo":        ["CPO是什么", "光模块还能涨吗", "通信ETF"],
    "semiconductor": ["芯片还能买吗", "半导体新手", "芯片ETF"],
}

def search_notes(keyword: str, count: int = 20) -> List[Dict]:
    """搜索小红书笔记"""
    if not API_KEY:
        print(f"  ⚠️ 未配置 RNODE_API_KEY，跳过小红书搜索: {keyword}")
        return []
    
    try:
        resp = requests.get(
            f"{API_BASE}/crawler/search/notes",
            params={"keyword": keyword, "count": count, "sort": "general"},
            headers={"X-API-Key": API_KEY, "User-Agent": "mom-index/1.0"},
            proxies=PROXY,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            # rnote.dev 响应结构: data.data.data.items[].note
            inner = data.get("data", {}).get("data", {})
            items = inner.get("items", [])
            notes = []
            for item in items:
                note = item.get("note") or item.get("note_card") or item
                if note and isinstance(note, dict):
                    notes.append(_parse_note(note))
            return notes
        else:
            print(f"  XHS API错误: {resp.status_code} {resp.text[:100]}")
            return []
    except Exception as e:
        print(f"  XHS 请求失败: {e}")
        return []

def get_note_detail(note_id: str) -> Optional[Dict]:
    """获取笔记详情（含评论）"""
    if not API_KEY:
        return None
    try:
        resp = requests.get(
            f"{API_BASE}/crawler/note/image",
            params={"note_id": note_id},
            headers={"X-API-Key": API_KEY},
            proxies=PROXY,
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
    except:
        pass
    return None

def _parse_note(raw: Dict) -> Dict:
    """标准化笔记格式 — 适配 rnote.dev API 返回结构"""
    user = raw.get("user") or raw.get("author") or {}
    interact = raw.get("interact_info") or raw.get("note_interact_info") or {}
    tags = raw.get("tag_list") or raw.get("tags") or []
    
    return {
        "id": raw.get("id") or raw.get("note_id", ""),
        "title": (raw.get("title") or raw.get("desc") or "")[:100],
        "content": raw.get("desc") or raw.get("content") or "",
        "platform": "xiaohongshu",
        "author": user.get("nickname") or user.get("nick_name", "未知"),
        "author_followers": user.get("follower_count", 0),
        "likes": interact.get("liked_count", 0),
        "comments_count": interact.get("comment_count", 0),
        "collected_at": datetime.now().isoformat(),
        "tags": [t.get("name", t) if isinstance(t, dict) else t for t in tags],
    }

def collect_all() -> Dict[str, List[Dict]]:
    """采集所有板块的小红书数据。无 API Key 或设置 MOM_INDEX_NO_XHS=1 时不采集（rnote 需付费，默认手动调用）。"""
    if API_KEY and not os.environ.get("MOM_INDEX_NO_XHS"):
        result = {}
        for sector_key, keywords in SEARCH_KEYWORDS.items():
            all_notes = []
            for kw in keywords:
                notes = search_notes(kw, count=5)
                all_notes.extend(notes)
            seen = set()
            unique = []
            for n in all_notes:
                if n["id"] not in seen:
                    seen.add(n["id"])
                    unique.append(n)
            result[sector_key] = unique
            print(f"  [小红书-{sector_key}] 采集到 {len(unique)} 条")
        return result
    else:
        print("  ⚠️ 未配置 RNODE_API_KEY，跳过小红书（如需真实数据请手动调用）")
        return {}

if __name__ == "__main__":
    if not API_KEY:
        print("请先设置 RNODE_API_KEY 环境变量")
        print("注册地址: https://rnote.dev/auth/register")
    else:
        data = collect_all()
        for k, v in data.items():
            print(f"{k}: {len(v)} posts")
