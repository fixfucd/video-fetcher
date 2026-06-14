#!/usr/bin/env python3
"""
video-fetcher — 多平台视频获取工具
基于 yt-dlp + ffmpeg，封装 B站 / YouTube / X(Twitter) / 通用站点的最佳实践。

策略：
  优先通过浏览器 cookies 获取高清视频；
  若 cookies 不可用或高清下载失败，自动回退到低清晰度（无需登录）。
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# ── 平台预设 ──────────────────────────────────────────────
# 每个平台两套参数：high（需要 cookies，高清）和 fallback（无需 cookies，低清）
PLATFORM_PRESETS = {
    "bilibili": {
        "high": {
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "no_playlist": True,
        },
        "fallback": {
            "format": "bestvideo[height<=720]+bestaudio/best",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "no_playlist": True,
        },
    },
    "youtube": {
        "high": {
            "format": "bestvideo[height<=2160]+bestaudio/best",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "write_auto_subs": True,
            "sub_langs": "zh-Hans,en",
            "no_playlist": True,
            "extractor_args": "youtube:player_client=web",
        },
        "fallback": {
            "format": "bestvideo[height<=720]+bestaudio/best",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "no_playlist": True,
            "extractor_args": "youtube:player_client=android,ios",
        },
    },
    "douyin": {
        "high": {
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "no_playlist": True,
        },
        "fallback": {
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "no_playlist": True,
        },
    },
    "twitter": {
        "high": {
            "format": "best",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "no_playlist": True,
        },
        "fallback": None,  # X 必须登录
    },
    "generic": {
        "high": {
            "format": "bestvideo+bestaudio/best",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "no_playlist": True,
        },
        "fallback": {
            "format": "best",
            "merge_output_format": "mp4",
            "embed_metadata": True,
            "no_playlist": True,
        },
    },
}

# ── 配置加载 ──────────────────────────────────────────────
def normalize_douyin_url(url):
    """将抖音 /jingxuan 或 /user/self 等?modal_id= 格式转换为 /video/ 格式"""
    import re
    m = re.search(r'douyin\.com/\S*\?.*modal_id=(\d+)', url)
    if m:
        return f"https://www.douyin.com/video/{m.group(1)}"
    return url


def load_config(config_path=None):
    if config_path is None:
        config_path = Path(__file__).parent / "config.json"
    if Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_platform_presets(platform, config):
    preset = PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["generic"])
    high = dict(preset["high"])
    fallback = dict(preset["fallback"]) if preset.get("fallback") else None

    user_platforms = config.get("platforms", {})
    if platform in user_platforms:
        user = user_platforms[platform]
        high.update(user)
        if fallback:
            fallback.update(user)
    return high, fallback


def build_yt_dlp_args(url, output_dir, platform_opts, config, use_cookies, extra_args=None):
    output_template = str(Path(output_dir) / "%(title).100s [%(id)s].%(ext)s")
    args = ["yt-dlp", url, "-o", output_template]

    if use_cookies:
        cookies_browser = config.get("cookies_from_browser")
        cookies_file = config.get("cookies_file")
        # 优先使用 cookies 文件（绕过浏览器锁定）
        if cookies_file and Path(cookies_file).exists():
            args += ["--cookies", cookies_file]
        elif cookies_browser:
            args += ["--cookies-from-browser", cookies_browser]

    for key, val in platform_opts.items():
        if isinstance(val, bool):
            if val:
                args.append(f"--{key.replace('_', '-')}")
        elif val is not None:
            args.append(f"--{key.replace('_', '-')}")
            args.append(str(val))

    global_opts = config.get("yt_dlp_global", {})
    for key, val in global_opts.items():
        if isinstance(val, bool):
            if val:
                args.append(f"--{key.replace('_', '-')}")
        elif val is not None:
            args.append(f"--{key.replace('_', '-')}")
            args.append(str(val))

    if extra_args:
        args.extend(extra_args)
    return args


def check_tool(name):
    try:
        subprocess.run([name, "--version"], capture_output=True, timeout=10)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return True


def cleanup_temp_files(output_dir):
    """删除 yt-dlp / ffmpeg 残留的临时文件"""
    patterns = ["*.part", "*.ytdl", "*.temp.*", "*.part-*"]
    removed = 0
    for pat in patterns:
        for f in Path(output_dir).glob(pat):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        print(f"[video-fetcher] 清理了 {removed} 个临时文件")


def fetch(url, platform="generic", output_dir=None, config_path=None, extra_args=None):
    config = load_config(config_path)
    if output_dir is None:
        output_dir = config.get("output_dir", str(Path.cwd() / "downloads"))

    # 自动检测并标准化抖音精选 URL（不限平台）
    normalized = normalize_douyin_url(url)
    if normalized != url:
        print(f"[video-fetcher] 抖音 URL 标准化: {normalized}")
        url = normalized

    os.makedirs(output_dir, exist_ok=True)

    high, fallback = get_platform_presets(platform, config)

    # Round 1: high quality + cookies
    print(f"[video-fetcher] 平台: {platform} | 尝试高清 (cookies)")
    print(f"[video-fetcher] 输出: {output_dir}")
    high_args = build_yt_dlp_args(url, output_dir, high, config, use_cookies=True, extra_args=extra_args)
    print(f"[video-fetcher] 命令: {' '.join(high_args)}")
    print("-" * 60)
    proc = subprocess.run(high_args, capture_output=True, text=True)
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    if proc.returncode == 0:
        print("[video-fetcher] 高清下载成功")
        cleanup_temp_files(output_dir)
        return 0

    # 检查 cookies 特定错误并给出诊断
    cookies_issue = None
    for line in (proc.stderr or "").splitlines():
        line_lower = line.lower()
        if "could not copy" in line_lower or "cookie database" in line_lower:
            cookies_issue = line.strip()
            break
        if "unsupported browser" in line_lower:
            cookies_issue = line.strip()
            break
    if cookies_issue:
        print(f"\n[video-fetcher] [!] Cookies 提取异常: {cookies_issue}")
        print("[video-fetcher]   可能原因: 浏览器正在运行，锁定了 cookies 数据库")
        print("[video-fetcher]   建议: 关闭浏览器后重试，或在 config.json 中设置 cookies_file 路径")
        # 自动重试一次（浏览器可能短暂释放锁）
        import time
        print("[video-fetcher]   2 秒后重试...")
        time.sleep(2)
        proc2 = subprocess.run(high_args, capture_output=True, text=True)
        sys.stdout.write(proc2.stdout or "")
        sys.stderr.write(proc2.stderr or "")
        if proc2.returncode == 0:
            print("[video-fetcher] 重试成功，高清下载完成")
            cleanup_temp_files(output_dir)
            return 0

        # 尝试备用浏览器（Edge 通常不被锁定）
        current_browser = config.get("cookies_from_browser", "")
        alt_browsers = ["edge", "chrome", "firefox"]
        alt_browser = None
        for b in alt_browsers:
            if b != current_browser:
                alt_browser = b
                break
        if alt_browser and current_browser:
            print(f"[video-fetcher]   尝试备用浏览器: {alt_browser}")
            alt_config = dict(config)
            alt_config["cookies_from_browser"] = alt_browser
            alt_args = build_yt_dlp_args(url, output_dir, high, alt_config, use_cookies=True, extra_args=extra_args)
            proc3 = subprocess.run(alt_args, capture_output=True, text=True)
            sys.stdout.write(proc3.stdout or "")
            sys.stderr.write(proc3.stderr or "")
            if proc3.returncode == 0:
                print(f"[video-fetcher] 备用浏览器 {alt_browser} 成功，高清下载完成")
                cleanup_temp_files(output_dir)
                return 0
            print(f"[video-fetcher] 备用浏览器也失败，继续回退")

    # 回退说明：对于抖音/generic，回退画质不变
    if platform in ("douyin", "generic"):
        print("[video-fetcher] (回退模式对抖音/generic 画质不变，仅跳过 cookies)")

    # Round 2: fallback
    if fallback is None:
        print(f"[video-fetcher] {platform} 需登录且 cookies 不可用，无法回退。")
        cleanup_temp_files(output_dir)
        return proc.returncode

    print(f"\n[video-fetcher] 高清失败 (exit={proc.returncode})，回退低清 (无需 cookies)")
    fallback_args = build_yt_dlp_args(url, output_dir, fallback, config, use_cookies=False, extra_args=extra_args)
    print(f"[video-fetcher] 命令: {' '.join(fallback_args)}")
    print("-" * 60)
    proc_fb = subprocess.run(fallback_args, capture_output=True, text=True)
    sys.stdout.write(proc_fb.stdout or "")
    sys.stderr.write(proc_fb.stderr or "")
    if proc_fb.returncode == 0:
        print("[video-fetcher] 低清下载成功")
    else:
        print(f"[video-fetcher] 低清也失败 (exit={proc_fb.returncode})")
        for line in (proc_fb.stderr or "").splitlines():
            if "error" in line.lower():
                print(f"  {line.strip()}")
    cleanup_temp_files(output_dir)
    return proc_fb.returncode


def main():
    parser = argparse.ArgumentParser(
        description="video-fetcher — 多平台视频获取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="策略: 优先 cookies 高清, 失败自动回退低清。Twitter 必须登录。",
    )
    parser.add_argument("url", help="视频 URL")
    parser.add_argument("-p", "--platform", choices=list(PLATFORM_PRESETS.keys()), default="generic")
    parser.add_argument("-o", "--output-dir", default=None)
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("--extra", nargs="*", default=[])

    args = parser.parse_args()

    if not check_tool("yt-dlp"):
        print("[错误] 未找到 yt-dlp。pip install yt-dlp")
        return 1
    if not check_tool("ffmpeg"):
        print("[警告] 未找到 ffmpeg。部分视频需合并音视频流。")

    return fetch(args.url, args.platform, args.output_dir, args.config, args.extra)


if __name__ == "__main__":
    sys.exit(main())
