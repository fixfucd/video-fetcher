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

> **兜底档位为空或与高清档相同则不重试**：当某平台的低清档没有更低的画质可选时
> （见下表「抖音」），程序会跳过这次注定同结果的请求并直接报错，不再打印
> 「LQ fallback」后再原样失败一次。

| 平台 | 高清 | 低清回退 | 实测 |
|------|------|----------|------|
| B站 | 4K (cookies) | 720p | 无 cookies → HTTP 412，**必须** cookies |
| YouTube | 4K+字幕 (web+cookies) | bestvideo[height<=720] (android,ios) | 无 cookies 实测仅得 360p，见下 |
| 抖音 | bestvideo+bestaudio (cookies) | **无独立低清档**（跳过重复请求） | 403 可能来自 Cookie 过期，也可能是 yt-dlp 缺少动态请求签名 |
| Twitter | best (cookies) | best (无 cookies) | 公开视频推文免 cookies 可提取，实测与带 cookies 结果一致 |
| 通用 | bestvideo+bestaudio | best | — |

**Twitter**：公开视频推文无需登录即可提取（实测三条公开推文，无 cookies 与带 cookies
结果完全相同），因此会先无 Cookie 尝试；受保护/受限内容失败后才进入浏览器 Cookie 链。

**抖音**：yt-dlp 的抖音提取器只提供**一个** format 串，不存在真正的低清档位；早先写作
`720p` 的回退并未生效（实测同一请求 3 秒后原样失败）。因此本平台不设独立回退档，程序在
所有 Cookie 来源失败后直接给出诊断。`Export & Use` 解决 Cookie 导出和登录态问题，但抖音
详情接口还可能要求浏览器为每次请求动态生成校验参数。当 yt-dlp 报 `Downloading web detail JSON`
与 403 / `Fresh cookies...` 时，原因可能是 Cookie 过期，也可能是当前提取器不支持所需动态签名。


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

**导出是原子替换**：DPAPI / CDP / browser_cookie3 三条导出路径都先写到临时文件，只有导出
成功才覆盖目标文件；任一环节失败时**保留上一份可用的 cookies 文件**（旧版本会先把目标文件
截断，一次失败的导出就会让后续高清下载静默失效）。CDP 回退同样如此，并且结束时会先请求浏览器
自行关闭、必要时按进程树终止，避免留下持锁的孤儿浏览器进程。

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
  "cookies_from_browser": null,
  "cookies_file": "C:\\path\\to\\cookies\\chrome.txt",
  "platforms": {},
  "yt_dlp_global": {
    "concurrent_fragments": 8
  }
}
```

- `cookies_file` 与 `cookies_from_browser` 是**两个不同用途**的字段：前者是
  「已导出的 Netscape cookies 文件」，只要存在且登录校验通过就直接用它下载；后者是
  「首选回退浏览器」（GUI 下拉框里的那一项），仅在文件不可用时才进入回退链。
  两者同时配置时，**文件优先**。
- `platforms.<平台>` 只覆盖该平台的**高清档**；低清档保留自己的画质上限与提取器参数，
  不会被配置覆盖。
- 键名以下划线开头的字段（如 `_说明`、`_extractor_note`）是文档键，不会作为 yt-dlp 参数。

## 测试

```bash
python -m unittest discover -s tests
```

GUI 相关用例需要可用的 Tk（无显示环境时自动跳过）。

## 项目结构

```
video-fetcher/
├── fetch.py          # 核心脚本（检测/下载/回退；cookies 获取链的唯一实现）
├── gui.py            # 可视化客户端
├── config.json       # 配置文件
├── README.md
├── _cdp_cookies.py   # CDP cookies 导出（绕开 Chrome/Edge v20 App-Bound Encryption）
├── _cookie_crypto.py # 原生 DPAPI + AES-GCM cookies 解密（零依赖）
├── _logger.py        # 统一日志（自动轮转，目录不可写时降级并告警）
├── _run_bili_test.py # B站端到端冒烟脚本（对固定 BV 号跑一次真实下载）
├── tests/            # 单元测试（python -m unittest discover -s tests）
├── cookies/          # cookies 导出目录（已在 .gitignore 中，切勿提交）
└── downloads/        # 默认下载目录（已在 .gitignore 中）
```

`fetch.try_browser_cookies()` 是「native DPAPI → browser_cookie3 → yt-dlp DPAPI」这条
Cookie 获取链的**唯一实现**，命令行与 GUI 都调用它，避免两处各自演化。

> **注意**：`cookies/` 下是**在线生效的登录凭据**，已加入 `.gitignore`，请勿提交。
