# 视频预览卡顿与播放器体验优化方案

> 状态：待实施
> 范围：生成工作台主界面的视频预览、媒体分发和预览播放器体验
> 目标：降低视频首帧等待和播放卡顿，减少历史消息对页面性能的影响，并用 Ant Design 统一播放卡片与操作界面

---

## 1. 结论摘要

当前卡顿很可能由多个因素叠加造成：主界面历史消息会持续挂载视频元素，点击放大后又创建第二个播放器；弹窗播放器使用 `preload="auto"` 和 `autoPlay`，会在打开时主动缓冲；后端媒体响应是否完整支持 HTTP Range 还需要通过实际请求确认；另外，生成视频的分辨率、码率和编码格式可能不适合直接作为在线播放版本。

代码层面已经确认的主要问题：

1. [MessageList.tsx](../frontend/src/components/MessageList.tsx) 中每条成功消息都会渲染一个带 `preload="metadata"` 的 `<video>`。
2. 点击放大预览后，`Modal` 内会再次渲染同一地址的 `<video>`。
3. 弹窗视频使用 `preload="auto"` 和 `autoPlay`，打开预览时会触发更激进的加载和解码。
4. 主界面没有封面优先和按需挂载策略，历史消息越多，页面中的媒体节点越多。
5. [backend/app/routers/media.py](../backend/app/routers/media.py) 通过 `FileResponse` 提供视频，但当前代码没有显式实现 Range 响应，需要用实际 HTTP 请求确认浏览器是否能得到 `206 Partial Content`。

编码兼容性、实际文件码率和 Range 行为不能只靠静态代码推断，必须在运行环境中通过 Network 面板、响应头和视频文件分析确认。

---

## 2. 现状与影响

### 2.1 前端播放链路

当前主界面的播放链路为：

```text
消息接口返回 video_src
        |
        v
每条成功消息直接渲染 <video preload="metadata">
        |
        v
点击“放大预览”
        |
        v
Modal 内再次渲染 <video preload="auto" autoPlay>
```

该结构可能产生以下影响：

- 多条历史消息同时创建媒体资源和解码上下文；
- 同一个视频在列表和弹窗中同时存在两个播放器；
- 打开弹窗时主动缓冲较多数据，首帧和播放启动竞争网络与解码资源；
- 切换、拖动进度时可能产生额外请求；
- 页面历史较长时，内存和 CPU 占用随消息数量增长。

### 2.2 后端媒体链路

生成完成后，后台任务会把 DashScope 返回的视频下载到 `data/videos`，然后通过签名地址提供给浏览器。当前播放地址优先使用本地视频，后端媒体路由负责校验签名并返回文件。

需要重点确认：

- 是否返回 `206 Partial Content`；
- 是否返回 `Accept-Ranges: bytes`；
- Range 请求是否正确返回 `Content-Range` 和 `Content-Length`；
- 反向代理是否覆盖或破坏了 Range 响应；
- 浏览器是否能在拖动进度时按区间读取，而不是重新获取整个文件。

---

## 3. 诊断与验证

### 3.1 浏览器 Network 检查

对一个已生成视频执行以下检查：

1. 打开主界面，确认未点击预览时是否已经发起视频请求。
2. 点击预览，记录从点击到 `playing` 的时间。
3. 检查视频请求状态是否为 `206 Partial Content`。
4. 检查响应是否包含：

```text
Accept-Ranges: bytes
Content-Range: bytes start-end/total
Content-Length: <range length>
Content-Type: video/mp4
```

5. 拖动进度条，确认是否产生合理的 Range 请求。
6. 查看是否有 `stalled`、`waiting`、重复完整下载或 5xx 响应。

### 3.2 后端 Range 检查

在获得签名视频地址后执行：

```powershell
curl.exe -I "<SIGNED_VIDEO_URL>"
curl.exe -H "Range: bytes=0-1023" -i "<SIGNED_VIDEO_URL>"
```

期望第二个请求返回 `206`，且 `Content-Range` 与 `Content-Length` 正确。若仍返回 `200` 或返回完整文件，应实现专用的 Range 响应，或确认当前 Starlette、反向代理和静态文件服务的配置。

### 3.3 视频文件分析

对实际生成文件记录以下信息：

- 文件大小；
- 分辨率；
- 总时长；
- 视频码率；
- 视频编码；
- 音频编码；
- MP4 元数据位置是否支持快速启动。

如果服务器安装了 FFmpeg，可使用：

```powershell
ffprobe -v error -show_format -show_streams "data/videos/<FILE>.mp4"
```

### 3.4 前端播放指标

预览播放器应记录以下事件，形成最小性能基线：

```text
loadedmetadata  元数据读取完成
canplay         具备开始播放的条件
playing         实际开始播放
waiting         播放等待
stalled         媒体数据获取停滞
error           播放失败
```

核心指标：

```text
首帧时间 = playing 时间 - 用户点击预览时间
卡顿次数 = waiting 事件次数
卡顿总时长 = waiting 到 playing 的累计时长
```

---

## 4. 解决方案

### 4.1 P0：只保留一个活动播放器

主界面默认显示视频封面或占位区域，不直接挂载可播放视频。点击封面后打开预览弹窗，弹窗内只创建一个 `<video>` 实例；关闭弹窗时销毁该实例。

推荐结构：

```text
VideoPreviewCard
  - 视频封面
  - 播放图标
  - 分辨率、时长、模型信息
  - 预览操作
  - 下载操作

VideoPreviewModal
  - 唯一的 video 元素
  - 播放状态和加载状态
  - 全屏操作
  - 下载操作
```

这样可以避免历史消息数量直接转化为视频节点数量，也避免列表播放器和弹窗播放器同时解码同一文件。

### 4.2 P0：调整加载策略

预览播放器默认使用：

```tsx
<video
  ref={videoRef}
  src={preview.video_src}
  preload="metadata"
  playsInline
  controls
/>
```

打开弹窗时不使用 `preload="auto"`。如果产品必须自动播放，应在 `canplay` 之后再调用 `play()`，并处理浏览器自动播放策略拒绝的情况。

### 4.3 P0：保证 HTTP Range 播放

视频接口应支持浏览器的分段读取。至少需要保证：

```http
Accept-Ranges: bytes
Content-Type: video/mp4
Content-Length: <range length>
Content-Range: bytes <start>-<end>/<total>
```

带 Range 请求时应返回 `206 Partial Content`。本地视频优先由 Nginx、Caddy 或对象存储直接分发，FastAPI 负责签发签名地址和权限校验，减少应用进程代理大文件的开销。

媒体响应可以增加：

```python
{
    "Accept-Ranges": "bytes",
    "Cache-Control": "private, max-age=21600",
    "X-Content-Type-Options": "nosniff",
}
```

缓存时间不能超过签名链接有效期。

### 4.4 P1：生成封面和媒体元信息

后台保存视频后提取封面和元信息，并在消息接口返回：

```json
{
  "video_src": "...",
  "poster_src": "...",
  "duration": 8,
  "width": 1280,
  "height": 720,
  "size": 18432000
}
```

建议在消息模型中增加或等价保存：

```text
video_width
video_height
video_duration
video_size
video_codec
audio_codec
video_poster
preview_video
```

未打开预览时直接使用 `poster_src`，避免浏览器为了显示列表内容而解码视频。

### 4.5 P1：生成在线播放版本

如果实际文件分析发现码率过高、编码兼容性不佳或 MP4 元数据位于文件末尾，后台在视频下载完成后异步生成预览文件。原始文件用于下载，预览文件用于播放。

推荐参数：

```bash
ffmpeg -i input.mp4 \
  -c:v libx264 \
  -preset veryfast \
  -crf 23 \
  -pix_fmt yuv420p \
  -movflags +faststart \
  -c:a aac \
  -b:a 128k \
  preview.mp4
```

关键点：

- 使用 H.264 和 AAC，覆盖主流浏览器硬件解码能力；
- 使用 `yuv420p`，提高兼容性；
- 使用 `faststart`，把 MP4 元数据移到文件开头；
- 预览版本控制码率和分辨率；
- 不在用户播放请求期间实时转码。

当前系统视频时长上限较短，暂不建议第一阶段引入 HLS/DASH。先修复 Range、重复播放器、加载策略和编码兼容性，复杂度更低。

---

## 5. Ant Design 播放界面方案

Ant Design 没有完整的视频播放器组件。推荐继续使用原生 `<video>` 负责播放能力，用 Ant Design 负责卡片、弹窗、按钮、加载和错误状态。

### 5.1 组件选型

| 场景 | 组件 |
| --- | --- |
| 视频结果卡片 | `Card` |
| 大尺寸预览 | `Modal` |
| 播放、全屏、下载 | `Button` |
| 图标说明 | `Tooltip` |
| 操作排列 | `Space` 或 `Flex` |
| 加载占位 | `Skeleton` |
| 加载进度 | `Progress` |
| 播放失败、视频过期 | `Alert` |
| 播放速度、画质等菜单 | `Dropdown` |
| 分辨率、时长、模型 | `Typography.Text`、`Tag` |

### 5.2 主界面卡片

主界面卡片建议包含：


```text
┌────────────────────────────┐
│                            │
│          视频封面           │
│            ▶               │
│                            │
├────────────────────────────┤
│  1280×720 · 8 秒           │
│  Wan 3.0 Video Prime       │
├────────────────────────────┤
│       预览          下载    │
└────────────────────────────┘
```

实现原则：

- 视频区域固定为稳定的 16:9 比例；
- 封面上使用 `PlayCircleFilled`；
- 预览、全屏使用 `ExpandOutlined` 或 `FullscreenOutlined`；
- 下载使用 `DownloadOutlined`；
- 图标按钮使用 `Tooltip`；
- 下载是明确命令，可以保留“下载”文字；
- 不再使用自定义的角落文字按钮覆盖在原生视频控制条上；
- 加载时显示 `Skeleton`，失败时显示 `Alert`；
- 卡片圆角控制在 8px 左右，与现有 Ant Design 风格保持一致。

### 5.3 预览弹窗

弹窗使用 `Modal`，播放器内部只有一个视频实例：

```tsx
<Modal
  open={Boolean(preview)}
  title="视频预览"
  width={960}
  centered
  footer={null}
  destroyOnHidden
>
  <Card className="video-player-card">
    <video
      ref={videoRef}
      src={preview.video_src}
      preload="metadata"
      playsInline
      controls
    />
    <Flex justify="space-between" align="center">
      <Typography.Text type="secondary">
        1280×720 · 8 秒
      </Typography.Text>
      <Space>
        <Tooltip title="全屏">
          <Button
            type="text"
            icon={<FullscreenOutlined />}
            onClick={enterFullscreen}
          />
        </Tooltip>
        <Button
          type="primary"
          icon={<DownloadOutlined />}
          onClick={download}
        >
          下载
        </Button>
      </Space>
    </Flex>
  </Card>
</Modal>
```

第一阶段保留浏览器原生播放控制条，避免为自定义进度条、音量、全屏和播放状态引入额外维护成本。等播放性能稳定后，再根据产品需要增加统一风格的自定义控制条。

---

## 6. 实施顺序

### P0：直接降低卡顿

- 主列表改为封面卡片，不直接挂载视频；
- 弹窗只保留一个视频播放器；
- 预览播放器使用 `preload="metadata"`；
- 默认关闭 `autoPlay`，或等待 `canplay` 后再播放；
- 验证并补齐 HTTP Range；
- 增加 `Accept-Ranges`、`Content-Length`、正确的 `Content-Type`；
- 记录首帧、等待和停滞指标。

### P1：完善播放体验

- 后台提取视频元信息；
- 生成视频封面；
- 使用 `Card + Modal + Button + Tooltip + Flex` 统一界面；
- 增加 `Skeleton`、`Alert` 和加载失败后的重试；
- 限制同时加载的媒体数量；
- 生产环境优先由反向代理或对象存储直接分发视频。

### P2：优化编码和分发

- 后台生成 H.264/AAC 预览版本；
- 使用 `yuv420p` 和 `faststart`；
- 原始视频用于下载，预览视频用于播放；
- 根据真实访问量评估 HLS/DASH。

---

## 7. 验收标准

改造完成后，至少满足以下条件：

- 未打开预览时，主界面不会为每条历史消息创建可播放视频实例；
- 打开预览时，页面同时最多存在一个活动播放器；
- 视频请求支持 `206 Partial Content` 和正确的 Range 响应；
- 拖动进度条不会重新下载完整文件；
- 首帧通常在 1-2 秒内出现，具体基线以部署网络和文件规格为准；
- 播放过程中无持续性的 `waiting` 或 `stalled`；
- 1080P 视频在 Chrome、Edge 等主流浏览器中可以连续播放；
- 预览卡片和弹窗在窄屏下不溢出；
- 播放、全屏、下载按钮使用 Ant Design 图标和 Tooltip；
- 视频过期、加载失败、权限错误均有明确提示；
- 原始视频下载能力不因播放优化而受影响。

---

## 8. 风险与取舍

| 方案 | 收益 | 风险或成本 |
| --- | --- | --- |
| 单一活动播放器 | 立即降低页面媒体开销 | 需要改变当前列表交互 |
| HTTP Range | 改善大文件播放和拖动 | 需要检查应用服务器和反向代理 |
| 视频封面 | 减少初始加载 | 需要增加封面提取和存储 |
| 预览转码 | 提高编码兼容性和播放稳定性 | 增加 CPU、磁盘和后台任务复杂度 |
| HLS/DASH | 适合长视频和自适应码率 | 对当前短视频场景偏重，增加部署复杂度 |

推荐先完成 P0，再依据真实的 Range 响应和视频编码数据决定是否进入 P1/P2。不要在播放请求期间实时转码。
