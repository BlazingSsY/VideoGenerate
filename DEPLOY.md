# 部署到云服务器

从零把系统跑到公网域名上。按顺序做，每步都有验证方法。

**预计耗时**：30~45 分钟（不含域名备案）

---

## 你需要准备的东西

| 项 | 说明 |
| --- | --- |
| 云服务器 | 2 核 4G 起，Ubuntu 22.04 / Debian 12 均可。视频文件占空间，磁盘建议 ≥ 50G |
| 域名 | 已解析到服务器公网 IP。**国内服务器必须先完成 ICP 备案**，否则 80/443 会被拦 |
| 两组 DashScope API Key | wan + MiniMax 一组，happyhorse 一组 |
| 安全组 | 放行 **80、443**。8008 不用对外开 |

> 备案没下来也能先用：跳过域名，直接用 `http://公网IP:8008` 访问。功能都能用，只是 MiniMax 的图片上传不可用（它要求公网 http(s) 图片地址），wan 和 happyhorse 会自动改用 Base64 直传。

---

## 第 1 步：装 Docker

登录服务器后执行：

```bash
curl -fsSL https://get.docker.com | sh
```

国内服务器换成阿里云镜像源会快很多：

```bash
curl -fsSL https://get.docker.com | sh -s -- --mirror Aliyun
```

验证：

```bash
docker --version && docker compose version
```

两条都有版本号输出就算成功。

---

## 第 2 步：上传代码

在**你自己的电脑**上，把项目打包（排除本地数据和依赖）：

```bash
tar --exclude=node_modules --exclude=.venv --exclude=data --exclude=.git --exclude=dist -czf videogen.tar.gz -C /path/to VideoGenerate
```

传到服务器：

```bash
scp videogen.tar.gz root@你的服务器IP:/opt/
```

在**服务器**上解压：

```bash
cd /opt && tar -xzf videogen.tar.gz && cd VideoGenerate
```

> 用 Git 的话更简单：服务器上直接 `git clone` 你的仓库即可。注意 `.env` 不在仓库里（已被 `.gitignore` 排除），需要单独创建。

---

## 第 3 步：写配置文件

```bash
cp .env.example .env
```

先生成一个密钥：

```bash
python3 -c "import secrets;print(secrets.token_hex(32))"
```

然后编辑 `.env`：

```bash
nano .env
```

**必须改的五项**：

```ini
# 粘贴上一步生成的 64 位字符串
SECRET_KEY=把上面生成的粘到这里

# 管理员账号，第一次启动时自动创建
ADMIN_USERNAME=admin
ADMIN_PASSWORD=换成你自己的强密码

# 两组 API Key
DASHSCOPE_API_KEY_WAN=sk-xxxxxxxx
DASHSCOPE_API_KEY_HAPPYHORSE=sk-yyyyyyyy

# 你的域名，不带 http:// 和结尾的 /
APP_DOMAIN=video.example.com
BEHIND_PROXY=true
```

`FORCE_HTTPS` **先保持 false**，等证书签发成功后第 6 步再打开。

> 填了 `APP_DOMAIN` 之后，`PUBLIC_BASE_URL`、`ALLOWED_ORIGINS`、`TRUSTED_HOSTS` 会自动推导出来，不用手填。

---

## 第 4 步：启动

```bash
docker compose --profile caddy up -d --build
```

第一次会构建镜像（编译前端 + 装 Python 依赖），大约 3~5 分钟。

看日志确认：

```bash
docker compose logs -f
```

看到这几行就正常了：

```
INFO  已创建默认管理员账号：admin
INFO  CORS 允许来源：['https://video.example.com']
INFO  允许的 Host：['video.example.com', ...]
INFO  Application startup complete.
```

按 `Ctrl+C` 退出日志（不会停服务）。

**如果启动时有 WARNING**，对照处理：

| 日志 | 含义 |
| --- | --- |
| `未配置 wan 分组的 API Key` | `.env` 里 Key 没填或填错 |
| `SECRET_KEY 过短或仍为默认值` | 第 3 步的密钥没粘进去 |
| `管理员仍在使用默认密码` | `ADMIN_PASSWORD` 还是 `admin123`，赶紧改 |

---

## 第 5 步：验证证书

Caddy 会自动向 Let's Encrypt 申请证书，需要 30 秒到 2 分钟。

```bash
docker compose logs caddy | grep -i certificate
```

看到 `certificate obtained successfully` 就成功了。

浏览器打开 `https://你的域名`，应该看到登录页，地址栏有锁标。

**证书申请失败的排查顺序**：

1. 域名解析生效了吗 —— `dig +short 你的域名`，返回的 IP 要和服务器一致
2. 80 端口通吗 —— 从别的机器 `curl -I http://你的域名`
3. 安全组放行 80 和 443 了吗（云控制台里配，不是服务器防火墙）
4. 国内服务器：域名备案下来了吗

> Let's Encrypt 对同一域名有失败次数限制（每小时 5 次）。**排查期间不要反复重启 Caddy**，否则会被限流锁一小时。

---

## 第 6 步：打开强制 HTTPS

确认 https 能访问后再做这步：

```bash
nano .env
```

改成：

```ini
FORCE_HTTPS=true
```

重启应用容器：

```bash
docker compose up -d
```

现在访问 http 会自动跳到 https，并带上 HSTS 头。

---

## 第 7 步：登录并创建用户

1. 打开 `https://你的域名`，用 `.env` 里的管理员账号登录
2. 右上角头像 → **修改密码**（即使 `.env` 里已经改过，也建议再改一次，避免密码留在文件里）
3. 右上角管理员菜单 → **用户管理** → 新建用户

| 角色 | 可用模型 |
| --- | --- |
| 管理员 | 全部 5 个模型 + 用户管理 |
| 普通用户 | 仅 HappyHorse 系列（t2v / i2v / r2v） |

---

## 第 8 步：跑通一次生成

在「生成工作台」里：

1. 选 **HappyHorse 1.1 T2V**，1080P，5 秒
2. 输入一句提示词，点「生成视频」
3. 等 1~5 分钟

出片就说明全链路通了。再试一次**图生视频**（上传一张图），验证图片上传和阿里云回源也正常。

---

## 日常运维

### 常用命令

启动 / 停止 / 重启：

```bash
docker compose --profile caddy up -d
```

```bash
docker compose --profile caddy down
```

看日志（最近 200 行）：

```bash
docker compose logs --tail 200 -f video-generate
```

### 更新代码

重新上传代码后：

```bash
docker compose --profile caddy up -d --build
```

数据在 `./data` 里，重建容器不会丢。

### 备份

所有数据都在 `data/` 目录：

```
data/app.db       用户、对话、画布、生成记录
data/uploads/     上传的参考图
data/videos/      生成的视频（占空间的大头）
```

打包备份：

```bash
tar -czf backup-$(date +%F).tar.gz data/
```

建议加个每天自动备份的定时任务：

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * cd /opt/VideoGenerate && tar -czf /opt/backups/vg-\$(date +\%F).tar.gz data/") | crontab -
```

### 磁盘清理

视频默认**保留 7 天**后自动删除，不需要你手动管。改保留期：

```ini
VIDEO_RETENTION_DAYS=7     # 改成你想要的天数，0 = 不清理
CLEANUP_INTERVAL_HOURS=6   # 多久扫一次
```

改完 `docker compose up -d` 生效。

清理时**只删视频文件，数据库记录保留**——对话历史和提示词都还在，界面上会显示「视频已过期清理」，可以直接重新生成。同时会清掉删除对话后残留的孤儿文件和中断下载的 `.part` 文件。

查看当前占用：

```bash
du -sh data/videos
```

想立刻清理一次（不等下一轮）：

```bash
docker compose exec video-generate python -c "from backend.app.cleanup import sweep; print(sweep())"
```

会输出类似 `{'expired': 3, 'orphans': 1, 'freed_bytes': 251658240}`。

磁盘特别紧张的话，还可以设 `DOWNLOAD_VIDEOS=false` 让视频不落盘——但阿里云的原始链接 24 小时后失效，之后视频就彻底没了，一般不建议。

---

## 常见问题

**打不开页面**

按顺序排查：

```bash
docker compose ps
```

容器是 `Up` 状态吗？不是就看 `docker compose logs`。

```bash
curl -I http://127.0.0.1:8008/api/health
```

服务器本机能通吗？能通说明是 Caddy 或安全组的问题，不能通说明是应用本身的问题。

**提示 `InvalidApiKey`**

`.env` 里对应分组的 Key 没填或填错。注意 wan/MiniMax 和 happyhorse 用的是**两个不同的 Key**。改完要重启：`docker compose up -d`。

**上传参考图返回 413**

图片超过了 `MAX_UPLOAD_MB`（默认 20MB）。如果图片本身不大，那就是反向代理的限制——Caddy 配置里已设 64MB；用 Nginx 的话检查 `client_max_body_size`。

**视频播不了，控制台报 403**

媒体链接带 6 小时有效期。页面开太久了，刷新即可。想延长就调大 `.env` 里的 `MEDIA_LINK_TTL_SECONDS`。

另外，**改过 `SECRET_KEY` 会让所有已签发的链接立即失效**，同样刷新页面解决。

**任务一直卡在「生成中」**

视频生成通常 1~5 分钟，1080P 15 秒会更久。超过 `POLL_TIMEOUT_SECONDS`（默认 30 分钟）会自动标记失败。可以关掉页面稍后回来看——任务在后端继续跑，服务重启也会自动恢复轮询。

**改了 `.env` 不生效**

配置在启动时读取，必须重启容器：

```bash
docker compose up -d
```

---

## 不用 Docker 的部署方式

不推荐，但如果你的环境有限制：

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
```

前端产物已经构建在 `dist/`，直接用即可。要自己重新构建：

```bash
cd frontend && npm install && npm run build
```

启动（生产环境请配 systemd，别用 nohup）：

```bash
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8008 --proxy-headers --forwarded-allow-ips='*'
```

前面仍然要挂 Nginx 或 Caddy 处理 HTTPS，配置见 [deploy/nginx.conf](deploy/nginx.conf)。

systemd 服务文件示例，存到 `/etc/systemd/system/videogen.service`：

```ini
[Unit]
Description=Video Generate Workbench
After=network.target

[Service]
WorkingDirectory=/opt/VideoGenerate
ExecStart=/opt/VideoGenerate/.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8008 --proxy-headers --forwarded-allow-ips=*
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now videogen
```

---

## 上线前对照检查

- [ ] `SECRET_KEY` 换成了随机 64 位字符串
- [ ] `ADMIN_PASSWORD` 不是 `admin123`，且登录后又改过一次
- [ ] 两组 API Key 都填了，且分组没搞混
- [ ] `APP_DOMAIN` 填了，`BEHIND_PROXY=true`
- [ ] 证书签发成功，`FORCE_HTTPS=true` 已打开
- [ ] 安全组只放行 80/443，8008 没有对外暴露
- [ ] 跑通了一次文生视频和一次图生视频
- [ ] 配了 `data/` 目录的定时备份
