"""回归返回用户的缓存与旧 DOM；浏览器只访问本机测试数据。"""
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import threading

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def web(tmp_path):
    for filename in ('dashboard.html', 'etf_oi.js', 'vendor/chart.umd.min.js'):
        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'frontend' / filename, target)
    html = (tmp_path / 'dashboard.html').read_text()
    (tmp_path / 'legacy.html').write_text(
        html.replace('<p class="etf-note" id="etf-oi-valuation"></p>', ''))
    (tmp_path / 'warm.html').write_text(
        '<p id="etf-oi-status"></p><script src="etf_oi.js"></script>')
    data = tmp_path / 'data'
    data.mkdir()
    payload = {
        'methodology_id': 'csi300_etf_free_float_v2',
        'records': [{
            'date': '2026-10-30', 'ratio_pct': 1.8673, 'etf_assets_yuan': 3.95e11,
            'index_free_float_mv_yuan': 2.12e13, 'fund_count': 25,
            'index_member_count': 300, 'membership_effective_from': '20261019',
            'funds': [], 'missing': [], 'index_close': 3985.5,
            'index_pre_close': 4041.8, 'volume_shares': 1.92e10,
            'carried_members': [], 'carried_member_count': 0, 'carried_weight_pct': 0,
        }],
    }
    target = data / 'csi300_etf_position.json'
    target.write_text(json.dumps(payload))
    (data / 'dashboard_data.json').write_text('{}')
    state = {'legacy': False}

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if state['legacy'] and self.path == '/etf_oi.js':
                body = b"document.getElementById('etf-oi-status').textContent='cached-v1';"
                self.send_response(200)
                self.send_header('Content-Type', 'application/javascript')
                self.send_header('Cache-Control', 'max-age=3600')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', state, target, payload
    server.shutdown()
    server.server_close()
    thread.join()


def wait_for_result(page):
    page.wait_for_function("""() => !document.getElementById('etf-oi-content').hidden
        || /暂不可用|cached-v1/.test(document.getElementById('etf-oi-status').textContent)""")


def test_旧页面缺少估值说明仍展示新版数据和说明(browser, web):
    url, _, _, _ = web
    page = browser.new_page()
    try:
        page.goto(f'{url}/legacy.html')
        wait_for_result(page)
        assert page.locator('#etf-oi-content').is_visible()
        assert page.locator('#etf-oi-ratio').inner_text() == '1.8673%'
        assert '停牌成分' in page.locator('#etf-oi-valuation').inner_text()
        assert page.evaluate("Chart.getChart('chart-etf-oi').data.datasets[0].data") == [1.8673]
    finally:
        page.close()


def test_新页面不复用浏览器里旧版脚本缓存(browser, web):
    url, state, _, _ = web
    page = browser.new_page()
    try:
        state['legacy'] = True
        page.goto(f'{url}/warm.html')
        assert page.locator('#etf-oi-status').inner_text() == 'cached-v1'
        state['legacy'] = False
        page.goto(f'{url}/dashboard.html')
        wait_for_result(page)
        assert page.locator('#etf-oi-content').is_visible()
        assert page.locator('#etf-oi-ratio').inner_text() == '1.8673%'
    finally:
        page.close()


def test_缓存修复仍拒绝旧口径数据(browser, web):
    url, _, target, payload = web
    payload['methodology_id'] = 'csi300_etf_free_float_v1'
    target.write_text(json.dumps(payload))
    page = browser.new_page()
    try:
        page.goto(f'{url}/dashboard.html')
        wait_for_result(page)
        assert not page.locator('#etf-oi-content').is_visible()
        assert '暂不可用' in page.locator('#etf-oi-status').inner_text()
    finally:
        page.close()


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
def test_网页显示尾部真实缺失原因并保留截至日和断线(browser, web, missing, reasons):
    url, _, target, payload = web
    pending = dict(payload['records'][0], date='2026-11-04', ratio_pct=None)
    if missing is None:
        pending.pop('missing')
    else:
        pending['missing'] = missing
    payload['records'].append(pending)
    target.write_text(json.dumps(payload))
    page = browser.new_page()
    try:
        page.goto(f'{url}/dashboard.html')
        wait_for_result(page)
        notice = page.locator('#etf-oi-status').inner_text()
        for reason in reasons:
            assert reason in notice
        assert '2026-11-04' in notice
        assert '2026-10-30' in page.locator('#etf-oi-date').inner_text()
        assert page.locator('#etf-oi-ratio').inner_text() == '1.8673%'
        assert page.evaluate("Chart.getChart('chart-etf-oi').data.datasets[0].data") == [1.8673, None]
        assert page.evaluate("Chart.getChart('chart-etf-oi').data.datasets[0].spanGaps") is False
        assert 'reference_outside_coverage' not in notice and 'share_or_nav' not in notice
        if missing == ['reference_outside_coverage']:
            assert '数据未齐' not in notice and '数据尚未齐全' not in notice
            assert 'ETF份额或净值缺失' not in notice
    finally:
        page.close()


def test_网页汇总截至日之后各交易日真实缺失原因(browser, web):
    url, _, target, payload = web
    payload['records'].extend([
        dict(payload['records'][0], date='2026-11-03', ratio_pct=None,
             missing=['reference_outside_coverage']),
        dict(payload['records'][0], date='2026-11-04', ratio_pct=None,
             missing=['510300.SH:share_or_nav']),
    ])
    target.write_text(json.dumps(payload))
    page = browser.new_page()
    try:
        page.goto(f'{url}/dashboard.html')
        wait_for_result(page)
        notice = page.locator('#etf-oi-status').inner_text()
        assert 'ETF参考资料覆盖不足' in notice and 'ETF份额或净值缺失' in notice
        assert '2026-10-30' in page.locator('#etf-oi-date').inner_text()
    finally:
        page.close()
