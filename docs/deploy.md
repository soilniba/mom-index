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
| mom-index-collect.timer / .service | `~/.config/systemd/user/` | 每日自动采集：每天 00:00 UTC（北京 08:00），`Persistent=true` 错过补跑 |
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

页面数据来自 `frontend/data/dashboard_data.json`（git 已提交的快照）。**每日自动刷新**：`mom-index-collect.timer` 每天 00:00 UTC（北京 08:00）跑 pipeline.py，写完 `data/` 自动同步到 `frontend/data/`，静态文件无需重启服务，线上立即生效。周末也会跑（股吧无新帖，结果与周五相同，同日记录被覆盖，幂等无害）。

**自动采集只含股吧**：定时任务通过 `Environment=MOM_INDEX_NO_XHS=1` 强制跳过小红书——即使 `~/.config/mom-index/env` 里有 key 也不会每天烧钱。**rnote 小红书需手动调用**：

```bash
cd ~/projects/mom-index
set -a && . ~/.config/mom-index/env && set +a   # 加载 RNODE_API_KEY（不设 MOM_INDEX_NO_XHS）
python pipeline.py                               # 含真实小红书
```

> 费用：14 次搜索请求 × $0.01 ≈ $0.14/次（4+4+3+3 关键词）。充值后先在后台确认计费粒度。
> 跳过条件：无 RNODE_API_KEY 或设置了 MOM_INDEX_NO_XHS=1，两者都不产出模拟数据。

常用命令：

```bash
systemctl --user list-timers mom-index-collect*
journalctl --user -u mom-index-collect -f
```

> 注意：系统时区是 UTC。timer 的 OnCalendar 按系统本地时区（UTC）写，北京 08:00 = UTC 00:00，改时间别改错时区。
> 每日自动采集只更新本地/线上数据文件，**不自动 git commit**——仓库数据快照保持手动提交。

## 踩坑记录

- **nginx 插入位置**：配置文件有 80(301) 和 443 两个 server 块、两个 `location / {`。插入新 location 必须锚定 443 块（文件末尾的 catch-all），否则会插进 80 块，https 下新路径静默落到 try_files 兜底返回默认欢迎页。本机验证用 `rfind` 锚定最后一个 `location / {`。
