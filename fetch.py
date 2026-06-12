#!/usr/bin/env python3
"""
video-fetcher — 多平台视频获取工具
基于 yt-dlp + ffmpeg，封装 B站 / YouTube / X(Twitter) / 通用站点的最佳实践。
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# ── 平台预设 ──────────────────────────────────────────────
PLATFORM_PRESETS = {
    "bilibili": {
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "embed_metadata": True,
        "no_playlist": True,
    },
    "youtube": {
        "format": "bestvideo[height<=2160]+bestaudio/best",
        "merge_output_format": "mp4",
        "embed_metadata": True,
        "write_auto_subs": True,
        "sub_langs": "zh-Hans,en",
        "no_playlist": True,
        # 默认使用 android+ios 客户端绕过 web 端的 n-sig 挑战
        # 如需高清 + 中文硬字幕，切换为 "web" 并确保 cookies 可用
        "extractor_args": "youtube:player_client=android,ios",
    },
    "twitter": {
        "format": "best",
        "merge_output_format": "mp4",
        "embed_metadata": True,
        "no_playlist": True,
    },
    "generic": {
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "embed_metadata": True,
        "no_playlist": True,
    },
}

# ── 配置加载 ──────────────────────────────────────────────
def load_config(config_path: str | None = None) -> dict:
    """加载 JSON 配置文件，不存在则返回默认值。"""
    if config_path is None:
        config_path = Path(__file__).parent / "config.json"
    if Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def merge_presets(platform: str, config: dict) -> dict:
    """合并平台预设与用户配置中的平台覆盖。"""
    preset = dict(PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["generic"]))
    user_platforms = config.get("platforms", {})
    if platform in user_platforms:
        preset.update(user_platforms[platform])
    return preset


def build_yt_dlp_args(
    url: str,
    output_dir: str,
    platform_opts: dict,
    config: dict,
    extra_args: list[str] | None = None,
) -> list[str]:
    """构建 yt-dlp 命令行参数列表。"""
    output_template = str(Path(output_dir) / "%(title).100s [%(id)s].%(ext)s")

    args = [
        "yt-dlp",
        url,
        "-o", output_template,
    ]

    # ── cookies 来源 ──
    cookies_browser = config.get("cookies_from_browser")
    cookies_file = config.get("cookies_file")
    if cookies_browser:
        args += ["--cookies-from-browser", cookies_browser]
    if cookies_file:
        args += ["--cookies", cookies_file]

    # ── 平台参数 ──
    for key, val in platform_opts.items():
        if isinstance(val, bool):
            if val:
                args.append(f"--{key.replace('_', '-')}")
        elif val is not None:
            args.append(f"--{key.replace('_', '-')}")
            args.append(str(val))

    # ── 全局配置覆盖 ──
    global_opts = config.get("yt_dlp_global", {})
    for key, val in global_opts.items():
        if isinstance(val, bool):
            if val:
                args.append(f"--{key.replace('_', '-')}")
        elif val is not None:
            args.append(f"--{key.replace('_', '-')}")
            args.append(str(val))

    # ── 额外参数 ──
    if extra_args:
        args.extend(extra_args)

    return args


def check_tool(name: str) -> bool:
    """检查命令行工具是否可用。"""
    try:
        subprocess.run([name, "--version"], capture_output=True, timeout=10)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return True


def fetch(
    url: str,
    platform: str = "generic",
    output_dir: str | None = None,
    config_path: str | None = None,
    extra_args: list[str] | None = None,
) -> int:
    """执行视频下载。返回 yt-dlp 的退出码。"""
    config = load_config(config_path)

    if output_dir is None:
        output_dir = config.get("output_dir", str(Path.cwd() / "downloads"))
    os.makedirs(output_dir, exist_ok=True)

    platform_opts = merge_presets(platform, config)
    args = build_yt_dlp_args(url, output_dir, platform_opts, config, extra_args)

    print(f"[video-fetcher] 平台: {platform}")
    print(f"[video-fetcher] 输出: {output_dir}")
    print(f"[video-fetcher] 命令: {' '.join(args)}")
    print("-" * 60)

    return subprocess.call(args)


# ── CLI ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="video-fetcher — 多平台视频获取工具 (基于 yt-dlp + ffmpeg)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  fetch.py https://www.bilibili.com/video/BV1xx411c7mD --platform bilibili
  fetch.py https://www.youtube.com/watch?v=hsTT42ZY_2Q --platform youtube
  fetch.py https://x.com/xxx/status/123456 --platform twitter
  fetch.py https://example.com/video.mp4 --platform generic -o ./videos
  fetch.py URL --platform youtube --extra "--write-thumbnail" "--embed-thumbnail"

YouTube 提示:
  默认使用 android+ios 客户端（无需 cookies，绕过 n-sig 挑战）。
  如需 web 端高清格式 + 字幕，在 config.json 中覆盖：
    "youtube": {"extractor_args": "youtube:player_client=web"}
  并确保 cookies_from_browser 配置正确。
        """,
    )
    parser.add_argument("url", help="视频 URL")
    parser.add_argument(
        "-p", "--platform",
        choices=list(PLATFORM_PRESETS.keys()),
        default="generic",
        help="平台预设 (默认: generic)",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default=None,
        help="输出目录 (默认: config.json 中的 output_dir 或 ./downloads)",
    )
    parser.add_argument(
        "-c", "--config",
        default=None,
        help="配置文件路径 (默认: 脚本同目录下的 config.json)",
    )
    parser.add_argument(
        "--extra",
        nargs="*",
        default=[],
        help="透传给 yt-dlp 的额外参数",
    )

    args = parser.parse_args()

    # ── 环境检查 ──
    if not check_tool("yt-dlp"):
        print("[错误] 未找到 yt-dlp。请先安装: pip install yt-dlp")
        return 1
    if not check_tool("ffmpeg"):
        print("[警告] 未找到 ffmpeg。部分视频可能需要合并音视频流。")
        print("[警告] 安装: winget install Gyan.FFmpeg 或从 https://ffmpeg.org 下载")

    return fetch(
        url=args.url,
        platform=args.platform,
        output_dir=args.output_dir,
        config_path=args.config,
        extra_args=args.extra,
    )


if __name__ == "__main__":
    sys.exit(main())
