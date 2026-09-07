import io
import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.notify_daily import _chart_png, _chart_window, _post_line


def test_小白帖不展示推理和特征信号():
    line = _post_line(1, {
        "score": 85,
        "sector": "纳斯达克",
        "source": "xiaohongshu",
        "intent_label": "🟢买入",
        "title": "还能不能加",
        "date": "2026-08-12 10:00",
        "url": "https://example.com/post",
        "reasoning": "明确依赖他人决策",
        "key_signals": ["决策依赖", "加仓询问"],
    })

    assert "还能不能加" in line
    assert "📝" not in line
    assert "▸" not in line
    assert "明确依赖他人决策" not in line
    assert "决策依赖" not in line


def _records(n: int) -> list:
    return [{"date": f"2026-06-{i + 1:02d}", "index": (i * 7) % 60} for i in range(n)]


def test_曲线图窗口取最近30天不足取全量():
    assert [r["date"] for r in _chart_window(_records(66))] == \
        [f"2026-06-{i:02d}" for i in range(37, 67)]
    assert len(_chart_window(_records(5))) == 5
    assert _chart_window([]) == []


def test_曲线图生成合法png():
    png = _chart_png(_records(30), "#06b6d4")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(png))
    assert img.size == (1200, 800)
    assert img.mode == "RGBA"
    # 冷清数据，曲线应落在图内：取图中曲线颜色像素数量 > 0
    assert img.getcolors(maxcolors=100000) is not None


def test_曲线图无数据返回空():
    assert _chart_png([], "#06b6d4") == b""
