#!/usr/bin/env python3
"""video-fetcher — yt-dlp + ffmpeg multi-platform video downloader"""

import argparse, json, os, shutil, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path
try: from _cookie_crypto import export_cookies as _native_export
except ImportError: _native_export = None
from _logger import log, set_log_file, close as close_log

# ─── console encoding guard ───
# Windows 控制台默认 GBK，yt-dlp 输出含 U+FFFD/emoji 时 print() 会抛
# UnicodeEncodeError，导致下载流程中断（标题含中文的视频几乎必现）。
def _force_utf8_streams():
    for name in ("stdout", "stderr"):
        st = getattr(sys, name, None)
        if st is None:
            continue
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

_force_utf8_streams()

def _safe_print(text=""):
    try:
        print(text)
    except UnicodeEncodeError:
        st = getattr(sys, "stdout", None)
        if st is None:
            return
        enc = getattr(st, "encoding", None) or "utf-8"
        try:
            st.write(str(text).encode(enc, "replace").decode(enc, "replace") + "\n")
        except Exception:
            try:
                st.write(str(text).encode("ascii", "replace").decode("ascii") + "\n")
            except Exception:
                pass

BROWSER_CONFIG = {
    "chrome": {"yt_name":"chrome","native":True,"label":"Chrome","engine":"chromium","base_dirs":["{localappdata}/Google/Chrome/User Data"],"cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"],"priority":1},
    "edge": {"yt_name":"edge","native":True,"label":"Edge","engine":"chromium","base_dirs":["{localappdata}/Microsoft/Edge/User Data"],"cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"],"priority":2},
    "brave": {"yt_name":"brave","native":True,"label":"Brave","engine":"chromium","base_dirs":["{localappdata}/BraveSoftware/Brave-Browser/User Data"],"cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"],"priority":3},
    "opera": {"yt_name":"opera","native":True,"label":"Opera","engine":"chromium","base_dirs":["{appdata}/Opera Software/Opera Stable"],"cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"],"priority":4},
    "firefox": {"yt_name":"firefox","native":True,"label":"Firefox","engine":"gecko","base_dirs":[],"cookies_paths":[],"priority":5},
}

PLATFORM_PRESETS = {
    "bilibili":{"high":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":{"format":"bestvideo[height<=720]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True}},
    "youtube":{"high":{"format":"bestvideo[height<=2160]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"write_auto_subs":True,"sub_langs":"zh-Hans,en","no_playlist":True,"extractor_args":"youtube:player_client=web"},"fallback":{"format":"bestvideo[height<=720]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True,"extractor_args":"youtube:player_client=android,ios"}},
    "douyin":{"high":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True,"add_header":["User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36","Referer:https://www.douyin.com/"]},"fallback":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True,"add_header":["User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36","Referer:https://www.douyin.com/"]}},
    # NOTE: twitter DOES have a usable no-cookie tier. Public video tweets are
    # extractable without auth (verified against three public tweets: identical
    # 720p results with and without cookies). Setting fallback=None here made
    # fetch() abort with "twitter needs login" instead of even trying.
    "twitter":{"high":{"format":"best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":{"format":"best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True}},
    "generic":{"high":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":{"format":"best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True}},
}

# ─── tools ───

def _expand_path(t, profile_path=None):
    m = {"localappdata":os.environ.get("LOCALAPPDATA",""),"appdata":os.environ.get("APPDATA",""),"userprofile":os.environ.get("USERPROFILE",""),"home":os.environ.get("USERPROFILE") or os.path.expanduser("~"),"profile":profile_path or ""}
    for k,v in m.items(): t = t.replace("{"+k+"}", v)
    return t

def detect_browser_profiles(browser_key):
    cfg = BROWSER_CONFIG.get(browser_key)
    if not cfg or cfg.get("engine")!="chromium": return []
    profiles, seen = [], set()
    for b in cfg.get("base_dirs",[]):
        base = _expand_path(b)
        if not os.path.isdir(base): continue
        for n in ["Default"]+[f"Profile {i}" for i in range(1,20)]:
            d = os.path.join(base, n)
            if d in seen: continue
            seen.add(d)
            if os.path.isdir(d):
                for ct in cfg.get("cookies_paths",[]):
                    cp = _expand_path(ct, d)
                    if os.path.isfile(cp): profiles.append((d, n if n=="Default" else "P"+n.split()[-1])); break
    return profiles

def find_cookies_file(browser_key, pi=0):
    cfg = BROWSER_CONFIG.get(browser_key)
    if not cfg: return None,None
    if cfg.get("engine")=="chromium":
        p = detect_browser_profiles(browser_key)
        if pi<len(p):
            d,dn = p[pi]
            for ct in cfg.get("cookies_paths",[]):
                cp = _expand_path(ct, d)
                if os.path.isfile(cp): return cp,dn
        return None,None
    if cfg.get("engine")=="gecko":
        f = os.path.join(os.environ.get("APPDATA",""),"Mozilla","Firefox","Profiles")
        if os.path.isdir(f):
            for it in os.listdir(f):
                dp = os.path.join(f,it,"cookies.sqlite")
                if os.path.isfile(dp): return dp,it.split(".")[-1][:20] if "." in it else it
    return None,None

def detect_installed_browsers():
    r = {}
    for k in sorted(BROWSER_CONFIG,key=lambda k:BROWSER_CONFIG[k]["priority"]):
        c = BROWSER_CONFIG[k]; e = {"installed":False,"profiles":0,"label":c["label"],"key":k}
        if c.get("engine")=="chromium":
            p = detect_browser_profiles(k)
            if p: e["installed"], e["profiles"] = True, len(p)
        elif c.get("engine")=="gecko":
            dp,_ = find_cookies_file(k)
            if dp: e["installed"], e["profiles"] = True, 1
        r[k] = e
    return r

def get_available_browsers(exclude=None):
    i = detect_installed_browsers()
    es = set() if exclude is None else ({exclude} if isinstance(exclude,str) else set(exclude))
    return [k for k in sorted(i,key=lambda k:BROWSER_CONFIG[k]["priority"]) if i[k]["installed"] and k not in es]

def get_alt_browsers(cur): return get_available_browsers(exclude=cur)

def is_cookie_lock_error(stderr_text):
    if not stderr_text: return False
    l = stderr_text.lower()
    for kw in ["could not copy","cookie database","unsupported browser","permission denied","database is locked","sqlite_busy","locked","access denied","sharing violation"]:
        if kw in l: return True
    return "cookies" in l and ("error" in l or "fail" in l)

# ─── browser_cookie3 — PREFERRED for ALL browsers ───

_BC3 = None
def _has_bc3():
    global _BC3
    if _BC3 is None:
        try: import browser_cookie3; _BC3 = True
        except ImportError: _BC3 = False
    return _BC3

_CDP = None
def _has_cdp():
    """Check if CDP fallback is available (any supported Chromium browser)."""
    global _CDP
    if _CDP is None:
        try:
            from _cdp_cookies import _BROWSER_FINDERS
            for finder in _BROWSER_FINDERS.values():
                if finder():
                    _CDP = True
                    break
            else:
                _CDP = False
        except ImportError:
            _CDP = False
    return _CDP

def bc3_export(browser_key, outpath):
    """Export cookies via browser_cookie3 → Netscape file. Works for ALL browsers."""
    if not _has_bc3(): return False
    try:
        import browser_cookie3
        loaders = {"chrome":browser_cookie3.chrome,"edge":browser_cookie3.edge,"firefox":browser_cookie3.firefox,"opera":browser_cookie3.opera,"brave":browser_cookie3.brave}
        loader = loaders.get(browser_key)
        if not loader: return False
        cj = loader()
        if not cj or len(cj)==0: return False
        with open(outpath,"w",encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n# video-fetcher\n\n")
            for c in cj:
                dom = c.domain if not c.domain.startswith(".") else c.domain
                flag = "TRUE" if dom.startswith(".") else "FALSE"
                sec = "TRUE" if c.secure else "FALSE"
                exp = str(int(c.expires)) if c.expires else "0"
                f.write(f"{dom}\t{flag}\t{c.path}\t{sec}\t{exp}\t{c.name}\t{c.value}\n")
        return True
    except Exception: return False

# ─── yt-dlp args ───

# ─── platform auto-detection ───

_URL_PLATFORM_MAP = [
    (r'(?:youtube\.com|youtu\.be)', 'youtube'),
    (r'bilibili\.com', 'bilibili'),
    (r'douyin\.com', 'douyin'),
    (r'(?:twitter\.com|x\.com)', 'twitter'),
]

# Platforms whose public content can be fetched WITHOUT cookies.
# NOTE: bilibili is intentionally NOT here — bilibili returns HTTP 412
# without cookies, so it must go through the cookie chain.
_NO_LOGIN_PLATFORMS = set()

def detect_platform(url):
    """Detect platform from URL. Returns platform key or 'generic'."""
    import re
    low = url.lower()
    for pattern, platform in _URL_PLATFORM_MAP:
        if re.search(pattern, low):
            return platform
    return 'generic'

# Per-platform cookie login indicators
_COOKIE_LOGIN_CHECKS = {
    'douyin': {
        'domain': 'douyin.com',
        'required': ['sessionid', 'passport'],
        'hint': 'Log into www.douyin.com in your browser first.',
    },
    'youtube': {
        'domain': 'youtube.com',
        'required': ['LOGIN_INFO', 'SID'],
        'hint': 'Log into youtube.com in your browser first.',
    },
    'twitter': {
        'domain': 'twitter.com',
        'required': ['auth_token', 'twid'],
        'hint': 'Log into twitter.com/x.com in your browser first.',
    },
}

def check_cookie_login(cookie_file, platform):
    """
    Check if a Netscape cookie file has login cookies for the given platform.
    Returns (is_logged_in, missing_names, hint_message).
    """
    check = _COOKIE_LOGIN_CHECKS.get(platform)
    if not check:
        return True, [], ''

    target = check['domain']
    required = set(check['required'])
    found = set()

    try:
        with open(cookie_file, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    domain = parts[0].lstrip('.')
                    name = parts[5]
                    if target in domain:
                        found.add(name)
    except Exception:
        return False, list(required), 'Could not read cookie file'

    # At least one of the required cookies must be present
    if not (required & found):
        missing_all = required - found
        return False, list(missing_all), check.get('hint', 'Login may be required.')
    return True, [], ''

def normalize_douyin_url(url):
    import re
    m = re.search(r'douyin\.com/\S*\?.*modal_id=(\d+)', url)
    return f"https://www.douyin.com/video/{m.group(1)}" if m else url

def load_config(cp=None):
    if cp is None: cp = Path(__file__).parent/"config.json"
    if Path(cp).exists():
        with open(cp,"r",encoding="utf-8") as f: return json.load(f)
    return {}

def get_platform_presets(platform, config):
    pr = PLATFORM_PRESETS.get(platform, PLATFORM_PRESETS["generic"])
    hi, fb = dict(pr["high"]), dict(pr["fallback"]) if pr.get("fallback") else None
    u = config.get("platforms",{}).get(platform,{})
    # User overrides apply to the HD preset ONLY.
    # The fallback preset must keep its own settings -- both its low-quality
    # format (bestvideo[height<=720]) and its fallback-specific extractor_args
    # (e.g. youtube player_client=android,ios, the only client that works with
    # no cookies). Merging user config into it silently replaced the 720p cap
    # with the user's HD format and could clobber the android/ios client,
    # defeating the purpose of the fallback tier.
    hi.update(u)
    return hi, fb

def build_yt_dlp_args(url, output_dir, opts, config, use_cookies, extra_args=None):
    args = ["yt-dlp", url, "-o", str(Path(output_dir)/"%(title).100s [%(id)s].%(ext)s")]
    if use_cookies:
        cf = config.get("cookies_file"); cb = config.get("cookies_from_browser")
        if cf and Path(cf).exists(): args += ["--cookies", cf]
        elif cb:
            c = BROWSER_CONFIG.get(cb,{})
            if c.get("native") and c.get("yt_name"): args += ["--cookies-from-browser", c["yt_name"]]
    for k,v in opts.items():
        if k.startswith("_"): continue   # documentation key (e.g. _extractor_note), not a yt-dlp flag
        flag = f"--{k.replace('_','-')}"
        if isinstance(v, list):
            for item in v: args += [flag, str(item)]
        elif isinstance(v, bool):
            if v: args.append(flag)
        elif v is not None: args += [flag, str(v)]
    for k,v in config.get("yt_dlp_global",{}).items():
        if k.startswith("_"): continue   # documentation key, not a yt-dlp flag
        flag = f"--{k.replace('_','-')}"
        if isinstance(v, list):
            for item in v: args += [flag, str(item)]
        elif isinstance(v, bool):
            if v: args.append(flag)
        elif v is not None: args += [flag, str(v)]
    if extra_args: args.extend(extra_args)
    return args

def check_tool(name):
    try: subprocess.run([name,"--version"],capture_output=True,timeout=10); return True
    except FileNotFoundError: return False
    except Exception: return True

def check_output_exists(output_dir, after_ts=None):
    exts = {".mp4",".mkv",".webm",".flv",".ts",".mov",".avi",".3gp"}
    cand = []
    try:
        for f in Path(output_dir).iterdir():
            if not f.is_file() or f.suffix.lower() not in exts: continue
            if after_ts is not None and f.stat().st_mtime < after_ts: continue
            cand.append(f)
    except OSError: pass
    return cand

def cleanup_temp_files(output_dir):
    for pat in ["*.part","*.ytdl","*.temp.*","*.part-*"]:
        for f in Path(output_dir).glob(pat):
            try: f.unlink()
            except OSError: pass

def _try_run(args, label=""):
    pfx = f"[{label}] " if label else ""
    _safe_print(f"{pfx}cmd: {' '.join(args)}\n{'-'*60}")
    # Use Popen for real-time output (important for long downloads)
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
    except Exception as e:
        print(f"[video-fetcher] launch failed: {e}")
        return 1, str(e)
    # Collect last N lines for error reporting
    tail_lines = []
    MAX_TAIL = 10
    for line in iter(proc.stdout.readline, ""):
        s = line.rstrip("\n")
        if s:
            _safe_print(s)
            tail_lines.append(s)
            if len(tail_lines) > MAX_TAIL:
                tail_lines.pop(0)
    proc.wait()
    stderr_tail = "\n".join(tail_lines[-5:]) if tail_lines else ""
    return proc.returncode, stderr_tail

# ─── core: per-browser attempt ───

def _try_browser(url, output_dir, high_opts, config, browser_key, start_time, platform="generic", extra_args=None):
    """Try download via one browser. native > bc3 > yt-dlp DPAPI."""
    cfg = BROWSER_CONFIG.get(browser_key,{})
    label = cfg.get("label", browser_key)

    log("debug", f"try browser: {browser_key} (label={label})")
    # Step 0: zero-dep native export (_cookie_crypto, ctypes DPAPI)
    if _native_export:
        tmp = os.path.join(tempfile.gettempdir(), f"vf_native_{browser_key}_cookies.txt")
        try:
            if _native_export(browser_key, tmp) and os.path.isfile(tmp) and os.path.getsize(tmp)>100:
                # Check login status for platforms that need it
                logged, missing, hint = check_cookie_login(tmp, platform)
                if not logged:
                    print(f"[video-fetcher] {label}: native OK but {platform} not logged in (missing: {', '.join(missing)})")
                    print(f"[video-fetcher]  → {hint}")
                    return False, ""
                print(f"[video-fetcher] {label}: native OK ({os.path.getsize(tmp)}B)")
                bc = dict(config); bc["cookies_file"]=tmp; bc["cookies_from_browser"]=None
                args = build_yt_dlp_args(url, output_dir, high_opts, bc, use_cookies=True, extra_args=extra_args)
                rc, stderr = _try_run(args, label)
                if rc==0: print(f"[video-fetcher] {label} HD OK"); return True, stderr
                if check_output_exists(output_dir, start_time): return True, stderr
                if is_cookie_lock_error(stderr): print(f"[video-fetcher] [!] {label} locked")
                else: print(f"[video-fetcher] {label} native FAIL (exit={rc})")
                return False, stderr
            else:
                print(f"[video-fetcher] {label}: native skipped (v20 App-Bound Encryption or DB locked)")
        except Exception as e:
            print(f"[video-fetcher] {label}: native error ({e})")

    # Step 1: browser_cookie3 (pip install browser-cookie3)
    tmp = os.path.join(tempfile.gettempdir(), f"vf_{browser_key}_cookies.txt")
    if bc3_export(browser_key, tmp):
        if os.path.isfile(tmp) and os.path.getsize(tmp)>100:
            print(f"[video-fetcher] {label}: bc3 OK ({os.path.getsize(tmp)}B)")
            bc = dict(config); bc["cookies_file"]=tmp; bc["cookies_from_browser"]=None
            args = build_yt_dlp_args(url, output_dir, high_opts, bc, use_cookies=True, extra_args=extra_args)
            rc, stderr = _try_run(args, label)
            if rc==0: print(f"[video-fetcher] {label} HD OK"); return True, stderr
            if check_output_exists(output_dir, start_time): return True, stderr
            if is_cookie_lock_error(stderr): print(f"[video-fetcher] [!] {label} locked")
            else: print(f"[video-fetcher] {label} bc3 FAIL (exit={rc})")
            return False, stderr

    # Step 2: yt-dlp native DPAPI (only for native browsers)
    if cfg.get("native") and cfg.get("yt_name"):
        ac = dict(config); ac["cookies_from_browser"]=browser_key; ac["cookies_file"]=None
        args = build_yt_dlp_args(url, output_dir, high_opts, ac, use_cookies=True, extra_args=extra_args)
        rc, stderr = _try_run(args, f"{label} (yt-dlp)")
        if rc==0: print(f"[video-fetcher] {label} HD OK (yt-dlp)"); return True, stderr
        if check_output_exists(output_dir, start_time): return True, stderr
        if is_cookie_lock_error(stderr): print(f"[video-fetcher] [!] {label} DPAPI locked")
        else: print(f"[video-fetcher] {label} DPAPI FAIL (exit={rc})")
        return False, stderr

    return False, "all methods failed"

def _log_ytdlp_fail(source, exit_code, stderr_text):
    """Log yt-dlp failure with stderr for diagnostics."""
    if stderr_text:
        lines = [l.strip() for l in stderr_text.split('\n') if l.strip()]
        # Prefer ERROR lines, fall back to last meaningful line
        err_lines = [l for l in lines if 'ERROR' in l or 'error' in l.lower()]
        if err_lines:
            summary = err_lines[-1]
        else:
            summary = lines[-1] if lines else 'no output'
        log("warn", f"[{source}] yt-dlp exited with code {exit_code}: {summary[:200]}")
    else:
        log("warn", f"[{source}] yt-dlp exited with code {exit_code}")


# ─── main download flow ───

def fetch(url, platform="generic", output_dir=None, config_path=None, extra_args=None):
    # Ensure file logging exists even when fetch() is called as an API
    # (main() already does this; set_log_file() is a no-op if a handle is open).
    set_log_file("fetch")
    config = load_config(config_path)
    if output_dir is None: output_dir = config.get("output_dir", str(Path.cwd()/"downloads"))
    url_new = normalize_douyin_url(url)
    if url_new != url:
        log("info", f"douyin url normalized: {url[:60]} -> {url_new[:60]}")
        url = url_new
    
    # Auto-detect platform — use detected if it's more specific than user selection
    detected = detect_platform(url)
    if detected != "generic":
        if platform == "generic":
            log("info", f"auto-detected platform: {detected} (was generic)")
            platform = detected
        elif platform != detected:
            log("warn", f"platform mismatch: user selected '{platform}' but URL is {detected}. Using {detected}.")
            platform = detected
    
    os.makedirs(output_dir, exist_ok=True)
    high, fallback = get_platform_presets(platform, config)
    start_time = time.time()
    log("info", f"fetch start: url={url[:80]} platform={platform} output={output_dir}")

    installed = detect_installed_browsers()
    available = [k for k,v in installed.items() if v["installed"]]
    labels = ", ".join(BROWSER_CONFIG.get(k, {}).get("label", k) for k in available) if available else "(none)"
    bc3_status = "available" if _has_bc3() else "NOT INSTALLED (pip install browser-cookie3)"
    cdp_status = "available" if _has_cdp() else "NOT AVAILABLE"
    print(f"[video-fetcher] browsers: {labels}")
    print(f"[video-fetcher] browser_cookie3: {bc3_status}")
    print(f"[video-fetcher] CDP cookie fallback: {cdp_status}")

    pref = config.get("cookies_from_browser","")
    print(f"[video-fetcher] platform: {platform} | preferred: {pref or '(none)'}")
    print(f"[video-fetcher] output: {output_dir}")
    
    # Skip browser cookie chain for platforms that don't need login
    skip_browsers = platform in _NO_LOGIN_PLATFORMS
    if skip_browsers:
        print(f"[video-fetcher] {platform}: skipping browser chain (no login needed)")
    
    tried = set()

    if not skip_browsers:
        # cookies file
        cf = config.get("cookies_file")
        if cf and Path(cf).exists():
            # Validate login status before attempting download
            logged, missing, hint = check_cookie_login(cf, platform)
            if not logged:
                print(f"[video-fetcher] cookies-file: {platform} not logged in (missing: {', '.join(missing)})")
                print(f"[video-fetcher]  → {hint}")
            else:
                print(f"[video-fetcher] using cookies file: {cf}")
                rc,stderr_f = _try_run(build_yt_dlp_args(url, output_dir, high, config, use_cookies=True, extra_args=extra_args), "file")
                if rc==0: cleanup_temp_files(output_dir); return 0
                _log_ytdlp_fail("cookies-file", rc, stderr_f)
                if check_output_exists(output_dir, start_time): cleanup_temp_files(output_dir); return 0

        # preferred
        if pref and installed.get(pref,{}).get("installed"):
            tried.add(pref)
            ok, stderr = _try_browser(url, output_dir, high, config, pref, start_time, platform, extra_args)
            if ok: cleanup_temp_files(output_dir); return 0
            _log_ytdlp_fail(pref, 1, stderr)
        elif pref:
            label = BROWSER_CONFIG.get(pref,{}).get("label", pref)
            print(f"[video-fetcher] '{label}' not installed")

        # alternates
        alts = [b for b in get_available_browsers() if b not in tried]
        if alts:
            print(f"\n[video-fetcher] alternates: {', '.join(BROWSER_CONFIG.get(b, {}).get('label', b) for b in alts)}")
        for b in alts:
            tried.add(b)
            ok,stderr_a = _try_browser(url, output_dir, high, config, b, start_time, platform, extra_args)
            if ok: cleanup_temp_files(output_dir); return 0
            _log_ytdlp_fail(b, 1, stderr_a)

    if check_output_exists(output_dir, start_time): cleanup_temp_files(output_dir); return 0

    # fallback
    if fallback is None:
        print(f"\n[video-fetcher] {platform} needs login, abort."); cleanup_temp_files(output_dir); return 1
    print(f"\n[video-fetcher] fallback LQ (no cookies)")
    fb_args = build_yt_dlp_args(url, output_dir, fallback, config, use_cookies=False, extra_args=extra_args)
    rc, stderr_fb = _try_run(fb_args, "LQ-fallback")
    if rc != 0:
        _log_ytdlp_fail("LQ-fallback", rc, stderr_fb)
        print(f"[video-fetcher] LQ FAIL ({rc})")
        if platform == "douyin":
            print("[video-fetcher] 💡 Douyin requires fresh browser cookies.")
            print("[video-fetcher]    → Log into www.douyin.com in Chrome or Edge.")
            print("[video-fetcher]    → Select that browser in the cookies dropdown, then retry.")
            print("[video-fetcher]    → Close the browser BEFORE downloading (avoids DB lock).")
            print("[video-fetcher]    → If cookies are v20 (Chrome 127+), try Firefox or bc3.")
    else:
        print("[video-fetcher] LQ OK")
    cleanup_temp_files(output_dir)
    return rc

def main():
    set_log_file("fetch")
    p = argparse.ArgumentParser(description="video-fetcher", epilog="native > bc3 > yt-dlp DPAPI > LQ fallback")
    p.add_argument("url"); p.add_argument("-p","--platform",choices=list(PLATFORM_PRESETS),default="generic")
    p.add_argument("-o","--output-dir",default=None); p.add_argument("-c","--config",default=None)
    p.add_argument("--list-browsers",action="store_true"); p.add_argument("--extra",nargs="*",default=[])
    args = p.parse_args()
    if args.list_browsers:
        for k,v in detect_installed_browsers().items():
            s = f"OK ({v['profiles']}P)" if v["installed"] else "NOT FOUND"
            print(f"  {v['label']:12s} {s}")
        print(f"\n  browser_cookie3: {'available' if _has_bc3() else 'NOT INSTALLED'}")
        return 0
    if not check_tool("yt-dlp"): log("error", "yt-dlp not found"); close_log(); return 1
    try:
        rc = fetch(args.url, args.platform, args.output_dir, args.config, args.extra)
        log("info", f"fetch done: exit={rc}")
        return rc
    except Exception:
        log("error", "unhandled exception", exc_info=True)
        close_log()
        raise
    finally:
        close_log()

if __name__ == "__main__":
    sys.exit(main())
