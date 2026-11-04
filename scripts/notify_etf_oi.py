#!/usr/bin/env python3
"""更新/读取配置强度代理 → 校验v2 → 原子发布、预览或发送；无缓存时发送失败告警。"""

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


STRUCTURAL_MISSING = {
    'reference_outside_coverage': 'ETF参考资料覆盖不足',
    'membership_outside_coverage': '指数成分资料覆盖不足',
    'daily_basic_snapshot_missing': '成分估值快照缺失',
}


def missing_reasons(records):
    reasons = []
    unspecified = False
    for row in records:
        for item in row.get('missing') or ['unknown']:
            reason = STRUCTURAL_MISSING.get(item)
            if reason is None and item.endswith(':share_or_nav'):
                reason = 'ETF份额或净值缺失'
            if reason:
                if reason not in reasons:
                    reasons.append(reason)
            else:
                unspecified = True
    if unspecified:
        reasons.append('其他数据尚未齐全' if reasons else '数据尚未齐全')
    return '、'.join(reasons)


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
        '停牌成分沿用最近有效估值，已核对可识别公司行动。',
        '这是配置强度代理，不代表国家队实际持仓；分红、调样及其他投资者行为也会影响比例。',
    ]
    if row.get('carried_member_count', 0):
        lines.append(f"沿用估值成分：{row['carried_member_count']} 只，占分母 {row['carried_weight_pct']:.4f}%。")
    pending = [r for r in records if r['date'] > row['date']]
    if pending:
        lines.append(f"⚠️ 截至最新交易日 {pending[-1]['date']}，更新受阻："
                     f"{missing_reasons(pending)}；当前占比截至 {row['date']}。")
    gaps = sum(r.get('ratio_pct') is None for r in records[-250:])
    if gaps:
        lines.append(f'图示区间有 {gaps} 个交易日指标缺失，缺口处不连线。')
    if update_failed:
        lines.append('⚠️ 本次更新失败，以上为最近成功保存的数据。')
    if data.get('updated_at'):
        updated = datetime.fromisoformat(data['updated_at']).astimezone(ZoneInfo('Asia/Shanghai'))
        lines.append(f"最近检查时间：{updated:%Y-%m-%d %H:%M}（北京时间）")
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
            _send_markdown(build_card({}, True), '沪深300 ETF配置强度代理')
            return False
    if not isinstance(data, dict) or data.get('methodology_id') != METHODOLOGY_ID:
        print('[etf_oi] 指标口径版本不匹配，跳过发布与通知', file=sys.stderr)
        return False
    records = data.get('records', [])
    complete = [r for r in records if r.get('ratio_pct') is not None]
    valid = bool(complete)
    blocked = valid and any(
        item in STRUCTURAL_MISSING
        for r in records if r['date'] > complete[-1]['date']
        for item in r.get('missing') or [])
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
    return bool(sent and valid and not failed and not blocked)


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
    if not isinstance(data, dict) or data.get('methodology_id') != METHODOLOGY_ID:
        raise ValueError('指标口径版本不匹配，禁止预览或发布旧历史')
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
