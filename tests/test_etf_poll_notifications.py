"""轮询只发送已保存结果；原23:30流水线可以独立发送宝妈报告。"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import daily_reports, notify_daily, notify_etf_oi


@pytest.mark.parametrize('sent', [False, True])
def test_cached_card_delivery_returns_actual_send_status_without_refresh(monkeypatch, sent):
    def forbidden(*args, **kwargs):
        raise AssertionError('卡片发送不得查询行情')
    monkeypatch.setattr(notify_etf_oi, 'update_history', forbidden)
    monkeypatch.setattr(notify_etf_oi, 'chart_png', lambda rows: b'')
    messages = []
    monkeypatch.setattr(notify_etf_oi, '_send_markdown',
                        lambda card, *a, **k: messages.append(card) or sent)
    data = {'methodology_id': 'csi300_etf_free_float_v2', 'records': [{
        'date': '2026-11-04', 'ratio_pct': 2.1, 'fund_count': 25,
        'etf_assets_yuan': 4.5e11, 'index_free_float_mv_yuan': 2.1e13,
        'index_close': 4100,
    }]}
    assert notify_etf_oi.send_etf_oi_card(data) is sent
    assert len(messages) == 1 and '2026-11-04' in messages[0]


def test_card_delivery_rejects_old_methodology(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('旧口径不得发送')
    monkeypatch.setattr(notify_etf_oi, '_send_markdown', forbidden)
    assert notify_etf_oi.send_etf_oi_card({'methodology_id': 'csi300_etf_free_float_v1'}) is False


@pytest.mark.parametrize('succeeded', [False, True])
def test_mom_only_daily_report_never_calls_etf(monkeypatch, tmp_path, succeeded):
    monkeypatch.setattr(notify_daily, 'send_daily_report', lambda path: succeeded)
    def forbidden(*args, **kwargs):
        raise AssertionError('原23:30任务不得查询或发送ETF')
    monkeypatch.setattr(notify_etf_oi, 'send_etf_oi_report', forbidden)
    assert daily_reports.send_reports(tmp_path, include_etf=False) is succeeded
