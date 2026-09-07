#!/usr/bin/env python3
"""
notify_daily.py — 每日把最新宝妈指数推送到飞书「宝妈指数」群

读取 data/dashboard_data.json，生成 4 张板块卡片（内嵌近 30 天指数曲线图）
+ 1 张"今日最小白帖"卡片，通过本机 feishu-bot relay API（127.0.0.1:8410）
发送 markdown 卡片。token 从环境变量 FEISHU_BOT_TOKEN 或 ~/.config/mom-index/env 读取。

用法：
  python3 scripts/notify_daily.py [data_dir]
  或 import send_daily_report(dashboard_path)

发送失败不抛异常（静默 stderr），不中断采集主流程。
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont

RELAY_URL = "http://127.0.0.1:8410/relay/send/markdown"
UPLOAD_URL = "http://127.0.0.1:8410/relay/upload/image"
MOM_CHAT_ID = "oc_47ca0e5ecb7d20cf524ba7e9899023c1"  # 宝妈指数群
ENV_FILE = Path("~/.config/mom-index/env").expanduser()
TOKEN_VAR = "FEISHU_BOT_TOKEN"

SECTOR_EMOJI = {"nasdaq": "📈", "gold": "🥇", "cpo": "🔌", "semiconductor": "💾"}
SECTOR_NAMES = {"nasdaq": "纳斯达克", "gold": "黄金", "cpo": "CPO通信", "semiconductor": "半导体"}
SOURCE_NAMES = {"xiaohongshu": "小红书", "weibo": "微博", "guba": "股吧"}

# 曲线图样式：与 frontend/dashboard.html 图表一致的暗色主题
CHART_DAYS = 30                 # 取最近 N 天；不足时用全部历史
CHART_SIZE = (1200, 800)        # 3:2，适配飞书卡片图片显示
CHART_BG = "#0f172a"
CHART_TEXT = "#e2e8f0"
CHART_TICK = "#64748b"
CHART_GRID = (51, 65, 85, 60)   # #334155 半透明
SECTOR_COLORS = {"nasdaq": "#06b6d4", "gold": "#fbbf24",
                 "cpo": "#a78bfa", "semiconductor": "#34d399"}
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_INDEX_SC = 2               # Noto Sans CJK SC（见 getname 遍历确认）


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


def _send_markdown(markdown: str, summary: str,
                   image_key: str = "", image_position: str = "") -> bool:
    """发送一张 markdown 卡片到宝妈指数群。image_key 非空时图片内嵌卡片标题下方。"""
    body = json.dumps({
        "chat_id": MOM_CHAT_ID,
        "markdown": markdown,
        "summary": summary,
        "source": "mom-index",
        "image_key": image_key,
        "image_position": image_position,
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


def _chart_window(records: list) -> list:
    """取最近 CHART_DAYS 天；不足则用全部历史。"""
    if len(records) > CHART_DAYS:
        return records[-CHART_DAYS:]
    return records


def _chart_png(records: list, color: str) -> bytes:
    """绘制单板块宝妈指数曲线图 PNG（暗色 0-100 轴，与网页 dashboard 一致）。"""
    records = _chart_window(records)
    if not records:
        return b""
    w, h = CHART_SIZE
    img = Image.new("RGBA", CHART_SIZE, CHART_BG)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, 22, index=FONT_INDEX_SC)
    font_sm = ImageFont.truetype(FONT_PATH, 20, index=FONT_INDEX_SC)

    left, right, top, bottom = 70, 30, 40, 60
    pw, ph = w - left - right, h - top - bottom
    n = len(records)
    x_at = lambda i: left + (i * pw / (n - 1) if n > 1 else pw / 2)
    y_at = lambda v: top + ph - (v / 100 * ph)

    # 横向网格 + 纵轴刻度（0-100，每 20 一格）
    for v in range(0, 101, 20):
        yy = y_at(v)
        draw.line([(left, yy), (w - right, yy)], fill=CHART_GRID, width=1)
        draw.text((left - 10, yy), str(v), font=font, fill=CHART_TICK, anchor="rm")

    # 纵向网格 + 日期刻度（最多约 6 条）
    step = max(1, -(-n // 6))  # ceil
    for i in range(0, n, step):
        xx = x_at(i)
        draw.line([(xx, top), (xx, h - bottom)], fill=CHART_GRID, width=1)
        draw.text((xx, h - bottom + 10), (records[i].get("date") or "")[5:],
                  font=font_sm, fill=CHART_TICK, anchor="mt")

    # 曲线 + 半透明区域填充 + 首末端点
    pts = [(x_at(i), y_at(float(r.get("index", 0)))) for i, r in enumerate(records)]
    rgb = tuple(int(color.lstrip("#")[j:j + 2], 16) for j in (0, 2, 4))
    draw.polygon(pts + [(pts[-1][0], top + ph), (pts[0][0], top + ph)], fill=(*rgb, 36))
    draw.line(pts, fill=color, width=3, joint="curve")
    for px, py in (pts[0], pts[-1]):
        draw.ellipse([px - 5, py - 5, px + 5, py + 5], fill=color)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_image(file_path: str) -> str:
    """上传图片到飞书，返回 image_key；失败返回 ""（不抛异常）。"""
    body = json.dumps({"file_path": file_path}).encode()
    headers = {"Content-Type": "application/json"}
    token = get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = Request(UPLOAD_URL, data=body, method="POST", headers=headers)
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("image_key", "") if data.get("ok") else ""
    except Exception as e:
        print(f"[notify_daily] 图片上传失败: {e}", file=sys.stderr)
        return ""


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
        ratio_str = f"**{ratio:g} : 1** {_buy_sell_label(ratio)}（买入{buy_count} / 卖出{sell_count}）"
    valid = d.get("valid_posts", d.get("total_posts", 0))
    return (
        f"**{emoji} {name} · 宝妈指数**\n\n"
        f"# {index}\n\n"
        f"{sector.get('interpretation', '')}\n\n"
        f"宝妈买入 **{d.get('mom_buy_index', 0) or 0}**"
        f"  |  宝妈卖出 **{d.get('mom_sell_index', 0) or 0}**\n"
        f"买卖比 {ratio_str}\n"
        f"小白帖 **{d.get('newbie_posts', 0)} / {valid} ({d.get('newbie_ratio', 0)}%)**"
    )


def _post_line(i: int, p: dict) -> str:
    """单条小白帖：🔗原帖链接、badge、板块、意图、标题和时间。"""
    badge = "纯小白" if (p.get("score") or 0) >= 50 else "偏小白"
    url = p.get("url", "")
    link = f"[🔗]({url}) " if url else ""
    src = p.get("source")
    src_tag = f"[{SOURCE_NAMES.get(src, src)}]" if src else ""
    lines = [
        f"{i}. {link}[{badge} {p.get('score') or 0:.0f}分] [{p.get('sector', '')}]{src_tag} "
        f"{p.get('intent_label', '')} {p.get('title', '')}"
    ]
    if p.get("date"):
        lines.append(f"   🕐 {p['date']}")
    return "\n".join(lines)


def _top_card(posts: list) -> str:
    """今日最小白帖卡片，模仿网页 top-posts 区块。"""
    body = ["**🔥 今日最\"小白\"的帖子**", ""]
    body.extend(_post_line(i + 1, p) for i, p in enumerate(posts))
    return "\n".join(body)


def send_daily_report(dashboard_path: str) -> bool:
    """读取 dashboard 数据，发送 4 张板块卡片（内嵌曲线图）+ 1 张最小白帖卡片。"""
    with open(dashboard_path, encoding="utf-8") as f:
        dashboard = json.load(f)
    if not isinstance(dashboard, dict):
        print("[notify_daily] dashboard 数据格式异常", file=sys.stderr)
        return False
    latest = dashboard.get("latest")
    sectors = latest.get("sectors") if isinstance(latest, dict) else {}
    if not sectors:
        print("[notify_daily] dashboard 无数据", file=sys.stderr)
        return False

    history = dashboard.get("sector_history") or {}
    ok = True
    for key in ("nasdaq", "gold", "cpo", "semiconductor"):
        sector = sectors.get(key)
        if not isinstance(sector, dict) or not sector:
            continue
        name = SECTOR_NAMES.get(key, key)
        # 板块曲线图：上传后内嵌卡片标题下方；失败静默降级为无图卡片
        image_key = ""
        records = history.get(key, []) if isinstance(history, dict) else []
        if records:
            try:
                png = _chart_png(records, SECTOR_COLORS.get(key, "#94a3b8"))
            except Exception as e:
                print(f"[notify_daily] 生成 {name} 曲线图失败: {e}", file=sys.stderr)
                png = b""
            if png:
                with tempfile.NamedTemporaryFile(suffix=".png") as f:
                    f.write(png)
                    f.flush()
                    image_key = _upload_image(f.name)
        ok = _send_markdown(
            _sector_card(name, SECTOR_EMOJI.get(key, "📊"), sector),
            f"{name} 宝妈指数 {sector.get('index', '')}",
            image_key=image_key,
            image_position="after_title" if image_key else "",
        ) and ok

    # 跨板块合并 top 小白帖，按分数降序取前 8（与网页一致）
    all_top = []
    for key, sector in sectors.items():
        if not isinstance(sector, dict):
            continue
        for p in sector.get("top_newbie_posts") or []:
            if not isinstance(p, dict):
                continue
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
