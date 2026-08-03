# 每日飞书推送（宝妈指数群）

每天 08:00 定时任务（`mom-index-collect.timer` → `pipeline.py`）跑完后，
自动向飞书「宝妈指数」群推送当日最新指数。

## 推送内容

每次推送 5 张 markdown 卡片：

| # | 卡片 | 内容 |
|---|------|------|
| 1-4 | 各板块卡片 | 指数、解析（信号）、宝妈买入/卖出、买卖比（追涨/平衡/恐慌）、小白帖数及占比 |
| 5 | 今日最"小白"的帖子 | 跨板块按小白分数降序前 8 条：badge、板块、买卖意图、标题、推理、信号、原帖链接 |

卡片格式模仿 `frontend/dashboard.html` 网页卡片，板块 emoji：
📈纳斯达克 / 🥇黄金 / 🔌CPO通信 / 💾半导体。

## 原帖链接来源

- 股吧帖子：采集器直接带 `url` 字段（`https://guba.eastmoney.com/...`）
- 小红书帖子：由 note_id 拼接 `https://www.xiaohongshu.com/explore/{note_id}`

链接经 `analyzer/llm_analyzer.py` 的 `_post_url()` 存入 `AnalysisResult.url`，
随 `top_newbie_posts` 写入 `data/dashboard_data.json`（字段名 `url`）。

## 发送方式

复用本机 feishu-bot relay（`http://127.0.0.1:8410/relay/send/markdown`），
token 从环境变量 `FEISHU_BOT_TOKEN` 读取，无则 source `~/.config/mom-index/env`。

目标群：`oc_47ca0e5ecb7d20cf524ba7e9899023c1`（宝妈指数）。

## 手动触发

```bash
python3 scripts/notify_daily.py            # 用 data/ 下最新 dashboard 数据重发
python3 scripts/notify_daily.py data
```

## 失败处理

发送失败/异常只打 stderr 日志，不中断 `pipeline.py` 主流程
（与 `notify_feishu.py` 告警同一模式）。relay 不可用、token 缺失时静默跳过。
