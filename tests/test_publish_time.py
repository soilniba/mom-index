"""小红书 publish_time 角标 → 绝对时间戳 解析测试

真实格式（2026-08-04 探测 so.xiaohongshu.com/api/sns/web/v2/search/notes）：
  corner_tag_info = [{"type": "publish_time", "text": "01-21" | "2025-02-06" | "2小时前" | null}]
"""
from datetime import datetime

import pytest

from collectors.xhs_playwright import parse_publish_time

NOW = datetime(2026, 8, 4, 12, 0, 0)  # 固定基准：2026-08-04 12:00


def test_relative_minutes():
    assert parse_publish_time("3分钟前", NOW) == "2026-08-04 11:57"


def test_relative_hours():
    assert parse_publish_time("2小时前", NOW) == "2026-08-04 10:00"


def test_relative_days():
    assert parse_publish_time("5天前", NOW) == "2026-07-30 12:00"


def test_just_now():
    assert parse_publish_time("刚刚", NOW) == "2026-08-04 12:00"


def test_yesterday():
    assert parse_publish_time("昨天", NOW) == "2026-08-03 12:00"


def test_mmdd_this_year():
    assert parse_publish_time("01-21", NOW) == "2026-01-21 00:00"


def test_mmdd_future_rolls_back_to_last_year():
    """今天 01-05 出现 12-30 → 属去年（与股吧 _fmt_date 边界一致）"""
    assert parse_publish_time("12-30", datetime(2026, 1, 5, 10, 0)) == "2025-12-30 00:00"


def test_full_date():
    assert parse_publish_time("2025-02-06", NOW) == "2025-02-06 00:00"


def test_empty_and_invalid_return_blank():
    assert parse_publish_time("", NOW) == ""
    assert parse_publish_time(None, NOW) == ""
    assert parse_publish_time("随便什么", NOW) == ""
