# Creator Agent Studio 部署手册

> 把系统装到一台真实机器上：裸机、容器或 Windows 任选，升级与回滚路径清晰，部署前有一道静态核验。
> **读者**：部署方与运维。**回答**：选哪种方案、每种怎么装与升级、哪些配置点必须先想清楚。
> **不负责**：界面操作与运行时配置（→ [`USER_GUIDE.md`](USER_GUIDE.md)）、架构与角色定义（→ [`creator.md`](creator.md)）、上线前必须由人做的事（→ [`README.md`](README.md)「上线前人工事项」）。

---

## 1. 选哪种（决策表）

| 场景 | 建议方案 | 仓库内清单 / 样例 |
|---|---|---|
| 个人电脑试跑 / 开发 | 直接 `python -m app.main` | [`README.md`](README.md)「快速开始」 |
| 一台 Linux 服务器长期运行 | **裸机 + systemd**，Nginx 或 Caddy 反向代理 | `deploy/systemd/` `deploy/nginx/` `deploy/caddy/` |
| 习惯 Docker / 想一键带 Jaeger | **docker compose**（含受限网络构建变体） | [`docker-compose.yml`](docker-compose.yml) `Dockerfile`×2 |
| Kubernetes 集群 | **k8s 清单**（当前单副本，见 §7） | `deploy/k8s.yaml` |
| Windows 主机、开机自启 | **任务计划**（系统内置，零下载） | `deploy/windows/` |

所有方案共用同一份应用与同一套环境变量；配置项清单的唯一归属是 [`USER_GUIDE.md`](USER_GUIDE.md) §6.2 与 [`.env.example`](.env.example)。

---

## 2. 三个必须先想清楚的点

**① 监听地址（`HOST`）**

- 默认 `127.0.0.1`：只有本机能访问，**配合反向代理**使用（推荐架构，应用不直接暴露）。
- 需要让别人直连（容器端口发布 / 局域网）时必须监听非 loopback（`0.0.0.0`）：
  端口发布的流量 DNAT 到容器 eth0，绑 loopback 时**容器内探针照常通过、宿主机却连不上**。
  仓库内 Dockerfile / compose / k8s 已显式声明，无需手动加；裸机直连则改 `.env` 的 `HOST`。
- **暴露到本机以外前，务必设置 `CREATOR_API_TOKENS`**（决定租户隔离边界，为空则完全不鉴权）。

**② 数据目录**

- 默认 `<项目根>/data`；容器内为 `/data`（必须挂卷）；systemd 单元固定为 `/var/lib/creator`。
- 任务、黑板、记忆库、评估历史、检查点、trace 全在这里 —— 备份策略围绕它做；跨版本升级前建议先拷一份。
- 目录必须可写：写不了时 SQLite 检查点会**静默降级**（界面顶栏出现「断点续跑不可用」，看 `/api/health` 的 `checkpointer.error`）。

**③ 静态页面来自 `dist/`**

后端只在 `dist/` 存在时挂载静态托管。**源码部署（非镜像）必须先 `npm ci && npm run build`**，
否则 `GET /` 是 404 —— 这是「后端能跑但打开是白屏/404」的最常见原因。

---

## 3. Linux 裸机（systemd）

前置：systemd 发行版（Debian / Ubuntu 等）；Python 3.12；Node ≥ 22.6（仅构建前端需要，装完可不用）。

```bash
# 1) 系统用户与代码目录（代码与数据分离）
sudo useradd --system --create-home --shell /usr/sbin/nologin creator
sudo mkdir -p /opt/creator-agent-studio && sudo chown creator:creator /opt/creator-agent-studio
sudo -u creator git clone <你的仓库地址> /opt/creator-agent-studio   # 或 rsync/scp 上传源码

cd /opt/creator-agent-studio

# 2) Python 依赖（venv 路径与单元文件约定一致）
sudo -u creator python3.12 -m venv .venv
sudo -u creator .venv/bin/pip install -r requirements.txt

# 3) 前端产物（后端静态托管所需）
sudo -u creator npm ci
sudo -u creator npm run build

# 4) 配置（可缺省；KEY=VALUE，与 .env 同语法）
sudo mkdir -p /etc/creator
sudo cp .env.example /etc/creator/creator.env     # 然后把生产值（令牌等）改掉
sudo chown root:creator /etc/creator/creator.env && sudo chmod 640 /etc/creator/creator.env

# 5) 安装并启动
sudo cp deploy/systemd/creator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now creator

# 6) 验证
systemctl status creator                         # active (running)
curl -fsS http://127.0.0.1:8787/api/health       # 免鉴权探针
journalctl -u creator -f                         # 看日志
```

单元文件要点（完整注释见 `deploy/systemd/creator.service`）：

- 数据目录 `/var/lib/creator` 由 `StateDirectory=creator` 自动创建并授权，升级 / 回滚都不触碰；
- `ProtectSystem=strict` 全盘只读，仅 `ReadWritePaths` 放行数据目录 —— 该一致性已进 `check_deploy.py` 断言；
- 配置只从 `/etc/creator/creator.env` 读（进程环境变量，优先级高于项目目录内 `.env`）。

应用只监听 127.0.0.1，**对外入口是下一步的反向代理**。

---

## 4. 反向代理（Nginx / Caddy）

二选一；样例已在仓库内，装完只需改域名并调整路径。

**Nginx**（`deploy/nginx/creator.conf`）：

```bash
sudo cp deploy/nginx/creator.conf /etc/nginx/conf.d/creator.conf
sudo nginx -t && sudo systemctl reload nginx
# TLS：sudo certbot --nginx -d 你的域名   （一键签发并自动补 443 段）
```

**Caddy**（`deploy/caddy/Caddyfile`，自动 HTTPS，无需 certbot）：

```bash
sudo cp deploy/caddy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

两条不能省的 SSE 设置（`check_deploy.py` 静态核验，改配置时不要删）：

- **关闭响应缓冲**（Nginx `proxy_buffering off` / Caddy `flush_interval -1`）——缓冲会把事件「攒着不发」，前端看起来像卡死；
- **读超时 ≥ 600s** —— 创作期间事件间隔可达分钟级，默认 60s 会周期性断流。

---

## 5. 容器化（Docker / compose / k8s）

```bash
docker compose up -d --build          # 应用 + Jaeger；加 --profile pg 连 PostgreSQL + Redis
docker compose up -d --build app      # 只要应用
DOCKERFILE=Dockerfile.offline docker compose up -d --build   # Docker Hub 不可达时（受限网络）
```

- 数据在命名卷 `creator-data`（容器内 `/data`）；**不挂卷则容器重建即丢数据**。
- 镜像与清单已按「容器端口发布」显式设 `HOST=0.0.0.0`；真实入口是宿主 `8787`，健康检查走容器内 loopback ——
  探针为免鉴权 `/api/health`，开 `CREATOR_API_TOKENS` 后照样探活。
- **k8s**：`kubectl apply -f deploy/k8s.yaml`。副本数固定 1（`Recreate`）——默认 file 存储多副本会状态分裂，
  要横向扩展先按 §7 切到 pg 存储；Ingress 已含 SSE 所需的关缓冲与长超时注解。
- 设计取舍（为什么单副本、镜像占位名、Secret / ConfigMap 分离）见 `docker-compose.yml` 与 `deploy/k8s.yaml` 文件头注释。

不用 compose 的单容器：

```bash
docker build -t creator-agent-studio:latest .
docker run -d --name creator -p 8787:8787 -v creator-data:/data \
  -e CREATOR_API_TOKENS=acme:tok_aaa,beta:tok_bbb creator-agent-studio:latest
```

---

## 6. Windows（任务计划）

前置：Python 3.12；Node ≥ 22.6（构建前端）；**管理员身份运行 PowerShell**。

```powershell
# 1) 依赖与前端产物
pip install -r requirements.txt
npm ci; npm run build

# 2) 安装并启动（默认探测 .venv 或 PATH 的 python；也可 -PythonExe 指定全路径）
powershell -ExecutionPolicy Bypass -File deploy\windows\install-task.ps1 install

# 3) 状态查询 / 卸载（数据保留）
powershell -ExecutionPolicy Bypass -File deploy\windows\install-task.ps1 status
powershell -ExecutionPolicy Bypass -File deploy\windows\install-task.ps1 uninstall
```

- 任务以 SYSTEM 运行、开机自启、崩溃后每分钟重试 3 次；日志在 `data\logs\service.log`（超 10MB 轮转为 `.old`）。
- 应用默认只监听 127.0.0.1（仅本机）。**局域网直连**：项目根 `.env` 设 `HOST=0.0.0.0`，放行防火墙后重启任务：

```powershell
New-NetFirewallRule -DisplayName "Creator 8787" -Direction Inbound -Protocol TCP -LocalPort 8787 -Action Allow
```

  暴露前务必设置 `CREATOR_API_TOKENS`。
- 需要 HTTPS / 域名时，可安装 Caddy 原生 Windows 版，配置与 `deploy/caddy/Caddyfile` 相同（改成 `http://` 形式或配好 DNS 自动签证书）。

---

## 7. 存储模式与副本数

| 模式 | 依赖 | 副本数 | 适用 |
|---|---|---|---|
| `file`（默认） | 无（JSON + SQLite） | **必须 1** | 全部单机方案 |
| `pg` | PostgreSQL + Redis | 可多副本 | k8s / 多实例横向扩展 |

切换到 pg 的步骤、实体映射与不变量见 [`storage_contract.md`](storage_contract.md)；
存量迁移用 `python scripts/pg_migrate.py`（幂等，先 `--dry-run`）。
多副本部署前先完成切换，再调整 k8s 的 `replicas` 与托管库连接串注入。

---

## 8. 升级与回滚

数据在数据目录、代码在代码目录，两者独立 —— 升级 = 替换代码 + 重启（数据不受影响）：

```bash
# systemd
cd /opt/creator-agent-studio
sudo -u creator git pull
sudo -u creator .venv/bin/pip install -r requirements.txt
sudo -u creator npm ci && sudo -u creator npm run build
sudo systemctl restart creator
```

```bash
# compose（重新构建镜像；数据在命名卷里）
docker compose up -d --build
```

回滚 = 切回旧 commit / 旧镜像 tag 后同样重启。停服时优雅退出会自动刷盘，但**跨版本升级前建议先拷一份数据目录**。

---

## 9. 部署前清单自检

```bash
python scripts/check_deploy.py   # 不需要 Docker daemon，退出码即结论
```

静态核对：Dockerfile 的 COPY 路径、compose / k8s / systemd / 反代 / Windows 脚本引用的文件与环境变量、
探针免鉴权、容器监听地址（`HOST`）、k8s 单副本约束、SSE 关键配置。

---

## 10. 上线前人工事项

见 [`README.md`](README.md)「上线前人工事项」（权威副本）：设置生产令牌、吊销并更换联调密钥、非中文市场法务复核等 ——
这些是代码无法替代、必须由人完成的事。