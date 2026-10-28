"""按顺序尝试完整宝妈报告和独立 ETF 图卡，互不阻断。"""

from pathlib import Path
import sys


def send_reports(data_dir) -> bool:
    from scripts.notify_daily import send_daily_report
    from scripts.notify_etf_oi import send_etf_oi_report

    ok = True
    try:
        ok = send_daily_report(str(Path(data_dir) / 'dashboard_data.json')) and ok
    except Exception as exc:
        print(f'[daily_reports] 宝妈报告失败：{type(exc).__name__}', file=sys.stderr)
        ok = False
    try:
        ok = send_etf_oi_report(data_dir) and ok
    except Exception as exc:
        print(f'[daily_reports] ETF报告失败：{type(exc).__name__}', file=sys.stderr)
        ok = False
    return ok


if __name__ == '__main__':
    sys.exit(0 if send_reports(sys.argv[1] if len(sys.argv) > 1 else 'data') else 1)
