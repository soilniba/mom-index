# 部署指南

## 相关文档

- 小红书 Cookie 获取/续期：[xhs-cookie.md](xhs-cookie.md)

## 访问地址

- 看板：https://sh.gjol.vip/mom/（自动跳转 dashboard.html）

## 链路架构

```
浏览器 → sh.gjol.vip:443 (docker nginx)
       → location /mom/ → proxy_pass http://172.17.0.1:8765/
       → frps (服务器, 8765)
       → frpc (本机 minipc, 用户级)
       → 本机 python http.server 8765 (frontend/)
```

## 本机组件（minipc）

| 组件 | 配置 | 说明 |
|------|------|------|
| mom-index-web.service | `~/.config/systemd/user/mom-index-web.service` | python3 http.server，`WorkingDirectory=frontend/`，端口 8765 |
| mom-index-frpc.service | `~/.config/systemd/user/mom-index-frpc.service` | 用户级 frpc，配置 `~/.config/mom-index/frpc.toml`（从系统 frpc 模板派生，仅含 mom-index 代理） |
| mom-index-collect.timer / .service | `~/.config/systemd/user/` | 每日自动采集：每天 15:30 UTC（北京 23:30，覆盖全天帖子），`Persistent=true` 错过补跑 |
| frpc 代理 | name=`mom-index-web`，local 127.0.0.1:8765 → remote 8765 | 与 cookie-server/renming 同模式 |

常用命令：

```bash
systemctl --user status mom-index-web mom-index-frpc
systemctl --user restart mom-index-web mom-index-frpc
journalctl --user -u mom-index-web -f
```

两个服务均 enable，且用户 Linger=yes，重启后自启。

## 服务器组件（sh.gjol.vip）

- nginx 跑在 docker 容器 `stock-t-nginx`，配置挂载自宿主机 `/opt/stock_t_helper_v2/app/deploy/nginx/conf.d/sh.gjol.vip.conf`。
- `/mom/` 反代条目（与 /renming/ 同模式）：

```nginx
location /mom/ {
    proxy_pass http://172.17.0.1:8765/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Accept-Encoding "";
}
```

> `proxy_pass` 带尾斜杠会剥掉 `/mom/` 前缀，frontend 内的相对路径引用（`data/dashboard_data.json`）不受影响。

修改配置流程：先 `cp sh.gjol.vip.conf sh.gjol.vip.conf.bak.$(date +%Y%m%d%H%M%S)`，改完 `docker exec stock-t-nginx nginx -t && docker exec stock-t-nginx nginx -s reload`。

## 数据刷新

页面数据来自 `frontend/data/dashboard_data.json`（本地生成，gitignore 不入库）。**每日自动刷新**：`mom-index-collect.timer` 每天 15:30 UTC（北京 23:30）跑 pipeline.py，写完 `data/` 自动同步到 `frontend/data/`，静态文件无需重启服务，线上立即生效。周末也会跑（股吧无新帖，结果与周五相同，同日记录被覆盖，幂等无害）。

**自动采集含股吧 + 小红书（Playwright 免费采集）**：定时任务直接跑 `python pipeline.py`。
小红书通过 `~/.config/mom-index/env` 的 XHS_COOKIE 登录态免费采集（原理见 [xhs-cookie.md](xhs-cookie.md)），
采集后自动续期 cookie；无 cookie 或设置 `MOM_INDEX_NO_XHS=1` 时自动跳过（不产出数据）。

> cookie 有效期 7-30 天：过期后定时任务的小红书部分会静默跳过，重新导出写入 env 即可恢复（见 xhs-cookie.md）。
> rnote API（付费 $0.14/次）已降为备用方案：`set -a && . ~/.config/mom-index/env && set +a && python -c "from collectors.xhs_collector import collect_all; print(len(collect_all()))"`。

常用命令：

```bash
systemctl --user list-timers mom-index-collect*
journalctl --user -u mom-index-collect -f
```

> 注意：系统时区是 UTC。timer 的 OnCalendar 按系统本地时区（UTC）写，北京 23:30 = UTC 15:30，改时间别改错时区。
> 每日自动采集只更新本地/线上数据文件。数据文件（`data/*.json`、`frontend/data/*.json`）已加入 .gitignore 不入 git：内容含小红书分享链接 xsec_token，提交会触发 GitGuardian 高熵误报（本地 .gitguardian.yaml 的 ignored_detectors 对服务器端扫描无效）。

## 踩坑记录

- **nginx 插入位置**：配置文件有 80(301) 和 443 两个 server 块、两个 `location / {`。插入新 location 必须锚定 443 块（文件末尾的 catch-all），否则会插进 80 块，https 下新路径静默落到 try_files 兜底返回默认欢迎页。本机验证用 `rfind` 锚定最后一个 `location / {`。
