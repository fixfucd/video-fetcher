# video-fetcher

多平台视频获取工具，基于 **yt-dlp + ffmpeg**。

**核心策略：优先 cookies 高清 → 失败自动回退低清。**

## 使用方式

### 命令行

```bash
# B站
python fetch.py "https://www.bilibili.com/video/BV1xx411c7mD" -p bilibili

# YouTube
python fetch.py "https://www.youtube.com/watch?v=mcTAHffEkIw" -p youtube

# X / Twitter
python fetch.py "https://x.com/xxx/status/123456" -p twitter
```

### 可视化客户端

```bash
python gui.py
```

- 输入 URL，选择平台，指定输出目录，点击下载
- 实时显示 yt-dlp 输出日志
- 支持停止下载

## 策略

```
高清 + cookies ──成功──▶ 完成
      │失败
      ▼
低清（无 cookies）──成功──▶ 完成（降级）
      │失败
      ▼
    报错（Twitter 无回退）
```

| 平台 | 高清 | 低清回退 |
|------|------|----------|
| B站 | 4K (cookies) | 720p |
| YouTube | 4K+字幕 (web+cookies) | 720p (android) |
| Twitter | 最佳 (cookies) | 无 |
| 通用 | bestvideo+bestaudio | best |

## 配置 (`config.json`)

```json
{
  "output_dir": "E:/工作/work2/downloads",
  "cookies_from_browser": "chrome",
  "platforms": {
    "youtube": { "format": "bestvideo[height<=2160]+bestaudio/best" }
  }
}
```

## 项目结构

```
video-fetcher/
├── fetch.py      # 命令行脚本（双轨策略）
├── gui.py         # 可视化客户端
├── config.json   # 配置文件
└── README.md
```
