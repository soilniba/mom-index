#!/usr/bin/env python3
"""
notify_daily.py — 每日把最新宝妈指数推送到飞书「宝妈指数」群

读取 data/dashboard_data.json，生成 4 张板块卡片 + 1 张"今日最小白帖"卡片，
通过本机 feishu-bot relay API（127.0.0.1:8410）发送 markdown 卡片。
token 从环境变量 FEISHU_BOT_TOKEN 或 ~/.config/mom-index/env 读取。

用法：
  python3 scripts/notify_daily.py [data_dir]
  或 import send_daily_report(dashboard_path)

发送失败不抛异常（静默 stderr），不中断采集主流程。
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

RELAY_URL = "http://127.0.0.1:8410/relay/send/markdown"
MOM_CHAT_ID = "oc_47ca0e5ecb7d20cf524ba7e9899023c1"  # 宝妈指数群
ENV_FILE = Path("~/.config/mom-index/env").expanduser()
TOKEN_VAR = "FEISHU_BOT_TOKEN"

SECTOR_EMOJI = {"nasdaq": "📈", "gold": "🥇", "cpo": "🔌", "semiconductor": "💾"}
SECTOR_NAMES = {"nasdaq": "纳斯达克", "gold": "黄金", "cpo": "CPO通信", "semiconductor": "半导体"}


def get_token() -> str:
    """优先环境变量，其次 source env 文件。"""
    token = os.environ.get(TOKEN_VAR, "")
    if token:
        return token
    r = subprocess.run(
        ["bash", "-c", f"source {ENV_FILE} 2>/dev/null; printf '%s' \"${TOKEN_VAR}\""],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def _send_markdown(markdown: str, summary: str) -> bool:
    """发送一张 markdown 卡片到宝妈指数群。"""
    body = json.dumps({
        "chat_id": MOM_CHAT_ID,
        "markdown": markdown,
        "summary": summary,
        "source": "mom-index",
    }).encode()
    headers = {"Content-Type": "application/json"}
    token = get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(RELAY_URL, data=body, method="POST", headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            ok = resp.status == 200
            if not ok:
                print(f"[notify_daily] relay 返回 HTTP {resp.status}", file=sys.stderr)
            return ok
    except Exception as e:
        print(f"[notify_daily] 发送失败: {e}", file=sys.stderr)
        return False


def _buy_sell_label(ratio: float) -> str:
    """买卖比状态标签，与网页一致：>1.5 追涨 / >0.8 平衡 / 否则恐慌。"""
    if ratio > 1.5:
        return "🔥追涨"
    if ratio > 0.8:
        return "⚖️平衡"
    return "🩸恐慌"


def _sector_card(name: str, emoji: str, sector: dict) -> str:
    """板块卡片：指数、解析、买入卖出、买卖比、小白帖（模仿网页卡片）。"""
    d = sector.get("details", {})
    index = sector.get("index", 0)
    buy_count = d.get("buy_count", 0) or 0
    sell_count = d.get("sell_count", 0) or 0
    if buy_count + sell_count == 0:
        ratio_str = "— 无买卖信号"
    else:
        ratio = d.get("buy_sell_ratio", 0) or 0
        ratio_str = f"**{ratio} : 1** {_buy_sell_label(ratio)}（买入{buy_count} / 卖出{sell_count}）"
    valid = d.get("valid_posts", d.get("total_posts", 0))
    return (
        f"**{emoji} {name} · 宝妈指数 {index}**\n\n"
        f"{sector.get('interpretation', '')}\n\n"
        f"🟢 宝妈买入 **{d.get('mom_buy_index', 0) or 0}**"
        f"  |  🔴 宝妈卖出 **{d.get('mom_sell_index', 0) or 0}**\n"
        f"买卖比 {ratio_str}\n"
        f"小白帖 **{d.get('newbie_posts', 0)} / {valid} ({d.get('newbie_ratio', 0)}%)**"
    )


def _post_line(i: int, p: dict) -> str:
    """单条小白帖：badge、板块、意图、标题、解析、信号、原帖链接。"""
    badge = "纯小白" if (p.get("score") or 0) >= 50 else "偏小白"
    lines = [
        f"{i}. [{badge} {p.get('score') or 0:.0f}分] [{p.get('sector', '')}] "
        f"{p.get('intent_label', '')} {p.get('title', '')}"
    ]
    if p.get("reasoning"):
        lines.append(f"   📝 {p['reasoning']}")
    signals = p.get("key_signals") or []
    if signals:
        lines.append(f"   ▸ {' · '.join(signals)}")
    url = p.get("url", "")
    if url:
        lines.append(f"   🔗 [查看原帖]({url})")
    return "\n".join(lines)


def _top_card(posts: list) -> str:
    """今日最小白帖卡片，模仿网页 top-posts 区块。"""
    body = ["**🔥 今日最\"小白\"的帖子**", ""]
    body.extend(_post_line(i + 1, p) for i, p in enumerate(posts))
    return "\n".join(body)


def send_daily_report(dashboard_path: str) -> bool:
    """读取 dashboard 数据，发送 4 张板块卡片 + 1 张最小白帖卡片。"""
    with open(dashboard_path, encoding="utf-8") as f:
        dashboard = json.load(f)
    sectors = (dashboard.get("latest") or {}).get("sectors") or {}
    if not sectors:
        print("[notify_daily] dashboard 无数据", file=sys.stderr)
        return False

    ok = True
    for key in ("nasdaq", "gold", "cpo", "semiconductor"):
        sector = sectors.get(key)
        if not sector:
            continue
        name = SECTOR_NAMES.get(key, key)
        ok = _send_markdown(
            _sector_card(name, SECTOR_EMOJI.get(key, "📊"), sector),
            f"{name} 宝妈指数 {sector.get('index', '')}",
        ) and ok

    # 跨板块合并 top 小白帖，按分数降序取前 8（与网页一致）
    all_top = []
    for key, sector in sectors.items():
        for p in sector.get("top_newbie_posts") or []:
            all_top.append({**p, "sector": SECTOR_NAMES.get(key, key)})
    all_top.sort(key=lambda x: x.get("score") or 0, reverse=True)
    top8 = all_top[:8]
    if top8:
        ok = _send_markdown(_top_card(top8), "今日最小白帖") and ok
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="发送每日宝妈指数到飞书宝妈指数群")
    parser.add_argument("data_dir", nargs="?", default="data",
                        help="dashboard_data.json 所在目录（默认 data）")
    args = parser.parse_args()
    dashboard_path = os.path.join(args.data_dir, "dashboard_data.json")
    if not os.path.exists(dashboard_path):
        print(f"[notify_daily] 找不到 {dashboard_path}", file=sys.stderr)
        return 1
    return 0 if send_daily_report(dashboard_path) else 1


if __name__ == "__main__":
    sys.exit(main())
