# video-fetcher

多平台视频获取工具，基于 **yt-dlp + ffmpeg**，封装 B站 / YouTube / X(Twitter) / 通用站点的下载最佳实践。

**核心策略：优先浏览器 cookies 获取高清 → 失败自动回退低清（无需登录）。**

## 环境要求

| 工具 | 安装方式 |
|------|----------|
| Python >= 3.9 | https://python.org |
| yt-dlp | `pip install yt-dlp` |
| ffmpeg | `winget install Gyan.FFmpeg` |

## 快速上手

```bash
# B站（高清优先，回退 720p）
python fetch.py "https://www.bilibili.com/video/BV1xx411c7mD" -p bilibili

# YouTube（web+4K 优先，回退 android 720p）
python fetch.py "https://www.youtube.com/watch?v=mcTAHffEkIw" -p youtube

# X / Twitter（必须 cookies，无回退）
python fetch.py "https://x.com/xxx/status/123456" -p twitter

# 任意站点
python fetch.py "https://example.com/video.mp4" -p generic -o ./videos
```

## 策略说明

```
  ┌─────────────────┐
  │ 高清 + cookies   │──成功──▶ 完成
  └────────┬────────┘
           │失败
  ┌────────▼────────┐
  │ 低清（无 cookies）│──成功──▶ 完成（降级）
  └────────┬────────┘
           │失败
  ┌────────▼────────┐
  │     报错         │（Twitter 无回退）
  └─────────────────┘
```

| 平台 | 高清 | 低清回退 | 说明 |
|------|------|----------|------|
| B站 | 4K (需 cookies) | 720p (无需登录) | 大会员高清需 cookies |
| YouTube | 4K + 字幕 (web客户端+cookies) | 720p (android客户端) | web端需过 n-sig 挑战 |
| Twitter | 最高质量 (需 cookies) | 无 | X 必须登录 |
| 通用 | bestvideo+bestaudio | best | — |

## 配置 (`config.json`)

```json
{
  "output_dir": "E:/工作/work2/downloads",
  "cookies_from_browser": "chrome",
  "cookies_file": null,
  "platforms": {
    "youtube": {
      "format": "bestvideo[height<=2160]+bestaudio/best"
    }
  }
}
```

- `cookies_from_browser`: chrome / firefox / edge / opera / brave
- `cookies_file`: 浏览器导出的 cookies.txt 路径（如 `E:/cookies/youtube.txt`）
- `platforms`: 覆盖各平台格式参数（同时影响高清和回退）

## 命令行参数

```
fetch.py URL [-p 平台] [-o 目录] [-c 配置] [--extra ...]

-p   bilibili | youtube | twitter | generic
-o   输出目录
-c   配置文件路径
--extra  透传 yt-dlp 参数
```

## 故障排查

| 症状 | 解决 |
|------|------|
| `n challenge solving failed` | 自动回退 android 客户端，无需处理 |
| `HTTP Error 403/412` | 检查 cookies 配置或接受低清回退 |
| Twitter 下载失败 | Twitter 必须 cookies，检查登录态 |
| `LOCALAPPDATA` 未设置 | `set LOCALAPPDATA=C:\Users\lenovo\AppData\Local` |

## 项目结构

```
video-fetcher/
├── fetch.py      # 主脚本（双轨策略）
├── config.json   # 配置文件
└── README.md     # 本文档
```
