"""微博 publish_time 展示文本 → 绝对时间戳 解析测试

真实格式（2026-08-04 实测 fetch_advanced_search）：
  "今天07:32"（当天）、"08月03日 11:46"（今年内前几天）
"""
from datetime import datetime

from collectors.weibo_collector import parse_publish_time

NOW = datetime(2026, 8, 4, 12, 0, 0)  # 固定基准：2026-08-04 12:00


def test_relative_minutes():
    assert parse_publish_time("58分钟前", NOW) == "2026-08-04 11:02"


def test_relative_hours():
    assert parse_publish_time("2小时前", NOW) == "2026-08-04 10:00"


def test_just_now_and_yesterday():
    assert parse_publish_time("刚刚", NOW) == "2026-08-04 12:00"
    assert parse_publish_time("昨天", NOW) == "2026-08-03 12:00"


def test_today():
    assert parse_publish_time("今天07:32", NOW) == "2026-08-04 07:32"


def test_today_single_digit_hour():
    assert parse_publish_time("今天 9:05", NOW) == "2026-08-04 09:05"


def test_month_day():
    assert parse_publish_time("08月03日 11:46", NOW) == "2026-08-03 11:46"


def test_earlier_month():
    assert parse_publish_time("07月29日 06:06", NOW) == "2026-07-29 06:06"


def test_future_date_rolls_back_to_last_year():
    """今天 01-05 出现 12-30 → 属去年（与股吧 _fmt_date 边界一致）"""
    assert parse_publish_time("12月30日 20:00", datetime(2026, 1, 5, 10, 0)) == "2025-12-30 20:00"


def test_full_date_format():
    assert parse_publish_time("2026年08月04日 07:32", NOW) == "2026-08-04 07:32"


def test_empty_and_invalid_return_blank():
    assert parse_publish_time("", NOW) == ""
    assert parse_publish_time(None, NOW) == ""
    assert parse_publish_time("随便什么", NOW) == ""
