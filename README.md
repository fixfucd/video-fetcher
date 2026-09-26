# video-fetcher

多平台视频获取工具，基于 **yt-dlp + ffmpeg**。

**核心策略：自动检测浏览器 → DB 拷贝绕过锁 → 多浏览器回退 → 低清兜底。**

## 使用方式

### 环境准备

需要 Python 3.9+、[yt-dlp](https://github.com/yt-dlp/yt-dlp) 和 ffmpeg：

```bash
python -m pip install -U yt-dlp
ffmpeg -version
```

`browser-cookie3` 是可选回退，不安装也可使用浏览器原生 Cookie 与低清回退：

```bash
python -m pip install browser-cookie3
```

### 命令行

```bash
# 列出已安装浏览器
python fetch.py --list-browsers

# B站
python fetch.py "https://www.bilibili.com/video/BV1xx411c7mD" -p bilibili
```

平台参数可省略，程序会按 URL 域名自动识别。短链域名 `youtu.be`、`b23.tv` 和
`v.douyin.com` 也支持识别：

```bash
python fetch.py "https://youtu.be/xxxxxxxxxxx"
```

需要传入额外的 yt-dlp 参数时，把它们放在 `--extra` 后；该标记之后的全部参数都会原样传递：

```bash
python fetch.py "https://example.com/video" --extra --proxy http://127.0.0.1:7890
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
Twitter/通用链接先尝试公开访问 ──成功──▶ 完成
      │失败
      ▼
首选浏览器 cookies ──成功──▶ 完成
      │失败 (被锁)
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
| 抖音 | bestvideo+bestaudio (cookies) | best | 403 可能来自 Cookie 过期，也可能是 yt-dlp 缺少动态请求签名 |
| Twitter | best (cookies) | best (无 cookies) | 公开视频推文免 cookies 可提取，实测与带 cookies 结果一致 |
| 通用 | bestvideo+bestaudio | best | — |

**Twitter**：公开视频推文无需登录即可提取（实测三条公开推文，无 cookies 与带 cookies
结果完全相同），因此会先无 Cookie 尝试；受保护/受限内容失败后才进入浏览器 Cookie 链。

**抖音**：`Export & Use` 解决 Cookie 导出和登录态问题，但抖音详情接口还可能要求浏览器为每次请求动态生成
校验参数。当 yt-dlp 报 `Downloading web detail JSON` 与 403 / `Fresh cookies...` 时，原因可能是 Cookie 过期，也可能是当前提取器
不支持所需动态签名。程序会先试完可用的独立 Cookie 来源；全部失败后跳过不可能改善结果的无 Cookie 回退。

**YouTube 客户端策略**：由 yt-dlp 自动选择播放器客户端。新版 YouTube 上强制 `web` 客户端
可能在缺少 JavaScript runtime 时只返回缩略图，强制 `android,ios` 也可能要求 PO Token；
因此项目不再写死客户端。公开视频通常可直接解析，登录 Cookie 仍可能提供更多格式或字幕。

## 浏览器支持

| 浏览器 | 自动检测 | yt-dlp 原生 | 路径 |
|--------|----------|------------|------|
| Chrome | ✓ | ✓ | `%LOCALAPPDATA%\Google\Chrome\User Data` |
| Edge | ✓ | ✓ | `%LOCALAPPDATA%\Microsoft\Edge\User Data` |
| Brave | ✓ | ✓ | `%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data` |
| Opera | ✓ | ✓ | `%APPDATA%\Opera Software\Opera Stable` |
| Firefox | ✓ | ✓ | `%APPDATA%\Mozilla\Firefox\Profiles` |

**DB 临时拷贝**：当浏览器 cookies 数据库被锁定时，自动拷贝到临时文件绕过锁。

**CDP 回退**：Chrome 127+ / Edge 的 cookies 使用 v20 App-Bound Encryption，本地 DPAPI
解密会失败。此时会尝试以 `--remote-debugging-port` 启动该浏览器，通过 CDP 让浏览器
自己解密并导出 cookies（见 `_cdp_cookies.py`）。程序不会再强制结束正在运行的浏览器；
若配置目录正被占用，关闭对应浏览器后再重试导出。

下载被停止或失败时会保留 yt-dlp 的 `.part` 文件，下一次下载可断点续传；程序不会清理
同一输出目录中其他任务的临时文件。

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
├── _cdp_cookies.py   # CDP cookies 导出（绕开 Chrome/Edge v20 App-Bound Encryption）
├── _cookie_crypto.py # 原生 DPAPI + AES-GCM cookies 解密（零依赖）
├── _logger.py        # 统一日志（自动轮转，目录不可写时降级并告警）
├── _run_bili_test.py # B站端到端冒烟测试
├── cookies/          # cookies 导出目录（已在 .gitignore 中，切勿提交）
└── downloads/        # 默认下载目录（已在 .gitignore 中）
```

> **注意**：`cookies/` 下是**在线生效的登录凭据**，已加入 `.gitignore`，请勿提交。
