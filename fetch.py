#!/usr/bin/env python3
"""video-fetcher — yt-dlp + ffmpeg multi-platform video downloader"""

import argparse, json, os, subprocess, sys, tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
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

_EXE_CACHE = {}

def _browser_executable_exists(browser_key):
    """Return whether a supported Chromium executable is installed.

    Cached: browser detection runs several times per fetch, and probing the
    filesystem again cannot change the answer within one process.
    """
    if browser_key in _EXE_CACHE:
        return _EXE_CACHE[browser_key]
    exists = False
    try:
        from _cdp_cookies import _BROWSER_FINDERS
        finder = _BROWSER_FINDERS.get(browser_key)
        exists = bool(finder and finder())
    except (ImportError, OSError):
        exists = False
    _EXE_CACHE[browser_key] = exists
    return exists

PLATFORM_PRESETS = {
    "bilibili":{"high":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":{"format":"bestvideo[height<=720]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True}},
    "youtube":{"high":{"format":"bestvideo[height<=2160]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"write_auto_subs":True,"sub_langs":"zh-Hans,en","no_playlist":True},"fallback":{"format":"bestvideo[height<=720]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True}},
    # NOTE: douyin has no separate low-quality tier. yt-dlp's Douyin extractor
    # offers exactly one format string, so a no-cookie "LQ" retry would repeat the
    # identical request that just failed (verified in logs/gui_20260924.log:
    # "falling back to low quality" -> same HTTP 403 three seconds later).
    # same_tier() below detects that and skips the repeat instead of faking a
    # quality downgrade. Login problems are reported by fetch()'s douyin branch.
    "douyin":{"high":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True,"add_header":["User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36","Referer:https://www.douyin.com/"]}},
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
            # Only pay for an executable probe when profiles gave no evidence.
            e["installed"], e["profiles"] = bool(p) or _browser_executable_exists(k), len(p)
        elif c.get("engine")=="gecko":
            dp,_ = find_cookies_file(k)
            if dp: e["installed"], e["profiles"] = True, 1
        r[k] = e
    return r

def available_browsers_from(installed, exclude=None):
    """Installed browser keys in priority order, derived from a detection result.

    Lets callers reuse one detect_installed_browsers() pass instead of walking
    every browser profile again for the same answer.
    """
    es = set() if exclude is None else ({exclude} if isinstance(exclude,str) else set(exclude))
    return [k for k in sorted(installed,key=lambda k:BROWSER_CONFIG[k]["priority"])
            if installed[k].get("installed") and k not in es]

def get_available_browsers(exclude=None):
    return available_browsers_from(detect_installed_browsers(), exclude=exclude)

def get_alt_browsers(cur): return get_available_browsers(exclude=cur)

def is_cookie_lock_error(stderr_text):
    if not stderr_text: return False
    l = stderr_text.lower()
    for kw in ["could not copy","cookie database","unsupported browser","permission denied","database is locked","sqlite_busy","locked","access denied","sharing violation"]:
        if kw in l: return True
    return "cookies" in l and ("error" in l or "fail" in l)

def is_douyin_web_detail_failure(output_text):
    """Recognize a Douyin web-detail failure that may involve verification.

    This signal alone cannot distinguish expired cookies from missing dynamic
    request signatures.  Callers should still try another independent cookie
    source before concluding that the extractor is blocked.
    """
    if not output_text:
        return False
    text = output_text.lower()
    if "web detail json" not in text:
        return False
    return any(marker in text for marker in (
        "fresh cookies (not necessarily logged in) are needed",
        "http error 403",
        "failed to parse json",
    ))

def _print_douyin_web_detail_help():
    print("[video-fetcher] Douyin rejected yt-dlp's web-detail request from every available cookie source.")
    print("[video-fetcher] Cookies may be expired, or Douyin may require a dynamic request signature.")
    print("[video-fetcher] If the freshly exported browser session can play this video, the current yt-dlp extractor is the likely limitation.")

# ─── browser_cookie3 — PREFERRED for ALL browsers ───

_BC3 = None
def _has_bc3():
    global _BC3
    if _BC3 is None:
        try: import browser_cookie3; _BC3 = True
        except ImportError: _BC3 = False
    return _BC3

_CDP = None
def _has_cdp(detected=None):
    """Check if CDP fallback is available (any supported Chromium browser).

    Pass a detect_installed_browsers() result to answer from that pass; the
    result is cached because probing every executable again cannot change it.
    """
    global _CDP
    if detected is not None:
        keys = _cdp_browser_keys()
        return bool(keys) and any(detected.get(k, {}).get("installed") for k in keys)
    if _CDP is None:
        _CDP = bool(_cdp_browser_keys())
    return _CDP

def _cdp_browser_keys():
    """Browser keys whose executable the CDP module can drive (empty if unavailable)."""
    try:
        from _cdp_cookies import _BROWSER_FINDERS
    except ImportError:
        return ()
    return tuple(k for k in _BROWSER_FINDERS if _browser_executable_exists(k))

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

_PLATFORM_DOMAINS = {
    "youtube": ("youtube.com", "youtu.be"),
    "bilibili": ("bilibili.com", "b23.tv"),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "twitter": ("twitter.com", "x.com"),
}

# Try public access before touching browser cookies. If that fails (for example,
# a protected tweet), the normal cookie chain still runs.
_PUBLIC_FIRST_PLATFORMS = {"twitter", "generic"}

def detect_platform(url):
    """Detect platform from URL. Returns platform key or 'generic'."""
    candidate = (url or "").strip()
    if not candidate:
        return "generic"
    try:
        parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    except ValueError:
        return "generic"
    host = (parsed.hostname or "").lower().rstrip(".")
    for platform, domains in _PLATFORM_DOMAINS.items():
        if any(host == domain or host.endswith(f".{domain}") for domain in domains):
            return platform
    return 'generic'

# Per-platform cookie login indicators
_COOKIE_LOGIN_CHECKS = {
    'douyin': {
        'domains': ('douyin.com',),
        'required': ['sessionid', 'passport'],
        # Douyin may accept fresh anonymous verification cookies such as
        # s_v_web_id; yt-dlp explicitly says login is not necessarily needed.
        'allow_any_cookie': True,
        'hint': 'Visit www.douyin.com in the browser first so it can set fresh verification cookies.',
    },
    'youtube': {
        'domains': ('youtube.com',),
        'required': ['LOGIN_INFO', 'SID'],
        'hint': 'Log into youtube.com in your browser first.',
    },
    'twitter': {
        'domains': ('twitter.com', 'x.com'),
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

    targets = check['domains']
    required = set(check['required'])
    found = set()

    try:
        with open(cookie_file, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#HttpOnly_'):
                    line = line[len('#HttpOnly_'):]
                elif not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    domain = parts[0].lstrip('.').lower()
                    name = parts[5]
                    if any(domain == target or domain.endswith(f'.{target}') for target in targets):
                        found.add(name)
    except Exception:
        return False, list(required), 'Could not read cookie file'

    # Some platforms (currently Douyin) can use fresh anonymous challenge
    # cookies, so login-cookie names must not be a hard gate for an attempt.
    if check.get('allow_any_cookie') and found:
        return True, [], ''

    # Otherwise at least one of the login indicators must be present.
    if not (required & found):
        missing_all = required - found
        return False, list(missing_all), check.get('hint', 'Login may be required.')
    return True, [], ''

def normalize_douyin_url(url):
    candidate = (url or "").strip()
    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        if host == "douyin.com" or host.endswith(".douyin.com"):
            modal_id = parse_qs(parsed.query).get("modal_id", [""])[0]
            if modal_id.isdigit():
                return f"https://www.douyin.com/video/{modal_id}"
    except ValueError:
        pass
    return candidate

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

def same_tier(a, b):
    """Return whether two presets request the identical download.

    Used to avoid repeating a request that cannot produce a different result:
    a fallback tier whose format (and extractor args) match the tier already
    attempted without cookies adds no new capability, only another failure.
    """
    if None in (a, b):
        return False
    keys = ("format", "extractor_args")
    return all(a.get(k) == b.get(k) for k in keys)

_same_tier = same_tier  # backwards-compatible private alias

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
    try:
        version_flag = "-version" if name.lower() in {"ffmpeg", "ffprobe", "ffplay"} else "--version"
        result = subprocess.run([name, version_flag],capture_output=True,timeout=10)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

def _utf8_subprocess_env():
    """Make Python-based tools emit UTF-8 even when stdout is a Windows pipe."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env

def _make_temp_cookie_file(browser_key, method):
    """Create a unique cookie path so simultaneous downloads cannot collide."""
    fd, path = tempfile.mkstemp(prefix=f"vf_{method}_{browser_key}_", suffix=".txt")
    os.close(fd)
    return path

def _remove_temp_cookie_file(path):
    try:
        os.unlink(path)
    except OSError:
        pass

def _try_run(args, label=""):
    pfx = f"[{label}] " if label else ""
    _safe_print(f"{pfx}cmd: {' '.join(args)}\n{'-'*60}")
    # Use Popen for real-time output (important for long downloads)
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1,
                                env=_utf8_subprocess_env())
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
    stderr_tail = "\n".join(tail_lines) if tail_lines else ""
    return proc.returncode, stderr_tail

# ─── core: per-browser attempt ───

def try_browser_cookies(url, output_dir, high_opts, config, browser_key, platform="generic",
                        extra_args=None, emit=None, run=None, is_cancelled=None):
    """Try one browser as a cookie source. native > bc3 > yt-dlp DPAPI.

    This is the single implementation of the cookie chain; fetch() and the GUI
    both call it, so a change here reaches both instead of drifting apart.

    emit(text)            — optional progress sink (defaults to stdout print)
    run(args, label)      — optional download runner, returns (exit_code, tail)
    is_cancelled()        — optional predicate; a True result aborts the chain

    Returns (ok, diagnostic): ``ok`` is True when a download succeeded, and
    ``diagnostic`` always carries the most useful reason a step was skipped or
    failed (so callers can log why instead of an empty string).
    """
    if emit is None:
        emit = lambda text="": print(text)
    if run is None:
        run = _try_run
    if is_cancelled is None:
        is_cancelled = lambda: False

    cfg = BROWSER_CONFIG.get(browser_key, {})
    label = cfg.get("label", browser_key)
    log("debug", f"try browser: {browser_key} (label={label})")

    def attempt(cookie_file, tail_label):
        """Run yt-dlp with one exported cookie file."""
        cfg_used = dict(config)
        cfg_used["cookies_file"] = cookie_file
        cfg_used["cookies_from_browser"] = None
        args = build_yt_dlp_args(url, output_dir, high_opts, cfg_used, use_cookies=True, extra_args=extra_args)
        return run(args, tail_label)

    # Step 0: zero-dep native export (_cookie_crypto, ctypes DPAPI)
    if _native_export:
        tmp = _make_temp_cookie_file(browser_key, "native")
        try:
            if _native_export(browser_key, tmp) and os.path.isfile(tmp) and os.path.getsize(tmp) > 100:
                logged, missing, hint = check_cookie_login(tmp, platform)
                if not logged:
                    note = f"{label}: native OK but {platform} not logged in (missing: {', '.join(missing)}). {hint}"
                    emit(f"[video-fetcher] {note}")
                    return False, note
                emit(f"[video-fetcher] {label}: native OK ({os.path.getsize(tmp)}B)")
                rc, stderr = attempt(tmp, label)
                if rc == 0:
                    emit(f"[video-fetcher] {label} HD OK")
                    return True, stderr
                if is_cookie_lock_error(stderr):
                    emit(f"[video-fetcher] [!] {label} locked")
                else:
                    emit(f"[video-fetcher] {label} native FAIL (exit={rc})")
                return False, stderr
            emit(f"[video-fetcher] {label}: native skipped (v20 App-Bound Encryption or DB locked)")
        except Exception as e:
            emit(f"[video-fetcher] {label}: native error ({e})")
        finally:
            _remove_temp_cookie_file(tmp)

    if is_cancelled():
        return False, "cancelled"

    # Step 1: browser_cookie3 (pip install browser-cookie3)
    tmp = _make_temp_cookie_file(browser_key, "bc3")
    try:
        if bc3_export(browser_key, tmp) and os.path.isfile(tmp) and os.path.getsize(tmp) > 100:
            logged, missing, hint = check_cookie_login(tmp, platform)
            if not logged:
                note = f"{label}: bc3 OK but {platform} not logged in (missing: {', '.join(missing)}). {hint}"
                emit(f"[video-fetcher] {note}")
                return False, note
            emit(f"[video-fetcher] {label}: bc3 OK ({os.path.getsize(tmp)}B)")
            rc, stderr = attempt(tmp, label)
            if rc == 0:
                emit(f"[video-fetcher] {label} HD OK")
                return True, stderr
            if is_cookie_lock_error(stderr):
                emit(f"[video-fetcher] [!] {label} locked")
            else:
                emit(f"[video-fetcher] {label} bc3 FAIL (exit={rc})")
            return False, stderr
    finally:
        _remove_temp_cookie_file(tmp)

    if is_cancelled():
        return False, "cancelled"

    # Step 2: yt-dlp native DPAPI (only for native browsers)
    if cfg.get("native") and cfg.get("yt_name"):
        cfg_used = dict(config)
        cfg_used["cookies_from_browser"] = browser_key
        cfg_used["cookies_file"] = None
        args = build_yt_dlp_args(url, output_dir, high_opts, cfg_used, use_cookies=True, extra_args=extra_args)
        rc, stderr = run(args, f"{label} (yt-dlp)")
        if rc == 0:
            emit(f"[video-fetcher] {label} HD OK (yt-dlp)")
            return True, stderr
        if is_cookie_lock_error(stderr):
            emit(f"[video-fetcher] [!] {label} DPAPI locked")
        else:
            emit(f"[video-fetcher] {label} DPAPI FAIL (exit={rc})")
        return False, stderr

    return False, "all cookie sources failed"


def _try_browser(url, output_dir, high_opts, config, browser_key, platform="generic", extra_args=None):
    """extract() wrapper around try_browser_cookies() that prints to stdout."""
    return try_browser_cookies(
        url, output_dir, high_opts, config, browser_key,
        platform=platform, extra_args=extra_args,
    )

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
    log("info", f"fetch start: url={url[:80]} platform={platform} output={output_dir}")

    # One detection pass feeds the banner, the cookie chain and the CDP check.
    installed = detect_installed_browsers()
    available = available_browsers_from(installed)
    labels = ", ".join(BROWSER_CONFIG.get(k, {}).get("label", k) for k in available) if available else "(none)"
    bc3_status = "available" if _has_bc3() else "NOT INSTALLED (pip install browser-cookie3)"
    cdp_status = "available" if _has_cdp(installed) else "NOT AVAILABLE"
    print(f"[video-fetcher] browsers: {labels}")
    print(f"[video-fetcher] browser_cookie3: {bc3_status}")
    print(f"[video-fetcher] CDP cookie fallback: {cdp_status}")

    pref = config.get("cookies_from_browser","")
    print(f"[video-fetcher] platform: {platform} | preferred: {pref or '(none)'}")
    print(f"[video-fetcher] output: {output_dir}")
    
    public_first = platform in _PUBLIC_FIRST_PLATFORMS
    public_rc = None
    if public_first:
        print(f"[video-fetcher] {platform}: trying public access before browser cookies")
        public_rc, public_err = _try_run(
            build_yt_dlp_args(url, output_dir, high, config, use_cookies=False, extra_args=extra_args),
            "public",
        )
        if public_rc == 0:
            return 0
        _log_ytdlp_fail("public", public_rc, public_err)
    
    tried = set()
    douyin_web_detail_seen = False
    douyin_web_detail_rc = 1
    # Exit code of the last real download attempt, so a skipped redundant
    # fallback can still report why the whole run failed.
    last_rc = public_rc if public_rc is not None else 1

    # cookies file
    cf = config.get("cookies_file")
    if cf and Path(cf).exists():
        logged, missing, hint = check_cookie_login(cf, platform)
        if not logged:
            print(f"[video-fetcher] cookies-file: {platform} not logged in (missing: {', '.join(missing)})")
            print(f"[video-fetcher]  → {hint}")
        else:
            print(f"[video-fetcher] using cookies file: {cf}")
            rc,stderr_f = _try_run(build_yt_dlp_args(url, output_dir, high, config, use_cookies=True, extra_args=extra_args), "file")
            if rc==0: return 0
            last_rc = rc
            _log_ytdlp_fail("cookies-file", rc, stderr_f)
            if platform == "douyin" and is_douyin_web_detail_failure(stderr_f):
                douyin_web_detail_seen = True
                douyin_web_detail_rc = rc

    # preferred
    if pref and installed.get(pref,{}).get("installed"):
        tried.add(pref)
        ok, stderr = _try_browser(url, output_dir, high, config, pref, platform, extra_args)
        if ok: return 0
        _log_ytdlp_fail(pref, 1, stderr)
        if platform == "douyin" and is_douyin_web_detail_failure(stderr):
            douyin_web_detail_seen = True
    elif pref:
        label = BROWSER_CONFIG.get(pref,{}).get("label", pref)
        print(f"[video-fetcher] '{label}' not installed")

    # alternates — reuse the detection pass already made above instead of
    # scanning every browser profile again.
    alts = [b for b in available if b not in tried]
    if alts:
        print(f"\n[video-fetcher] alternates: {', '.join(BROWSER_CONFIG.get(b, {}).get('label', b) for b in alts)}")
    for b in alts:
        tried.add(b)
        ok,stderr_a = _try_browser(url, output_dir, high, config, b, platform, extra_args)
        if ok: return 0
        _log_ytdlp_fail(b, 1, stderr_a)
        if platform == "douyin" and is_douyin_web_detail_failure(stderr_a):
            douyin_web_detail_seen = True

    # A no-cookie retry cannot improve on the same rejected web-detail request,
    # but only skip it after every available cookie source has had a chance.
    if platform == "douyin" and douyin_web_detail_seen:
        _print_douyin_web_detail_help()
        return douyin_web_detail_rc

    # fallback
    if fallback is None:
        print(f"\n[video-fetcher] {platform} needs login, abort."); return 1
    if fallback == high and public_rc is not None:
        print(f"\n[video-fetcher] public attempt already used the fallback settings; not repeating it")
        return public_rc
    # Same request, different label: the fallback adds nothing the no-cookie
    # attempt inside _try_browser has not already tried.
    if _same_tier(high, fallback):
        print(f"\n[video-fetcher] fallback tier is identical to the HD settings; skipping the repeat")
        return public_rc if public_rc is not None else last_rc
    print(f"\n[video-fetcher] fallback LQ (no cookies)")
    fb_args = build_yt_dlp_args(url, output_dir, fallback, config, use_cookies=False, extra_args=extra_args)
    rc, stderr_fb = _try_run(fb_args, "LQ-fallback")
    if rc != 0:
        _log_ytdlp_fail("LQ-fallback", rc, stderr_fb)
        print(f"[video-fetcher] LQ FAIL ({rc})")
        if platform == "douyin":
            if is_douyin_web_detail_failure(stderr_fb):
                _print_douyin_web_detail_help()
            else:
                print("[video-fetcher] Douyin cookies may be missing or expired.")
                print("[video-fetcher]    → Log into www.douyin.com, export cookies, then retry.")
    else:
        print("[video-fetcher] LQ OK")
    return rc

def main():
    set_log_file("fetch")
    p = argparse.ArgumentParser(description="video-fetcher", epilog="native > bc3 > yt-dlp DPAPI > LQ fallback")
    p.add_argument("url", nargs="?"); p.add_argument("-p","--platform",choices=list(PLATFORM_PRESETS),default="generic")
    p.add_argument("-o","--output-dir",default=None); p.add_argument("-c","--config",default=None)
    p.add_argument("--list-browsers",action="store_true")
    p.add_argument("--extra",nargs=argparse.REMAINDER,default=[],help="pass all remaining arguments to yt-dlp")
    args = p.parse_args()
    if args.list_browsers:
        for k,v in detect_installed_browsers().items():
            s = f"OK ({v['profiles']}P)" if v["installed"] else "NOT FOUND"
            print(f"  {v['label']:12s} {s}")
        print(f"\n  browser_cookie3: {'available' if _has_bc3() else 'NOT INSTALLED'}")
        return 0
    if not args.url:
        p.error("url is required unless --list-browsers is used")
    if not check_tool("yt-dlp"): log("error", "yt-dlp not found"); close_log(); return 1
    if not check_tool("ffmpeg"):
        log("warn", "ffmpeg not found; separate audio/video formats may fail to merge")
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
