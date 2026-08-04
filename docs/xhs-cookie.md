# 小红书 Cookie：获取、存储与自动续期

## 存储位置

```bash
~/.config/mom-index/env   # 与 RNODE_API_KEY 同文件，变量名 XHS_COOKIE
```

格式：`export XHS_COOKIE='gid=...; web_session=...; id_token=...'`（浏览器 Cookie 头格式）。

## 获取方式（手动，一次性的）

**不能用 iOS 捷径**：小红书核心登录 cookie `web_session` 是 HttpOnly，iOS Safari 捷径的
`document.cookie` 读不到（浏览器安全模型，无法绕过）。捷径方案只适用于非 HttpOnly 登录
cookie 的站点（如点点/七麦）。

正确获取（PC Chrome + Cookie-Editor 插件）：

1. PC Chrome 打开 https://www.xiaohongshu.com 并登录（登录后不能退出，退出即失效）
2. 安装 Cookie-Editor 插件 → 点击图标 → Export → 复制 Header String
3. 粘贴写入 env：`export XHS_COOKIE='<复制的字符串>'`
   （必须包含 `web_session`，否则无登录态）
4. 验证：`python3 collectors/xhs_cookie_check.py`（退出码 0 = 有效）

## 自动续期（cookie-set 随时更新）

`collectors/xhs_cookie_check.py` 验证流程：

1. 从 env 读 XHS_COOKIE，Playwright 隐身参数访问小红书首页
2. 判定登录态：localStorage `RWP_LOGIN_TOKEN` 或侧边栏「我」入口（`user.side-bar-component`）
3. **验证通过后，把服务端最新下发的 cookie（含新 web_session）序列化写回 env** ——
   每次刷新页面时 cookie-set 的变化自动落盘，实现续期

退出码：0=有效（已写回），1=未设置，2=无效/风控，3=异常

## 采集

`collectors/xhs_playwright.py` 用登录态免费采集（替代付费 rnote API）：

```bash
python3 collectors/xhs_playwright.py   # 10 关键词 × 8 条 → data/xhs_posts.json
```

原理：Playwright 隐身访问搜索页，**截获前端自己的 search/notes API 响应**
（签名由前端生成，无需实现 x-s）。采集完成后自动把服务端最新 cookie 写回 env 续期。
单次 ~80 条（10 关键词 × 8 条，4 板块去重后），无费用；采集 0 条时不覆盖旧数据。

## cookie_server 上传路由（备用通道）

knowworld 的 cookie_server（`POST /update-cookie`）已支持 `xiaohongshu.com` 域名路由：
cookie 写入本文件 XHS_COOKIE，后台自动跑 xhs_cookie_check.py 验证并通知 Telegram/飞书。
适用于以后有新的 cookie 获取渠道（如浏览器插件自动推送）时复用。

## 注意事项

- Cookie 等同密码：`web_session` 泄露等于账号泄露，勿提交 git（env 文件在 git 外）
- Cookie 有效期 7-30 天；网页端登录后不能退出登录，退出即失效
- 风控：访问频繁会触发「安全限制」，验证脚本退出码 2 即被拦截
- **自动告警**：cookie 未设置/失效/风控、采集 0 条、pipeline 异常都会自动发飞书「瞎报错」群
  （经本机 feishu-bot relay，`scripts/notify_feishu.py`）
- 续期只更新服务端变更的字段（`merge_cookies`），不覆盖 env 中未变化的字段

## 搜索页"安全验证"拦截踩坑（实为频率限流）

现象：`xhs_playwright.py` 采集时搜索页 title 变「安全验证」，搜索 API 不发、
采集 0 条，但 `xhs_cookie_check.py` 却显示登录态有效（首页正常）。

**实际拦截页面正文是「请求太频繁，请一分钟后再试」——是频率限流，不是
二维码/图形验证**。页面里的 `redcaptcha/v2/qr/*` 请求是登录弹窗组件的预加载，
与拦截无关，不要被误导。

经验（2026-08-03 实测）：

1. **拦截后不要频繁重试**：连续探测只会加重限流，应立即停止，等待至少
   5-10 分钟再重试（页面提示 1 分钟，实测多次探测后需更久）。
2. **直接 `goto` 搜索 URL 仍是高危信号**：真人流程是首页 → 点搜索框 → 输入 →
   回车；直接导航 `search_result?keyword=...` 更易触发限流。
3. **无需指纹伪装/图像识别/破解**：不是图形验证码，等冷却即恢复。
4. **恢复手段**：停止探测 → 冷却 10 分钟+ → 重试；仍不行则 Chrome +
   Cookie-Editor 重新导出 XHS_COOKIE（同「获取方式」）。

## 扫码重新登录（xhs_relogin）

`collectors/xhs_relogin.py`：无 cookie 上下文打开小红书，登录二维码截图发飞书
「瞎报错」群等扫码，扫码成功后把新 cookie 写回 env（仅覆盖 XHS_COOKIE 字段）。

**触发方式**（飞书 bot）：在「瞎报错」群引用 bot 发的二维码/失效告警消息说
"重发一下/过期了"，或 @bot"重新登录小红书"。

**退出码**：0=扫码成功（cookie 已写回）、1=超时未扫码、2=异常。
**超时**：二维码每 60s 刷新重发，总时限 5 分钟；成功前绝不触碰 env 旧 cookie。

踩坑速查（详见 feishu-bot docs/xhs-relogin-tool.md 真机校准记录）：

- 登录弹窗 `.login-container` 偶发不自动出现（风控），点击 `.login-btn:visible`
  后等 2s 重试、上限 30s
- 二维码元素 `img.qrcode-img`（128x128）；截图只取弹窗左半（`page.screenshot`
  clip），`locator.screenshot` 不支持 clip
- 通知文本换行用真 `\n`，`\\n` 会显示为字面量
- 连续多次无头访问会触发风控，两次验证间隔建议 1 分钟以上
