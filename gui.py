#!/usr/bin/env python3
"""
video-fetcher GUI — visual video download client
Start: python gui.py
"""

import os, sys, traceback, subprocess, threading
from _logger import log, set_log_file, close as close_log

def _popen_group_kwargs():
    """Start each download in its own process group for reliable cancellation."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}

def _terminate_process_tree(proc):
    """Terminate only the download process tree owned by this GUI."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return
        else:
            import signal
            os.killpg(proc.pid, signal.SIGTERM)
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        proc.terminate()
    except OSError:
        pass

def _crash_log(exc_info):
    try: log("error", "GUI crash", exc_info=True)
    except: pass
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_error.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n")
        traceback.print_exception(*exc_info, file=f)
    traceback.print_exception(*exc_info)
    print(f"\nerror log: {log_path}", file=sys.stderr)

def _check_tkinter():
    try: import tkinter; return True
    except ImportError:
        print("tkinter not available.", file=sys.stderr); return False

def main():
    if not _check_tkinter(): input("Press Enter..."); sys.exit(1)

    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from fetch import (
            PLATFORM_PRESETS, BROWSER_CONFIG,
            load_config, get_platform_presets, build_yt_dlp_args,
            check_tool, normalize_douyin_url,
            find_cookies_file, get_alt_browsers,
            is_cookie_lock_error, detect_installed_browsers,
            detect_browser_profiles, get_available_browsers,
            _expand_path, _has_bc3, bc3_export, _native_export, _has_cdp,
            detect_platform, _PUBLIC_FIRST_PLATFORMS, check_cookie_login,
            _make_temp_cookie_file, _remove_temp_cookie_file,
            _utf8_subprocess_env, is_douyin_web_detail_failure,
        )
    except ImportError as e:
        print(f"import failed: {e}", file=sys.stderr)
        input("Press Enter..."); sys.exit(1)

    class VideoFetcherGUI:
        def __init__(self, root):
            self.root = root
            self.root.title("Video Fetcher")
            self.root.geometry("760x600"); self.root.minsize(640, 480)
            self.config = load_config()
            self.process = None
            self._installed_browsers = {}
            self._last_failed = {}  # (url, platform) -> timestamp
            self._last_cfg = {}     # (url, platform) -> (browser, cookies_file)
            self._last_ytdlp_tail = ""
            self._cancel_event = threading.Event()
            self._fix_env()
            self._refresh_browser_detection()
            self._setup_ui()
            self._check_env()

        @staticmethod
        def _fix_env():
            import getpass
            u = os.environ.get("USERNAME") or getpass.getuser()
            h = os.environ.get("USERPROFILE") or f"C:\\Users\\{u}"
            if not os.environ.get("LOCALAPPDATA"): os.environ["LOCALAPPDATA"] = f"{h}\\AppData\\Local"
            if not os.environ.get("APPDATA"): os.environ["APPDATA"] = f"{h}\\AppData\\Roaming"

        def _refresh_browser_detection(self):
            self._installed_browsers = detect_installed_browsers()

        def _setup_ui(self):
            main = ttk.Frame(self.root, padding=12)
            main.pack(fill="both", expand=True)

            ttk.Label(main, text="Video URL").pack(anchor="w")
            uf = ttk.Frame(main); uf.pack(fill="x", pady=(2,8))
            self.url_var = tk.StringVar()
            self.url_entry = ttk.Entry(uf, textvariable=self.url_var, font=("Consolas",10))
            self.url_entry.pack(fill="x", expand=True)
            self.url_entry.bind("<Button-3>", self._right_click_url)
            self.url_var.trace_add("write", self._on_url_change)

            hr = ttk.Frame(main); hr.pack(fill="x", pady=(8,0))
            ttk.Label(hr, text="Cookies (HD needed)").pack(side="left")
            ttk.Button(hr, text="Refresh", command=self._on_refresh, width=8).pack(side="right")

            cr = ttk.Frame(main); cr.pack(fill="x", pady=(2,8))
            self.cookies_browser_var = tk.StringVar(value=self.config.get("cookies_from_browser",""))
            self._build_dropdown(cr)
            ttk.Label(cr, text="or").pack(side="left", padx=4)
            self.cookies_file_var = tk.StringVar(value=self.config.get("cookies_file",""))
            ttk.Entry(cr, textvariable=self.cookies_file_var, width=30).pack(side="left", padx=4)
            ttk.Button(cr, text="Browse...", command=self._browse_file, width=6).pack(side="left")
            self.cookies_status = ttk.Label(cr, text="", foreground="gray")
            self.cookies_status.pack(side="left", padx=8)
            ttk.Button(cr, text="Export & Use", command=self._export_and_use, width=12).pack(side="left")
            self._update_cookies_status()

            self.browser_bar = ttk.Label(main, text="", foreground="gray")
            self.browser_bar.pack(anchor="w", pady=(0,4))
            self._update_browser_bar()

            row = ttk.Frame(main); row.pack(fill="x", pady=4)
            ttk.Label(row, text="Platform").pack(side="left")
            self.platform_var = tk.StringVar(value="generic")
            ttk.Combobox(row, textvariable=self.platform_var, values=list(PLATFORM_PRESETS),
                         state="readonly", width=10).pack(side="left", padx=6)
            ttk.Label(row, text="Output").pack(side="left", padx=(12,0))
            self.output_var = tk.StringVar(value=self.config.get("output_dir", os.path.join(os.getcwd(),"downloads")))
            ttk.Entry(row, textvariable=self.output_var, font=("Consolas",9)).pack(side="left", fill="x", expand=True, padx=4)
            ttk.Button(row, text="Browse...", command=self._browse_output, width=6).pack(side="left")

            self.strat_label = ttk.Label(main, foreground="gray",
                text="Strategy: native DPAPI/CDP > bc3 > yt-dlp DPAPI > LQ fallback")
            self.strat_label.pack(anchor="w", pady=(4,6))

            bf = ttk.Frame(main); bf.pack(fill="x", pady=4)
            self.dl_btn = ttk.Button(bf, text="Download", command=self._start)
            self.dl_btn.pack(side="left", padx=(0,8))
            self.stop_btn = ttk.Button(bf, text="Stop", command=self._stop, state="disabled")
            self.stop_btn.pack(side="left")
            self.status_var = tk.StringVar(value="Ready")
            ttk.Label(bf, textvariable=self.status_var, foreground="gray").pack(side="right")

            lf = ttk.Frame(main); lf.pack(fill="both", expand=True, pady=(8,0))
            self.log = tk.Text(lf, wrap="word", state="disabled", font=("Consolas",9),
                               bg="#1e1e1e", fg="#d4d4d4", relief="flat", borderwidth=0, padx=8, pady=6)
            sb = ttk.Scrollbar(lf, command=self.log.yview)
            self.log.configure(yscrollcommand=sb.set)
            self.log.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
            for t,c in [("info","#569cd6"),("success","#6a9955"),("warn","#ce9178"),("error","#f44747"),("dim","#808080")]:
                self.log.tag_configure(t, foreground=c)

        def _build_dropdown(self, parent):
            ik = [k for k,v in self._installed_browsers.items() if v["installed"]]
            ak = sorted(BROWSER_CONFIG, key=lambda k: BROWSER_CONFIG[k]["priority"])
            ok = [k for k in ak if k not in ik]
            # Destroy old combobox if rebuilding (refresh)
            if hasattr(self, '_browser_combo') and self._browser_combo is not None:
                try: self._browser_combo.destroy()
                except: pass
            self._browser_combo = ttk.Combobox(parent, textvariable=self.cookies_browser_var,
                                                values=ik+ok, width=10)
            self._browser_combo.pack(side="left")
            self._browser_combo.bind("<<ComboboxSelected>>", self._on_cookies_change)
            self._dropdown_parent = parent  # remember for refresh

        def _update_browser_bar(self):
            inst = [k for k,v in self._installed_browsers.items() if v["installed"]]
            if inst:
                parts = [f"{BROWSER_CONFIG[k]['label']}({self._installed_browsers[k]['profiles']}P)" for k in inst if k in BROWSER_CONFIG]
                self.browser_bar.config(text=f"Detected: {', '.join(parts)}", foreground="#6a9955")
            else:
                self.browser_bar.config(text="No browsers detected", foreground="#ce9178")

        def _on_refresh(self):
            self._log("Refreshing browser detection...\n", "info")
            self._refresh_browser_detection()
            self._build_dropdown(getattr(self, '_dropdown_parent', self.cookies_browser_var))
            self._update_browser_bar()
            self._update_cookies_status()
            self._log("Done.\n", "success")

        def _check_env(self):
            if not check_tool("yt-dlp"): self._log("yt-dlp not found. pip install yt-dlp\n", "error"); self.dl_btn.config(state="disabled")
            if not check_tool("ffmpeg"): self._log("ffmpeg not found.\n", "warn")
            if _native_export: self._log("native cookie crypto: available\n", "success")
            else: self._log("native cookie crypto: AES backend missing (pip install cryptography)\n", "warn")
            if _has_bc3(): self._log("browser_cookie3: available\n", "success")
            else: self._log("browser_cookie3: NOT INSTALLED\n", "dim")
            if _has_cdp(): self._log("CDP cookie fallback: available\n", "success")
            else: self._log("CDP cookie fallback: NOT AVAILABLE\n", "dim")

        def _log(self, text, tag="info"):
            def w():
                self.log.configure(state="normal"); self.log.insert("end", text, tag)
                self.log.see("end"); self.log.configure(state="disabled")
            self.root.after(0, w)

        def _clear_log(self):
            def c():
                self.log.configure(state="normal"); self.log.delete("1.0","end")
                self.log.configure(state="disabled")
            self.root.after(0, c)

        def _browse_output(self):
            p = filedialog.askdirectory(title="Select output directory")
            if p: self.output_var.set(p)

        def _browse_file(self):
            p = filedialog.askopenfilename(title="Select cookies.txt", filetypes=[("Cookies","*.txt"),("All","*.*")])
            if p: self.cookies_file_var.set(p); self._save_config(); self._update_cookies_status()

        def _right_click_url(self, event):
            m = tk.Menu(self.root, tearoff=0); m.add_command(label="Paste", command=self._paste)
            try: m.tk_popup(event.x_root, event.y_root)
            finally: m.grab_release()

        def _paste(self):
            try:
                t = self.root.clipboard_get().strip()
                if t: self.url_var.set(t)
            except Exception: pass

        def _on_url_change(self, *_):
            """Auto-detect platform when URL changes, override if mismatched."""
            url = self.url_var.get().strip()
            if not url:
                return
            detected = detect_platform(url)
            if detected != "generic":
                current = self.platform_var.get()
                if current == "generic":
                    self.platform_var.set(detected)
                    self._log(f"Auto-detected platform: {detected}\n", "info")
                elif current != detected:
                    self.platform_var.set(detected)
                    self._log(f"⚠ Platform changed: {current} → {detected} (auto-detected from URL)\n", "warn")
            # Normalize douyin URL
            if normalize_douyin_url(url) != url:
                self.url_var.set(normalize_douyin_url(url))

        def _on_cookies_change(self, event=None): self._save_config(); self._update_cookies_status()

        def _save_config(self):
            self.config["cookies_from_browser"] = self.cookies_browser_var.get().strip() or None
            self.config["cookies_file"] = self.cookies_file_var.get().strip() or None
            try:
                import json
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),"config.json"),"w",encoding="utf-8") as f:
                    json.dump(self.config, f, ensure_ascii=False, indent=2)
            except Exception: pass

        def _update_cookies_status(self):
            b = self.cookies_browser_var.get().strip()
            fp = self.cookies_file_var.get().strip()
            if fp:
                if os.path.isfile(fp): self.cookies_status.config(text=f"OK ({os.path.basename(fp)})", foreground="#6a9955")
                else: self.cookies_status.config(text="File not found", foreground="#f44747")
            elif b:
                label = BROWSER_CONFIG.get(b,{}).get("label", b)
                inst = self._installed_browsers.get(b,{}).get("installed", False)
                self.cookies_status.config(text=f"{label} ({'installed' if inst else 'not found'})", foreground="#ce9178")
            else: self.cookies_status.config(text="none (LQ only)", foreground="#808080")

        def _export_and_use(self):
            """Export cookies, save persistently, and activate for downloads."""
            b = self.cookies_browser_var.get().strip()
            if not b: messagebox.showinfo("Info", "Select a browser first."); return
            cfg = BROWSER_CONFIG.get(b,{}); label = cfg.get("label", b)
            self.cookies_status.config(text=f"Extracting {label}...", foreground="#808080")
            self._log(f"=== Cookie Export & Use: {label} ===\n", "info")

            # Check if cookies database exists
            found = False
            profs = detect_browser_profiles(b)
            if profs:
                self._log(f"  {len(profs)} profile(s) detected\n", "dim")
                for pd, pn in profs:
                    for ct in cfg.get("cookies_paths",[]):
                        p = _expand_path(ct, pd)
                        if os.path.isfile(p):
                            self._log(f"    {pn}: {p} ({os.path.getsize(p):,}B)\n", "dim")
                            found = True
            elif cfg.get("engine")=="gecko":
                dp,_ = find_cookies_file(b)
                if dp:
                    self._log(f"  {dp} ({os.path.getsize(dp):,}B)\n", "dim")
                    found = True
            if not found:
                self._log("  No cookies database found\n", "error")
                self.cookies_status.config(text=f"{label} not installed", foreground="#f44747")
                return

            # Persistent save directory
            save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies")
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, f"{b}.txt")

            # Step 1: native DPAPI export (falls back to CDP internally if needed)
            extracted = False
            if _native_export:
                self._log("  → native DPAPI export...\n", "dim")
                try:
                    if _native_export(b, save_path) and os.path.isfile(save_path) and os.path.getsize(save_path) > 100:
                        extracted = True
                        self._show_cookie_result(save_path, label, method="native DPAPI")
                    else:
                        self._log("  native export: no cookies (v20 encrypted or locked)\n", "warn")
                except Exception as e:
                    self._log(f"  native error: {e}\n", "warn")

            # Step 2: CDP fallback (any installed Chromium browser)
            if not extracted and cfg.get("engine") == "chromium":
                self._log(f"  → CDP fallback (launching {label}, ~15s)...\n", "dim")
                try:
                    from _cdp_cookies import export_cookies_cdp
                    if export_cookies_cdp(save_path, browser_key=b):
                        extracted = True
                        self._show_cookie_result(save_path, label, method="CDP (browser)")
                    else:
                        self._log("  CDP fallback failed\n", "warn")
                except ImportError:
                    self._log("  CDP module not available\n", "warn")
                except Exception as e:
                    self._log(f"  CDP error: {e}\n", "warn")

            # Step 3: bc3 fallback
            if not extracted:
                self._log("  → browser_cookie3 fallback...\n", "dim")
                if bc3_export(b, save_path) and os.path.isfile(save_path) and os.path.getsize(save_path) > 100:
                    extracted = True
                    self._show_cookie_result(save_path, label, method="browser_cookie3")
                else:
                    self._log("  bc3: no cookies or not installed\n", "warn")

            if not extracted:
                self._log("\n  ❌ All methods failed\n", "error")
                self.cookies_status.config(text=f"{label}: FAILED", foreground="#f44747")
                try: os.unlink(save_path)
                except: pass
                return

            # Success — activate exported cookies
            self.cookies_file_var.set(save_path)
            self.cookies_browser_var.set("")  # clear browser: using file now
            self._save_config()
            self._update_cookies_status()
            self._log(f"\n  💾 Saved: {save_path}\n", "info")
            self._log(f"  ✅ Active: exported cookies will be used for HD downloads\n", "success")

        def _show_cookie_result(self, cookie_file, label, method=""):
            """Parse exported cookie file and display stats."""
            sz = os.path.getsize(cookie_file)
            domains = set()
            total = 0
            try:
                with open(cookie_file, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        parts = line.split('\t')
                        if len(parts) >= 7:
                            total += 1
                            domains.add(parts[0].lstrip('.'))
            except Exception:
                pass

            method_str = f" ({method})" if method else ""
            self._log(f"\n  ✅ Exported{method_str}: {total} cookies, {len(domains)} domains, {sz:,}B\n", "success")
            if domains:
                top = sorted(domains, key=lambda d: d.count('.'), reverse=True)[:8]
                self._log(f"     Top domains: {', '.join(top)}\n", "dim")
            self.cookies_status.config(
                text=f"{label}: {total} cookies ({len(domains)} domains)", foreground="#6a9955"
            )

            # Check login status for key platforms
            for plat in ['douyin', 'youtube', 'twitter']:
                logged, missing, hint = check_cookie_login(cookie_file, plat)
                if not logged:
                    self._log(f"\n  ⚠ {plat}: NOT logged in (missing: {', '.join(missing)})\n", "warn")
                    self._log(f"     → {hint}\n", "dim")

        def _start(self):
            url = self.url_var.get().strip()
            if not url: messagebox.showwarning("Warning", "Enter a URL"); return
            plat = self.platform_var.get()
            out = self.output_var.get().strip()
            if not out: messagebox.showwarning("Warning", "Select output directory"); return
            try: os.makedirs(out, exist_ok=True)
            except OSError as e: messagebox.showerror("Error", str(e)); return
            # Editable fields may not have emitted a selection/browse event.
            # Synchronize what the user sees before taking the run snapshot.
            self._save_config()
            # Prevent rapid re-download of same failing URL
            key = (url, plat)
            import time
            last_ts = self._last_failed.get(key, 0)
            if time.time() - last_ts < 60:
                # Allow retry if configuration changed (different browser / cookies)
                cur_browser = self.cookies_browser_var.get().strip()
                cur_file = self.cookies_file_var.get().strip()
                cfg_key = (cur_browser, cur_file)
                prev_cfg = self._last_cfg.get(key, ("",""))
                if cfg_key != prev_cfg:
                    self._log(f"Config changed, allowing retry.\n", "info")
                else:
                    self._log(f"⚠ This URL+platform failed {int(time.time()-last_ts)}s ago. Skipping re-attempt.\n", "warn")
                    self._log("  Wait 60s or change platform/URL/cookies to retry.\n", "dim")
                    return
            self._last_cfg[key] = (self.cookies_browser_var.get().strip(), self.cookies_file_var.get().strip())

            self._clear_log()
            self._log(f"Platform: {plat}\nOutput: {out}\nURL: {url}\n\n", "info")
            self.dl_btn.config(state="disabled"); self.stop_btn.config(state="normal")
            self.status_var.set("Downloading..."); self.process = None
            self._cancel_event.clear()
            log("info", f"GUI download start: url={url[:80]} platform={plat} output={out}")
            threading.Thread(target=self._worker, args=(url, plat, out), daemon=True).start()

        def _stop(self):
            self._cancel_event.set()
            self._log("\n[Stop requested; partial file will be kept for resume]\n", "warn")
            self.status_var.set("Stopping...")
            _terminate_process_tree(self.process)

        def _done(self, ec, msg=""):
            def d():
                self.dl_btn.config(state="normal"); self.stop_btn.config(state="disabled")
                self.status_var.set(msg or ("Done" if ec==0 else f"FAIL ({ec})"))
                self._cancel_event.clear()
            self.root.after(0, d)
            # Record failure for duplicate prevention
            if ec != 0 and msg != "Stopped":
                import time
                url = self.url_var.get().strip()
                plat = self.platform_var.get()
                self._last_failed[(url, plat)] = time.time()

        def _worker(self, url, plat, out):
            import time, traceback
            try:
                self._worker_impl(url, plat, out)
            except Exception:
                self._log(f"\n‼ Worker crashed:\n{traceback.format_exc()}\n", "error")
                log("error", "worker thread crash", exc_info=True)
                self._done(1, "Worker error")

        def _worker_impl(self, url, plat, out):
            self._last_ytdlp_tail = ""
            url_new = normalize_douyin_url(url)
            if url_new != url:
                self._log(f"URL normalized: {url[:60]} -> {url_new[:60]}\n", "dim")
                url = url_new

            # Auto-detect platform — use detected if it's more specific than user selection
            detected = detect_platform(url)
            if detected != "generic":
                if plat == "generic":
                    self._log(f"Auto-detected platform: {detected}\n", "info")
                    plat = detected
                elif plat != detected:
                    self._log(f"⚠ Platform mismatch: you selected '{plat}' but URL is {detected}\n", "warn")
                    self._log(f"  → Using detected platform: {detected}\n", "info")
                    plat = detected

            high, fallback = get_platform_presets(plat, self.config)
            self._refresh_browser_detection()
            inst = self._installed_browsers
            avail = [k for k,v in inst.items() if v["installed"]]
            self._log(f"Platform: {plat}\n", "info")
            self._log(f"Browsers: {', '.join(BROWSER_CONFIG[k]['label'] for k in avail) if avail else '(none)'}\n", "info")

            public_first = plat in _PUBLIC_FIRST_PLATFORMS
            public_rc = None
            if public_first:
                self._log(f"{plat}: trying public access before browser cookies\n", "info")
                self._log("--- Public access ---\n", "info")
                rc = self._run(url, out, high, use_cookies=False)
                if rc == 0:
                    self._log("\nPublic access OK\n", "success"); self._done(0); return
                public_rc = rc
                if self._cancel_event.is_set(): self._done(1, "Stopped"); return
                self._log(f"Public access failed (exit={rc}), trying browser cookies...\n", "warn")

            pref = self.config.get("cookies_from_browser","")
            self._log(f"Preferred: {pref or '(none)'}\n", "info")
            douyin_web_detail_seen = False

            # cookies file
            cf = self.config.get("cookies_file")
            if cf and os.path.isfile(cf):
                self._log(f"Using cookies file: {cf}\n", "info")
                # Validate login status before attempting download
                logged, missing, hint = check_cookie_login(cf, plat)
                if not logged:
                    self._log(f"  ⚠ {plat}: cookies-file not logged in (missing: {', '.join(missing)})\n", "warn")
                    self._log(f"  → {hint}\n", "dim")
                else:
                    self._log("--- HD (file) ---\n", "info")
                    rc = self._run(url, out, high, use_cookies=True)
                    if rc==0: self._log("\nHD OK\n", "success"); self._done(0); return
                    if self._cancel_event.is_set(): self._done(1, "Stopped"); return
                    if plat == "douyin" and is_douyin_web_detail_failure(self._last_ytdlp_tail):
                        douyin_web_detail_seen = True

            tried = set()
            # preferred (single attempt, no retry — lock means move on)
            if pref and inst.get(pref,{}).get("installed"):
                tried.add(pref)
                self._log(f"--- HD ({BROWSER_CONFIG.get(pref,{}).get('label',pref)}) ---\n", "info")
                ok = self._try_bc3_then_native(url, out, high, pref, plat)
                if ok: self._done(0); return
                if self._cancel_event.is_set(): self._done(1, "Stopped"); return
                if plat == "douyin" and is_douyin_web_detail_failure(self._last_ytdlp_tail):
                    douyin_web_detail_seen = True
            elif pref:
                self._log(f"'{BROWSER_CONFIG.get(pref,{}).get('label',pref)}' not installed\n", "warn")

            # alternates
            alts = [b for b in (get_alt_browsers(pref) if pref else get_available_browsers()) if b not in tried]
            if alts:
                self._log(f"\nAlternates: {', '.join(BROWSER_CONFIG.get(b,{}).get('label',b) for b in alts)}\n", "info")
            for b in alts:
                tried.add(b)
                self._log(f"--- HD ({BROWSER_CONFIG.get(b,{}).get('label',b)}) ---\n", "info")
                ok = self._try_bc3_then_native(url, out, high, b, plat)
                if ok: self._done(0); return
                if self._cancel_event.is_set(): self._done(1, "Stopped"); return
                if plat == "douyin" and is_douyin_web_detail_failure(self._last_ytdlp_tail):
                    douyin_web_detail_seen = True

            if plat == "douyin" and douyin_web_detail_seen:
                self._report_douyin_web_detail_failure(); self._done(1, "Douyin blocked"); return

            log("warn", f"all browser attempts failed, falling back")
            # fallback
            if fallback is None:
                self._log(f"\n{plat} needs login, abort.\n", "error"); log("error", f"all methods failed for {plat}"); self._done(1); return
            if public_first and fallback == high and public_rc is not None:
                self._log("\nPublic attempt already used the fallback settings; not repeating it.\n", "warn")
                self._done(public_rc); return
            self._log("\n--- LQ fallback ---\n", "warn")
            log("info", "falling back to low quality")
            rc = self._run(url, out, fallback, use_cookies=False)
            if rc==0: self._log("\nLQ OK\n", "success"); log("info", "LQ success")
            else:
                self._log(f"\nLQ FAIL ({rc})\n", "error")
                log("error", f"LQ failed exit={rc}")
                # Platform-specific help
                if plat == "douyin":
                    if is_douyin_web_detail_failure(self._last_ytdlp_tail):
                        self._report_douyin_web_detail_failure()
                    else:
                        self._log("\nDouyin cookies may be missing or expired.\n", "dim")
                        self._log("   → Log into www.douyin.com, export cookies, then retry.\n", "dim")
            self._done(rc, "Stopped" if self._cancel_event.is_set() else "")

        def _report_douyin_web_detail_failure(self):
            self._log("\nDouyin rejected yt-dlp's web-detail request from every available cookie source.\n", "error")
            self._log("Cookies may be expired, or Douyin may require a dynamic request signature.\n", "warn")
            self._log("If the freshly exported browser session can play this video, the current yt-dlp extractor is the likely limitation.\n", "warn")

        def _try_bc3_then_native(self, url, out, high, bk, plat="generic"):
            """Try native > bc3 > yt-dlp DPAPI for one browser."""
            self._last_ytdlp_tail = ""
            if self._cancel_event.is_set():
                return False
            label = BROWSER_CONFIG.get(bk, {}).get("label", bk)

            # Step 0: zero-dep native export (may trigger the CDP fallback)
            if _native_export:
                try:
                    if BROWSER_CONFIG[bk].get("engine") == "chromium":
                        self._log(f"  native (may launch CDP)...\n", "dim")
                    tmp = _make_temp_cookie_file(bk, "native")
                    try:
                        if _native_export(bk, tmp) and os.path.isfile(tmp) and os.path.getsize(tmp)>100:
                            if self._cancel_event.is_set():
                                return False
                            self._log(f"  native OK ({os.path.getsize(tmp)}B)\n", "success")
                            logged, missing, hint = check_cookie_login(tmp, plat)
                            if not logged:
                                self._log(f"  ⚠ {plat}: not logged in (missing: {', '.join(missing)})\n", "warn")
                                self._log(f"  → {hint}\n", "dim")
                                return False
                            bc = dict(self.config); bc["cookies_file"]=tmp; bc["cookies_from_browser"]=None
                            args = build_yt_dlp_args(url, out, high, bc, use_cookies=True)
                            rc = self._run_with_args(args)
                            if rc==0: self._log(f"\n{label} HD OK (native)\n", "success"); return True
                            return False
                    finally:
                        _remove_temp_cookie_file(tmp)
                except Exception as e:
                    self._log(f"  native error: {e}\n", "dim")

            # Step 1: browser_cookie3
            if self._cancel_event.is_set():
                return False
            tmp = _make_temp_cookie_file(bk, "bc3")
            try:
                if bc3_export(bk, tmp) and os.path.isfile(tmp) and os.path.getsize(tmp)>100:
                    if self._cancel_event.is_set():
                        return False
                    self._log(f"  bc3 OK ({os.path.getsize(tmp)}B)\n", "success")
                    logged, missing, hint = check_cookie_login(tmp, plat)
                    if not logged:
                        self._log(f"  ⚠ {plat}: not logged in via bc3 (missing: {', '.join(missing)})\n", "warn")
                        self._log(f"  → {hint}\n", "dim")
                        return False
                    bc = dict(self.config); bc["cookies_file"]=tmp; bc["cookies_from_browser"]=None
                    args = build_yt_dlp_args(url, out, high, bc, use_cookies=True)
                    rc = self._run_with_args(args)
                    if rc==0: self._log(f"\n{label} HD OK (bc3)\n", "success"); return True
                    return False
            finally:
                _remove_temp_cookie_file(tmp)

            # Step 2: yt-dlp DPAPI
            if self._cancel_event.is_set():
                return False
            if BROWSER_CONFIG[bk].get("native"):
                ac = dict(self.config); ac["cookies_from_browser"] = bk; ac["cookies_file"] = None
                rc = self._run_with_args(build_yt_dlp_args(url, out, high, ac, use_cookies=True))
                if rc==0: self._log(f"\n{label} HD OK (yt-dlp)\n", "success"); return True
                self._log(f"  {label} DPAPI FAIL (exit={rc})\n", "warn")
            return False

        def _run(self, url, out, opts, use_cookies):
            return self._run_with_args(build_yt_dlp_args(url, out, opts, self.config, use_cookies=use_cookies))

        def _run_with_args(self, args):
            if self._cancel_event.is_set():
                return 130
            self._last_ytdlp_tail = ""
            self._log(f"cmd: {' '.join(args)}\n", "dim")
            try:
                proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, encoding="utf-8", errors="replace", bufsize=1,
                                        env=_utf8_subprocess_env(), **_popen_group_kwargs())
                self.process = proc
                # Close the narrow race where Stop was clicked after the first
                # cancellation check but before self.process was assigned.
                if self._cancel_event.is_set():
                    _terminate_process_tree(proc)
            except Exception as e: self._log(f"Launch failed: {e}\n", "error"); return 1
            last_errors = []  # ring buffer: last N error-like lines
            last_lines = []   # ring buffer: last N lines overall (fallback)
            MAX_TAIL = 10
            for line in iter(proc.stdout.readline, ""):
                s = line.strip()
                if not s: continue
                last_lines.append(s)
                if len(last_lines) > MAX_TAIL: last_lines.pop(0)
                if "ERROR:" in s or "error" in s.lower() or "fail" in s.lower():
                    last_errors.append(s)
                    if len(last_errors) > MAX_TAIL: last_errors.pop(0)
                tag = "error" if "ERROR" in s else ("warn" if "WARNING" in s else ("info" if "[download]" in s and "%" in s else ("success" if "Merger" in s or "Metadata" in s else "dim")))
                if "[download]" in s and "%" in s: s = s[:140]
                self._log(f"{s}\n", tag)
            proc.wait(); rc = proc.returncode
            self._last_ytdlp_tail = "\n".join(last_lines)
            if self.process is proc:
                self.process = None
            if rc != 0:
                if last_errors:
                    err_info = "; ".join(last_errors[-3:])[:300]
                elif last_lines:
                    err_info = "; ".join(last_lines[-3:])[:300]
                else:
                    err_info = "no output captured"
                log("warn", f"yt-dlp exited with code {rc}: {err_info}")
            return rc

    print("Starting Video Fetcher GUI...")
    set_log_file("gui")
    log("info", "GUI starting")
    root = tk.Tk(); app = VideoFetcherGUI(root)
    # A GUI started from another application can otherwise open behind that
    # application. Raise it once, then immediately return to normal z-order.
    root.deiconify(); root.lift(); root.attributes("-topmost", True)
    root.after(800, lambda: root.attributes("-topmost", False))
    root.mainloop()
    log("info", "GUI closed")
    close_log()
    print("GUI closed.")

if __name__ == "__main__":
    try: main()
    except SystemExit: pass
    except Exception: _crash_log(sys.exc_info()); input("\nPress Enter..."); sys.exit(1)
