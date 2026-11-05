"""发送完整宝妈报告；手动兼容ETF串行发送，自动流水线由轮询独立负责ETF。"""

from pathlib import Path
import sys


def send_reports(data_dir, include_etf=True) -> bool:
    from scripts.notify_daily import send_daily_report

    ok = True
    try:
        ok = send_daily_report(str(Path(data_dir) / 'dashboard_data.json')) and ok
    except Exception as exc:
        print(f'[daily_reports] 宝妈报告失败：{type(exc).__name__}', file=sys.stderr)
        ok = False
    if include_etf:
        from scripts.notify_etf_oi import send_etf_oi_report
        try:
            ok = send_etf_oi_report(data_dir) and ok
        except Exception as exc:
            print(f'[daily_reports] ETF报告失败：{type(exc).__name__}', file=sys.stderr)
            ok = False
    return ok


if __name__ == '__main__':
    sys.exit(0 if send_reports(sys.argv[1] if len(sys.argv) > 1 else 'data') else 1)
