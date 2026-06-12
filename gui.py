#!/usr/bin/env python3
"""
video-fetcher GUI — 可视化视频下载客户端
基于 tkinter，调用 fetch 模块的双轨策略。
"""

import os
import sys
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch import (
    PLATFORM_PRESETS, load_config, get_platform_presets,
    build_yt_dlp_args, check_tool,
)


class VideoFetcherGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Video Fetcher")
        self.root.geometry("720x560")
        self.root.minsize(600, 450)
        self.config = load_config()
        self.process = None
        self._setup_ui()
        self._check_env()

    def _setup_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        # URL
        ttk.Label(main, text="视频 URL").pack(anchor="w")
        url_frame = ttk.Frame(main)
        url_frame.pack(fill="x", pady=(2, 8))
        self.url_var = tk.StringVar()
        self.url_entry = ttk.Entry(url_frame, textvariable=self.url_var, font=("Consolas", 10))
        self.url_entry.pack(fill="x", expand=True)

        # 平台 + 输出
        row = ttk.Frame(main)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="平台").pack(side="left")
        self.platform_var = tk.StringVar(value="generic")
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

        # 策略
        self.strategy_label = ttk.Label(main, foreground="gray",
            text="策略: 优先 cookies 高清 -> 失败自动回退低清")
        self.strategy_label.pack(anchor="w", pady=(4, 6))

        # 按钮
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=4)
        self.download_btn = ttk.Button(btn_frame, text="开始下载", command=self._start_download)
        self.download_btn.pack(side="left", padx=(0, 8))
        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self._stop_download, state="disabled")
        self.stop_btn.pack(side="left")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(btn_frame, textvariable=self.status_var, foreground="gray").pack(side="right")

        # 日志
        log_frame = ttk.Frame(main)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text = tk.Text(log_frame, wrap="word", state="disabled",
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            relief="flat", borderwidth=0, padx=8, pady=6)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.log_text.tag_configure("info", foreground="#569cd6")
        self.log_text.tag_configure("success", foreground="#6a9955")
        self.log_text.tag_configure("warn", foreground="#ce9178")
        self.log_text.tag_configure("error", foreground="#f44747")
        self.log_text.tag_configure("dim", foreground="#808080")

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

        os.makedirs(output_dir, exist_ok=True)
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
            self._on_done(1, "已停止")

    def _on_done(self, exit_code, msg=""):
        def _done():
            self.download_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.status_var.set(msg or (f"完成" if exit_code == 0 else f"失败(exit={exit_code})"))
        self.root.after(0, _done)

    def _download_worker(self, url, platform, output_dir):
        high, fallback = get_platform_presets(platform, self.config)

        self._log("--- Round 1: 高清 (cookies) ---\n", "info")
        rc = self._run(url, output_dir, high, use_cookies=True)
        if rc == 0:
            self._log("\n高清下载成功\n", "success")
            self._on_done(0)
            return

        if fallback is None:
            self._log(f"\n{platform} 需登录且cookies不可用，无回退。\n", "error")
            self._on_done(rc)
            return

        self._log(f"\n高清失败(exit={rc})，回退低清...\n", "warn")
        self._log("--- Round 2: 低清 (无cookies) ---\n", "info")
        rc2 = self._run(url, output_dir, fallback, use_cookies=False)
        if rc2 == 0:
            self._log("\n低清下载成功（已降级）\n", "success")
        else:
            self._log(f"\n低清也失败(exit={rc2})\n", "error")
        self._on_done(rc2)

    def _run(self, url, output_dir, opts, use_cookies):
        args = build_yt_dlp_args(url, output_dir, opts, self.config, use_cookies=use_cookies)
        self._log(f"cmd: {' '.join(args)}\n", "dim")
        try:
            self.process = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                env={**os.environ, "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", "")},
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


def main():
    root = tk.Tk()
    VideoFetcherGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
