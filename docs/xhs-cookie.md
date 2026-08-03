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

## cookie_server 上传路由（备用通道）

knowworld 的 cookie_server（`POST /update-cookie`）已支持 `xiaohongshu.com` 域名路由：
cookie 写入本文件 XHS_COOKIE，后台自动跑 xhs_cookie_check.py 验证并通知 Telegram/飞书。
适用于以后有新的 cookie 获取渠道（如浏览器插件自动推送）时复用。

## 注意事项

- Cookie 等同密码：`web_session` 泄露等于账号泄露，勿提交 git（env 文件在 git 外）
- Cookie 有效期 7-30 天；网页端登录后不能退出登录，退出即失效
- 风控：访问频繁会触发「安全限制」，验证脚本退出码 2 即被拦截
