# 沪深300 ETF 规模占比实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 每天先发完整宝妈指数，再发四只沪深300 ETF 规模占比、指数点位与成交量图卡。

**Architecture:** `stock_data` 提供具名基金份额和净值接口，复用现有指数入口。业务模块精确按日连接、保存独立历史；Pillow 绘图复用飞书 relay。两个库各自提交，数据接口与业务实现可独立开展。

**Tech Stack:** Python、stock_data/Tushare、标准库、已有 Pillow。

**Spec:** [design.md](design.md)；用户已明确授权按设计实施，并要求完整宝妈报告先发。

## Global Constraints

- 固定四只 `510300.SH/510310.SH/510330.SH/159919.SZ`，净值乘份额，普通流通市值，均人民币元。
- 首次回填 2023-01-01 起；默认图窗 250 个交易日；北京时间 23:30 原任务先发原报告再发新卡。
- 只从 stock_data 顶层取行情，凭据仅内存使用；无国家队持仓估算、无网页扩展。
- 缺项不补零、不跨日混算；失败保留历史并标明日期；测试不得真实发送飞书。
- 新文档单文件不超过 150 行，中文提交，推送前安全检查。

## Review Focus

- 净值供应商可能修订或重复：等值去重，冲突不得悄悄覆盖。
- 某基金整段请求为空：不得删除旧历史或变成三只基金的规模。
- 数据未齐的最新交易日：明确截至日，图上不补平。
- 柱状图成交量单位：手转亿股，不能把成交额当量。
- 原报告发送失败：仍尝试新指标，顺序保持原报告尝试在前。

## Task 1: stock_data 基金事实入口

**Files:** `stock_data/etf_facts.py`、顶层 `__init__.py`、`tests/test_etf_facts.py`，该库对应需求/设计/运维文档及 README 索引。

**Interfaces:** 产出 `fetch_etf_share_history(instrument_id, start_date, end_date) -> list[dict]`，字段 `ts_code/trade_date/fd_share`；`fetch_etf_nav_history(...) -> list[dict]`，字段 `ts_code/nav_date/unit_nav`。日期 YYYYMMDD；保留原始单位；明确错误，内部按年分段、检测截断；调用方不接触 provider。

- [ ] 先写行为测试并运行到缺少入口的预期失败。
- [ ] 实现两个具名入口，校验代码、日期范围和供应商结果，最小复用已有供应商基础设施。
- [ ] 运行定向测试和相关公开 API 回归，记录结果，以中文提交。

## Task 2: 计算与独立历史

**Files:** `analyzer/etf_oi.py`、`tests/test_etf_oi.py`。

**Interfaces:** `build_records(shares, navs, valuations, prices) -> list[dict]`；`update_history(path, end_date=None) -> dict`。记录字段为 `date/ratio_pct/etf_assets_yuan/index_float_mv_yuan/index_close/volume_shares/funds`；价格基准日期保留缺失记录 `ratio_pct=None`，以便图中断线。顶层有 `updated_at/as_of/records`。

- [ ] 测试手算：四只均 100 万份、净值 2 元，分母 100 亿元，占比应为 0.08%；100 手应为 10,000 股。
- [ ] 测试缺一只、错日、NaN、零分母和重复冲突；写后运行，确认预期失败。
- [ ] 实现精确连接、单日质量门禁、2023 起回填与近期 45 日刷新；原子写入、异常不改旧文件。
- [ ] 验证修订替换、历史保留、重复运行一致和空响应失败保护；运行整套测试，中文提交。

## Task 3: 图卡与推送顺序

**Files:** `scripts/notify_etf_oi.py`、`tests/test_notify_etf_oi.py`、`pipeline.py`，必要时 `scripts/notify_daily.py` 的复用入口。

**Interfaces:** `chart_png(records) -> bytes`、`send_etf_oi_report(data_dir, refresh=True) -> bool`；CLI 支持 `--update-only`、`--preview PATH`，默认仅发送新增指标。

- [ ] 先写 PNG 实际颜色像素测试、单位/日期卡片测试与顺序行为测试，运行到预期失败。
- [ ] Pillow 绘制 250 交易日双轴折线和量柱，缺失断线；复用上传/发送和临时文件清理。
- [ ] 主任务在完整 `send_daily_report` 返回或异常后再尝试新增卡片；数据不可用时显示日期和异常，不能误报当日结果。
- [ ] 运行完整 45 项原有测试及新增用例，验证预览真实可读并审查两库 diff。

## Task 4: 实际数据验收与交付

**Files:** 更新本目录运维记录、`docs/feishu-daily.md` 和部署说明。

- [ ] 接入现有运行解释器所需 stock_data 依赖，确认环境变量读取不暴露凭据；不更换社交采集运行环境。
- [ ] 实际回填四只 2023 起数据，核对最近交易日、缺口、手算比例和 PNG。
- [ ] 独立审查实现，修复重要问题并定向复测；确认定时任务调用已覆盖新增链路。
- [ ] 按用户要求先真实发送现有宝妈报告，再真实发送新增图卡，记录 relay 结果。
- [ ] 敏感信息/可疑代码检查，清理临时产物，两库完成中文 commit 和 push，报告实际截至日期。
