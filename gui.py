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
            PLATFORM_PRESETS, load_config, get_platform_presets,
            build_yt_dlp_args, check_tool, normalize_douyin_url,
            cleanup_temp_files, check_output_exists,
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
            self.root.geometry("720x560")
            self.root.minsize(600, 450)
            self.config = load_config()
            self.process = None
            self._fix_env()
            self._setup_ui()
            self._check_env()

        @staticmethod
        def _fix_env():
            """确保 yt-dlp 能找到浏览器 cookies 所需的环境变量。"""
            import getpass
            username = os.environ.get("USERNAME") or getpass.getuser()
            home = os.environ.get("USERPROFILE") or f"C:\\Users\\{username}"
            if not os.environ.get("LOCALAPPDATA"):
                os.environ["LOCALAPPDATA"] = f"{home}\\AppData\\Local"
            if not os.environ.get("APPDATA"):
                os.environ["APPDATA"] = f"{home}\\AppData\\Roaming"

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
            ttk.Label(main, text="Cookies 来源 (高清需要)").pack(anchor="w", pady=(8, 0))
            cookies_row = ttk.Frame(main)
            cookies_row.pack(fill="x", pady=(2, 8))
            self.cookies_browser_var = tk.StringVar(
                value=self.config.get("cookies_from_browser", "")
            )
            browsers = ["chrome", "edge", "firefox", "opera", "brave", ""]
            cb_cookies = ttk.Combobox(cookies_row, textvariable=self.cookies_browser_var,
                                      values=browsers, width=8)
            cb_cookies.pack(side="left")
            cb_cookies.bind("<<ComboboxSelected>>", self._on_cookies_change)
            ttk.Label(cookies_row, text="浏览器 或").pack(side="left", padx=4)
            self.cookies_file_var = tk.StringVar(
                value=self.config.get("cookies_file", "")
            )
            file_entry = ttk.Entry(cookies_row, textvariable=self.cookies_file_var, width=35)
            file_entry.pack(side="left", padx=4)
            ttk.Button(cookies_row, text="浏览...", command=self._browse_cookies_file, width=6).pack(side="left")
            self.cookies_status = ttk.Label(cookies_row, text="", foreground="gray")
            self.cookies_status.pack(side="left", padx=8)
            ttk.Button(cookies_row, text="测试", command=self._test_cookies, width=4).pack(side="left")
            self._update_cookies_status()

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
                text="策略: 优先 cookies 高清 -> 失败自动回退低清")
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
            """浏览器 cookies 选择变化时更新配置并保存。"""
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
            """将当前 cookies 设置保存到 config.json。"""
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
            """更新 cookies 状态显示。"""
            browser = self.cookies_browser_var.get().strip()
            file_path = self.cookies_file_var.get().strip()
            if file_path:
                if os.path.isfile(file_path):
                    self.cookies_status.config(text=f"✓ 文件就绪 ({os.path.basename(file_path)})", foreground="#6a9955")
                else:
                    self.cookies_status.config(text="✗ 文件不存在", foreground="#f44747")
            elif browser:
                self.cookies_status.config(text=f"从 {browser} 读取 (关闭浏览器后生效)", foreground="#ce9178")
            else:
                self.cookies_status.config(text="未配置 (仅低清)", foreground="#808080")

        def _test_cookies(self):
            """测试浏览器 cookies 是否可读取。"""
            browser = self.cookies_browser_var.get().strip()
            if not browser:
                messagebox.showinfo("提示", "请先选择浏览器。")
                return
            self.cookies_status.config(text=f"检查 {browser}...", foreground="#808080")
            self._log(f"=== cookies 检查: {browser} ===\n", "info")

            # 1. 本地文件检查
            lap = os.environ.get("LOCALAPPDATA", "")
            cookie_paths = [
                os.path.join(lap, "Google", "Chrome", "User Data", "Default", "Network", "Cookies"),
                os.path.join(lap, "Google", "Chrome", "User Data", "Default", "Cookies"),
                os.path.join(lap, "Microsoft", "Edge", "User Data", "Default", "Network", "Cookies"),
                os.path.join(lap, "Microsoft", "Edge", "User Data", "Default", "Cookies"),
            ]
            found = False
            for p in cookie_paths:
                if os.path.isfile(p):
                    self._log(f"  本地文件: {p} ({os.path.getsize(p)} bytes)\n", "success")
                    found = True
            # Firefox detection
            ff_profiles = os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox", "Profiles")
            if os.path.isdir(ff_profiles):
                for item in os.listdir(ff_profiles):
                    ff_cookies = os.path.join(ff_profiles, item, "cookies.sqlite")
                    if os.path.isfile(ff_cookies):
                        self._log(f"  本地文件: {ff_cookies} ({os.path.getsize(ff_cookies)} bytes)\n", "success")
                        found = True
            if not found:
                self._log("  (未在常见位置找到，尝试 yt-dlp 读取...)\n", "dim")

            # 2. yt-dlp 快速提取 (用 about:blank 避免网络请求)
            try:
                proc = subprocess.Popen(
                    ["yt-dlp", "--cookies-from-browser", browser, "-j", "about:blank"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                    env=os.environ,
                )
                out, _ = proc.communicate(timeout=8)
                out_lower = out.lower()

                if "extracting cookies" in out_lower and "extracted" in out_lower:
                    cnt = ""
                    for w in out_lower.split():
                        if w.isdigit() and int(w) > 10:
                            cnt = w; break
                    self._log(f"  yt-dlp 提取: {cnt} cookies\n" if cnt else "  yt-dlp 提取成功\n", "success")
                    self.cookies_status.config(text=f"{browser} cookies OK", foreground="#6a9955")
                elif "could not find" in out_lower or "permission" in out_lower:
                    self.cookies_status.config(text=f"{browser} 已锁定", foreground="#ce9178")
                    self._log("  cookies 被浏览器锁定。关闭浏览器后重试，或改用 cookies 文件。\n", "warn")
                else:
                    self.cookies_status.config(text=f"{browser} 异常", foreground="#ce9178")
                    self._log(out[:400] + "\n", "dim")
            except subprocess.TimeoutExpired:
                proc.kill()
                self.cookies_status.config(text=f"{browser} 超时 (文件存在)", foreground="#ce9178")
                self._log("  网络超时，但本地 cookies 文件存在，下载时自动重试。\n", "warn")
            except Exception as e:
                self.cookies_status.config(text=f"✗ 测试失败: {e}", foreground="#f44747")

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
            # 自动检测并标准化抖音精选 URL
            normalized = normalize_douyin_url(url)
            if normalized != url:
                self._log(f"抖音 URL 标准化: {normalized}\n", "info")
                url = normalized

            high, fallback = get_platform_presets(platform, self.config)

            # 记录开始时间，用于后续文件存在检查
            import time
            start_time = time.time()

            # Round 1: 高清 (cookies)
            self._log("--- Round 1: 高清 (cookies) ---\n", "info")
            rc = self._run(url, output_dir, high, use_cookies=True)
            if rc == 0:
                self._log("\n高清下载成功\n", "success")
                cleanup_temp_files(output_dir)
                self._on_done(0)
                return

            # 检查视频文件是否实际已存在
            existing = check_output_exists(output_dir, start_time)
            if existing:
                self._log(f"\nyt-dlp 返回非零(exit={rc})，但检测到 {len(existing)} 个视频文件，视为下载成功\n", "success")
                self._log("  " + ", ".join(f.name for f in existing) + "\n", "dim")
                cleanup_temp_files(output_dir)
                self._on_done(0)
                return

            # cookies 错误 + 自动重试
            self._log("\n[!] Cookies 可能被浏览器锁定，2秒后重试...\n", "warn")
            self._log("  建议: 关闭 Chrome 后重试，或在 config.json 中设置 cookies_file\n", "dim")
            time.sleep(2)
            self._log("--- Round 1 重试: 高清 (cookies) ---\n", "info")
            rc = self._run(url, output_dir, high, use_cookies=True)
            if rc == 0:
                self._log("\n重试成功，高清下载完成\n", "success")
                cleanup_temp_files(output_dir)
                self._on_done(0)
                return

            # 重试后也检查文件
            existing = check_output_exists(output_dir, start_time)
            if existing:
                self._log(f"\n重试后 yt-dlp 仍返回非零，但检测到 {len(existing)} 个视频文件，视为下载成功\n", "success")
                self._log("  " + ", ".join(f.name for f in existing) + "\n", "dim")
                cleanup_temp_files(output_dir)
                self._on_done(0)
                return

            if fallback is None:
                self._log(f"\n{platform} 需登录且cookies不可用，无回退。\n", "error")
                cleanup_temp_files(output_dir)
                self._on_done(rc)
                return
            self._log(f"\n高清仍失败(exit={rc})，回退低清 (无需cookies)...\n", "warn")
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
