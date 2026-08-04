# 微博数据源（TikHub API）

## 为什么接入

- 微博是散户小白密度高的平台，且**返回完整正文**（股吧/小红书搜索卡片只有标题），LLM 语义分类的输入质量更高
- 价格是全平台最低档：**$0.001/次**（抖音/快手/小红书搜索都是 $0.01/次）

## API 信息

| 项 | 值 |
|----|----|
| 端点 | `GET /api/v1/weibo/web_v2/fetch_advanced_search` |
| 价格 | $0.001/次，**无折扣、无免费额度**（需充值，余额 $0.001 起扣） |
| 限流 | 10/second（每日 12 次请求远够） |
| 认证 | `Authorization: Bearer <TIKHUB_API_KEY>` |
| 代理 | 本机 mihomo（`127.0.0.1:7890`），`api.tikhub.io` 国内被墙 |

## 配置

`TIKHUB_API_KEY` 从 `~/.config/mom-index/env` 读取（定时任务 systemd 环境无 .bashrc，必须放这里；交互 shell 里 .bashrc 也有，采集器两者都认）。

## 采集逻辑

- 复用板块关键词（`SEARCH_KEYWORDS`，与股吧/小红书一致）
- `timescope=custom:当天0点:当天23点` — 定时任务 23:30 跑，覆盖全天帖子，时间精确
- 返回 `parsed_data.results`（已解析结构），按 `weibo_id` 去重
- `publish_time` 三种格式解析为绝对时间戳（`parse_publish_time`，见 tests/test_weibo_collector.py）：
  - `"58分钟前"` / `"2小时前"` / `"刚刚"` / `"昨天"` — 近期帖
  - `"今天07:32"` — 当天
  - `"08月03日 11:46"` — 今年内前几天（未来日期回退去年，与股吧 `_fmt_date` 边界一致）

## 标准化字段

`weibo_id, title(正文前100字), content(完整正文), platform="weibo", author, likes/comments_count(interaction), url(https 补全), publish_time(原文), published_at(时间戳)`

注意：**高级搜索不返回作者粉丝数**（`author_followers=0`）；无 IP 归属地。

## 分析链路

微博有完整正文 → 与小红书一样走 **LLM 语义分类**（`analyzer/llm_analyzer.analyze_all` 中 `llm_platforms = {"xiaohongshu", "weibo"}`）；A/B 对比日志仅小红书参与（校准口径不变）。LLM 失败自动回退关键词规则。

## 踩坑记录

- **超时**：TikHub 偶发慢响应，timeout 需 30s（20s 会 Read timed out）
- **App 综合搜索**（`weibo/app/fetch_search_all`，$0.001 可用免费额度）返回复杂卡片流（card_type 嵌套），解析成本高，弃用；高级搜索结构干净
- **费用核对**：非 200 响应不扣费；余额可在 `get_user_info` 查询（余额 $5 够跑一年）
- 当日量少（板块 6-26 条/日），指数样本偏小，观察期数据积累后评估是否放宽 timescope 窗口
