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
        if cookies_browser:
            args += ["--cookies-from-browser", cookies_browser]
        if cookies_file:
            args += ["--cookies", cookies_file]

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


def fetch(url, platform="generic", output_dir=None, config_path=None, extra_args=None):
    config = load_config(config_path)
    if output_dir is None:
        output_dir = config.get("output_dir", str(Path.cwd() / "downloads"))
    os.makedirs(output_dir, exist_ok=True)

    high, fallback = get_platform_presets(platform, config)

    # Round 1: high quality + cookies
    print(f"[video-fetcher] 平台: {platform} | 尝试高清 (cookies)")
    print(f"[video-fetcher] 输出: {output_dir}")
    high_args = build_yt_dlp_args(url, output_dir, high, config, use_cookies=True, extra_args=extra_args)
    print(f"[video-fetcher] 命令: {' '.join(high_args)}")
    print("-" * 60)
    rc = subprocess.call(high_args)
    if rc == 0:
        print("[video-fetcher] 高清下载成功")
        return 0

    # Round 2: fallback
    if fallback is None:
        print(f"[video-fetcher] {platform} 需登录且 cookies 不可用，无法回退。")
        return rc

    print(f"\n[video-fetcher] 高清失败 (exit={rc})，回退低清 (无需 cookies)")
    fallback_args = build_yt_dlp_args(url, output_dir, fallback, config, use_cookies=False, extra_args=extra_args)
    print(f"[video-fetcher] 命令: {' '.join(fallback_args)}")
    print("-" * 60)
    rc2 = subprocess.call(fallback_args)
    print(f"[video-fetcher] {'低清成功' if rc2 == 0 else f'低清也失败 (exit={rc2})'}")
    return rc2


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
