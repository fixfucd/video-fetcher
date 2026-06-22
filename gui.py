#!/usr/bin/env python3
"""
video-fetcher GUI — visual video download client
Start: python gui.py
"""

import os, sys, traceback, subprocess, threading, tempfile as tmpmod

def _crash_log(exc_info):
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
            cleanup_temp_files, check_output_exists,
            find_cookies_file, get_alt_browsers,
            is_cookie_lock_error, detect_installed_browsers,
            detect_browser_profiles, get_available_browsers,
            _expand_path, _has_bc3, bc3_export, _native_export,
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
            ttk.Button(cr, text="Test", command=self._test_cookies, width=4).pack(side="left")
            self._update_cookies_status()

            self.browser_bar = ttk.Label(main, text="", foreground="gray")
            self.browser_bar.pack(anchor="w", pady=(0,4))
            self._update_browser_bar()

            row = ttk.Frame(main); row.pack(fill="x", pady=4)
            ttk.Label(row, text="Platform").pack(side="left")
            self.platform_var = tk.StringVar(value="douyin")
            ttk.Combobox(row, textvariable=self.platform_var, values=list(PLATFORM_PRESETS),
                         state="readonly", width=10).pack(side="left", padx=6)
            ttk.Label(row, text="Output").pack(side="left", padx=(12,0))
            self.output_var = tk.StringVar(value=self.config.get("output_dir", os.path.join(os.getcwd(),"downloads")))
            ttk.Entry(row, textvariable=self.output_var, font=("Consolas",9)).pack(side="left", fill="x", expand=True, padx=4)
            ttk.Button(row, text="Browse...", command=self._browse_output, width=6).pack(side="left")

            self.strat_label = ttk.Label(main, foreground="gray",
                text="Strategy: bc3 > yt-dlp DPAPI > multi-browser > LQ fallback")
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
            self._browser_combo = ttk.Combobox(parent, textvariable=self.cookies_browser_var,
                                                values=ik+ok, width=10)
            self._browser_combo.pack(side="left")
            self._browser_combo.bind("<<ComboboxSelected>>", self._on_cookies_change)

        def _update_browser_bar(self):
            inst = [k for k,v in self._installed_browsers.items() if v["installed"]]
            if inst:
                parts = [f"{BROWSER_CONFIG[k]['label']}({self._installed_browsers[k]['profiles']}P)" for k in inst]
                self.browser_bar.config(text=f"Detected: {', '.join(parts)}", foreground="#6a9955")
            else:
                self.browser_bar.config(text="No browsers detected", foreground="#ce9178")

        def _on_refresh(self):
            self._log("Refreshing browser detection...\n", "info")
            self._refresh_browser_detection()
            self._build_dropdown(self.cookies_browser_var.master)
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

        def _test_cookies(self):
            b = self.cookies_browser_var.get().strip()
            if not b: messagebox.showinfo("Info", "Select a browser first."); return
            cfg = BROWSER_CONFIG.get(b,{}); label = cfg.get("label", b)
            self.cookies_status.config(text=f"Testing {label}...", foreground="#808080")
            self._log(f"=== Test: {label} ===\n", "info")

            # profiles
            found = False
            profs = detect_browser_profiles(b)
            if profs:
                self._log(f"  {len(profs)} profile(s):\n", "info")
                for pd, pn in profs:
                    for ct in cfg.get("cookies_paths",[]):
                        p = _expand_path(ct, pd)
                        if os.path.isfile(p): self._log(f"    {pn}: {p} ({os.path.getsize(p)}B)\n", "success"); found = True
            elif cfg.get("engine")=="gecko":
                dp,_ = find_cookies_file(b)
                if dp: self._log(f"  {dp} ({os.path.getsize(dp)}B)\n", "success"); found = True
            if not found: self._log("  no cookies file found\n", "dim"); self.cookies_status.config(text=f"{label} not installed", foreground="#ce9178"); return

            # bc3 test
            self._log("  testing bc3 export...\n", "dim")
            tmp = os.path.join(tmpmod.gettempdir(), f"vf_test_{b}_cookies.txt")
            if bc3_export(b, tmp):
                sz = os.path.getsize(tmp)
                self._log(f"  bc3 OK ({sz}B)\n", "success")
                self.cookies_status.config(text=f"{label} bc3 OK", foreground="#6a9955")
            else:
                self._log("  bc3 not available\n", "warn")
                self.cookies_status.config(text=f"{label} found (bc3 not installed)", foreground="#ce9178")

        def _start(self):
            url = self.url_var.get().strip()
            if not url: messagebox.showwarning("Warning", "Enter a URL"); return
            plat = self.platform_var.get()
            out = self.output_var.get().strip()
            if not out: messagebox.showwarning("Warning", "Select output directory"); return
            try: os.makedirs(out, exist_ok=True)
            except OSError as e: messagebox.showerror("Error", str(e)); return
            self._clear_log()
            self._log(f"Platform: {plat}\nOutput: {out}\nURL: {url}\n\n", "info")
            self.dl_btn.config(state="disabled"); self.stop_btn.config(state="normal")
            self.status_var.set("Downloading..."); self.process = None
            threading.Thread(target=self._worker, args=(url, plat, out), daemon=True).start()

        def _stop(self):
            if self.process and self.process.poll() is None:
                self.process.terminate(); self._log("\n[Stopped]\n", "warn")
                cleanup_temp_files(self.output_var.get().strip()); self._done(1, "Stopped")

        def _done(self, ec, msg=""):
            def d():
                self.dl_btn.config(state="normal"); self.stop_btn.config(state="disabled")
                self.status_var.set(msg or ("Done" if ec==0 else f"FAIL ({ec})"))
            self.root.after(0, d)

        def _worker(self, url, plat, out):
            import time
            url = normalize_douyin_url(url)
            high, fallback = get_platform_presets(plat, self.config)
            st = time.time()
            self._refresh_browser_detection()
            inst = self._installed_browsers
            avail = [k for k,v in inst.items() if v["installed"]]
            self._log(f"Browsers: {', '.join(BROWSER_CONFIG[k]['label'] for k in avail) if avail else '(none)'}\n", "info")

            pref = self.config.get("cookies_from_browser","")
            self._log(f"Preferred: {pref or '(none)'}\n", "info")

            # cookies file
            cf = self.config.get("cookies_file")
            if cf and os.path.isfile(cf):
                self._log(f"Using cookies file: {cf}\n", "info")
                self._log("--- HD (file) ---\n", "info")
                rc = self._run(url, out, high, use_cookies=True)
                if rc==0: self._log("\nHD OK\n", "success"); cleanup_temp_files(out); self._done(0); return
                if check_output_exists(out, st): cleanup_temp_files(out); self._done(0); return

            tried = set()
            # preferred
            if pref and inst.get(pref,{}).get("installed"):
                tried.add(pref)
                self._log(f"--- HD ({BROWSER_CONFIG[pref]['label']}) ---\n", "info")
                ok = self._try_bc3_then_native(url, out, high, pref)
                if ok: cleanup_temp_files(out); self._done(0); return
                time.sleep(2)
                ok = self._try_bc3_then_native(url, out, high, pref)
                if ok: cleanup_temp_files(out); self._done(0); return
            elif pref:
                self._log(f"'{BROWSER_CONFIG.get(pref,{}).get('label',pref)}' not installed\n", "warn")

            # alternates
            alts = [b for b in (get_alt_browsers(pref) if pref else get_available_browsers()) if b not in tried]
            if alts:
                self._log(f"\nAlternates: {', '.join(BROWSER_CONFIG[b]['label'] for b in alts)}\n", "info")
            for b in alts:
                tried.add(b)
                self._log(f"--- HD ({BROWSER_CONFIG[b]['label']}) ---\n", "info")
                ok = self._try_bc3_then_native(url, out, high, b)
                if ok: cleanup_temp_files(out); self._done(0); return

            if check_output_exists(out, st): cleanup_temp_files(out); self._done(0); return

            # fallback
            if fallback is None:
                self._log(f"\n{plat} needs login, abort.\n", "error"); cleanup_temp_files(out); self._done(1); return
            self._log("\n--- LQ fallback ---\n", "warn")
            rc = self._run(url, out, fallback, use_cookies=False)
            if rc==0: self._log("\nLQ OK\n", "success")
            else: self._log(f"\nLQ FAIL ({rc})\n", "error")
            cleanup_temp_files(out); self._done(rc)

        def _try_bc3_then_native(self, url, out, high, bk):
            """Try native > bc3 > yt-dlp DPAPI for one browser."""
            label = BROWSER_CONFIG[bk]["label"]

            # Step 0: zero-dep native export
            if _native_export:
                try:
                    tmp = os.path.join(tmpmod.gettempdir(), f"vf_native_{bk}_cookies.txt")
                    if _native_export(bk, tmp) and os.path.isfile(tmp) and os.path.getsize(tmp)>100:
                        self._log(f"  native OK ({os.path.getsize(tmp)}B)\n", "success")
                        bc = dict(self.config); bc["cookies_file"]=tmp; bc["cookies_from_browser"]=None
                        args = build_yt_dlp_args(url, out, high, bc, use_cookies=True)
                        rc = self._run_with_args(args)
                        if rc==0: self._log(f"\n{label} HD OK (native)\n", "success"); return True
                        return False
                except Exception as e:
                    self._log(f"  native error: {e}\n", "dim")

            # Step 1: browser_cookie3
            tmp = os.path.join(tmpmod.gettempdir(), f"vf_{bk}_cookies.txt")
            if bc3_export(bk, tmp) and os.path.isfile(tmp) and os.path.getsize(tmp)>100:
                self._log(f"  bc3 OK ({os.path.getsize(tmp)}B)\n", "success")
                bc = dict(self.config); bc["cookies_file"]=tmp; bc["cookies_from_browser"]=None
                args = build_yt_dlp_args(url, out, high, bc, use_cookies=True)
                rc = self._run_with_args(args)
                if rc==0: self._log(f"\n{label} HD OK (bc3)\n", "success"); return True
                return False

            # Step 2: yt-dlp DPAPI
            if BROWSER_CONFIG[bk].get("native"):
                rc = self._run(url, out, high, use_cookies=True)
                if rc==0: self._log(f"\n{label} HD OK (yt-dlp)\n", "success"); return True
                self._log(f"  {label} DPAPI FAIL (exit={rc})\n", "warn")
            return False

        def _run(self, url, out, opts, use_cookies):
            return self._run_with_args(build_yt_dlp_args(url, out, opts, self.config, use_cookies=use_cookies))

        def _run_with_args(self, args):
            self._log(f"cmd: {' '.join(args)}\n", "dim")
            try:
                self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                 text=True, encoding="utf-8", errors="replace", bufsize=1, env=os.environ)
            except Exception as e: self._log(f"Launch failed: {e}\n", "error"); return 1
            for line in iter(self.process.stdout.readline, ""):
                if self.process is None: break
                s = line.strip()
                if not s: continue
                tag = "error" if "ERROR" in s else ("warn" if "WARNING" in s else ("info" if "[download]" in s and "%" in s else ("success" if "Merger" in s or "Metadata" in s else "dim")))
                if "[download]" in s and "%" in s: s = s[:140]
                self._log(f"{s}\n", tag)
            self.process.wait(); rc = self.process.returncode; self.process = None
            return rc

    print("Starting Video Fetcher GUI...")
    root = tk.Tk(); app = VideoFetcherGUI(root); root.mainloop()
    print("GUI closed.")

if __name__ == "__main__":
    try: main()
    except SystemExit: pass
    except Exception: _crash_log(sys.exc_info()); input("\nPress Enter..."); sys.exit(1)
