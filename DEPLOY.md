# 生产部署与运维

本文对应当前的单镜像、单机部署方式：React 前端在镜像构建阶段编译，FastAPI 在运行阶段同时提供 SPA、API 和签名媒体文件。默认数据存储为 SQLite 和宿主机 `data/` 目录。

## 部署前准备

| 项目 | 建议 |
| --- | --- |
| 操作系统 | Ubuntu 22.04+ 或 Debian 12+ |
| 服务器 | 至少 2 核 4 GB；按视频保留策略准备磁盘 |
| 软件 | Docker Engine + Docker Compose v2 |
| 视频服务 | Wan/MiniMax Key；HappyHorse Key（只使用对应模型时可按需配置） |
| 域名 | 推荐；国内服务器需提前完成备案 |
| 网络 | 直连方式放行应用端口；Caddy 方式放行 80/443 |

Docker 镜像已包含 Python 3.11、FFmpeg/FFprobe 和编译后的前端，不需要在宿主机另装这些运行时。

## 1. 获取项目

使用 Git：

```bash
git clone <repository-url> VideoGenerate
cd VideoGenerate
```

也可以上传项目压缩包，但不要包含本地 `.env`、`data/`、`.venv/`、`frontend/node_modules/` 或真实密钥。

## 2. 创建环境配置

```bash
cp .env.example .env
```

生成签名密钥：

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

最小配置：

```ini
PORT=8008
SECRET_KEY=<上一步生成的随机值>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<首次登录密码>

DASHSCOPE_API_KEY_WAN=sk-xxx
DASHSCOPE_API_KEY_HAPPYHORSE=sk-yyy
HAPPYHORSE_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com
```

说明：

- `DASHSCOPE_API_KEY_WAN` 用于 Wan 3.0、MiniMax H3，也是 HappyHorse 余额不足时的备用通道。
- `DASHSCOPE_API_KEY_HAPPYHORSE` 是 HappyHorse 首选通道。只有明确的余额/额度错误才会回退，普通请求错误不会自动换 Key 重试。
- 管理员账号只在数据库没有管理员时按环境变量创建。以后修改 `.env` 不会覆盖数据库中已经存在的密码；请在界面中修改密码。
- `.env` 不应提交到版本库，也不要放入发布压缩包或镜像。

## 3. 选择访问方式

### 方式 A：公网 IP 和应用端口

适合临时验证或尚未准备域名的环境：

```ini
PUBLIC_BASE_URL=http://<公网 IP>:8008
BEHIND_PROXY=false
FORCE_HTTPS=false
```

启动：

```bash
docker compose up -d --build
```

需要在云安全组和防火墙放行 TCP 8008。浏览器访问 `http://<公网 IP>:8008`。

`PUBLIC_BASE_URL` 必须是 DashScope 能访问的地址。若留空，Wan/HappyHorse 图片会尝试 Base64 直传，但 MiniMax 图片输入不可用。

### 方式 B：域名 + Caddy 自动 HTTPS（推荐）

先完成以下准备：

1. 域名 A/AAAA 记录指向服务器。
2. 云安全组和防火墙放行 TCP 80、TCP/UDP 443。
3. 确认其他服务没有占用 80/443。

`.env`：

```ini
APP_DOMAIN=video.example.com
USE_HTTPS=true
BEHIND_PROXY=true
FORCE_HTTPS=false
```

启动应用与 Caddy：

```bash
docker compose --profile caddy up -d --build
```

查看证书和代理日志：

```bash
docker compose logs --tail 200 caddy
```

确认 `https://video.example.com` 可以访问后，再设置：

```ini
FORCE_HTTPS=true
```

应用配置在进程启动时读取，修改 `.env` 后需要重建容器配置：

```bash
docker compose --profile caddy up -d
```

Caddy 的证书保存在 `caddy-data` 命名卷中。不要随意执行 `docker compose down -v`，否则会删除证书卷并可能触发证书签发限流。

若只允许代理访问应用，可把 `docker-compose.yml` 的应用端口映射改为：

```yaml
ports:
  - "127.0.0.1:${PORT:-8008}:${PORT:-8008}"
```

### 方式 C：已有 Nginx

参考 `deploy/nginx.conf`，至少保留：

```nginx
client_max_body_size 64m;
proxy_read_timeout 30m;
proxy_buffering off;
```

并设置：

```ini
APP_DOMAIN=video.example.com
USE_HTTPS=true
BEHIND_PROXY=true
FORCE_HTTPS=false
```

证书和 HTTPS 跳转确认无误后再开启 `FORCE_HTTPS=true`。代理必须正确传递 `Host`、`X-Forwarded-For` 和 `X-Forwarded-Proto`。

## 4. 配置创作智能体

Agent 使用 OpenAI Chat Completions 兼容接口。若 `AGENT_API_KEY` 留空，单模型兼容配置会复用 `DASHSCOPE_API_KEY_WAN`。

### 单模型

```ini
AGENT_ENABLED=true
AGENT_PROVIDER=openai
AGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENT_MODEL=qwen-plus
AGENT_API_KEY=sk-xxx
AGENT_FALLBACK_RULES=true
```

### 多模型

目录中只写密钥对应的环境变量名：

```ini
AGENT_MODELS_JSON=[{"id":"qwen-plus","name":"Qwen Plus","provider":"openai","base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1","api_key_env":"AGENT_QWEN_KEY"}]
AGENT_DEFAULT_MODEL=qwen-plus
AGENT_QWEN_KEY=sk-xxx
```

若不配置 Provider：

- `AGENT_FALLBACK_RULES=true`：保留基础规则规划和部分规则画布修改能力。
- `AGENT_FALLBACK_RULES=false`：Agent 接口返回 503，手动画布仍可使用。

生产环境还应根据预算设置：

```ini
AGENT_MAX_AUTO_COST=20
AGENT_MAX_AUTO_NODES=3
AGENT_DAILY_COST_LIMIT=200
AGENT_DAILY_TOKEN_LIMIT=200000
AGENT_RUN_CONCURRENCY=2
AGENT_TASK_MAX_ATTEMPTS=2
```

本地成本只是执行闸门，不是供应商账单的权威数据。应同时在供应商控制台配置预算和告警。

## 5. 启动验证

查看容器：

```bash
docker compose ps
```

查看应用日志：

```bash
docker compose logs --tail 200 video-generate
```

检查健康接口：

```bash
curl -fsS http://127.0.0.1:8008/api/health
```

使用 Caddy 且应用端口未直接暴露时，改为：

```bash
curl -fsS https://video.example.com/api/health
```

首次验收建议按顺序执行：

1. 使用管理员账号登录并立刻修改密码。
2. 创建普通用户，确认普通用户只看到 HappyHorse 模型。
3. 创建画布，运行一个短的 T2V 镜头。
4. 上传一张图并验证 I2V；MiniMax 必须同时验证公网素材回源。
5. 创建两个短镜头，连接输出节点并验证 FFmpeg 合成。
6. 打开 Agent，验证手动确认计划；启用自动模式前先使用较低预算。
7. 刷新页面，确认画布、运行进度和视频仍能恢复。

## 数据目录

Compose 把宿主机 `./data` 挂载为容器 `/app/data`：

```text
data/
├─ app.db          # 用户、技能、画布、消息、Agent 运行和事件
├─ uploads/        # 本地上传素材
├─ videos/         # 生成镜头与最终合成视频
└─ caddy-logs/     # 使用 Caddy profile 时的访问日志
```

`VIDEO_RETENTION_DAYS` 只清理过期视频文件，数据库记录仍会保留并标记过期。`0` 或负数关闭自动清理。上传素材不会按该设置自动删除。

## 备份与恢复

### 一致性备份

为确保 SQLite 和文件快照一致，先短暂停止应用容器：

```bash
docker compose stop video-generate
```

备份整个 `data/` 和当前 `.env`（密钥文件应单独加密保存）：

```bash
tar -czf videogen-data-$(date +%F-%H%M).tar.gz data
```

然后恢复服务：

```bash
docker compose start video-generate
```

### 恢复

1. 停止应用。
2. 将备份恢复为项目下的 `data/`。
3. 恢复匹配的 `.env`，尤其是 `SECRET_KEY`；更换该值会使旧 JWT 和旧媒体签名失效。
4. 启动服务并检查日志、画布、素材和视频。

不要只备份 `app.db` 而遗漏 `uploads/` 和 `videos/`，否则记录仍在但媒体文件不可用。

## 更新与回滚

更新前先备份。拉取代码后重新构建：

```bash
git pull --ff-only
docker compose --profile caddy up -d --build
```

应用启动时会创建新表并执行仓库内的兼容迁移。迁移目标是已有 SQLite，但仍应先备份生产数据。

回滚时需要同时使用旧版本代码/镜像和与其兼容的数据备份。不要假设新结构的数据库一定能被更旧版本安全读取。

## 日常运维

```bash
docker compose ps
docker compose logs --tail 200 -f video-generate
docker compose restart video-generate
docker compose --profile caddy up -d
docker compose --profile caddy down
```

查看磁盘：

```bash
du -sh data data/videos data/uploads
df -h
```

任务可靠性说明：

- 应用启动会恢复未完成的 DashScope 任务和状态为 queued/running 的 Agent 运行。
- 运行输入在启动时冻结，画布后续编辑不会改变已提交任务。
- “取消”会阻止尚未提交的后续任务；已经提交给供应商的任务不保证可远程撤销。
- 单机部署的事件订阅和调度依赖应用进程；不要直接横向扩容多个副本。

## 安全检查清单

- [ ] 已修改 `SECRET_KEY`、管理员用户名和初始密码。
- [ ] `.env`、数据库、上传文件和视频未进入 Git 或公开制品。
- [ ] HTTPS 正常后才开启 `FORCE_HTTPS`。
- [ ] 代理场景启用了 `BEHIND_PROXY=true`，并且只有可信代理能访问应用端口。
- [ ] 设置了 `APP_DOMAIN`/`TRUSTED_HOSTS`，没有使用宽泛的 CORS 来源。
- [ ] 云安全组只开放必要端口。
- [ ] DashScope 与 Agent Provider 均设置了独立预算和额度告警。
- [ ] `VIDEO_RETENTION_DAYS`、备份和磁盘告警符合业务保留要求。
- [ ] 定期更新基础镜像和 Python/npm 依赖，并重新运行测试。

## 常见问题

### 图片上传成功，但生成时报“外部无法访问”

检查 `PUBLIC_BASE_URL` 是否真能从公网访问。`localhost`、`127.0.0.1`、`10.x`、`172.16-31.x`、`192.168.x` 等地址不能供 DashScope 回源。MiniMax 不支持图片 Base64，必须使用公网 URL。

### Caddy 无法申请证书

依次检查域名解析、80/443 安全组、宿主机防火墙、端口占用和域名备案。避免频繁删除 Caddy 数据卷或反复申请，以免触发 CA 限流。

### 上传返回 413

同时检查应用的 `MAX_UPLOAD_MB`、模型槽位自身上限以及代理的 `client_max_body_size`。三者中最小值决定实际限制。

### 视频能生成但不能拼接

查看应用日志中的 FFmpeg 错误以及 `data/videos` 的空间和写权限。最终合成要求片段能下载到本地；即使 `DOWNLOAD_VIDEOS=false`，画布合成仍会按需下载。

### Agent 不可用或只返回基础计划

检查 `AGENT_ENABLED`、Provider URL、模型名和 Key。`AGENT_FALLBACK_RULES=true` 时，Provider 未配置会自动使用规则规划器，这是预期降级行为。

### 修改 `.env` 后没有生效

配置只在应用启动时读取。执行 `docker compose up -d` 让 Compose 重新创建使用新环境的容器；单纯刷新浏览器不会加载新配置。
