from scripts.notify_daily import _post_line


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
