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

# YouTube（需浏览器已登录 Google）
python fetch.py "https://www.youtube.com/watch?v=hsTT42ZY_2Q" -p youtube

# X / Twitter
python fetch.py "https://x.com/ErenChenAI/status/2065141531082645722" -p twitter

# 任意站点
python fetch.py "https://example.com/video.mp4" -p generic -o ./videos
```

## 配置说明 (`config.json`)

```json
{
  "output_dir": "E:/工作/work2/downloads",     // 默认输出目录
  "cookies_from_browser": "chrome",            // 从浏览器读取 cookies (null 关闭)
  "cookies_file": null,                        // 或指定 cookies.txt 文件路径
  "platforms": {
    "bilibili": { "format": "bestvideo+bestaudio/best" },
    "youtube":  { "format": "bestvideo[height<=2160]+bestaudio/best" }
  }
}
```

### cookies 配置

YouTube、X/Twitter 等站点需要登录态才能下载高清/受限内容。两种方式：

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
# 下载字幕并嵌入
python fetch.py URL -p youtube --extra "--write-auto-subs" "--embed-subs"

# 仅下载音频
python fetch.py URL -p youtube --extra "-x" "--audio-format" "mp3"

# 限制分辨率
python fetch.py URL -p youtube --extra "-f" "bestvideo[height<=1080]+bestaudio"
```

## 各平台注意事项

### B站 (bilibili)

- 大会员高清可能需要 cookies，配置 `cookies_from_browser` 指向已登录 B站 的浏览器
- 部分视频音视频分离，依赖 ffmpeg 自动合并

### YouTube

- **必须配置 cookies**（`cookies_from_browser` 或 `cookies_file`），否则可能被限速或返回 403
- 格式预设为最高 4K (2160p)，可在 `config.json` 中调整 `height` 限制
- 自动下载中英文字幕（`sub_langs: "zh-Hans,en"`）

### X / Twitter

- **必须配置 cookies**，X 严格限制未登录访问
- 视频默认选用最高可用质量（`format: "best"`）
- 注意 X 单视频可能有多分辨率版本，`best` 会选最佳

## 输出文件命名

```
{标题前100字符} [{视频ID}].{扩展名}

例：
Eren Chen - Robots are already out on the streets asking for money [2065141495192039424].mp4
```

## 故障排查

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| `未找到 yt-dlp` | 未安装 | `pip install yt-dlp` |
| `HTTP Error 403` | 需要 cookies | 配置 `cookies_from_browser` |
| 下载后只有音频 | 缺 ffmpeg | `winget install Gyan.FFmpeg` |
| B站 无法下载 | yt-dlp 版本过旧 | `pip install -U yt-dlp` |
| 速度慢 | 单线程 | `concurrent_fragments` 调大至 8-16 |

## 项目结构

```
video-fetcher/
├── fetch.py      # 主脚本
├── config.json   # 配置文件
└── README.md     # 本文档
```
