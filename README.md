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
           报错 (Twitter 例外见下: 公开推文无 cookies 也可取)
```

| 平台 | 高清 | 低清回退 | 实测 |
|------|------|----------|------|
| B站 | 4K (cookies) | 720p | 无 cookies → HTTP 412，**必须** cookies |
| YouTube | 4K+字幕 (web+cookies) | bestvideo[height<=720] (android,ios) | 无 cookies 实测仅得 360p，见下 |
| 抖音 | bestvideo+bestaudio (cookies) | best | 需**新鲜** cookies |
| Twitter | best (cookies) | best (无 cookies) | 公开视频推文免 cookies 可提取，实测与带 cookies 结果一致 |
| 通用 | bestvideo+bestaudio | best | — |

**Twitter**：公开视频推文无需登录即可提取（实测三条公开推文，无 cookies 与带 cookies
结果完全相同）。仅受保护/受限内容需要 cookies。

**YouTube 低清档现状**：`player_client=android,ios` 在 yt-dlp 2026.06.09 上已明显退化 ——
android 端 https 格式被 SABR-only 流媒体实验跳过，ios 端要求 GVS PO Token，
因此实际只能拿到遗留格式 18（360p）。想要 4K+字幕必须登录 youtube.com 后重新导出 cookies。

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
├── fetch.py          # 核心脚本（检测/下载/回退）
├── gui.py            # 可视化客户端
├── config.json       # 配置文件
├── README.md
├── _cdp_cookies.py   # 联想浏览器 cookies 导出（CDP，yt-dlp 不支持该浏览器）
├── _cookie_crypto.py # 原生 DPAPI + AES-GCM cookies 解密（零依赖）
├── _logger.py        # 统一日志（自动轮转，目录不可写时降级并告警）
├── _run_bili_test.py # B站端到端冒烟测试
├── cookies/          # cookies 导出目录（已在 .gitignore 中，切勿提交）
└── downloads/        # 默认下载目录（已在 .gitignore 中）
```

> **注意**：`cookies/` 下是**在线生效的登录凭据**，已加入 `.gitignore`，请勿提交。
