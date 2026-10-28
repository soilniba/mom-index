# 资料调研与数据核验

核验日期：2026-10-28。区分已确认事实、用户选定口径与尚未核实的研究归属。

## 研究资料

- 光大证券高瑞东、查惠俐 2024-12-25《A股资金结构分析框架》，[作者署名全文（12-26 发布）](https://finance.sina.com.cn/stock/stockzmt/2024-12-26/doc-ineauuyr6705046.shtml)第 2.3 节：按挂钩指数汇总 ETF 净资产，并扣除指数涨跌观察规模变化；沪深300样本是全部 26 只 ETF，分母用**自由流通市值**。因此这不是本期“四只 ETF / 流通市值”的完全同口径复刻。
- 国金证券 2025-06-25《2025年权益中期投资策略：新的循环》，[原始报告 PDF](https://pdf.dfcfw.com/pdf/H3_AP202506271698625022_1.pdf)，第 54 页选取前十大持有人中有汇金的 ETF，观察其 2022 年以来累计净申购。该集合不等于仅持有四只沪深300 ETF，也不能证明所有净申购都属于汇金。
- 未找到足以确认“ETF OI”为光大正式指标名的原始依据，也未核实用户描述的完整国金日度持仓模型公式、参数与假设。实现不能声称精确复现了这些模型。
- [上交所指标说明](https://star.sse.com.cn/aboutus/publication/monthly/explain/)：流通股数为非限售普通股数量；流通市值为价格乘流通数量的合计。自由流通量另行剔除非活跃战略持股，不能混用。
- [上交所统计月报](https://star.sse.com.cn/aboutus/publication/monthly/index/)可用于核对指数收盘、成交与流通市值；本项目生产来源仍按要求统一为 `stock_data`。

## 四只基金身份

- 510300.SH 华泰柏瑞：[上交所 ETF 信息](https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml?etfClass=03&fundid=510300&type=008)。
- 510310.SH 易方达：[上交所规模信息](https://etf.sse.com.cn/fundlist/scalelist/index.shtml?code=510310)。
- 510330.SH 华夏：[基金公司产品页](https://f.chinaamc.com/fund/510330/index.shtml)。
- 159919.SZ 嘉实：[基金公司公告](https://www.jsfund.cn/cms/Services/AttachDownLoad.jsp?id=421384)。

## 数据契约与实测

| 所需数据 | 来源与单位 | 2026-10-28 探活结果 |
| --- | --- | --- |
| 沪深300流通市值 | [`index_dailybasic.float_mv`](https://tushare.pro/document/2?doc_id=128)，元 | 000300.SH 在 2018 年及 2026 年指定窗口均有数据；官方简介未列沪深300，但示例及实测支持 |
| ETF 总份额 | [`fund_share.fd_share`](https://tushare.pro/document/2?doc_id=207)，万份 | 510300.SH 可取；长区间恰好 2,000 行，需分段 |
| 单位净值 | [`fund_nav.unit_nav`](https://tushare.pro/document/2?doc_id=119)，元 | 510300.SH 近期 7 日均有效；net_asset/total_netasset 均空，不能依赖直接净资产字段 |
| 指数点位与成交量 | [`index_daily`](https://tushare.pro/document/2?doc_id=95)，点、手 | 库中已有历史行情；vol 是成分股量，amount 为千元，不能混用 |
| ETF 份额规模新接口 | [`etf_share_size`](https://tushare.pro/document/2?doc_id=408)，万份、万元 | 账户无权限；文档称次日 08:30 左右分批更新，不适合作为本期必需入口 |

探活样本：2026-10-27 沪深300 `float_mv` 为 48,412,916,550,161.22 元。
`float_share` 与 `free_share` 是不同字段；不能将二者比例乘总市值来冒充逐证券自由流通市值。

## 接入问题与处理

- `stock_data` 是 Python 数据库，不是统一行情 HTTP 服务。其本机 LongPort HTTP 代理不提供本指标需要的历史份额/净值/市值。
- 指数估值与日线已有公开入口；基金份额/净值尚缺封装。`get_tushare_pro()` 只许探活，生产接口需先在该库补 fetcher，不能在业务库绕过数据边界。
- 运行宝妈指数的 `/home/wangr/venvs/py314` 有 Pillow 与 pytest，但尚未安装 `stock_data`，需确认兼容的运行环境接入方式。
- 完整可计算日期必须取四只基金与指数的共同交易日；四只完整历史覆盖率尚待实施阶段拉取核验。
- `fund_share/fund_nav` 无精确更新时点承诺，晚间任务必须展示最后完整日期，避免不同日数据混算。
- 原有测试基线：`/home/wangr/venvs/py314/bin/python -m pytest -q`，45 项通过；存在一条原有 Pillow 弃用提醒。
