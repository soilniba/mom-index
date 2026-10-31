import io
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import notify_etf_oi as report
import sync_data


@pytest.fixture(autouse=True)
def isolated_web_data(tmp_path, monkeypatch):
    monkeypatch.setattr(sync_data, 'FRONTEND_DATA_DIR', str(tmp_path / 'web'))


def records():
    return [dict(date=f'2026-10-{d:02d}', ratio_pct=r,
                 etf_assets_yuan=8e8, index_free_float_mv_yuan=1e12, fund_count=25,
                 index_close=c, index_pre_close=4000, volume_shares=v, missing=[])
            for d, r, c, v in [(20, .08, 4000, 1e9), (21, .09, 3900, 2e9),
                                (22, None, 4100, 3e9), (23, .1, 4200, 4e9)]]


def test_真实绘制双曲线和成交量柱():
    png = report.chart_png(records())
    im = Image.open(io.BytesIO(png)).convert('RGB')
    assert im.size == (1600, 1100)
    pixels = list(im.get_flattened_data())
    for color in [(251, 191, 36), (96, 165, 250), (71, 85, 105)]:
        assert pixels.count(color) > 100
    assert report.chart_png([]) == b''


def test_成交量按同日收盘与昨收涨红跌绿平盘灰():
    im = Image.open(io.BytesIO(report.chart_png(records()))).convert('RGB')
    bars = list(im.crop((145, 830, 1430, 976)).get_flattened_data())
    assert bars.count((239, 68, 68)) > 100
    assert bars.count((34, 197, 94)) > 100
    assert bars.count((71, 85, 105)) > 100


def test_卡片明确数据日期百分点变化和金额单位():
    card = report.build_card({'methodology_id': 'csi300_etf_free_float_v1', 'records': records(), 'updated_at': '2026-10-28T12:00:00+00:00'})
    assert '2026-10-23' in card
    assert '0.1000%' in card
    assert '+0.0100 个百分点' in card
    assert '8.00 亿元' in card
    assert '1.00 万亿元' in card
    assert '25 只普通ETF合计规模' in card
    assert '自由流通市值' in card
    assert '沪深300 ETF配置强度代理' in card
    assert '固定样本' not in card
    assert '国家队实际持仓' in card


def test_更新失败发送带截至日的缓存卡且清理临时图(tmp_path, monkeypatch):
    path = tmp_path / 'csi300_etf_position.json'
    path.write_text(json.dumps({'methodology_id': 'csi300_etf_free_float_v1', 'records': records()}))
    monkeypatch.setattr(report, 'update_history', lambda *args: (_ for _ in ()).throw(ValueError('test')))
    uploaded, sent = [], []
    def upload(file_path):
        assert Path(file_path).exists()
        uploaded.append(file_path)
        return 'image-test'
    monkeypatch.setattr(report, '_upload_image', upload)
    monkeypatch.setattr(report, '_send_markdown', lambda card, summary, **kw: sent.append((card, kw)) or True)
    assert report.send_etf_oi_report(tmp_path)
    assert '更新失败' in sent[0][0] and '2026-10-23' in sent[0][0]
    assert sent[0][1]['image_key'] == 'image-test'
    assert not Path(uploaded[0]).exists()


def test_更新后的历史也发布到网页(tmp_path, monkeypatch):
    path = tmp_path / 'csi300_etf_position.json'
    def update(_):
        data = {'methodology_id': 'csi300_etf_free_float_v1', 'records': records(), 'as_of': '2026-10-23'}
        path.write_text(json.dumps(data))
        return data
    monkeypatch.setattr(report, 'update_history', update)
    monkeypatch.setattr(report, '_upload_image', lambda _: 'image-test')
    monkeypatch.setattr(report, '_send_markdown', lambda *args, **kw: True)
    assert report.send_etf_oi_report(tmp_path)
    assert json.loads((tmp_path / 'web' / 'csi300_etf_position.json').read_text())['as_of'] == '2026-10-23'


def test_无缓存且更新失败发送明确异常而不伪造比例(tmp_path, monkeypatch):
    monkeypatch.setattr(report, 'update_history', lambda *args: (_ for _ in ()).throw(ValueError('test')))
    sent = []
    monkeypatch.setattr(report, '_send_markdown', lambda card, summary, **kw: sent.append(card) or True)
    assert not report.send_etf_oi_report(tmp_path)
    assert '暂无完整数据' in sent[0]
    assert '0.0000%' not in sent[0]


def test_绘图失败仍发送数值(tmp_path, monkeypatch):
    monkeypatch.setattr(report, 'update_history', lambda *args: {'methodology_id': 'csi300_etf_free_float_v1', 'records': records()})
    monkeypatch.setattr(report, 'chart_png', lambda *args: (_ for _ in ()).throw(OSError('font')))
    sent = []
    monkeypatch.setattr(report, '_send_markdown', lambda card, summary, **kw: sent.append((card, kw)) or True)
    assert report.send_etf_oi_report(tmp_path)
    assert '0.1000%' in sent[0][0]
    assert not sent[0][1].get('image_key')


@pytest.mark.parametrize('failed', [False, True])
def test_先发送完整宝妈报告再发新指标即使原报告异常(tmp_path, monkeypatch, failed):
    from scripts import notify_daily
    import scripts.daily_reports as daily
    events = []
    def mom(path):
        events.append('mom-complete')
        if failed:
            raise OSError('relay')
        return True
    def etf(data_dir):
        events.append('etf')
        return True
    monkeypatch.setattr(notify_daily, 'send_daily_report', mom)
    monkeypatch.setattr(report, 'send_etf_oi_report', etf)
    assert daily.send_reports(tmp_path) is (not failed)
    assert events == ['mom-complete', 'etf']


def test_旧缓存不冒充新指标且缺行情仍有数值卡():
    old = report.build_card({'records': records()})
    assert '暂无完整数据' in old and '0.1000%' not in old
    rows = records()
    rows[-1]['index_close'] = None
    card = report.build_card({'methodology_id': 'csi300_etf_free_float_v1', 'records': rows})
    assert '0.1000%' in card and '暂缺' in card


def test_旧文件存在但新数据失败时不回退旧口径(tmp_path, monkeypatch):
    (tmp_path / 'etf_oi.json').write_text(json.dumps({'records': records()}))
    monkeypatch.setattr(report, 'update_history', lambda *args: (_ for _ in ()).throw(ValueError('test')))
    sent = []
    monkeypatch.setattr(report, '_send_markdown', lambda card, summary, **kw: sent.append(card) or True)
    assert not report.send_etf_oi_report(tmp_path)
    assert '暂无完整数据' in sent[0] and '0.1000%' not in sent[0]
