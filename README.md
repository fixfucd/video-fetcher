# video-fetcher

多平台视频获取工具，基于 **yt-dlp + ffmpeg**。

**核心策略：自动检测浏览器 → DB 拷贝绕过锁 → 多浏览器回退 → 低清兜底。**

## 使用方式

### 命令行

```bash
# 列出已安装浏览器
python fetch.py --list-browsers

# B站
python fetch.py "https://www.bilibili.com/video/BV1xx411c7mD" -p bilibili
```

```bash
# YouTube
python fetch.py "https://www.youtube.com/watch?v=mcTAHffEkIw" -p youtube
```

```bash
# X / Twitter
python fetch.py "https://x.com/xxx/status/123456" -p twitter

# 抖音
python fetch.py "https://www.douyin.com/video/7649969359930982011" -p douyin
```

```bash
# 抖音 / 抖音精选
python fetch.py "https://www.douyin.com/video/7649969359930982011" -p douyin
python fetch.py "https://www.douyin.com/jingxuan?modal_id=7649969359930982011" -p douyin
```

### 可视化客户端

```bash
python gui.py
```

- 启动时自动检测已安装浏览器，标注安装状态
- 点击「刷新检测」重新扫描
- 输入 URL → 选择平台 → 指定输出目录 → 下载
- 实时显示 yt-dlp 输出日志，支持停止下载

## 策略

```
首选浏览器 cookies ──成功──▶ 完成
      │失败 (被锁)
      ├── 等待2秒重试
      ├── 备用浏览器1 (仅已安装)
      ├── 备用浏览器2 (仅已安装)
      ├── ...
      ├── browser_cookie3 导出兜底 (可选)
      │      │全部失败
      │      ▼
      └── 低清 (无 cookies) ──成功──▶ 完成 (降级)
             │失败
             ▼
           报错 (Twitter 无回退)
```

| 平台 | 高清 | 低清回退 |
|------|------|----------|
| B站 | 4K (cookies) | 720p |
| YouTube | 4K+字幕 (web+cookies) | 720p (android) |
| 抖音 | bestvideo+bestaudio (cookies) | best |
| Twitter | 最佳 (cookies) | 无 |
| 通用 | bestvideo+bestaudio | best |

## 浏览器支持

| 浏览器 | 自动检测 | yt-dlp 原生 | 路径 |
|--------|----------|------------|------|
| Chrome | ✓ | ✓ | `%LOCALAPPDATA%\Google\Chrome\User Data` |
| Edge | ✓ | ✓ | `%LOCALAPPDATA%\Microsoft\Edge\User Data` |
| 联想浏览器 | ✓ | ✗ (DB拷贝) | `%LOCALAPPDATA%\Lenovo\SLBrowser\User Data` |
| Brave | ✓ | ✓ | `%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data` |
| Opera | ✓ | ✓ | `%APPDATA%\Opera Software\Opera Stable` |
| Firefox | ✓ | ✓ | `%APPDATA%\Mozilla\Firefox\Profiles` |

**DB 临时拷贝**：当浏览器 cookies 数据库被锁定时，自动拷贝到临时文件绕过锁。

**可选依赖**：`pip install browser-cookie3` 可获得额外浏览器支持（作为最终兜底）。

## 配置 (`config.json`)

```json
{
  "output_dir": "downloads",
  "cookies_from_browser": "chrome",
  "cookies_file": null,
  "platforms": {},
  "yt_dlp_global": {
    "concurrent_fragments": 8
  }
}
```

## 项目结构

```
video-fetcher/
├── fetch.py         # 核心脚本（检测/下载/回退）
├── gui.py           # 可视化客户端
├── config.json      # 配置文件
├── README.md
├── _test_detect.py  # 浏览器检测验证
├── _test_import.py  # 导入链验证
└── _test_gui_import.py
```
