#!/usr/bin/env python3
"""
video-fetcher GUI — 可视化视频下载客户端

启动: python gui.py
"""

import os
import sys
import traceback
import subprocess
import threading


def _crash_log(exc_info):
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gui_error.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n")
        traceback.print_exception(*exc_info, file=f)
    traceback.print_exception(*exc_info)
    print(f"\n错误日志: {log_path}", file=sys.stderr)


def _check_tkinter():
    try:
        import tkinter
        return True
    except ImportError:
        print(
            "tkinter 不可用。请安装 Python 时勾选 'tcl/tk and IDLE' 选项。",
            file=sys.stderr,
        )
        return False


def main():
    if not _check_tkinter():
        input("按 Enter 退出...")
        sys.exit(1)

    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from fetch import (
            PLATFORM_PRESETS, BROWSER_CONFIG,
            load_config, get_platform_presets,
            build_yt_dlp_args, check_tool, normalize_douyin_url,
            cleanup_temp_files, check_output_exists,
            find_cookies_file, get_browser_cookies_args,
            get_alt_browsers, is_cookie_lock_error,
            detect_installed_browsers, detect_browser_profiles,
            copy_cookies_db, get_available_browsers,
            _expand_path,
        )
    except ImportError as e:
        print(f"导入 fetch 模块失败: {e}", file=sys.stderr)
        print("请确保 fetch.py 与 gui.py 在同一目录。", file=sys.stderr)
        input("按 Enter 退出...")
        sys.exit(1)

    class VideoFetcherGUI:
        def __init__(self, root):
            self.root = root
            self.root.title("Video Fetcher")
            self.root.geometry("760x600")
            self.root.minsize(640, 480)
            self.config = load_config()
            self.process = None
            self._installed_browsers = {}  # 缓存检测结果
            self._fix_env()
            self._refresh_browser_detection()
            self._setup_ui()
            self._check_env()

        @staticmethod
        def _fix_env():
            import getpass
            username = os.environ.get("USERNAME") or getpass.getuser()
            home = os.environ.get("USERPROFILE") or f"C:\\Users\\{username}"
            if not os.environ.get("LOCALAPPDATA"):
                os.environ["LOCALAPPDATA"] = f"{home}\\AppData\\Local"
            if not os.environ.get("APPDATA"):
                os.environ["APPDATA"] = f"{home}\\AppData\\Roaming"

        def _refresh_browser_detection(self):
            """重新检测系统中已安装的浏览器。"""
            self._installed_browsers = detect_installed_browsers()

        def _setup_ui(self):
            main = ttk.Frame(self.root, padding=12)
            main.pack(fill="both", expand=True)

            ttk.Label(main, text="视频 URL").pack(anchor="w")
            url_frame = ttk.Frame(main)
            url_frame.pack(fill="x", pady=(2, 8))
            self.url_var = tk.StringVar()
            self.url_entry = ttk.Entry(url_frame, textvariable=self.url_var, font=("Consolas", 10))
            self.url_entry.pack(fill="x", expand=True)
            self.url_entry.bind("<Button-3>", self._on_right_click_url)

            # ── Cookies 配置 ──
            header_row = ttk.Frame(main)
            header_row.pack(fill="x", pady=(8, 0))
            ttk.Label(header_row, text="Cookies 来源 (高清需要)").pack(side="left")
            ttk.Button(header_row, text="⟳ 刷新检测", command=self._on_refresh_browsers,
                       width=10).pack(side="right")

            cookies_row = ttk.Frame(main)
            cookies_row.pack(fill="x", pady=(2, 8))

            self.cookies_browser_var = tk.StringVar(
                value=self.config.get("cookies_from_browser", "")
            )
            # 下拉：已安装浏览器 + 全部浏览器
            self._build_browser_dropdown(cookies_row)

            ttk.Label(cookies_row, text="或").pack(side="left", padx=4)
            self.cookies_file_var = tk.StringVar(
                value=self.config.get("cookies_file", "")
            )
            file_entry = ttk.Entry(cookies_row, textvariable=self.cookies_file_var, width=30)
            file_entry.pack(side="left", padx=4)
            ttk.Button(cookies_row, text="浏览...", command=self._browse_cookies_file, width=6).pack(side="left")
            self.cookies_status = ttk.Label(cookies_row, text="", foreground="gray")
            self.cookies_status.pack(side="left", padx=8)
            ttk.Button(cookies_row, text="测试", command=self._test_cookies, width=4).pack(side="left")
            self._update_cookies_status()

            # 已安装浏览器状态显示行
            self.browser_status_label = ttk.Label(main, text="", foreground="gray")
            self.browser_status_label.pack(anchor="w", pady=(0, 4))
            self._update_browser_status_bar()

            row = ttk.Frame(main)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text="平台").pack(side="left")
            self.platform_var = tk.StringVar(value="douyin")
            cb = ttk.Combobox(row, textvariable=self.platform_var,
                              values=list(PLATFORM_PRESETS.keys()), state="readonly", width=10)
            cb.pack(side="left", padx=6)
            cb.bind("<<ComboboxSelected>>", self._on_platform_change)

            ttk.Label(row, text="输出目录").pack(side="left", padx=(12, 0))
            self.output_var = tk.StringVar(
                value=self.config.get("output_dir", os.path.join(os.getcwd(), "downloads"))
            )
            ttk.Entry(row, textvariable=self.output_var, font=("Consolas", 9)).pack(
                side="left", fill="x", expand=True, padx=4)
            ttk.Button(row, text="浏览...", command=self._browse_output, width=6).pack(side="left")

            self.strategy_label = ttk.Label(main, foreground="gray",
                text="策略: 自动检测浏览器 → DB拷贝绕过锁 → 多浏览器回退 → 低清兜底")
            self.strategy_label.pack(anchor="w", pady=(4, 6))

            btn_frame = ttk.Frame(main)
            btn_frame.pack(fill="x", pady=4)
            self.download_btn = ttk.Button(btn_frame, text="开始下载", command=self._start_download)
            self.download_btn.pack(side="left", padx=(0, 8))
            self.stop_btn = ttk.Button(btn_frame, text="停止", command=self._stop_download, state="disabled")
            self.stop_btn.pack(side="left")
            self.status_var = tk.StringVar(value="就绪")
            ttk.Label(btn_frame, textvariable=self.status_var, foreground="gray").pack(side="right")

            log_frame = ttk.Frame(main)
            log_frame.pack(fill="both", expand=True, pady=(8, 0))
            self.log_text = tk.Text(log_frame, wrap="word", state="disabled",
                font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                relief="flat", borderwidth=0, padx=8, pady=6)
            scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
            self.log_text.configure(yscrollcommand=scrollbar.set)
            self.log_text.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            for tag, color in [("info", "#569cd6"), ("success", "#6a9955"),
                               ("warn", "#ce9178"), ("error", "#f44747"), ("dim", "#808080")]:
                self.log_text.tag_configure(tag, foreground=color)

        def _build_browser_dropdown(self, parent):
            """构建浏览器下拉列表，标注安装状态。"""
            # 已安装的排前面
            installed_keys = [k for k, v in self._installed_browsers.items() if v["installed"]]
            all_keys = sorted(BROWSER_CONFIG.keys(), key=lambda k: BROWSER_CONFIG[k]["priority"])
            other_keys = [k for k in all_keys if k not in installed_keys]

            display_values = []
            for k in installed_keys:
                v = self._installed_browsers[k]
                display_values.append(f"{k} | {v['label']} ✓")
            for k in other_keys:
                cfg = BROWSER_CONFIG[k]
                display_values.append(f"{k} | {cfg['label']}")

            cb = ttk.Combobox(parent, textvariable=self.cookies_browser_var,
                              values=[k for k in installed_keys + other_keys], width=10)
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", self._on_cookies_change)
            self._browser_combo = cb

        def _update_browser_status_bar(self):
            """更新底部浏览器状态栏。"""
            installed = [k for k, v in self._installed_browsers.items() if v["installed"]]
            if installed:
                parts = []
                for k in installed:
                    v = self._installed_browsers[k]
                    parts.append(f"{v['label']}({v['profiles']}P)")
                self.browser_status_label.config(
                    text=f"已检测: {', '.join(parts)}", foreground="#6a9955")
            else:
                self.browser_status_label.config(
                    text="未检测到已安装浏览器", foreground="#ce9178")

        def _on_refresh_browsers(self):
            """手动刷新浏览器检测。"""
            self._log("正在重新检测浏览器...\n", "info")
            self._refresh_browser_detection()
            # 重建下拉
            self._build_browser_dropdown(self.cookies_browser_var.master)
            self._update_browser_status_bar()
            self._update_cookies_status()
            self._log("检测完成。\n", "success")

        def _check_env(self):
            if not check_tool("yt-dlp"):
                self._log("未找到 yt-dlp。pip install yt-dlp\n", "error")
                self.download_btn.config(state="disabled")
            if not check_tool("ffmpeg"):
                self._log("未找到 ffmpeg。部分视频可能无法合并。\n", "warn")

        def _log(self, text, tag="info"):
            def _write():
                self.log_text.configure(state="normal")
                self.log_text.insert("end", text, tag)
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
            self.root.after(0, _write)

        def _clear_log(self):
            def _clear():
                self.log_text.configure(state="normal")
                self.log_text.delete("1.0", "end")
                self.log_text.configure(state="disabled")
            self.root.after(0, _clear)

        def _browse_output(self):
            path = filedialog.askdirectory(title="选择下载目录")
            if path:
                self.output_var.set(path)

        def _on_right_click_url(self, event):
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="粘贴", command=self._paste_url)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        def _paste_url(self):
            try:
                text = self.root.clipboard_get().strip()
                if text:
                    self.url_var.set(text)
            except Exception:
                pass

        def _on_cookies_change(self, event=None):
            self._save_cookies_config()
            self._update_cookies_status()

        def _browse_cookies_file(self):
            path = filedialog.askopenfilename(
                title="选择 cookies.txt 文件",
                filetypes=[("Cookies 文件", "*.txt"), ("所有文件", "*.*")]
            )
            if path:
                self.cookies_file_var.set(path)
                self._save_cookies_config()
                self._update_cookies_status()

        def _save_cookies_config(self):
            browser = self.cookies_browser_var.get().strip() or None
            file_path = self.cookies_file_var.get().strip() or None
            self.config["cookies_from_browser"] = browser
            self.config["cookies_file"] = file_path
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            try:
                import json
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(self.config, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

        def _update_cookies_status(self):
            browser = self.cookies_browser_var.get().strip()
            file_path = self.cookies_file_var.get().strip()
            if file_path:
                if os.path.isfile(file_path):
                    self.cookies_status.config(
                        text=f"✓ 文件就绪 ({os.path.basename(file_path)})", foreground="#6a9955")
                else:
                    self.cookies_status.config(text="✗ 文件不存在", foreground="#f44747")
            elif browser:
                cfg = BROWSER_CONFIG.get(browser, {})
                label = cfg.get("label", browser)
                is_installed = self._installed_browsers.get(browser, {}).get("installed", False)
                if is_installed:
                    self.cookies_status.config(
                        text=f"从 {label} 读取 (已安装)", foreground="#ce9178")
                else:
                    self.cookies_status.config(
                        text=f"从 {label} 读取 (未检测到)", foreground="#ce9178")
            else:
                self.cookies_status.config(text="未配置 (仅低清)", foreground="#808080")

        def _test_cookies(self):
            """测试浏览器 cookies，含 DB 拷贝测试。"""
            browser = self.cookies_browser_var.get().strip()
            if not browser:
                messagebox.showinfo("提示", "请先选择浏览器。")
                return
            cfg = BROWSER_CONFIG.get(browser, {})
            label = cfg.get("label", browser)
            self.cookies_status.config(text=f"检查 {label}...", foreground="#808080")
            self._log(f"=== cookies 检查: {label} ===\n", "info")

            # 1. 本地文件检查（含多 Profile）
            found = False
            profiles = detect_browser_profiles(browser)
            if profiles:
                self._log(f"  检测到 {len(profiles)} 个 Profile:\n", "info")
                for prof_dir, prof_name in profiles:
                    for cookie_tmpl in cfg.get("cookies_paths", []):
                        path = _expand_path(cookie_tmpl, prof_dir)
                        if os.path.isfile(path):
                            self._log(f"    {prof_name}: {path} ({os.path.getsize(path)} bytes)\n", "success")
                            found = True
            elif cfg.get("engine") == "gecko":
                db_path, prof_name = find_cookies_file(browser)
                if db_path:
                    self._log(f"  本地文件: {db_path} ({os.path.getsize(db_path)} bytes)\n", "success")
                    found = True

            if not found:
                self._log(f"  (未找到 {label} cookies 文件)\n", "dim")
                self.cookies_status.config(text=f"{label} 未安装", foreground="#ce9178")
                return

            # 2. DB 拷贝测试
            self._log("  测试 cookies DB 拷贝 (绕过锁)...\n", "dim")
            tmp_path = copy_cookies_db(browser)
            if tmp_path:
                self._log(f"  DB 拷贝成功: {os.path.basename(tmp_path)} ({os.path.getsize(tmp_path)} bytes)\n", "success")
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            else:
                self._log("  DB 拷贝失败 (浏览器可能正在运行，下载时会重试)\n", "warn")

            # 3. yt-dlp 测试
            try:
                if cfg.get("native") and cfg.get("yt_name"):
                    cmd = ["yt-dlp", "--cookies-from-browser", cfg["yt_name"], "-j", "about:blank"]
                else:
                    cookies_path, _ = find_cookies_file(browser)
                    if not cookies_path:
                        self._log("  cookies 文件未找到\n", "warn")
                        return
                    cmd = ["yt-dlp", "--cookies", cookies_path, "-j", "about:blank"]

                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", env=os.environ)
                out, _ = proc.communicate(timeout=8)
                out_lower = out.lower()

                if "extracting cookies" in out_lower and "extracted" in out_lower:
                    cnt = ""
                    for w in out_lower.split():
                        if w.isdigit() and int(w) > 10:
                            cnt = w; break
                    self._log(f"  yt-dlp 提取: {cnt} cookies\n" if cnt else "  yt-dlp 提取成功\n", "success")
                    self.cookies_status.config(text=f"{label} cookies OK", foreground="#6a9955")
                elif any(kw in out_lower for kw in ["could not find", "permission", "locked"]):
                    self.cookies_status.config(text=f"{label} 已锁定 (DB拷贝可用)", foreground="#ce9178")
                    self._log("  cookies 被锁定。下载时将自动拷贝 DB 绕过。\n", "warn")
                else:
                    self.cookies_status.config(text=f"{label} 异常", foreground="#ce9178")
                    self._log(out[:400] + "\n", "dim")
            except subprocess.TimeoutExpired:
                proc.kill()
                self.cookies_status.config(text=f"{label} 超时", foreground="#ce9178")
                self._log("  网络超时，本地文件存在，下载时自动重试。\n", "warn")
            except Exception as e:
                self.cookies_status.config(text=f"✗ 测试失败", foreground="#f44747")
                self._log(f"  {e}\n", "error")

        def _on_platform_change(self, event=None):
            tips = {
                "bilibili": "B站: 高清 -> 720p",
                "youtube": "YouTube: 4K+字幕(web) -> 720p(android)",
                "twitter": "X: 最高质量(必须cookies)",
                "generic": "通用: bestvideo+bestaudio -> best",
            }
            self.strategy_label.config(text="策略: " + tips.get(self.platform_var.get(), ""))

        def _start_download(self):
            url = self.url_var.get().strip()
            if not url:
                messagebox.showwarning("提示", "请输入视频 URL")
                return
            platform = self.platform_var.get()
            output_dir = self.output_var.get().strip()
            if not output_dir:
                messagebox.showwarning("提示", "请指定输出目录")
                return
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as e:
                messagebox.showerror("错误", f"无法创建输出目录:\n{e}")
                return

            self._clear_log()
            self._log(f"平台: {platform}\n输出: {output_dir}\nURL: {url}\n\n", "info")
            self.download_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.status_var.set("下载中...")
            self.process = None
            threading.Thread(target=self._download_worker,
                             args=(url, platform, output_dir), daemon=True).start()

        def _stop_download(self):
            if self.process and self.process.poll() is None:
                self.process.terminate()
                self._log("\n[已停止]\n", "warn")
                cleanup_temp_files(self.output_var.get().strip())
                self._on_done(1, "已停止")

        def _on_done(self, exit_code, msg=""):
            def _done():
                self.download_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                self.status_var.set(msg or ("完成" if exit_code == 0 else f"失败(exit={exit_code})"))
            self.root.after(0, _done)

        def _download_worker(self, url, platform, output_dir):
            import time

            normalized = normalize_douyin_url(url)
            if normalized != url:
                self._log(f"抖音 URL 标准化: {normalized}\n", "info")
                url = normalized

            high, fallback = get_platform_presets(platform, self.config)
            start_time = time.time()

            # 刷新检测
            self._refresh_browser_detection()
            installed = self._installed_browsers
            available = [k for k, v in installed.items() if v["installed"]]
            labels = ", ".join(BROWSER_CONFIG[k]["label"] for k in available) if available else "(无)"
            self._log(f"已安装浏览器: {labels}\n", "info")

            preferred_browser = self.config.get("cookies_from_browser", "")
            self._log(f"首选浏览器: {preferred_browser or '(无)'}\n", "info")

            # cookies 文件
            cookies_file = self.config.get("cookies_file")
            if cookies_file and os.path.isfile(cookies_file):
                self._log(f"使用 cookies 文件: {cookies_file}\n", "info")
                self._log("--- Round 1: 高清 (cookies 文件) ---\n", "info")
                rc = self._run(url, output_dir, high, use_cookies=True)
                if rc == 0:
                    self._log("\n高清下载成功\n", "success")
                    cleanup_temp_files(output_dir)
                    self._on_done(0)
                    return
                if check_output_exists(output_dir, start_time):
                    cleanup_temp_files(output_dir)
                    self._on_done(0)
                    return

            tried_browsers = set()

            # 首选浏览器
            if preferred_browser and installed.get(preferred_browser, {}).get("installed"):
                tried_browsers.add(preferred_browser)
                label = BROWSER_CONFIG.get(preferred_browser, {}).get("label", preferred_browser)
                self._log(f"--- Round 1: 高清 ({label}) ---\n", "info")
                rc = self._run(url, output_dir, high, use_cookies=True)
                if rc == 0:
                    self._log(f"\n{label} 高清下载成功\n", "success")
                    cleanup_temp_files(output_dir)
                    self._on_done(0)
                    return
                if check_output_exists(output_dir, start_time):
                    cleanup_temp_files(output_dir)
                    self._on_done(0)
                    return

                self._log(f"\n[!] {label} 可能被锁定，等待 2 秒后重试...\n", "warn")
                time.sleep(2)
                self._log(f"--- 重试: 高清 ({label}) ---\n", "info")
                rc = self._run(url, output_dir, high, use_cookies=True)
                if rc == 0:
                    self._log(f"\n{label} 重试成功\n", "success")
                    cleanup_temp_files(output_dir)
                    self._on_done(0)
                    return
                if check_output_exists(output_dir, start_time):
                    cleanup_temp_files(output_dir)
                    self._on_done(0)
                    return

            elif preferred_browser:
                label = BROWSER_CONFIG.get(preferred_browser, {}).get("label", preferred_browser)
                self._log(f"首选 '{label}' 未安装，直接尝试备用\n", "warn")

            # 备用浏览器
            alt_browsers = [b for b in get_alt_browsers(preferred_browser) if b not in tried_browsers]
            if alt_browsers:
                labels = ", ".join(BROWSER_CONFIG[b]["label"] for b in alt_browsers)
                self._log(f"\n备用浏览器: {labels}\n", "info")

            for alt_browser in alt_browsers:
                tried_browsers.add(alt_browser)
                label = BROWSER_CONFIG.get(alt_browser, {}).get("label", alt_browser)
                self._log(f"--- 备用: 高清 ({label}) ---\n", "info")

                alt_config = dict(self.config)
                alt_config["cookies_from_browser"] = alt_browser
                alt_config["cookies_file"] = None
                args = build_yt_dlp_args(url, output_dir, high, alt_config, use_cookies=True)
                rc = self._run_with_args(args)
                if rc == 0:
                    self._log(f"\n{label} 高清下载成功\n", "success")
                    cleanup_temp_files(output_dir)
                    self._on_done(0)
                    return
                if check_output_exists(output_dir, start_time):
                    cleanup_temp_files(output_dir)
                    self._on_done(0)
                    return

            # browser_cookie3 兜底
            self._log("\n尝试 browser_cookie3 导出...\n", "dim")
            from fetch import try_browser_cookie3_export
            import tempfile as tmpmod
            tmp_cookie_file = os.path.join(tmpmod.gettempdir(), "video_fetcher_cookies.txt")
            for bk in available:
                if try_browser_cookie3_export(bk, tmp_cookie_file):
                    if os.path.isfile(tmp_cookie_file) and os.path.getsize(tmp_cookie_file) > 100:
                        label = BROWSER_CONFIG[bk]["label"]
                        self._log(f"browser_cookie3 从 {label} 导出成功\n", "success")
                        bc3_config = dict(self.config)
                        bc3_config["cookies_file"] = tmp_cookie_file
                        bc3_config["cookies_from_browser"] = None
                        rc = self._run(url, output_dir, high, use_cookies=True)
                        if rc == 0:
                            cleanup_temp_files(output_dir)
                            self._on_done(0)
                            return
                    break
            else:
                self._log("browser_cookie3 不可用或导出失败\n", "dim")

            if check_output_exists(output_dir, start_time):
                cleanup_temp_files(output_dir)
                self._on_done(0)
                return

            # 低清回退
            if fallback is None:
                self._log(f"\n{platform} 需登录且所有 cookies 来源均不可用。\n", "error")
                cleanup_temp_files(output_dir)
                self._on_done(1)
                return

            self._log("\n所有浏览器均失败，回退低清...\n", "warn")
            self._log("--- Round 2: 低清 (无cookies) ---\n", "info")
            rc2 = self._run(url, output_dir, fallback, use_cookies=False)
            if rc2 == 0:
                self._log("\n低清下载成功（已降级）\n", "success")
            else:
                self._log(f"\n低清也失败(exit={rc2})\n", "error")
            cleanup_temp_files(output_dir)
            self._on_done(rc2)

        def _run(self, url, output_dir, opts, use_cookies):
            args = build_yt_dlp_args(url, output_dir, opts, self.config, use_cookies=use_cookies)
            return self._run_with_args(args)

        def _run_with_args(self, args):
            self._log(f"cmd: {' '.join(args)}\n", "dim")
            try:
                self.process = subprocess.Popen(
                    args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    env=os.environ,
                )
            except Exception as e:
                self._log(f"启动失败: {e}\n", "error")
                return 1
            for line in iter(self.process.stdout.readline, ""):
                if self.process is None:
                    break
                s = line.strip()
                if not s:
                    continue
                if "ERROR" in s:
                    tag = "error"
                elif "WARNING" in s:
                    tag = "warn"
                elif "[download]" in s and "%" in s:
                    tag = "info"
                    s = s[:140]
                elif "Merger" in s or "Metadata" in s:
                    tag = "success"
                else:
                    tag = "dim"
                self._log(f"{s}\n", tag)
            self.process.wait()
            rc = self.process.returncode
            self.process = None
            return rc

    print("正在启动 Video Fetcher GUI...")
    root = tk.Tk()
    app = VideoFetcherGUI(root)
    root.mainloop()
    print("GUI 已关闭。")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception:
        _crash_log(sys.exc_info())
        input("\n按 Enter 退出...")
        sys.exit(1)
