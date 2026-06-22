#!/usr/bin/env python3
"""
video-fetcher — 多平台视频获取工具
基于 yt-dlp + ffmpeg

策略：
  原生浏览器 → yt-dlp 内置 DPAPI 解密
  非原生浏览器（联想等）→ browser_cookie3 导出 Netscape → --cookies
  多浏览器回退 + 低清兜底
"""

import argparse, json, os, shutil, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

BROWSER_CONFIG = {
    "chrome":  {"yt_name":"chrome", "native":True, "label":"Chrome", "engine":"chromium", "base_dirs":["{localappdata}/Google/Chrome/User Data"], "cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"], "priority":1},
    "edge":    {"yt_name":"edge", "native":True, "label":"Edge", "engine":"chromium", "base_dirs":["{localappdata}/Microsoft/Edge/User Data"], "cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"], "priority":2},
    "lenovo":  {"yt_name":None, "native":False, "label":"联想浏览器", "engine":"chromium", "base_dirs":["{localappdata}/Lenovo/SLBrowser/User Data","{localappdata}/Lenovo/SLB Browser/User Data","{localappdata}/Lenovo/LenovoBrowser/User Data"], "cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"], "priority":3},
    "brave":   {"yt_name":"brave", "native":True, "label":"Brave", "engine":"chromium", "base_dirs":["{localappdata}/BraveSoftware/Brave-Browser/User Data"], "cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"], "priority":4},
    "opera":   {"yt_name":"opera", "native":True, "label":"Opera", "engine":"chromium", "base_dirs":["{appdata}/Opera Software/Opera Stable"], "cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"], "priority":5},
    "firefox": {"yt_name":"firefox", "native":True, "label":"Firefox", "engine":"gecko", "base_dirs":[], "cookies_paths":[], "priority":6},
}

PLATFORM_PRESETS = {
    "bilibili":{"high":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":{"format":"bestvideo[height<=720]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True}},
    "youtube":{"high":{"format":"bestvideo[height<=2160]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"write_auto_subs":True,"sub_langs":"zh-Hans,en","no_playlist":True,"extractor_args":"youtube:player_client=web"},"fallback":{"format":"bestvideo[height<=720]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True,"extractor_args":"youtube:player_client=android,ios"}},
    "douyin":{"high":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True}},
    "twitter":{"high":{"format":"best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":None},
    "generic":{"high":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":{"format":"best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True}},
}

# ── 工具函数 ──

def _expand_path(template, profile_path=None):
    for k, v in {"localappdata":os.environ.get("LOCALAPPDATA",""),"appdata":os.environ.get("APPDATA",""),"userprofile":os.environ.get("USERPROFILE",""),"home":os.environ.get("USERPROFILE") or os.path.expanduser("~"),"profile":profile_path or ""}.items():
        template = template.replace("{"+k+"}", v)
    return template

def detect_browser_profiles(browser_key):
    cfg = BROWSER_CONFIG.get(browser_key)
    if not cfg or cfg.get("engine") != "chromium": return []
    profiles, seen = [], set()
    for base_tmpl in cfg.get("base_dirs",[]):
        base = _expand_path(base_tmpl)
        if not os.path.isdir(base): continue
        for name in ["Default"]+[f"Profile {i}" for i in range(1,20)]:
            d = os.path.join(base, name)
            if d in seen: continue
            seen.add(d)
            if os.path.isdir(d):
                for ct in cfg.get("cookies_paths",[]):
                    cp = _expand_path(ct, d)
                    if os.path.isfile(cp):
                        profiles.append((d, name if name=="Default" else name.replace("Profile ","用户")))
                        break
    return profiles

def find_cookies_file(browser_key, profile_index=0):
    cfg = BROWSER_CONFIG.get(browser_key)
    if not cfg: return None, None
    if cfg.get("engine")=="chromium":
        profs = detect_browser_profiles(browser_key)
        if profile_index < len(profs):
            pd, dn = profs[profile_index]
            for ct in cfg.get("cookies_paths",[]):
                p = _expand_path(ct, pd)
                if os.path.isfile(p): return p, dn
        return None, None
    if cfg.get("engine")=="gecko":
        ff = os.path.join(os.environ.get("APPDATA",""),"Mozilla","Firefox","Profiles")
        if os.path.isdir(ff):
            for item in os.listdir(ff):
                dp = os.path.join(ff, item, "cookies.sqlite")
                if os.path.isfile(dp): return dp, item.split(".")[-1][:20] if "." in item else item
    return None, None

def detect_installed_browsers():
    result = {}
    for k in sorted(BROWSER_CONFIG, key=lambda k: BROWSER_CONFIG[k]["priority"]):
        cfg = BROWSER_CONFIG[k]
        e = {"installed":False,"profiles":0,"label":cfg["label"],"key":k}
        if cfg.get("engine")=="chromium":
            profs = detect_browser_profiles(k)
            if profs: e["installed"], e["profiles"] = True, len(profs)
        elif cfg.get("engine")=="gecko":
            dp, _ = find_cookies_file(k)
            if dp: e["installed"], e["profiles"] = True, 1
        result[k] = e
    return result

def get_available_browsers(exclude=None):
    inst = detect_installed_browsers()
    es = set() if exclude is None else ({exclude} if isinstance(exclude,str) else set(exclude))
    return [k for k in sorted(inst,key=lambda k:BROWSER_CONFIG[k]["priority"]) if inst[k]["installed"] and k not in es]

def get_alt_browsers(cur): return get_available_browsers(exclude=cur)

def copy_cookies_db(browser_key, profile_index=0):
    src, _ = find_cookies_file(browser_key, profile_index)
    if not src: return None
    for _ in range(2):
        try:
            fd, tp = tempfile.mkstemp(suffix=".sqlite", prefix=f"cookies_{browser_key}_")
            os.close(fd)
            shutil.copy2(src, tp)
            conn = sqlite3.connect(tp)
            conn.execute("SELECT count(*) FROM cookies"); conn.close()
            return tp
        except Exception:
            continue
    return None

def try_browser_cookie3_export(browser_key, output_path):
    """导出 Netscape 格式 cookies.txt。失败返回 False。"""
    try:
        import browser_cookie3
        loaders = {"chrome":browser_cookie3.chrome,"edge":browser_cookie3.edge,"firefox":browser_cookie3.firefox,"opera":browser_cookie3.opera,"brave":browser_cookie3.brave}
        loader = loaders.get(browser_key)
        if not loader: return False
        cj = loader()
        if not cj: return False
        with open(output_path,"w",encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n# Generated by video-fetcher\n\n")
            for c in cj:
                dom = c.domain if not c.domain.startswith(".") else c.domain
                flag = "TRUE" if dom.startswith(".") else "FALSE"
                sec = "TRUE" if c.secure else "FALSE"
                exp = str(int(c.expires)) if c.expires else "0"
                f.write(f"{dom}\t{flag}\t{c.path}\t{sec}\t{exp}\t{c.name}\t{c.value}\n")
        return True
    except ImportError:
        return False
    except Exception:
        return False

def get_browser_cookies_args(browser_key, profile_index=0, use_temp_copy=True):
    """原生→--cookies-from-browser | 非原生→browser_cookie3 导出 Netscape→--cookies"""
    cfg = BROWSER_CONFIG.get(browser_key)
    if not cfg: return [], None
    if cfg.get("native") and cfg.get("yt_name"):
        return ["--cookies-from-browser", cfg["yt_name"]], None
    # 非原生：必须走 browser_cookie3 导出 Netscape（不能直接传 SQLite）
    cookies_path, _ = find_cookies_file(browser_key, profile_index)
    if not cookies_path: return [], None
    tmp_ns = os.path.join(tempfile.gettempdir(), f"video_fetcher_{browser_key}_cookies.txt")
    if try_browser_cookie3_export(browser_key, tmp_ns):
        if os.path.isfile(tmp_ns) and os.path.getsize(tmp_ns) > 100:
            return ["--cookies", tmp_ns], tmp_ns
    label = cfg.get("label", browser_key)
    print(f"[video-fetcher] {label} needs browser_cookie3 (pip install browser-cookie3), skipped")
    return [], None

def is_cookie_lock_error(stderr_text):
    if not stderr_text: return False
    lower = stderr_text.lower()
    for kw in ["could not copy","cookie database","unsupported browser","permission denied","database is locked","sqlite_busy","locked","access denied","sharing violation","being used by another process"]:
        if kw in lower: return True
    return "cookies" in lower and ("error" in lower or "fail" in lower)

# ── 配置 / yt-dlp ──

def normalize_douyin_url(url):
    import re
    m = re.search(r'douyin\.com/\S*\?.*modal_id=(\d+)', url)
    return f"https://www.douyin.com/video/{m.group(1)}" if m else url

def load_config(config_path=None):
    if config_path is None: config_path = Path(__file__).parent / "config.json"
    if Path(config_path).exists():
        with open(config_path,"r",encoding="utf-8") as f: return json.load(f)
    return {}

def get_platform_presets(platform, config):
    preset = PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["generic"])
    high, fallback = dict(preset["high"]), dict(preset["fallback"]) if preset.get("fallback") else None
    user = config.get("platforms",{}).get(platform,{})
    high.update(user)
    if fallback: fallback.update(user)
    return high, fallback

def build_yt_dlp_args(url, output_dir, platform_opts, config, use_cookies, extra_args=None):
    args = ["yt-dlp", url, "-o", str(Path(output_dir) / "%(title).100s [%(id)s].%(ext)s")]
    if use_cookies:
        cf = config.get("cookies_file"); cb = config.get("cookies_from_browser")
        if cf and Path(cf).exists(): args += ["--cookies", cf]
        elif cb: args += get_browser_cookies_args(cb)[0]
    for k,v in platform_opts.items():
        if isinstance(v,bool):
            if v: args.append(f"--{k.replace('_','-')}")
        elif v is not None: args += [f"--{k.replace('_','-')}", str(v)]
    for k,v in config.get("yt_dlp_global",{}).items():
        if isinstance(v,bool):
            if v: args.append(f"--{k.replace('_','-')}")
        elif v is not None: args += [f"--{k.replace('_','-')}", str(v)]
    if extra_args: args.extend(extra_args)
    return args

def check_tool(name):
    try: subprocess.run([name,"--version"],capture_output=True,timeout=10); return True
    except FileNotFoundError: return False
    except Exception: return True

def check_output_exists(output_dir, after_timestamp=None):
    exts = {".mp4",".mkv",".webm",".flv",".ts",".mov",".avi",".3gp"}
    cand = []
    try:
        for f in Path(output_dir).iterdir():
            if not f.is_file() or f.suffix.lower() not in exts: continue
            if after_timestamp is not None and f.stat().st_mtime < after_timestamp: continue
            cand.append(f)
    except OSError: pass
    return cand

def cleanup_temp_files(output_dir):
    for pat in ["*.part","*.ytdl","*.temp.*","*.part-*"]:
        for f in Path(output_dir).glob(pat):
            try: f.unlink()
            except OSError: pass
    try:
        for f in Path(tempfile.gettempdir()).glob("cookies_*.sqlite"):
            if (time.time()-f.stat().st_mtime)>3600:
                try: f.unlink()
                except OSError: pass
    except OSError: pass

def _try_run(args, label=""):
    pfx = f"[{label}] " if label else ""
    print(f"{pfx}cmd: {' '.join(args)}\n{'-'*60}")
    proc = subprocess.run(args, capture_output=True, text=True)
    sys.stdout.write(proc.stdout or ""); sys.stderr.write(proc.stderr or "")
    return proc.returncode, proc.stderr

def _try_with_browser(url, output_dir, high_opts, config, browser_key, start_time, extra_args=None):
    cfg = BROWSER_CONFIG.get(browser_key,{})
    label = cfg.get("label", browser_key)
    temp_files = []
    alt_config = dict(config); alt_config["cookies_from_browser"] = browser_key
    browser_args, tmp_path = get_browser_cookies_args(browser_key)
    if tmp_path: temp_files.append(tmp_path)
    if not cfg.get("native") and not browser_args:
        return False, "browser_cookie3 not available", temp_files
    args = build_yt_dlp_args(url, output_dir, high_opts, alt_config, use_cookies=True, extra_args=extra_args)
    rc, stderr = _try_run(args, label)
    if rc == 0:
        print(f"[video-fetcher] {label} HD OK")
        return True, stderr, temp_files
    if check_output_exists(output_dir, start_time):
        return True, stderr, temp_files
    if is_cookie_lock_error(stderr):
        print(f"[video-fetcher] [!] {label} locked")
    else:
        print(f"[video-fetcher] {label} FAIL (exit={rc})")
    return False, stderr, temp_files

def fetch(url, platform="generic", output_dir=None, config_path=None, extra_args=None):
    config = load_config(config_path)
    if output_dir is None: output_dir = config.get("output_dir", str(Path.cwd()/"downloads"))
    url = normalize_douyin_url(url)
    if "modal_id" not in locals(): pass
    os.makedirs(output_dir, exist_ok=True)
    high, fallback = get_platform_presets(platform, config)
    start_time = time.time()

    installed = detect_installed_browsers()
    available = [k for k,v in installed.items() if v["installed"]]
    labels = ", ".join(BROWSER_CONFIG[k]["label"] for k in available) if available else "(none)"
    print(f"[video-fetcher] browsers: {labels}")
    pref = config.get("cookies_from_browser","")
    print(f"[video-fetcher] platform: {platform} | preferred: {pref or '(none)'}")
    print(f"[video-fetcher] output: {output_dir}")
    tried = set()

    # cookies file
    cf = config.get("cookies_file")
    if cf and Path(cf).exists():
        print(f"[video-fetcher] using cookies file: {cf}")
        rc, _ = _try_run(build_yt_dlp_args(url, output_dir, high, config, use_cookies=True, extra_args=extra_args), "file")
        if rc == 0: cleanup_temp_files(output_dir); return 0
        if check_output_exists(output_dir, start_time): cleanup_temp_files(output_dir); return 0

    # preferred
    if pref and installed.get(pref,{}).get("installed"):
        tried.add(pref)
        ok, stderr, _ = _try_with_browser(url, output_dir, high, config, pref, start_time, extra_args)
        if ok: cleanup_temp_files(output_dir); return 0
        if is_cookie_lock_error(stderr):
            print(f"[video-fetcher] retry in 2s..."); time.sleep(2)
            ok, _, _ = _try_with_browser(url, output_dir, high, config, pref, start_time, extra_args)
            if ok: cleanup_temp_files(output_dir); return 0
    elif pref:
        print(f"[video-fetcher] preferred '{pref}' not installed")

    # alternates
    alts = [b for b in get_available_browsers() if b not in tried]
    if alts:
        print(f"\n[video-fetcher] alternates: {', '.join(BROWSER_CONFIG[b]['label'] for b in alts)}")
    for b in alts:
        tried.add(b)
        ok, _, _ = _try_with_browser(url, output_dir, high, config, b, start_time, extra_args)
        if ok: cleanup_temp_files(output_dir); return 0

    # browser_cookie3 last resort
    print("\n[video-fetcher] trying browser_cookie3...")
    tmp_cf = os.path.join(tempfile.gettempdir(), "video_fetcher_cookies.txt")
    for bk in available:
        if try_browser_cookie3_export(bk, tmp_cf):
            if os.path.isfile(tmp_cf) and os.path.getsize(tmp_cf)>100:
                print(f"[video-fetcher] browser_cookie3 OK from {BROWSER_CONFIG[bk]['label']}")
                bc3 = dict(config); bc3["cookies_file"]=tmp_cf; bc3["cookies_from_browser"]=None
                rc, _ = _try_run(build_yt_dlp_args(url, output_dir, high, bc3, use_cookies=True, extra_args=extra_args), f"bc3/{bk}")
                if rc == 0: cleanup_temp_files(output_dir); return 0
                if check_output_exists(output_dir, start_time): cleanup_temp_files(output_dir); return 0
            break
    else:
        print("[video-fetcher] browser_cookie3 unavailable")
        print("[video-fetcher] hint: pip install browser-cookie3")

    if check_output_exists(output_dir, start_time): cleanup_temp_files(output_dir); return 0

    # fallback
    if fallback is None:
        print(f"\n[video-fetcher] {platform} needs login, no fallback.")
        cleanup_temp_files(output_dir); return 1
    print(f"\n[video-fetcher] fallback low quality (no cookies)")
    fb_args = build_yt_dlp_args(url, output_dir, fallback, config, use_cookies=False, extra_args=extra_args)
    proc = subprocess.run(fb_args, capture_output=True, text=True)
    sys.stdout.write(proc.stdout or ""); sys.stderr.write(proc.stderr or "")
    print("[video-fetcher] LQ OK" if proc.returncode==0 else f"[video-fetcher] LQ FAIL (exit={proc.returncode})")
    cleanup_temp_files(output_dir)
    return proc.returncode

def main():
    p = argparse.ArgumentParser(description="video-fetcher", epilog="auto-detect browsers + multi-fallback + lq safety net")
    p.add_argument("url"); p.add_argument("-p","--platform",choices=list(PLATFORM_PRESETS),default="generic")
    p.add_argument("-o","--output-dir",default=None); p.add_argument("-c","--config",default=None)
    p.add_argument("--list-browsers",action="store_true"); p.add_argument("--extra",nargs="*",default=[])
    args = p.parse_args()
    if args.list_browsers:
        for k,v in detect_installed_browsers().items():
            s = f"OK ({v['profiles']}P)" if v["installed"] else "NOT FOUND"
            print(f"  {v['label']:12s} {s}")
        return 0
    if not check_tool("yt-dlp"): print("[error] yt-dlp not found"); return 1
    return fetch(args.url, args.platform, args.output_dir, args.config, args.extra)

if __name__ == "__main__":
    sys.exit(main())
