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
                 index_close=c, index_pre_close=4000, volume_shares=v, missing=[],
                 carried_members=[], carried_member_count=0, carried_weight_pct=0.0)
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
    card = report.build_card({'methodology_id': 'csi300_etf_free_float_v2', 'records': records(), 'updated_at': '2026-10-28T12:00:00+00:00'})
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
    assert '最近检查时间：2026-10-28 20:00' in card


def test_更新失败发送带截至日的缓存卡且清理临时图(tmp_path, monkeypatch):
    path = tmp_path / 'csi300_etf_position.json'
    path.write_text(json.dumps({'methodology_id': 'csi300_etf_free_float_v2', 'records': records()}))
    monkeypatch.setattr(report, 'update_history', lambda *args: (_ for _ in ()).throw(ValueError('test')))
    uploaded, sent = [], []
    def upload(file_path):
        assert Path(file_path).exists()
        uploaded.append(file_path)
        return 'image-test'
    monkeypatch.setattr(report, '_upload_image', upload)
    monkeypatch.setattr(report, '_send_markdown', lambda card, summary, **kw: sent.append((card, kw)) or True)
    assert report.send_etf_oi_report(tmp_path) is False
    assert '更新失败' in sent[0][0] and '2026-10-23' in sent[0][0]
    assert sent[0][1]['image_key'] == 'image-test'
    assert not Path(uploaded[0]).exists()


def test_更新后的历史也发布到网页(tmp_path, monkeypatch):
    path = tmp_path / 'csi300_etf_position.json'
    def update(_):
        data = {'methodology_id': 'csi300_etf_free_float_v2', 'records': records(), 'as_of': '2026-10-23'}
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
    monkeypatch.setattr(report, 'update_history', lambda *args: {'methodology_id': 'csi300_etf_free_float_v2', 'records': records()})
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
    card = report.build_card({'methodology_id': 'csi300_etf_free_float_v2', 'records': rows})
    assert '0.1000%' in card and '暂缺' in card


def test_旧文件存在但新数据失败时不回退旧口径(tmp_path, monkeypatch):
    (tmp_path / 'etf_oi.json').write_text(json.dumps({'records': records()}))
    monkeypatch.setattr(report, 'update_history', lambda *args: (_ for _ in ()).throw(ValueError('test')))
    sent = []
    monkeypatch.setattr(report, '_send_markdown', lambda card, summary, **kw: sent.append(card) or True)
    assert not report.send_etf_oi_report(tmp_path)
    assert '暂无完整数据' in sent[0] and '0.1000%' not in sent[0]


@pytest.mark.parametrize('count,weight', [(0, 0.0), (2, 1.2345)])
def test_停牌估值不是缺失且卡片显示已核验说明和动态权重(count, weight):
    rows = records()
    rows[-1].update(carried_member_count=count, carried_weight_pct=weight)
    card = report.build_card({'methodology_id': 'csi300_etf_free_float_v2', 'records': rows})
    assert '0.1000%' in card
    assert '停牌成分沿用最近有效估值，已核对可识别公司行动' in card
    if count:
        assert '2 只' in card and '1.2345%' in card
    else:
        assert '沿用估值成分：0' not in card
    assert '3 个交易日数据未齐' not in card


def test_v1缓存不冒充停牌修订后的v2卡片():
    card = report.build_card({'methodology_id': 'csi300_etf_free_float_v1', 'records': records()})
    assert '暂无完整数据' in card
    assert '0.1000%' not in card


@pytest.mark.parametrize('version', ['csi300_etf_free_float_v1', None])
@pytest.mark.parametrize('mode', ['preview', 'update_only'])
def test_CLI拒绝无刷新旧口径且不覆盖网页v2(tmp_path, monkeypatch, version, mode):
    current = {'methodology_id': 'csi300_etf_free_float_v2', 'records': records(), 'as_of': '2026-10-23'}
    snapshot = dict(current)
    if version is None:
        snapshot.pop('methodology_id')
    else:
        snapshot['methodology_id'] = version
    (tmp_path / 'csi300_etf_position.json').write_text(json.dumps(snapshot))
    web = tmp_path / 'web'
    web.mkdir()
    published = web / 'csi300_etf_position.json'
    original = json.dumps(current)
    published.write_text(original)
    preview = tmp_path / 'preview.png'
    flags = ['--preview', str(preview)] if mode == 'preview' else ['--update-only']
    monkeypatch.setattr(sys, 'argv', ['notify_etf_oi.py', str(tmp_path), '--no-refresh', *flags])
    with pytest.raises(ValueError, match='版本|口径'):
        report.main()
    assert published.read_text() == original
    assert not preview.exists()


def test_CLI无刷新v2可正常预览发布且不更新或发送(tmp_path, monkeypatch):
    current = {'methodology_id': 'csi300_etf_free_float_v2', 'records': records(), 'as_of': '2026-10-23'}
    original = json.dumps(current)
    (tmp_path / 'csi300_etf_position.json').write_text(original)
    preview = tmp_path / 'preview.png'
    def forbidden(*args, **kwargs):
        raise AssertionError('禁止更新或发送')
    monkeypatch.setattr(report, 'update_history', forbidden)
    monkeypatch.setattr(report, '_upload_image', forbidden)
    monkeypatch.setattr(report, '_send_markdown', forbidden)
    monkeypatch.setattr(sys, 'argv', ['notify_etf_oi.py', str(tmp_path), '--no-refresh', '--preview', str(preview)])
    assert report.main() == 0
    assert Image.open(preview).size == (1600, 1100)
    assert (tmp_path / 'web' / 'csi300_etf_position.json').read_text() == original


@pytest.mark.parametrize('version', ['csi300_etf_free_float_v1', None])
def test_旧口径通知不发布不上传不发送(tmp_path, monkeypatch, version):
    current = {'methodology_id': 'csi300_etf_free_float_v2', 'records': records(), 'as_of': '2026-10-23'}
    snapshot = dict(current)
    if version is None:
        snapshot.pop('methodology_id')
    else:
        snapshot['methodology_id'] = version
    (tmp_path / 'csi300_etf_position.json').write_text(json.dumps(snapshot))
    web = tmp_path / 'web'
    web.mkdir()
    published = web / 'csi300_etf_position.json'
    original = json.dumps(current)
    published.write_text(original)
    def forbidden(*args, **kwargs):
        raise AssertionError('旧口径不得上传或发送')
    monkeypatch.setattr(report, '_upload_image', forbidden)
    monkeypatch.setattr(report, '_send_markdown', forbidden)
    assert report.send_etf_oi_report(tmp_path, refresh=False) is False
    assert published.read_text() == original


def test_合法v2空记录仍可发送既有数据不足告警(tmp_path, monkeypatch):
    current = {'methodology_id': 'csi300_etf_free_float_v2', 'records': []}
    (tmp_path / 'csi300_etf_position.json').write_text(json.dumps(current))
    sent = []
    monkeypatch.setattr(report, '_send_markdown', lambda card, summary, **kw: sent.append(card) or True)
    assert report.send_etf_oi_report(tmp_path, refresh=False) is False
    assert len(sent) == 1 and '暂无完整数据' in sent[0]


@pytest.mark.parametrize('missing,reasons', [
    (['reference_outside_coverage'], ['ETF参考资料覆盖不足']),
    (['membership_outside_coverage'], ['指数成分资料覆盖不足']),
    (['daily_basic_snapshot_missing'], ['成分估值快照缺失']),
    (['510300.SH:share_or_nav'], ['ETF份额或净值缺失']),
    (['reference_outside_coverage', 'membership_outside_coverage',
      'daily_basic_snapshot_missing', '510300.SH:share_or_nav'],
     ['ETF参考资料覆盖不足', '指数成分资料覆盖不足', '成分估值快照缺失', 'ETF份额或净值缺失']),
    (['reference_outside_coverage', '000001.SZ:close_or_free_share'],
     ['ETF参考资料覆盖不足', '其他数据尚未齐全']),
    (['000001.SZ:close_or_free_share'], ['数据尚未齐全']),
    ([], ['数据尚未齐全']),
    (None, ['数据尚未齐全']),
])
def test_卡片如实区分尾部缺失原因且兼容旧记录(missing, reasons):
    rows = records()
    pending = dict(rows[-1], date='2026-11-04', ratio_pct=None)
    if missing is None:
        pending.pop('missing')
    else:
        pending['missing'] = missing
    rows.append(pending)
    card = report.build_card({'methodology_id': 'csi300_etf_free_float_v2', 'records': rows})
    assert '数据截至 **2026-10-23**' in card and '2026-11-04' in card
    for reason in reasons:
        assert reason in card
    assert '当日规模数据尚未齐全' not in card
    assert 'reference_outside_coverage' not in card and 'share_or_nav' not in card
    if missing and missing[0] == 'reference_outside_coverage' and len(missing) == 1:
        assert '数据未齐' not in card and '数据尚未齐全' not in card
        assert 'ETF份额或净值缺失' not in card


def test_卡片汇总截至日之后各交易日真实缺失原因():
    rows = records()
    rows.extend([
        dict(rows[-1], date='2026-11-03', ratio_pct=None, missing=['reference_outside_coverage']),
        dict(rows[-1], date='2026-11-04', ratio_pct=None, missing=['510300.SH:share_or_nav']),
    ])
    card = report.build_card({'methodology_id': 'csi300_etf_free_float_v2', 'records': rows})
    assert 'ETF参考资料覆盖不足' in card and 'ETF份额或净值缺失' in card


@pytest.mark.parametrize('missing,successful', [
    (['reference_outside_coverage'], False),
    (['membership_outside_coverage'], False),
    (['daily_basic_snapshot_missing'], False),
    (['510300.SH:share_or_nav'], True),
    ([], True),
])
def test_尾部结构缺失发送缓存后仍返回失败(tmp_path, monkeypatch, missing, successful):
    rows = records()
    rows.append(dict(rows[-1], date='2026-11-04', ratio_pct=None, missing=missing))
    current = {'methodology_id': 'csi300_etf_free_float_v2', 'records': rows}
    (tmp_path / 'csi300_etf_position.json').write_text(json.dumps(current))
    monkeypatch.setattr(report, 'update_history', lambda *args: current)
    monkeypatch.setattr(report, 'chart_png', lambda *args: b'')
    sent = []
    monkeypatch.setattr(report, '_send_markdown', lambda card, summary, **kw: sent.append(card) or True)
    assert report.send_etf_oi_report(tmp_path) is successful
    assert len(sent) == 1 and '0.1000%' in sent[0]


def test_历史结构缺口不使已更新的通知失败(tmp_path, monkeypatch):
    rows = records()
    rows[2]['missing'] = ['reference_outside_coverage']
    current = {'methodology_id': 'csi300_etf_free_float_v2', 'records': rows}
    (tmp_path / 'csi300_etf_position.json').write_text(json.dumps(current))
    monkeypatch.setattr(report, 'update_history', lambda *args: current)
    monkeypatch.setattr(report, 'chart_png', lambda *args: b'')
    monkeypatch.setattr(report, '_send_markdown', lambda *args, **kw: True)
    assert report.send_etf_oi_report(tmp_path) is True


def test_尾部较早的结构缺失不会被最新日份额缺失掩盖(tmp_path, monkeypatch):
    rows = records()
    rows.extend([
        dict(rows[-1], date='2026-11-03', ratio_pct=None, missing=['reference_outside_coverage']),
        dict(rows[-1], date='2026-11-04', ratio_pct=None, missing=['510300.SH:share_or_nav']),
    ])
    current = {'methodology_id': 'csi300_etf_free_float_v2', 'records': rows}
    (tmp_path / 'csi300_etf_position.json').write_text(json.dumps(current))
    monkeypatch.setattr(report, 'update_history', lambda *args: current)
    monkeypatch.setattr(report, 'chart_png', lambda *args: b'')
    monkeypatch.setattr(report, '_send_markdown', lambda *args, **kw: True)
    assert report.send_etf_oi_report(tmp_path) is False
