# video-fetcher

多平台视频获取工具，基于 **yt-dlp + ffmpeg**，封装了 B站 / YouTube / X(Twitter) / 通用站点的最佳下载实践。

## 环境要求

| 工具 | 版本 | 安装方式 |
|------|------|----------|
| Python | >= 3.9 | https://python.org |
| yt-dlp | >= 2024 | `pip install yt-dlp` |
| ffmpeg | >= 6.0 | `winget install Gyan.FFmpeg` |

验证安装：
```bash
yt-dlp --version
ffmpeg -version
```

## 快速上手

```bash
# B站
python fetch.py "https://www.bilibili.com/video/BV1xx411c7mD" -p bilibili

# YouTube（默认 android 客户端，无需 cookies 也能下载）
python fetch.py "https://www.youtube.com/watch?v=mcTAHffEkIw" -p youtube

# X / Twitter
python fetch.py "https://x.com/ErenChenAI/status/2065141531082645722" -p twitter

# 任意站点
python fetch.py "https://example.com/video.mp4" -p generic -o ./videos
```

## 配置说明 (`config.json`)

```json
{
  "output_dir": "E:/工作/work2/downloads",
  "cookies_from_browser": "chrome",
  "platforms": {
    "bilibili": { "format": "bestvideo+bestaudio/best" },
    "youtube":  { "format": "bestvideo[height<=2160]+bestaudio/best" }
  }
}
```

### cookies 配置

B站、YouTube、X/Twitter 需要登录态才能下载高清/受限内容。两种方式：

1. **从浏览器读取**（推荐）：`"cookies_from_browser": "chrome"`
   - 支持：chrome, firefox, edge, opera, brave
   - 确保浏览器中已登录对应站点

2. **cookies 文件**：`"cookies_file": "E:/cookies/youtube.txt"`
   - 使用浏览器扩展（如 Get cookies.txt LOCALLY）导出

## 命令行参数

```
fetch.py URL [-p 平台] [-o 输出目录] [-c 配置文件] [--extra ...]

-p, --platform   bilibili | youtube | twitter | generic
-o, --output-dir  输出目录
-c, --config      配置文件路径
--extra          透传给 yt-dlp 的原生参数
```

透传示例：
```bash
# 下载字幕并嵌入（需 web 客户端）
python fetch.py URL -p youtube --extra "--extractor-args" "youtube:player_client=web"

# 仅下载音频
python fetch.py URL -p youtube --extra "-x" "--audio-format" "mp3"

# 限制分辨率
python fetch.py URL -p youtube --extra "-f" "bestvideo[height<=1080]+bestaudio"
```

## 各平台注意事项

### B站 (bilibili)

- 大会员高清需要 cookies，配置 `cookies_from_browser` 指向已登录 B站 的浏览器
- 部分视频音视频分离，依赖 ffmpeg 自动合并
- 未登录可下载 480P 及以下，登录后可获得更高分辨率

### YouTube

- **默认使用 Android + iOS 客户端**，无需 cookies，可绕过 web 端的 n-sig JavaScript 挑战
- 如需 web 端 4K + 字幕，在 `config.json` 中为 youtube 添加：
  ```json
  "extractor_args": "youtube:player_client=web"
  ```
  并确保 `cookies_from_browser` 配置正确，且 Node.js 已安装
- Android 客户端不支持 cookies；iOS 客户端需要 PO Token（2026 年起）

### X / Twitter

- **必须配置 cookies**，X 严格限制未登录访问
- 视频默认选用最高可用质量（`format: "best"`）

## 输出文件命名

```
{标题前100字符} [{视频ID}].{扩展名}
```

## 故障排查

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| `未找到 yt-dlp` | 未安装 | `pip install yt-dlp` |
| `HTTP Error 403` | 需要 cookies | 配置 `cookies_from_browser` |
| 下载后只有音频 | 缺 ffmpeg | `winget install Gyan.FFmpeg` |
| B站 无法下载 | yt-dlp 版本过旧 | `pip install -U yt-dlp` |
| `n challenge solving failed` | YouTube web 端反爬 | 使用 android 客户端（默认） |
| `HTTP Error 412` (B站) | 需要 cookies | 配置 `cookies_from_browser` |
| 速度慢 | 单线程 | `concurrent_fragments` 调大至 8-16 |
| `LOCALAPPDATA` 未设置 | 子进程环境丢失 | 在调用前 `set LOCALAPPDATA=...` |

## 项目结构

```
video-fetcher/
├── fetch.py      # 主脚本
├── config.json   # 配置文件
└── README.md     # 本文档
```
