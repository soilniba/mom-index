#!/usr/bin/env python3
"""更新共享配置强度代理 → 原子发布网页 → 预览或发送独立图卡。"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzer.etf_oi import HISTORY_FILENAME, METHODOLOGY_ID, update_history
from scripts.etf_oi_chart import chart_png
from scripts.notify_daily import _send_markdown, _upload_image
from sync_data import publish_etf_oi


def build_card(data, update_failed=False):
    records = data.get('records', []) if data.get('methodology_id') == METHODOLOGY_ID else []
    complete = [r for r in records if r.get('ratio_pct') is not None]
    if not complete:
        return '**沪深300 ETF配置强度代理**\n\n⚠️ 暂无完整数据，本次更新失败。'
    row = complete[-1]
    change = (f"{row['ratio_pct'] - complete[-2]['ratio_pct']:+.4f} 个百分点"
              if len(complete) > 1 else '历史不足，暂无变化值')
    lines = [
        '**沪深300 ETF配置强度代理**', '',
        f"数据截至 **{row['date']}**", '',
        f"# {row['ratio_pct']:.4f}%", '',
        f'较上一完整交易日 **{change}**',
        f"{row['fund_count']} 只普通ETF合计规模 **{row['etf_assets_yuan']/1e8:,.2f} 亿元**",
        f"沪深300自由流通市值 **{row['index_free_float_mv_yuan']/1e12:,.2f} 万亿元**",
        (f"沪深300收盘 **{row['index_close']:,.2f} 点**" if row.get('index_close') is not None
         else '沪深300收盘点位暂缺'), '',
        '口径：全部普通沪深300ETF同日份额 × 单位净值 / 当日有效成分自由流通市值。',
        '这是配置强度代理，不代表国家队实际持仓；分红、调样及其他投资者行为也会影响比例。',
    ]
    if records and records[-1]['date'] > row['date']:
        lines.append(f"⚠️ 行情已至 {records[-1]['date']}，当日规模数据尚未齐全。")
    gaps = sum(r.get('ratio_pct') is None for r in records[-250:])
    if gaps:
        lines.append(f'图示区间有 {gaps} 个交易日数据未齐，缺口处不连线。')
    if update_failed:
        lines.append('⚠️ 本次更新失败，以上为最近成功保存的数据。')
    if data.get('updated_at'):
        updated = datetime.fromisoformat(data['updated_at']).astimezone(ZoneInfo('Asia/Shanghai'))
        lines.append(f"最近成功更新：{updated:%Y-%m-%d %H:%M}（北京时间）")
    return '\n'.join(lines)


def send_etf_oi_report(data_dir, refresh=True) -> bool:
    path = Path(data_dir) / HISTORY_FILENAME
    failed = False
    try:
        data = update_history(path) if refresh else json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        failed = True
        print(f'[etf_oi] 更新失败：{type(exc).__name__}', file=sys.stderr)
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
    valid = data.get('methodology_id') == METHODOLOGY_ID and any(
        r.get('ratio_pct') is not None for r in data.get('records', []))
    try:
        publish_etf_oi(data_dir)
    except OSError as exc:
        print(f'[etf_oi] 网页同步失败：{type(exc).__name__}', file=sys.stderr)
    image_key = ''
    if valid:
        try:
            png = chart_png(data['records'])
            if png:
                with tempfile.NamedTemporaryFile(suffix='.png') as image:
                    image.write(png)
                    image.flush()
                    image_key = _upload_image(image.name)
        except Exception as exc:
            print(f'[etf_oi] 图像生成失败：{type(exc).__name__}', file=sys.stderr)
    sent = _send_markdown(build_card(data, failed), '沪深300 ETF配置强度代理',
                          image_key=image_key, image_position='after_title' if image_key else '')
    return bool(sent and valid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('data_dir', nargs='?', default='data')
    parser.add_argument('--update-only', action='store_true', help='只更新历史，不发送')
    parser.add_argument('--preview', type=Path, help='只更新并将PNG保存至指定路径，不发送')
    parser.add_argument('--no-refresh', action='store_true', help='仅发送/预览现有历史')
    args = parser.parse_args()
    if not args.update_only and not args.preview:
        return 0 if send_etf_oi_report(args.data_dir, refresh=not args.no_refresh) else 1
    path = Path(args.data_dir) / HISTORY_FILENAME
    data = (json.loads(path.read_text(encoding='utf-8')) if args.no_refresh else update_history(path))
    publish_etf_oi(args.data_dir)
    if args.preview:
        png = chart_png(data['records'])
        if not png:
            raise ValueError('没有完整数据可绘制')
        args.preview.write_bytes(png)
    print(f"ETF配置强度代理截至 {data['as_of']}，历史 {len(data['records'])} 个交易日")
    return 0


if __name__ == '__main__':
    sys.exit(main())
