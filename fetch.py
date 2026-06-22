#!/usr/bin/env python3
"""
video-fetcher — 多平台视频获取工具
基于 yt-dlp + ffmpeg，封装 B站 / YouTube / X(Twitter) / 通用站点的最佳实践。

策略：
  优先通过浏览器 cookies 获取高清视频；
  自动检测系统中已安装的浏览器；
  若当前浏览器被占用，拷贝 cookies DB 到临时文件绕过锁；
  按优先级回退到备用已安装浏览器；
  若所有浏览器均不可用，回退到低清晰度（无需登录）。
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ── 浏览器配置 ──────────────────────────────────────────────
# 每个浏览器: yt-dlp 名称、cookies DB 路径、回退优先级
# engine: chromium 或 gecko，用于区分检测策略
# base_dirs: User Data 根目录列表（支持多个可能位置）
# cookies_paths: 相对于 profile 目录的 cookies 文件路径
BROWSER_CONFIG = {
    "chrome": {
        "yt_name": "chrome",
        "native": True,
        "label": "Chrome",
        "engine": "chromium",
        "base_dirs": [
            "{localappdata}/Google/Chrome/User Data",
        ],
        "cookies_paths": [
            "{profile}/Network/Cookies",
            "{profile}/Cookies",
        ],
        "priority": 1,
    },
    "edge": {
        "yt_name": "edge",
        "native": True,
        "label": "Edge",
        "engine": "chromium",
        "base_dirs": [
            "{localappdata}/Microsoft/Edge/User Data",
        ],
        "cookies_paths": [
            "{profile}/Network/Cookies",
            "{profile}/Cookies",
        ],
        "priority": 2,
    },
    "lenovo": {
        "yt_name": None,
        "native": False,
        "label": "联想浏览器",
        "engine": "chromium",
        "base_dirs": [
            "{localappdata}/Lenovo/SLBrowser/User Data",
            "{localappdata}/Lenovo/SLB Browser/User Data",
            "{localappdata}/Lenovo/LenovoBrowser/User Data",
        ],
        "cookies_paths": [
            "{profile}/Network/Cookies",
            "{profile}/Cookies",
        ],
        "priority": 3,
    },
    "brave": {
        "yt_name": "brave",
        "native": True,
        "label": "Brave",
        "engine": "chromium",
        "base_dirs": [
            "{localappdata}/BraveSoftware/Brave-Browser/User Data",
        ],
        "cookies_paths": [
            "{profile}/Network/Cookies",
            "{profile}/Cookies",
        ],
        "priority": 4,
    },
    "opera": {
        "yt_name": "opera",
        "native": True,
        "label": "Opera",
        "engine": "chromium",
        "base_dirs": [
            "{appdata}/Opera Software/Opera Stable",
        ],
        "cookies_paths": [
            "{profile}/Network/Cookies",
            "{profile}/Cookies",
        ],
        "priority": 5,
    },
    "firefox": {
        "yt_name": "firefox",
        "native": True,
        "label": "Firefox",
        "engine": "gecko",
        "base_dirs": [],
        "cookies_paths": [],
        "priority": 6,
    },
}


# ── 平台预设 ──────────────────────────────────────────────
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
        "fallback": None,
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


# ═══════════════════════════════════════════════════════════════
#  浏览器检测 & Cookies 工具
# ═══════════════════════════════════════════════════════════════

def _expand_path(template, profile_path=None):
    """展开路径模板中的环境变量和 {profile} 占位符。"""
    env_map = {
        "localappdata": os.environ.get("LOCALAPPDATA", ""),
        "appdata": os.environ.get("APPDATA", ""),
        "userprofile": os.environ.get("USERPROFILE", ""),
        "home": os.environ.get("USERPROFILE") or os.path.expanduser("~"),
        "profile": profile_path or "",
    }
    result = template
    for key, val in env_map.items():
        result = result.replace("{" + key + "}", val)
    return result


def detect_browser_profiles(browser_key):
    """检测 Chromium 系浏览器的所有用户 Profile 目录。

    返回 [(profile_path, display_name), ...]。
    eg: [("C:/.../Default", "Default"), ("C:/.../Profile 1", "用户1")]
    """
    cfg = BROWSER_CONFIG.get(browser_key)
    if not cfg or cfg.get("engine") != "chromium":
        return []

    profiles = []
    seen = set()
    for base_tmpl in cfg.get("base_dirs", []):
        base = _expand_path(base_tmpl)
        if not os.path.isdir(base):
            continue

        for name in ["Default"] + [f"Profile {i}" for i in range(1, 20)]:
            profile_dir = os.path.join(base, name)
            if profile_dir in seen:
                continue
            seen.add(profile_dir)

            if os.path.isdir(profile_dir):
                for cookie_tmpl in cfg.get("cookies_paths", []):
                    cookie_path = _expand_path(cookie_tmpl, profile_dir)
                    if os.path.isfile(cookie_path):
                        display = name if name == "Default" else name.replace("Profile ", "用户")
                        profiles.append((profile_dir, display))
                        break

    return profiles


def find_cookies_file(browser_key, profile_index=0):
    """查找浏览器的 cookies 数据库文件。

    profile_index=0 表示 Default, 1=Profile 1...
    返回 (cookies_db_path, profile_name) 或 (None, None)。
    """
    cfg = BROWSER_CONFIG.get(browser_key)
    if not cfg:
        return None, None

    if cfg.get("engine") == "chromium":
        profiles = detect_browser_profiles(browser_key)
        if profile_index < len(profiles):
            profile_dir, display = profiles[profile_index]
            for cookie_tmpl in cfg.get("cookies_paths", []):
                path = _expand_path(cookie_tmpl, profile_dir)
                if os.path.isfile(path):
                    return path, display
        return None, None

    if cfg.get("engine") == "gecko":
        ff_base = os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox", "Profiles")
        if os.path.isdir(ff_base):
            for item in os.listdir(ff_base):
                db_path = os.path.join(ff_base, item, "cookies.sqlite")
                if os.path.isfile(db_path):
                    return db_path, item.split(".")[-1][:20] if "." in item else item
        return None, None

    return None, None


def detect_installed_browsers():
    """检测系统中所有已安装的浏览器。

    返回 {browser_key: {'installed': bool, 'profiles': int, 'label': str}, ...}
    按 priority 排序。
    """
    result = {}
    for key in sorted(BROWSER_CONFIG.keys(), key=lambda k: BROWSER_CONFIG[k]["priority"]):
        cfg = BROWSER_CONFIG[key]
        entry = {"installed": False, "profiles": 0, "label": cfg["label"], "key": key}

        if cfg.get("engine") == "chromium":
            profiles = detect_browser_profiles(key)
            if profiles:
                entry["installed"] = True
                entry["profiles"] = len(profiles)
        elif cfg.get("engine") == "gecko":
            db_path, _ = find_cookies_file(key)
            if db_path:
                entry["installed"] = True
                entry["profiles"] = 1

        result[key] = entry
    return result


def get_available_browsers(exclude=None):
    """获取已安装且在配置中的浏览器列表（按 priority 排序）。

    exclude: 要排除的 key 或 keys 集合。
    """
    installed = detect_installed_browsers()
    if exclude is None:
        exclude_set = set()
    elif isinstance(exclude, str):
        exclude_set = {exclude}
    else:
        exclude_set = set(exclude)

    return [k for k in sorted(installed, key=lambda k: BROWSER_CONFIG[k]["priority"])
            if installed[k]["installed"] and k not in exclude_set]


def get_alt_browsers(current_browser):
    """获取除当前浏览器之外的已安装备用浏览器列表。"""
    return get_available_browsers(exclude=current_browser)


def copy_cookies_db(browser_key, profile_index=0):
    """将浏览器 cookies 数据库拷贝到临时文件（绕过浏览器锁定）。

    返回临时文件路径，失败返回 None。
    调用者负责使用后清理。
    """
    source, profile_name = find_cookies_file(browser_key, profile_index)
    if not source:
        return None

    try:
        # 先尝试直接只读打开验证完整性
        conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        conn.execute("SELECT count(*) FROM cookies")
        conn.close()

        fd, tmp_path = tempfile.mkstemp(suffix=".sqlite", prefix=f"cookies_{browser_key}_")
        os.close(fd)
        shutil.copy2(source, tmp_path)
        return tmp_path
    except (sqlite3.OperationalError, PermissionError, OSError):
        # 被锁 → 文件级快照
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".sqlite", prefix=f"cookies_{browser_key}_")
            os.close(fd)
            shutil.copy2(source, tmp_path)
            # 验证
            conn = sqlite3.connect(tmp_path)
            conn.execute("SELECT count(*) FROM cookies")
            conn.close()
            return tmp_path
        except Exception:
            return None


def get_browser_cookies_args(browser_key, profile_index=0, use_temp_copy=True):
    """根据浏览器 key 返回 yt-dlp cookies 参数列表。

    use_temp_copy: 是否尝试拷贝 DB 到临时文件（绕过锁）。
    返回 (args_list, temp_file_path 或 None)。
    """
    cfg = BROWSER_CONFIG.get(browser_key)
    if not cfg:
        return [], None

    # 原生 → --cookies-from-browser
    if cfg.get("native") and cfg.get("yt_name"):
        return ["--cookies-from-browser", cfg["yt_name"]], None

    # 非原生 → 找 cookies 文件
    cookies_path, _ = find_cookies_file(browser_key, profile_index)
    if not cookies_path:
        return [], None

    if use_temp_copy:
        tmp_path = copy_cookies_db(browser_key, profile_index)
        if tmp_path:
            return ["--cookies", tmp_path], tmp_path

    return ["--cookies", cookies_path], None


def is_cookie_lock_error(stderr_text):
    """检测 stderr 中是否包含 cookies 被浏览器锁定的错误。"""
    if not stderr_text:
        return False
    lower = stderr_text.lower()
    for indicator in [
        "could not copy", "cookie database", "unsupported browser",
        "permission denied", "database is locked", "sqlite_busy",
        "locked", "access denied", "sharing violation",
        "being used by another process",
    ]:
        if indicator in lower:
            return True
    if "cookies" in lower and ("error" in lower or "fail" in lower or "warning" in lower):
        return True
    return False


def try_browser_cookie3_export(browser_key, output_path):
    """尝试使用 browser_cookie3 导出 cookies 到 Netscape 格式文件。

    返回 True 表示成功。
    """
    try:
        import browser_cookie3
    except ImportError:
        return False

    loaders = {
        "chrome": browser_cookie3.chrome,
        "edge": browser_cookie3.edge,
        "firefox": browser_cookie3.firefox,
        "opera": browser_cookie3.opera,
        "brave": browser_cookie3.brave,
    }
    loader = loaders.get(browser_key)
    if not loader:
        return False

    try:
        cj = loader()
        if not cj:
            return False

        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# Generated by video-fetcher via browser_cookie3\n\n")
            for cookie in cj:
                domain = cookie.domain if not cookie.domain.startswith(".") else cookie.domain
                flag = "TRUE" if domain.startswith(".") else "FALSE"
                secure = "TRUE" if cookie.secure else "FALSE"
                expires = str(int(cookie.expires)) if cookie.expires else "0"
                f.write(f"{domain}\t{flag}\t{cookie.path}\t"
                        f"{secure}\t{expires}\t"
                        f"{cookie.name}\t{cookie.value}\n")
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════
#  配置 / yt-dlp 参数
# ═══════════════════════════════════════════════════════════════

def normalize_douyin_url(url):
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
        cookies_file = config.get("cookies_file")
        cookies_browser = config.get("cookies_from_browser")

        if cookies_file and Path(cookies_file).exists():
            args += ["--cookies", cookies_file]
        elif cookies_browser:
            browser_args, _ = get_browser_cookies_args(cookies_browser, use_temp_copy=True)
            args += browser_args

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


def check_output_exists(output_dir, after_timestamp=None):
    video_exts = {".mp4", ".mkv", ".webm", ".flv", ".ts", ".mov", ".avi", ".3gp"}
    candidates = []
    try:
        for f in Path(output_dir).iterdir():
            if not f.is_file() or f.suffix.lower() not in video_exts:
                continue
            if after_timestamp is not None and f.stat().st_mtime < after_timestamp:
                continue
            candidates.append(f)
    except OSError:
        pass
    return candidates


def cleanup_temp_files(output_dir):
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

    # 清理旧临时 cookies 文件
    tmpdir = tempfile.gettempdir()
    try:
        for f in Path(tmpdir).glob("cookies_*.sqlite"):
            if (time.time() - f.stat().st_mtime) > 3600:
                try:
                    f.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _try_run(args, label=""):
    prefix = f"[{label}] " if label else ""
    print(f"{prefix}命令: {' '.join(args)}")
    print("-" * 60)
    proc = subprocess.run(args, capture_output=True, text=True)
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    return proc.returncode, proc.stderr


def _try_with_browser(url, output_dir, high_opts, config, browser_key, start_time, extra_args=None):
    """使用指定浏览器尝试高清下载。返回 (success, stderr, temp_files)。"""
    cfg = BROWSER_CONFIG.get(browser_key, {})
    label = cfg.get("label", browser_key)
    temp_files = []

    alt_config = dict(config)
    alt_config["cookies_from_browser"] = browser_key

    browser_args, tmp_path = get_browser_cookies_args(browser_key, use_temp_copy=True)
    if tmp_path:
        temp_files.append(tmp_path)
        print(f"[video-fetcher] {label} cookies DB 已拷贝到临时文件 (绕过锁)")

    # 非原生浏览器且找不到文件
    if not cfg.get("native") and not browser_args:
        ck_path, _ = find_cookies_file(browser_key)
        if not ck_path:
            print(f"[video-fetcher] {label} cookies 文件未找到，跳过")
            return False, "cookies file not found", temp_files

    high_args = build_yt_dlp_args(url, output_dir, high_opts, alt_config, use_cookies=True, extra_args=extra_args)
    rc, stderr = _try_run(high_args, label)

    if rc == 0:
        print(f"[video-fetcher] {label} 高清下载成功")
        return True, stderr, temp_files

    existing = check_output_exists(output_dir, start_time)
    if existing:
        print(f"[video-fetcher] {label} 返回非零但文件已存在 ({len(existing)} 个)")
        for f in existing:
            print(f"  \u2713 {f.name}")
        return True, stderr, temp_files

    if is_cookie_lock_error(stderr):
        print(f"[video-fetcher] [!] {label} cookies 被占用")
    else:
        print(f"[video-fetcher] {label} 下载失败 (exit={rc})")

    return False, stderr, temp_files


def fetch(url, platform="generic", output_dir=None, config_path=None, extra_args=None):
    config = load_config(config_path)
    if output_dir is None:
        output_dir = config.get("output_dir", str(Path.cwd() / "downloads"))

    normalized = normalize_douyin_url(url)
    if normalized != url:
        print(f"[video-fetcher] 抖音 URL 标准化: {normalized}")
        url = normalized

    os.makedirs(output_dir, exist_ok=True)
    high, fallback = get_platform_presets(platform, config)
    start_time = time.time()

    # 显示已安装浏览器
    installed = detect_installed_browsers()
    available = [k for k, v in installed.items() if v["installed"]]
    labels = ", ".join(BROWSER_CONFIG[k]["label"] for k in available) if available else "(无)"
    print(f"[video-fetcher] 已安装浏览器: {labels}")

    preferred_browser = config.get("cookies_from_browser", "")
    print(f"[video-fetcher] 平台: {platform} | 首选: {preferred_browser or '(无)'}")
    print(f"[video-fetcher] 输出: {output_dir}")

    temp_files_to_clean = []
    tried_browsers = set()

    # ── cookies 文件优先 ──
    cookies_file = config.get("cookies_file")
    if cookies_file and Path(cookies_file).exists():
        print(f"[video-fetcher] 使用 cookies 文件: {cookies_file}")
        high_args = build_yt_dlp_args(url, output_dir, high, config, use_cookies=True, extra_args=extra_args)
        rc, _ = _try_run(high_args, "cookies-file")
        if rc == 0:
            print("[video-fetcher] 高清下载成功 (cookies 文件)")
            cleanup_temp_files(output_dir)
            return 0
        if check_output_exists(output_dir, start_time):
            cleanup_temp_files(output_dir)
            return 0

    # ── Round 1: 首选浏览器 ──
    if preferred_browser and installed.get(preferred_browser, {}).get("installed"):
        tried_browsers.add(preferred_browser)
        success, stderr, temps = _try_with_browser(
            url, output_dir, high, config, preferred_browser, start_time, extra_args)
        temp_files_to_clean.extend(temps)
        if success:
            cleanup_temp_files(output_dir)
            return 0

        if is_cookie_lock_error(stderr):
            label = BROWSER_CONFIG.get(preferred_browser, {}).get("label", preferred_browser)
            print(f"[video-fetcher] {label} 被锁定，等待 2 秒后重试...")
            time.sleep(2)
            success, _, temps = _try_with_browser(
                url, output_dir, high, config, preferred_browser, start_time, extra_args)
            temp_files_to_clean.extend(temps)
            if success:
                cleanup_temp_files(output_dir)
                return 0
    elif preferred_browser:
        label = BROWSER_CONFIG.get(preferred_browser, {}).get("label", preferred_browser)
        print(f"[video-fetcher] 首选 '{label}' 未安装，直接尝试备用")

    # ── Round 1b: 备用浏览器（仅已安装）──
    alt_browsers = [b for b in get_available_browsers() if b not in tried_browsers]
    if alt_browsers:
        labels = ", ".join(BROWSER_CONFIG[b]["label"] for b in alt_browsers)
        print(f"\n[video-fetcher] 尝试备用浏览器: {labels}")

    for alt_browser in alt_browsers:
        tried_browsers.add(alt_browser)
        success, _, temps = _try_with_browser(
            url, output_dir, high, config, alt_browser, start_time, extra_args)
        temp_files_to_clean.extend(temps)
        if success:
            cleanup_temp_files(output_dir)
            return 0

    # ── browser_cookie3 兜底 ──
    print("\n[video-fetcher] 尝试 browser_cookie3 导出...")
    tmp_cookie_file = os.path.join(tempfile.gettempdir(), "video_fetcher_cookies.txt")
    for bk in available:
        if try_browser_cookie3_export(bk, tmp_cookie_file):
            if os.path.isfile(tmp_cookie_file) and os.path.getsize(tmp_cookie_file) > 100:
                label = BROWSER_CONFIG[bk]["label"]
                print(f"[video-fetcher] browser_cookie3 从 {label} 导出成功")
                bc3_config = dict(config)
                bc3_config["cookies_file"] = tmp_cookie_file
                bc3_config["cookies_from_browser"] = None
                high_args = build_yt_dlp_args(url, output_dir, high, bc3_config, use_cookies=True, extra_args=extra_args)
                rc, _ = _try_run(high_args, f"browser_cookie3/{bk}")
                if rc == 0:
                    cleanup_temp_files(output_dir)
                    return 0
                if check_output_exists(output_dir, start_time):
                    cleanup_temp_files(output_dir)
                    return 0
            break
    else:
        print("[video-fetcher] browser_cookie3 不可用或导出失败")

    # 最终文件检查
    if check_output_exists(output_dir, start_time):
        cleanup_temp_files(output_dir)
        return 0

    # ── Round 2: 低清回退 ──
    if fallback is None:
        print(f"\n[video-fetcher] {platform} 需登录且所有 cookies 来源均不可用。")
        cleanup_temp_files(output_dir)
        return 1

    if platform in ("douyin", "generic"):
        print("\n[video-fetcher] (回退模式对抖音/generic 画质不变)")

    print(f"\n[video-fetcher] 所有浏览器均失败，回退低清 (无需 cookies)")
    fallback_args = build_yt_dlp_args(url, output_dir, fallback, config, use_cookies=False, extra_args=extra_args)
    proc_fb = subprocess.run(fallback_args, capture_output=True, text=True)
    sys.stdout.write(proc_fb.stdout or "")
    sys.stderr.write(proc_fb.stderr or "")
    print("[video-fetcher] 低清下载成功" if proc_fb.returncode == 0 else
          f"[video-fetcher] 低清也失败 (exit={proc_fb.returncode})")
    cleanup_temp_files(output_dir)
    return proc_fb.returncode


def main():
    parser = argparse.ArgumentParser(
        description="video-fetcher — 多平台视频获取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="自动检测已安装浏览器 + cookies DB 临时拷贝绕过锁 + 多浏览器回退 + 低清兜底。",
    )
    parser.add_argument("url", help="视频 URL")
    parser.add_argument("-p", "--platform", choices=list(PLATFORM_PRESETS.keys()), default="generic")
    parser.add_argument("-o", "--output-dir", default=None)
    parser.add_argument("-c", "--config", default=None)
    parser.add_argument("--list-browsers", action="store_true", help="列出已安装的浏览器")
    parser.add_argument("--extra", nargs="*", default=[])

    args = parser.parse_args()

    if args.list_browsers:
        installed = detect_installed_browsers()
        print("已安装浏览器:")
        for k, v in installed.items():
            status = f"✓ ({v['profiles']} 个Profile)" if v["installed"] else "✗ 未安装"
            print(f"  {v['label']:12s}  {status}")
        return 0

    if not check_tool("yt-dlp"):
        print("[错误] 未找到 yt-dlp。pip install yt-dlp")
        return 1
    if not check_tool("ffmpeg"):
        print("[警告] 未找到 ffmpeg。部分视频需合并音视频流。")

    return fetch(args.url, args.platform, args.output_dir, args.config, args.extra)


if __name__ == "__main__":
    sys.exit(main())
