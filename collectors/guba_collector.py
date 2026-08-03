"""
东方财富股吧采集器 — 反检测升级版
支持板块: 纳斯达克ETF, 黄金ETF, 通信ETF(CPO), 半导体ETF
"""
import re
import html as html_mod
import requests
import time
from datetime import datetime, date
from typing import List, Dict, Optional

from .anti_detection import get_anti_detection

SECTORS = {
    "nasdaq":     {"name": "纳斯达克", "code": "of159941", "etf": "513100"},
    "gold":       {"name": "黄金",     "code": "of518880", "etf": "518880"},
    "cpo":        {"name": "CPO通信",  "code": "of515880", "etf": "515880"},
    "semiconductor": {"name": "半导体", "code": "of512480", "etf": "512480"},
}

PROXY = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

_ad = get_anti_detection()


def fetch_board(code: str) -> str:
    """获取股吧页面HTML — 使用反检测请求头"""
    url = f"https://guba.eastmoney.com/list,{code}.html"
    headers = _ad.get_common_headers(referer="https://guba.eastmoney.com")
    resp = requests.get(url, headers=headers, proxies=PROXY, timeout=15)
    resp.encoding = 'utf-8'
    return resp.text


def _fmt_date(s: str) -> str:
    """股吧日期（'MM-DD HH:MM' 或 'MM-DD'）补年份 → 'YYYY-MM-DD HH:MM'。"""
    s = s.strip()
    m = re.match(r'^(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}))?$', s)
    if not m:
        return s
    mm, dd = int(m.group(1)), int(m.group(2))
    now = datetime.now()
    try:
        cand = date(now.year, mm, dd)
    except ValueError:  # 无效日期（如 02-30）
        return s
    # 晚于今天超过 1 天 → 属去年（1 月初可见去年 12 月底帖子）；
    # 晚 1 天以内视为当年（跨午夜采集时帖子显示为次日凌晨）
    if (cand - date(now.year, now.month, now.day)).days > 1:
        cand = date(now.year - 1, mm, dd)
    time_part = f" {m.group(3)}:{m.group(4)}" if m.group(3) else ""
    return f"{cand.isoformat()}{time_part}"


def parse_posts(html_content: str) -> List[Dict]:
    """解析帖子列表（按行提取：div.articleh 内含 l1阅读/l2评论/l3标题/l4作者/l5日期）"""
    row_pattern = re.compile(r'<div class="articleh[^"]*">(.*?)</div>', re.DOTALL)
    title_pattern = re.compile(
        r'<a[^>]*href="(/news,[^"]*)"[^>]*title="([^"]*)"[^>]*>',
        re.DOTALL
    )
    read_pattern = re.compile(r'<span[^>]*class="[^"]*l1[^"]*"[^>]*>(.*?)</span>', re.DOTALL)
    reply_pattern = re.compile(r'<span[^>]*class="[^"]*l2[^"]*"[^>]*>(.*?)</span>', re.DOTALL)
    author_pattern = re.compile(r'<span[^>]*class="[^"]*l4[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a>', re.DOTALL)
    date_pattern = re.compile(r'<span[^>]*class="[^"]*l5[^"]*"[^>]*>(.*?)</span>', re.DOTALL)

    posts = []
    for row in row_pattern.findall(html_content):
        tm = title_pattern.search(row)
        if not tm:
            continue
        url, title = tm.groups()
        title = html_mod.unescape(title.strip())
        if not title or title == '点击开始搜索':
            continue
        rm = read_pattern.search(row)
        rym = reply_pattern.search(row)
        am = author_pattern.search(row)
        dm = date_pattern.search(row)
        author = re.sub(r'<[^>]+>', '', am.group(1)).strip() if am else "未知"
        posts.append({
            "id": f"guba_{url.split(',')[-1].replace('.html','')}",
            "title": title,
            "url": f"https://guba.eastmoney.com{url}",
            "platform": "guba",
            "author": author,
            "reads": rm.group(1).strip() if rm else "0",
            "replies": rym.group(1).strip() if rym else "0",
            "date": _fmt_date(dm.group(1)) if dm else "未知",
            "collected_at": datetime.now().isoformat(),
        })
    return posts


def collect_all() -> Dict[str, List[Dict]]:
    """采集所有板块 — 带人类延迟防触发风控"""
    result = {}
    for sector_key, cfg in SECTORS.items():
        try:
            html = fetch_board(cfg["code"])
            posts = parse_posts(html)
            result[sector_key] = posts
            print(f"  [{cfg['name']}] 采集到 {len(posts)} 条帖子")
            # 板块之间加延迟
            _ad.sleep_like_human("scroll")
        except Exception as e:
            print(f"  [{cfg['name']}] 采集失败: {e}")
            result[sector_key] = []
    return result


if __name__ == "__main__":
    data = collect_all()
    for k, v in data.items():
        print(f"{k}: {len(v)} posts")
