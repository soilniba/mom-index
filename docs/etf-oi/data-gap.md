# 10月28日数据缺口核查

取证：2026-10-28北京时间22:53–23:03；实际重查stock_data公开接口仍缺四只当日份额，以及510300/159919单位净值。

## 官方事实

| 基金 | 10/28单位净值（元） | 10/28盘后总份额（万份） |
| --- | ---: | ---: |
| 510300 | 4.1603 | 3,637,758.77 |
| 510310 | 4.0430 | 2,208,124.40 |
| 510330 | 4.3389 | 1,361,454.98 |
| 159919 | 4.3437 | 尚无确认值 |

- 份额：[上交所ETF规模](https://www.sse.com.cn/market/funddata/volumn/etfvolumn/)，明确为当日清算后数据；`query.sse.com.cn/commonQuery.do` 的 `sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L`、`STAT_DATE=2026-10-28`，返回 `SEC_CODE/STAT_DATE/TOT_VOL`。10/27对照三值与Tushare一致，排除了错日。
- 净值：[华泰柏瑞](https://www.huatai-pb.com/products/zhishu/510300/)、[易方达](https://www.efunds.com.cn/en/fund/510310.shtml)、[华夏](https://www.chinaamc.com/fund/510330/index.shtml)、[嘉实历史净值](https://www.jsfund.cn/Services/cn/jsp/product/NavHistory.jsp?FundCode=159919&SiteID=1)。日期均为净值归属日，不能用页面更新日替代。

## 为什么仍不能计算10/28

[深交所基金列表](https://fund.szse.cn/marketdata/fundslist/index.html)当时的规模列明确标为10/27；页面说明T日晚间规模仅供参考，以T+1早间确认值为准。

官方 `https://www.szse.cn/api/report/ShowReport/data` 查询参数 `SHOWTYPE=JSON&CATALOGID=fund_jjgm&TABKEY=tab1&txtDm=159919&txtStart=2026-10-28&txtEnd=2026-10-28` 返回0条。10/27样本 `current_size=1,039,611.66`，单位万份。

[嘉实申赎清单](https://www.jsfund.cn/main/fund/tradelist.shtml?product_code=159919)的 `servlet/json?funcNo=742002&product_code=159919&query_date=2026-10-29` 返回 `pcf_info=[]`，场内模式 `159919_1` 同样为空。已存在的10/28清单实际前一交易日为10/27、净值4.3077，不能当10/28盘后数据。

因此沪市三只份额及两只净值为供应商同步延迟；深市159919还缺官方确认份额。补零、沿用昨日份额或错移PCF日期都会制造假数据，不能用于补齐。

## 处理

保留真实缺口与最后完整日10/27；明早9点一次性任务经stock_data重拉，最多5次、每30分钟一次，齐后重新计算、同步网页并补发图卡。正常每日更新仍重叠刷新45天，可吸收源数据修订。不为本次缺口建设多源抓取系统。
