#!/usr/bin/env python3
"""video-fetcher — yt-dlp + ffmpeg multi-platform video downloader"""

import argparse, json, os, shutil, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path
try: from _cookie_crypto import export_cookies as _native_export
except ImportError: _native_export = None
from _logger import log, set_log_file, close as close_log

BROWSER_CONFIG = {
    "chrome": {"yt_name":"chrome","native":True,"label":"Chrome","engine":"chromium","base_dirs":["{localappdata}/Google/Chrome/User Data"],"cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"],"priority":1},
    "edge": {"yt_name":"edge","native":True,"label":"Edge","engine":"chromium","base_dirs":["{localappdata}/Microsoft/Edge/User Data"],"cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"],"priority":2},
    "lenovo": {"yt_name":None,"native":False,"label":"Lenovo","engine":"chromium","base_dirs":["{localappdata}/Lenovo/SLBrowser/User Data","{localappdata}/Lenovo/SLB Browser/User Data","{localappdata}/Lenovo/LenovoBrowser/User Data"],"cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"],"priority":3},
    "brave": {"yt_name":"brave","native":True,"label":"Brave","engine":"chromium","base_dirs":["{localappdata}/BraveSoftware/Brave-Browser/User Data"],"cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"],"priority":4},
    "opera": {"yt_name":"opera","native":True,"label":"Opera","engine":"chromium","base_dirs":["{appdata}/Opera Software/Opera Stable"],"cookies_paths":["{profile}/Network/Cookies","{profile}/Cookies"],"priority":5},
    "firefox": {"yt_name":"firefox","native":True,"label":"Firefox","engine":"gecko","base_dirs":[],"cookies_paths":[],"priority":6},
}

PLATFORM_PRESETS = {
    "bilibili":{"high":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":{"format":"bestvideo[height<=720]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True}},
    "youtube":{"high":{"format":"bestvideo[height<=2160]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"write_auto_subs":True,"sub_langs":"zh-Hans,en","no_playlist":True,"extractor_args":"youtube:player_client=web"},"fallback":{"format":"bestvideo[height<=720]+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True,"extractor_args":"youtube:player_client=android,ios"}},
    "douyin":{"high":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":{"format":"bestvideo+bestaudio/best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True}},
    "twitter":{"high":{"format":"best","merge_output_format":"mp4","embed_metadata":True,"no_playlist":True},"fallback":None},
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
    hi.update(u)
    if fb: fb.update(u)
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
    print(f"{pfx}cmd: {' '.join(args)}\n{'-'*60}")
    proc = subprocess.run(args, capture_output=True, text=True)
    sys.stdout.write(proc.stdout or ""); sys.stderr.write(proc.stderr or "")
    return proc.returncode, proc.stderr

# ─── core: per-browser attempt ───

def _try_browser(url, output_dir, high_opts, config, browser_key, start_time, extra_args=None):
    """Try download via one browser. native > bc3 > yt-dlp DPAPI."""
    cfg = BROWSER_CONFIG.get(browser_key,{})
    label = cfg.get("label", browser_key)

    log("debug", f"try browser: {browser_key} (label={label})")
    # Step 0: zero-dep native export (_cookie_crypto, ctypes DPAPI)
    if _native_export:
        tmp = os.path.join(tempfile.gettempdir(), f"vf_native_{browser_key}_cookies.txt")
        try:
            if _native_export(browser_key, tmp) and os.path.isfile(tmp) and os.path.getsize(tmp)>100:
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

    return False, "all methods failed", None

# ─── main download flow ───

def fetch(url, platform="generic", output_dir=None, config_path=None, extra_args=None):
    config = load_config(config_path)
    if output_dir is None: output_dir = config.get("output_dir", str(Path.cwd()/"downloads"))
    url = normalize_douyin_url(url)
    os.makedirs(output_dir, exist_ok=True)
    high, fallback = get_platform_presets(platform, config)
    start_time = time.time()
    log("info", f"fetch start: url={url[:80]} platform={platform} output={output_dir}")

    installed = detect_installed_browsers()
    available = [k for k,v in installed.items() if v["installed"]]
    labels = ", ".join(BROWSER_CONFIG[k]["label"] for k in available) if available else "(none)"
    bc3_status = "available" if _has_bc3() else "NOT INSTALLED (pip install browser-cookie3)"
    print(f"[video-fetcher] browsers: {labels}")
    print(f"[video-fetcher] browser_cookie3: {bc3_status}")

    pref = config.get("cookies_from_browser","")
    print(f"[video-fetcher] platform: {platform} | preferred: {pref or '(none)'}")
    print(f"[video-fetcher] output: {output_dir}")
    tried = set()

    # cookies file
    cf = config.get("cookies_file")
    if cf and Path(cf).exists():
        print(f"[video-fetcher] using cookies file: {cf}")
        rc,_ = _try_run(build_yt_dlp_args(url, output_dir, high, config, use_cookies=True, extra_args=extra_args), "file")
        if rc==0: cleanup_temp_files(output_dir); return 0
        if check_output_exists(output_dir, start_time): cleanup_temp_files(output_dir); return 0

    # preferred
    if pref and installed.get(pref,{}).get("installed"):
        tried.add(pref)
        ok, stderr = _try_browser(url, output_dir, high, config, pref, start_time, extra_args)
        if ok: cleanup_temp_files(output_dir); return 0
        if is_cookie_lock_error(stderr or ""):
            time.sleep(2)
            ok,_ = _try_browser(url, output_dir, high, config, pref, start_time, extra_args)
            if ok: cleanup_temp_files(output_dir); return 0
    elif pref:
        label = BROWSER_CONFIG.get(pref,{}).get("label", pref)
        print(f"[video-fetcher] '{label}' not installed")

    # alternates
    alts = [b for b in get_available_browsers() if b not in tried]
    if alts:
        print(f"\n[video-fetcher] alternates: {', '.join(BROWSER_CONFIG[b]['label'] for b in alts)}")
    for b in alts:
        tried.add(b)
        ok,_ = _try_browser(url, output_dir, high, config, b, start_time, extra_args)
        if ok: cleanup_temp_files(output_dir); return 0

    if check_output_exists(output_dir, start_time): cleanup_temp_files(output_dir); return 0

    # fallback
    if fallback is None:
        print(f"\n[video-fetcher] {platform} needs login, abort."); cleanup_temp_files(output_dir); return 1
    print(f"\n[video-fetcher] fallback LQ (no cookies)")
    fb_args = build_yt_dlp_args(url, output_dir, fallback, config, use_cookies=False, extra_args=extra_args)
    proc = subprocess.run(fb_args, capture_output=True, text=True)
    sys.stdout.write(proc.stdout or ""); sys.stderr.write(proc.stderr or "")
    print("[video-fetcher] LQ OK" if proc.returncode==0 else f"[video-fetcher] LQ FAIL ({proc.returncode})")
    cleanup_temp_files(output_dir)
    return proc.returncode

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
