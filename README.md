# 视频生成工作台

对接阿里云百炼（DashScope）多个视频生成模型的 Web 应用：前端选模型和参数，后端统一管理 API Key、任务轮询与用户权限，支持多轮对话持续修改。

## 功能

| 能力 | 说明 |
| --- | --- |
| 5 个模型 | wan3.0-video-prime、MiniMax/MiniMax-H3、happyhorse-1.1-t2v / -i2v / -r2v |
| 3 种生成方式 | 文生视频、图生视频（首帧）、参考生视频，界面上按模型自动切换 |
| 参数可选 | 分辨率、画面比例、时长（最长 15 秒）、水印、音频 |
| 双 Key 分组 | wan + MiniMax 共用一组 Key，happyhorse 系列共用另一组，均在 `.env` 中填写 |
| 用户管理 | 管理员可用全部模型并管理账号；普通用户仅可用 HappyHorse 系列 |
| 对话记忆 | 同一对话内可持续追加修改（“把猫换成狗”“节奏再快一点”），自动合并成完整提示词 |
| 视频预览与下载 | 对话内直接播放，可放大预览；下载走后端代理，强制保存为文件 |
| 对话管理 | 左侧列表支持重命名、删除（带二次确认） |
| 任务可靠性 | 异步提交 + 后台轮询，服务重启后自动续跟未完成任务，视频自动落盘长期保存 |

## 模型能力对照

| 模型 | 文生视频 | 图生视频 | 参考生视频 | 权限 | 分辨率 | 时长 |
| --- | :---: | :---: | :---: | --- | --- | --- |
| [wan3.0-video-prime](https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference) | ✓ | ✓ | ✓（≤10 张） | 管理员 | 480P/720P/1080P | 2-15 秒 |
| [MiniMax/MiniMax-H3](https://help.aliyun.com/zh/model-studio/minimax-video-generation-api-reference) | ✓ | ✓ | ✓（≤9 张） | 管理员 | 768P / 2K | 4-15 秒 |
| [happyhorse-1.1-t2v](https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference) | ✓ | — | — | 全部 | 480P/720P/1080P | 3-15 秒 |
| [happyhorse-1.1-i2v](https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference) | — | ✓ | — | 全部 | 480P/720P/1080P | 3-15 秒 |
| [happyhorse-1.1-r2v](https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference) | — | — | ✓（≤9 张） | 全部 | 480P/720P/1080P | 3-15 秒 |

三种生成方式的区别：

- **文生视频 (t2v)** — 只给文字提示词，模型凭空生成画面，不传任何素材。
- **图生视频 (i2v)** — 上传 1 张图作为视频首帧让画面动起来，接口字段 `media[].type = first_frame`。happyhorse-1.1-i2v 的画面比例自动跟随首帧图，**没有 ratio 参数**。
- **参考生视频 (r2v)** — 上传多张参考图，模型融合其中的人物、道具与风格；提示词里用 `[Image 1]`、`[Image 2]` 按上传顺序指名引用。happyhorse/wan 用 `reference_image`，MiniMax 用 `image_url`。

几处容易踩坑的差异（均已在后端强制校验）：

- MiniMax H3 只有 **768P 和 2K**，没有 1080P（官方档位就这两档）；文生视频时**不能用 adaptive 比例**，图生视频时比例恒为自适应、界面上不提供选择；时长最短 4 秒。
- HappyHorse 系列**不支持 adaptive 比例**，水印默认**开**；wan 与 MiniMax 水印默认关。
- wan3.0-video-prime 官方时长上限 30 秒，被系统的 `MAX_VIDEO_DURATION` 收窄到 15 秒。
- wan 与 MiniMax 官方还支持尾帧控制、参考视频、参考音频、视频编辑等，本系统暂未开放，界面的「模型能力说明」里有标注。

登录后点左侧「模型能力说明」可以在界面里查看这张对照表和每个模型的完整参数范围。

技术栈：FastAPI + SQLite + React 18 + Ant Design 5（深色主题）+ Vite，单镜像部署。

界面为深色 + 蓝紫渐变风格：底色 `#08080c`，主色 `#6d7dff`，强调元素统一用 `#3b6cff → #6d5cff → #a855f7` 渐变。配色集中在 `frontend/src/styles.css` 顶部的 CSS 变量和 `frontend/src/main.tsx` 的 ConfigProvider theme 里，改这两处即可整体换肤。

> **部署到云服务器**：完整的分步操作手册见 [DEPLOY.md](DEPLOY.md)（含域名、HTTPS 证书、备份与排错）。下面是本地快速跑通的最简步骤。

## 快速开始（Docker，推荐）

```bash
cp .env.example .env
```

编辑 `.env`，至少填写这几项：

```ini
SECRET_KEY=<随机字符串，至少32位>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<你的管理员密码>
DASHSCOPE_API_KEY_WAN=sk-xxx          # wan3.0-video-prime + MiniMax/MiniMax-H3，共用备用通道
DASHSCOPE_API_KEY_HAPPYHORSE=sk-yyy   # HappyHorse 首选通道
HAPPYHORSE_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
# HappyHorse 专用 Key 余额不足时，自动使用 DASHSCOPE_API_KEY_WAN + DASHSCOPE_BASE_URL
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com
PUBLIC_BASE_URL=http://你的服务器IP:8008
```

生成随机 SECRET_KEY：

```bash
python -c "import secrets;print(secrets.token_hex(32))"
```

启动：

```bash
docker compose up -d --build
```

浏览器打开 `http://服务器IP:8008`，用 `.env` 里的管理员账号登录。

查看日志 / 停止：

```bash
docker compose logs -f
```

```bash
docker compose down
```

### 不用 compose

```bash
docker build -t video-generate:latest .
```

```bash
docker run -d --name video-generate -p 8008:8008 --env-file .env -v $(pwd)/data:/app/data --restart unless-stopped video-generate:latest
```

## 本地开发

后端（终端 1）：

```bash
pip install -r backend/requirements.txt
```

```bash
uvicorn backend.app.main:app --reload --port 8008
```

前端（终端 2）：

```bash
cd frontend && npm install && npm run dev
```

开发时访问 `http://localhost:5173`，Vite 会把 `/api` 与 `/media` 代理到 8008 端口。

## `.env` 配置说明

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | - | JWT 签名密钥，**生产必须改** |
| `TOKEN_EXPIRE_MINUTES` | 10080 | 登录有效期（分钟） |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | admin / admin123 | 首次启动自动创建的管理员，**登录后请改密码** |
| `DASHSCOPE_API_KEY_WAN` | - | wan3.0-video-prime、MiniMax/MiniMax-H3 使用；也是 HappyHorse 余额不足时的备用 Key |
| `DASHSCOPE_API_KEY_HAPPYHORSE` | - | HappyHorse 系列首选专用 Key |
| `HAPPYHORSE_BASE_URL` | `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` | HappyHorse 首选 Token Plan 地址；余额不足时切换到共享通道 |
| `DASHSCOPE_BASE_URL` | `https://dashscope.aliyuncs.com` | Wan/MiniMax 共享通道地址，也是 HappyHorse 的备用地址 |
| `APP_DOMAIN` | 空 | 对外域名，填了它下面三项自动推导 |
| `USE_HTTPS` | true | 域名是否已配 HTTPS，影响 `PUBLIC_BASE_URL` 推导 |
| `PUBLIC_BASE_URL` | 由域名推导 | 本服务的公网地址。留空且无域名时，支持 Base64 的模型会自动改用 Base64 |
| `ALLOWED_ORIGINS` | 由域名推导 | CORS 白名单，逗号分隔。留空 = 只允许同源 |
| `TRUSTED_HOSTS` | 由域名推导 | 允许的 Host 头，逗号分隔，防 Host 头注入 |
| `BEHIND_PROXY` | false | 在反向代理后面时打开，用于取真实客户端 IP |
| `FORCE_HTTPS` | false | 强制 HTTPS 跳转 + HSTS，证书生效后再开 |
| `LOGIN_MAX_ATTEMPTS` | 8 | 连续登录失败多少次后锁定 |
| `LOGIN_LOCKOUT_SECONDS` | 300 | 锁定时长（秒） |
| `MAX_VIDEO_DURATION` | 15 | 全局时长上限（秒），超过的选项不会出现在前端 |
| `POLL_INTERVAL_SECONDS` | 8 | 轮询任务状态的间隔 |
| `POLL_TIMEOUT_SECONDS` | 1800 | 单个任务最长等待时间 |
| `DOWNLOAD_VIDEOS` | true | 生成成功后把视频存到 `data/videos` 长期保存；关掉则下载时由后端实时代理 DashScope 链接（24 小时后失效） |
| `MAX_UPLOAD_MB` | 20 | 单张参考图大小上限 |
| `MEDIA_LINK_TTL_SECONDS` | 21600 | 媒体签名链接有效期（秒），默认 6 小时 |
| `VIDEO_RETENTION_DAYS` | 7 | 视频保留天数，超期自动删除文件。0 = 不清理 |
| `CLEANUP_INTERVAL_HOURS` | 6 | 清理任务运行间隔（小时） |
| `CONTEXT_ENABLED` | true | 是否用文本模型合并多轮提示词 |
| `CONTEXT_MODEL` | qwen-plus | 用于合并提示词的文本模型 |
| `CONTEXT_API_KEY` | 空 | 留空则复用当前视频模型所属分组的 Key |
| `CONTEXT_MAX_TURNS` | 6 | 往前追溯的对话轮数 |
| `DATA_DIR` | ./data | 数据库、上传图片、视频的存放目录 |

## 配置域名

只需要填一个 `APP_DOMAIN`，其余项都会自动推导：

```ini
APP_DOMAIN=video.example.com   # 不带 http:// 和结尾的 /
USE_HTTPS=true
BEHIND_PROXY=true              # 走 Nginx / Caddy 时必须打开
FORCE_HTTPS=true               # 等证书生效后再打开
```

自动推导出来的值：

| 变量 | 推导结果 | 作用 |
| --- | --- | --- |
| `PUBLIC_BASE_URL` | `https://video.example.com` | 阿里云回源下载参考图用 |
| `ALLOWED_ORIGINS` | `https://video.example.com` | CORS 白名单 |
| `TRUSTED_HOSTS` | 该域名 + www + localhost | 防 Host 头注入 |

需要和域名不一致时（例如图片走 CDN）再单独覆盖对应变量。

### 方式一：Caddy（推荐，自动 HTTPS）

证书自动申请与续期，不用管 certbot。

1. 域名 A 记录解析到服务器公网 IP，安全组放行 80 / 443
2. `.env` 填好 `APP_DOMAIN`、`BEHIND_PROXY=true`
3. 启动：

```bash
docker compose --profile caddy up -d --build
```

配置在 [deploy/Caddyfile](deploy/Caddyfile)。证书存在 `caddy-data` 卷里，**不要删这个卷**，否则重建会重复申请触发 Let's Encrypt 限流。

### 方式二：已有 Nginx

用 [deploy/nginx.conf](deploy/nginx.conf)，改掉里面的域名和证书路径。两个关键设置别漏：

```nginx
client_max_body_size 64m;   # 要大于 MAX_UPLOAD_MB，否则上传参考图会 413
proxy_read_timeout 30m;     # 视频代理下载耗时长，默认 60s 会断
proxy_buffering off;        # 视频是流式转发，开缓冲会先落盘到 Nginx
```

证书用 certbot 申请：

```bash
certbot certonly --nginx -d video.example.com
```

用 Nginx 时建议把应用端口改成只监听本机，不直接暴露公网 —— 在 `docker-compose.yml` 里把端口映射改成 `"127.0.0.1:${PORT:-8008}:${PORT:-8008}"`。

### 图片上传与公网地址的关系

阿里云是**反向下载**图片的：请求体里给的是图片地址，DashScope 服务器自己去抓。所以本机跑时生成的 `http://localhost:8008/media/...` 它是访问不到的。

系统会自动选择提交方式，**不需要手动切换**：

| 情况 | 提交方式 |
| --- | --- |
| 配好了域名（`APP_DOMAIN` 或 `PUBLIC_BASE_URL`） | 拼成公网 URL 交给阿里云下载 |
| 没配，且模型支持 Base64 | 编码成 `data:image/xxx;base64,...` 塞进请求体 |
| 没配，且模型不支持 Base64 | 提交时直接报错，提示去配置或改用图片外链 |

| 模型 | 公网 URL | Base64 |
| --- | :-: | :-: |
| wan3.0-video-prime | ✓ | ✓ ≤20MB |
| happyhorse-1.1-i2v / -r2v | ✓ | ✓ ≤20MB |
| MiniMax/MiniMax-H3 | ✓ | ✗ 官方只接受 http(s) |

**结论**：本机开发直接上传就能用 wan 和 happyhorse；只有 MiniMax 需要一个阿里云够得着的地址。

> **不需要域名。**`PUBLIC_BASE_URL` 要的是「阿里云能访问到的地址」，填公网 IP 一样有效：
> `PUBLIC_BASE_URL=http://你的公网IP:8008`（记得安全组放行该端口）。
> 配上之后 wan 与 happyhorse 也会从 Base64 切换成 URL 方式，请求体更小、提交更快。

`PUBLIC_BASE_URL` 填成 `localhost`、`127.0.0.1`、`192.168.x.x` 等内网地址会被识别为不可用，自动回退到 Base64。

## 安全说明

已做的防护：

| 项 | 说明 |
| --- | --- |
| 登录限流 | 同账号或同 IP 连续失败 `LOGIN_MAX_ATTEMPTS` 次后锁定 `LOGIN_LOCKOUT_SECONDS` 秒 |
| 上传校验 | 校验文件头而非扩展名，分块读取并随时截断，拒绝超限文件 |
| 静态文件 | 前端兜底路由做了路径归属校验，无法读取 `frontend/dist` 目录之外的文件 |
| 响应头 | `X-Content-Type-Options: nosniff`、`X-Frame-Options`、`Referrer-Policy`；开启 `FORCE_HTTPS` 后附带 HSTS |
| CORS | 默认只允许同源；`ALLOWED_ORIGINS` 显式配置才放开 |
| Host 校验 | 配置 `APP_DOMAIN` 后自动启用，拒绝伪造 Host 的请求 |
| 重复提交 | 同一画布节点在生成中时拒绝再次运行，避免重复扣费 |
| 媒体文件 | `/media/` 下的视频与参考图需要带签名的临时链接才能访问，默认 6 小时过期 |

### 媒体文件的签名链接

`/media/uploads/` 与 `/media/videos/` 不能走 Bearer 鉴权——浏览器的 `<video src>`、`<img src>` 带不了请求头，阿里云回源下载参考图时也没有登录态。

所以改成**链接自带签名与有效期**：

```
/media/videos/8bd5....mp4?exp=1789044752&sig=79627a6f66404be642e8af46caaa2a17
```

- 签名是 `HMAC-SHA256(SECRET_KEY, 路径 + 过期时间)`，只有后端能签发
- 有效期由 `MEDIA_LINK_TTL_SECONDS` 控制，默认 6 小时
- 签名与**具体路径绑定**，换个文件名复用签名无效
- 过期后链接自动失效，即使被转发出去也访问不了

前端不再自己拼 `/media/` 路径，播放地址和缩略图地址都由后端在接口响应里签发。

两点注意：

1. **改 `SECRET_KEY` 会让此前签发的所有链接立即失效。**页面刷新后会拿到新链接，不影响使用。
2. 页面开着超过 6 小时后再点播放可能会 403，刷新页面即可。想减少这种情况就调大 `MEDIA_LINK_TTL_SECONDS`；想更严格就调小。

## 用户与权限

| 角色 | 可用模型 | 其他权限 |
| --- | --- | --- |
| 管理员 admin | 全部 5 个模型 | 用户管理（新建 / 编辑 / 停用 / 删除） |
| 普通用户 user | happyhorse-1.1-t2v / -i2v / -r2v | 仅自己的对话 |

权限在后端强制校验：普通用户即使伪造请求调用管理员模型也会被 403 拒绝。

调整某个模型开放给哪些角色，改 `backend/app/catalog.py` 里对应模型的 `allowed_roles` 即可。

## 视频预览与下载

生成成功后视频直接在对话里播放，卡片只保留播放器和右下角的「下载到本地」。视频右上角的「放大预览」会弹出大窗自动播放，方便看清细节。

「下载到本地」走后端接口 `GET /api/messages/{id}/download`：

- 视频已落盘（`DOWNLOAD_VIDEOS=true`）时直接返回本地文件；
- 未落盘时由后端实时代理 DashScope 链接转发；
- 统一带 `Content-Disposition: attachment`，浏览器会真正保存成文件，而不是新开标签页播放；
- 文件名取自对话标题，例如 `清晨老街早餐铺-80fd5804.mp4`；
- 接口校验登录态与归属，别人的视频返回 404。

## 多轮对话记忆

同一个对话里的每次输入都会结合历史记录，合成一条完整提示词再提交给模型：

1. 第 1 轮：`一只小猫在月光下的屋顶上奔跑，城市霓虹闪烁，电影级画质`
2. 第 2 轮：`把猫换成柴犬` → 实际提交的是保留了月光、霓虹、电影级画质等全部细节、只把主体换成柴犬的完整提示词
3. 第 3 轮：`镜头再拉远一点` → 继续在第 2 轮结果上叠加

合成结果可以在每条回复的“查看本次实际提交的提示词”里展开确认。想抛开历史重新开始，关掉输入框上方的「延续上下文」开关，或新建一个对话。

`CONTEXT_ENABLED=false`（或文本模型调用失败）时会自动降级为规则拼接，不影响视频生成。

## 新增 / 调整模型

编辑 `backend/app/catalog.py`，在 `MODELS` 列表里加一项：

```python
VideoModel(
    id="模型ID",
    label="显示名称",
    description="一句话说明",
    key_group="wan",              # 用哪一组 API Key：wan / happyhorse
    allowed_roles=ROLE_ALL,       # 或 ROLE_ADMIN_ONLY
    capabilities=[
        Capability(T2V, "只给提示词，不传任何素材"),
        Capability(I2V, "以一张图作为视频首帧", "first_frame", 1, 1),
        Capability(R2V, "传入参考图", "reference_image", 1, 9),
    ],
    resolutions=["720P", "1080P"],
    default_resolution="1080P",
    ratios=["16:9", "9:16"],
    default_ratio="16:9",
    duration_min=3,
    duration_max=15,
    supports_watermark=True,
    watermark_default=True,
    doc_url="https://help.aliyun.com/...",
    notes=["官方支持但本系统未开放的能力写在这里"],
)
```

`Capability` 的第 3 个参数就是提交给接口的 `media[].type`，第 4、5 个是图片数量下限和上限。
某个生成方式不需要选比例时（如 happyhorse-1.1-i2v）传 `supports_ratio=False`。

前端的下拉选项由这份配置自动生成，无需改动前端代码。各模型实际支持的参数请以百炼官方文档为准。

## 目录结构

```
├── backend/                    Python 后端
│   ├── app/
│   │   ├── main.py             应用入口、静态资源、启动初始化
│   │   ├── config.py           .env 配置
│   │   ├── catalog.py          视频模型目录（模型/参数/权限）
│   │   ├── dashscope.py        DashScope 接口封装
│   │   ├── tasks.py            后台提交与轮询
│   │   ├── prompt_context.py   多轮对话提示词合成
│   │   ├── media_resolver.py   参考图解析（公网 URL / Base64 自动选择）
│   │   ├── ratelimit.py        登录失败限流
│   │   ├── canvas_graph.py     画布图校验（类型 / 重复输入 / 环检测）
│   │   ├── canvas_executor.py  画布节点 -> 生成任务
│   │   ├── models.py           数据库表
│   │   └── routers/            auth / users / catalog / uploads / conversations / canvases
│   └── tests/                  后端测试
├── frontend/                   React 前端（构建产物在 frontend/dist）
│   └── src/
│       ├── pages/              Login / Studio / Canvas / Users
│       └── components/         Composer / MessageList / ModelInfo
├── docs/                       设计与技术文档
├── deploy/                     Caddyfile / nginx.conf 反向代理配置
├── scripts/                    独立开发辅助脚本
├── artifacts/                  发布归档（不参与运行和镜像构建）
├── data/                       SQLite + 上传图片 + 生成视频
├── Dockerfile
└── docker-compose.yml
```

`scripts/generate_video.py` 是独立的 DashScope 验证脚本，读取项目根目录 `.env`
中的 `DASHSCOPE_API_KEY_WAN`，输出视频保存到 `data/videos/legacy/`。

## 测试

```bash
pip install pytest
```

```bash
pytest
```

覆盖画布图校验、画布接口、以及本次 review 修掉的几个安全问题（路径穿越、上传校验、登录限流、域名推导）。

## 数据与备份

所有持久化数据都在 `data/` 目录：

- `data/app.db` — 用户、对话、消息记录
- `data/uploads/` — 上传的参考图
- `data/videos/` — 生成成功后落盘的视频

备份整个 `data/` 目录即可。DashScope 返回的视频链接通常 24 小时后失效，`DOWNLOAD_VIDEOS=true` 保证过期后仍能在页面里播放和下载。

### 视频自动清理

视频是最占磁盘的东西（1080P 15 秒可达上百 MB），默认**保留 7 天**后自动删除。

清理做两件事：

1. **过期清理** —— 超过 `VIDEO_RETENTION_DAYS` 的视频删文件，但**保留数据库记录**。对话历史、提示词、参数都还在，界面上会显示「视频已过期清理」并提示可重新生成，而不是一个坏掉的播放器。
2. **孤儿清理** —— 删掉没有任何记录指向的视频文件。删除对话或画布时只级联删了数据行，文件会残留在磁盘上；另外还会清理中断下载留下的 `.part` 文件。

清理在服务启动时跑一次（补上停机期间欠的），之后每 `CLEANUP_INTERVAL_HOURS` 小时跑一次。刚落盘不到 1 小时的文件有宽限期，不会被误删。

不想自动清理就设 `VIDEO_RETENTION_DAYS=0`。

手动立刻跑一次：

```bash
docker compose exec video-generate python -c "from backend.app.cleanup import sweep; print(sweep())"
```

## 常见问题

**提示 `InvalidApiKey`** — `.env` 里对应分组的 Key 没填或填错。注意 wan/MiniMax 与 happyhorse 用的是两个不同的 Key。

**上传的图片阿里云取不到** — wan / happyhorse 会自动改用 Base64 直传，本机也能用；只有 MiniMax 不支持 Base64，需要配 `PUBLIC_BASE_URL` 或改用公网图片外链。详见上文「关于图片上传」。

**任务一直在“生成中”** — 视频生成通常 1–5 分钟，1080P 15 秒会更久。超过 `POLL_TIMEOUT_SECONDS`（默认 30 分钟）会自动标记失败。可以关掉页面稍后回来看，任务在后端继续跑。

**重启服务后任务丢了吗** — 不会。已提交的任务在启动时会自动恢复轮询。
