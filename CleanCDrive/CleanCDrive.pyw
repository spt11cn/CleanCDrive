# -*- coding: utf-8 -*-
"""
CleanCDrive.pyw —— C 盘清理工具（图形界面）

双击运行，或执行 start_CleanCDrive.cmd。
界面只负责展示与交互，真正的清理逻辑全在 cleaner_core.py。
"""

import os
import sys
import ctypes
import threading
import queue
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 日志与崩溃记录的存放位置。
# 打包成 onefile exe 后，HERE 指向的是临时解压目录，程序退出即被删除，
# 所以必须换到一个稳定可写的位置，否则日志等于没写。
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
        "CleanCDrive")
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except OSError:
        DATA_DIR = os.path.expanduser("~")
else:
    DATA_DIR = HERE

import tkinter as tk
from tkinter import ttk, messagebox

import cleaner_core as core

# ---------------------------------------------------------------- 视觉常量
BG = "#F2F5F8"
CARD = "#FFFFFF"
BORDER = "#DDE3EA"
TEXT = "#1A202C"
MUTED = "#718096"
ACCENT = "#2B6CB0"
GREEN = "#2F855A"
ORANGE = "#B7791F"
RED = "#C53030"
DIM = "#A0AEC0"

F_TITLE = ("Microsoft YaHei UI", 15, "bold")
F_SUB = ("Microsoft YaHei UI", 9)
F_BODY = ("Microsoft YaHei UI", 9)
F_NAME = ("Microsoft YaHei UI", 10, "bold")
F_SIZE = ("Microsoft YaHei UI", 10, "bold")
F_MONO = ("Consolas", 9)

APP_TITLE = "C 盘清理工具"


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class CleanApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1000x900")
        self.minsize(880, 680)
        self.configure(bg=BG)

        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.modules = {}
        self.rows = {}
        self.log_path = os.path.join(DATA_DIR, "cleanup_log.txt")

        self._init_style()
        self._build_ui()
        self._refresh_disk()
        self._build_rows()          # 预渲染模块列表，尺寸显示"—"，不触发扫描
        self._bind_shortcuts()
        self.after(120, self._poll)
        # 不自动扫描：由用户点击「扫描体检」或按 F5 触发
        self.log("已就绪。点击「扫描体检」开始检测可清理项。", "dim")

    # ------------------------------------------------------------ 样式
    def _init_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=F_BODY, background=BG, foreground=TEXT)
        st.configure("Card.TFrame", background=CARD, relief="flat")
        st.configure("Bg.TFrame", background=BG)

        st.configure("Scan.TButton", font=("Microsoft YaHei UI", 10),
                     background="#EDF2F7", foreground=TEXT, borderwidth=0,
                     focusthickness=0, padding=(16, 8))
        st.map("Scan.TButton", background=[("active", "#E2E8F0"), ("disabled", "#F7FAFC")])

        st.configure("Go.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                     background=GREEN, foreground="#FFFFFF", borderwidth=0,
                     focusthickness=0, padding=(18, 8))
        st.map("Go.TButton", background=[("active", "#276749"), ("disabled", "#A0AEC0")])

        st.configure("Stop.TButton", font=("Microsoft YaHei UI", 10),
                     background="#EDF2F7", foreground=MUTED, borderwidth=0,
                     focusthickness=0, padding=(14, 8))
        st.map("Stop.TButton", background=[("active", "#E2E8F0")])

        st.configure("Link.TButton", font=("Microsoft YaHei UI", 9),
                     background=CARD, foreground=ACCENT, borderwidth=0,
                     focusthickness=0, padding=(6, 2))
        st.map("Link.TButton", background=[("active", CARD)])

        st.configure("Disk.Horizontal.TProgressbar", troughcolor="#E6EBF1",
                     background=ACCENT, borderwidth=0, thickness=8)

        st.configure("Row.TCheckbutton", background=CARD)
        st.map("Row.TCheckbutton", background=[("active", CARD)])

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        self.grid_rowconfigure(4, weight=8)
        self.grid_rowconfigure(5, weight=2)
        self.grid_columnconfigure(0, weight=1)

        self._build_header().grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        self._build_disk().grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 7))
        self._build_banner().grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 7))
        self._build_toolbar().grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 7))
        self._build_modules().grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 7))
        self._build_log().grid(row=5, column=0, sticky="nsew", padx=16, pady=(0, 7))
        self._build_status().grid(row=6, column=0, sticky="ew", padx=16, pady=(0, 12))

    def _build_header(self):
        f = tk.Frame(self, bg=BG)
        left = tk.Frame(f, bg=BG)
        left.pack(side="left")
        tk.Label(left, text=APP_TITLE, font=F_TITLE, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(left, text="清理缓存、更新残留与旧版本目录 —— 删除前先给你看清楚会删什么",
                 font=F_SUB, bg=BG, fg=MUTED).pack(anchor="w", pady=(2, 0))

        badge = tk.Frame(f, bg="#EBF8F1" if core.is_admin() else "#FFF5F5",
                         highlightthickness=1,
                         highlightbackground="#C6F6D5" if core.is_admin() else "#FED7D7")
        badge.pack(side="right", pady=(6, 0))
        txt = "  管理员权限  " if core.is_admin() else "  普通权限  "
        tk.Label(badge, text=txt, font=("Microsoft YaHei UI", 9, "bold"),
                 bg=badge["bg"], fg=GREEN if core.is_admin() else RED).pack()
        return f

    def _build_disk(self):
        card = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=16, pady=13)

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x")
        tk.Label(top, text="C 盘", font=("Microsoft YaHei UI", 11, "bold"),
                 bg=CARD, fg=TEXT).pack(side="left")
        self.lbl_disk = tk.Label(top, text="读取中…", font=F_BODY, bg=CARD, fg=MUTED)
        self.lbl_disk.pack(side="left", padx=(10, 0))
        self.lbl_pct = tk.Label(top, text="", font=("Microsoft YaHei UI", 11, "bold"),
                                bg=CARD, fg=ACCENT)
        self.lbl_pct.pack(side="right")

        self.disk_bar = ttk.Progressbar(inner, style="Disk.Horizontal.TProgressbar",
                                        orient="horizontal", mode="determinate")
        self.disk_bar.pack(fill="x", pady=(9, 0))
        return card

    def _build_banner(self):
        self.banner = tk.Frame(self, bg="#FFFAF0", highlightthickness=1,
                               highlightbackground="#FEEBC8")
        if core.is_admin():
            return self.banner  # 空白，grid 会占 0 高度

        inner = tk.Frame(self.banner, bg="#FFFAF0")
        inner.pack(fill="x", padx=12, pady=9)
        tk.Label(inner, text="⚠", font=("Microsoft YaHei UI", 11), bg="#FFFAF0",
                 fg=ORANGE).pack(side="left")
        tk.Label(inner,
                 text="当前不是管理员权限：Edge / WebView2 / 夸克 / WinSxS / 更新缓存 这几个模块无法执行",
                 font=F_BODY, bg="#FFFAF0", fg="#8A5A12").pack(side="left", padx=(8, 0))
        ttk.Button(inner, text="以管理员身份重启", style="Link.TButton",
                   command=self.relaunch_as_admin).pack(side="right")
        return self.banner

    def _build_toolbar(self):
        f = tk.Frame(self, bg=BG)
        self.btn_scan = ttk.Button(f, text="扫描体检", style="Scan.TButton",
                                   command=self.start_scan)
        self.btn_scan.pack(side="left")
        self.btn_clean = ttk.Button(f, text="开始清理", style="Go.TButton",
                                    command=self.start_clean)
        self.btn_clean.pack(side="left", padx=(8, 0))
        self.btn_stop = ttk.Button(f, text="停止", style="Stop.TButton",
                                   command=self.request_stop)
        self.btn_stop.pack(side="left", padx=(8, 0))
        self.btn_stop.configure(state="disabled")

        right = tk.Frame(f, bg=BG)
        right.pack(side="right")
        self.var_resetbase = tk.BooleanVar(value=False)
        tk.Checkbutton(right, text="DISM ResetBase（之后无法卸载更新）",
                       variable=self.var_resetbase, bg=BG, fg=MUTED,
                       activebackground=BG, font=("Microsoft YaHei UI", 9),
                       selectcolor=CARD, bd=0, highlightthickness=0).pack(side="right", padx=(12, 2))

        self.var_all = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="全选", variable=self.var_all, command=self._toggle_all,
                       bg=BG, fg=TEXT, activebackground=BG, font=F_BODY,
                       selectcolor=CARD, bd=0, highlightthickness=0).pack(side="right")
        return f

    def _build_modules(self):
        wrap = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        head = tk.Frame(wrap, bg=CARD)
        head.pack(fill="x", padx=14, pady=(10, 6))
        tk.Label(head, text="清理项目", font=("Microsoft YaHei UI", 10, "bold"),
                 bg=CARD, fg=TEXT).pack(side="left")
        self.lbl_total = tk.Label(head, text="尚未扫描", font=F_BODY, bg=CARD, fg=MUTED)
        self.lbl_total.pack(side="right")

        box = tk.Frame(wrap, bg=BG)
        box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.canvas = tk.Canvas(box, bg=BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.mod_frame = tk.Frame(self.canvas, bg=BG)
        self._win = self.canvas.create_window((0, 0), window=self.mod_frame, anchor="nw")
        self.mod_frame.bind("<Configure>", self._on_frame_config)
        self.canvas.bind("<Configure>", self._on_canvas_config)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        return wrap

    def _build_log(self):
        wrap = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        head = tk.Frame(wrap, bg=CARD)
        head.pack(fill="x", padx=14, pady=(8, 4))
        tk.Label(head, text="执行日志", font=("Microsoft YaHei UI", 10, "bold"),
                 bg=CARD, fg=TEXT).pack(side="left")
        ttk.Button(head, text="打开日志文件", style="Link.TButton",
                   command=self.open_log).pack(side="right")
        ttk.Button(head, text="清空显示", style="Link.TButton",
                   command=self._clear_log).pack(side="right")

        body = tk.Frame(wrap, bg=CARD)
        body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.txt = tk.Text(body, height=6, bg="#FBFCFD", fg="#2D3748", relief="flat",
                           font=F_MONO, wrap="word", padx=10, pady=6,
                           insertbackground=TEXT, highlightthickness=0)
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.txt.yview)
        self.txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        self.txt.tag_configure("ok", foreground=GREEN)
        self.txt.tag_configure("warn", foreground=ORANGE)
        self.txt.tag_configure("err", foreground=RED)
        self.txt.tag_configure("dim", foreground=DIM)
        self.txt.tag_configure("head", foreground=ACCENT, font=("Consolas", 9, "bold"))
        self.txt.configure(state="disabled")
        return wrap

    def _build_status(self):
        f = tk.Frame(self, bg=BG)
        self.lbl_status = tk.Label(f, text="就绪", font=F_BODY, bg=BG, fg=MUTED)
        self.lbl_status.pack(side="left")
        self.lbl_est = tk.Label(f, text="", font=("Microsoft YaHei UI", 9, "bold"),
                                bg=BG, fg=GREEN)
        self.lbl_est.pack(side="right")
        return f

    # ------------------------------------------------------------ 滚动
    def _on_frame_config(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_config(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    # ------------------------------------------------------------ 日志
    def log(self, msg, tag=None):
        self.txt.configure(state="normal")
        self.txt.insert("end", msg + "\n", tag or ())
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def open_log(self):
        if os.path.exists(self.log_path):
            try:
                os.startfile(self.log_path)
                return
            except Exception:
                pass
        messagebox.showinfo(APP_TITLE, "还没有日志文件。")

    # ------------------------------------------------------------ 磁盘
    def _refresh_disk(self):
        total, free = core.disk_usage()
        if not total:
            self.lbl_disk.configure(text="读取失败")
            return
        used = total - free
        pct = used / total * 100
        self.lbl_disk.configure(text="剩余 %s / 共 %s" % (core.fmt_size(free), core.fmt_size(total)))
        self.lbl_pct.configure(text="已用 %.1f%%" % pct,
                               fg=RED if pct > 90 else (ORANGE if pct > 80 else ACCENT))
        self.disk_bar.configure(maximum=total, value=used)

    # ------------------------------------------------------------ 模块行
    def _build_rows(self):
        for w in self.mod_frame.winfo_children():
            w.destroy()
        self.rows.clear()

        for spec in core.module_specs():
            key = spec["key"]
            row = tk.Frame(self.mod_frame, bg=CARD, highlightthickness=1,
                           highlightbackground=BORDER)
            row.pack(fill="x", pady=(0, 4), padx=2)

            var = tk.BooleanVar(value=spec["default_checked"])
            cb = tk.Checkbutton(row, variable=var, command=self._update_estimate,
                                bg=CARD, activebackground=CARD, selectcolor="#FFFFFF",
                                bd=0, highlightthickness=0, takefocus=0, cursor="hand2")
            cb.pack(side="left", padx=(10, 6), pady=4)

            namebox = tk.Frame(row, bg=CARD, width=170, height=18)
            namebox.pack(side="left")
            namebox.pack_propagate(False)
            name = tk.Label(namebox, text=spec["name"], font=F_NAME, bg=CARD, fg=TEXT,
                            anchor="w")
            name.pack(fill="both", expand=True)

            detail = ttk.Button(row, text="详情", style="Link.TButton",
                                command=lambda k=key: self._show_detail(k))
            detail.pack(side="right", padx=(0, 10))

            size = tk.Label(row, text="—", font=F_SIZE, bg=CARD, fg=MUTED,
                            width=15, anchor="e")
            size.pack(side="right")

            tag = tk.Label(row, text="", font=("Microsoft YaHei UI", 8), bg=CARD,
                           fg=DIM, width=9, anchor="e")
            tag.pack(side="right", padx=(4, 4))

            desc = tk.Label(row, text=spec["desc"], font=("Microsoft YaHei UI", 8),
                            bg=CARD, fg=MUTED, anchor="w")
            desc.pack(side="left", fill="x", expand=True, padx=(0, 4))

            self.rows[key] = {"var": var, "cb": cb, "size": size, "tag": tag,
                              "row": row, "name": name, "desc": desc}

    def _show_detail(self, key):
        m = self.modules.get(key)
        if not m:
            self.log("  还没扫描过，先点「扫描体检」", "dim")
            return
        self.log("")
        self.log("== %s ==" % m.name, "head")
        if m.note:
            self.log("   %s" % m.note, "dim")
        for it in m.items:
            mark = "[删]" if it.kind == "delete" else ("[留]" if it.kind == "keep" else "[·]")
            tag = None if it.kind == "delete" else "dim"
            self.log("   %s %-46s %10s  %s" % (mark, it.label, core.fmt_size(it.size), it.detail), tag)
        if not m.items:
            self.log("   无项目", "dim")

    def _toggle_all(self):
        on = self.var_all.get()
        for key, r in self.rows.items():
            if str(r["cb"].cget("state")) != "disabled":
                r["var"].set(on)
        self._update_estimate()

    def _selected(self):
        out = []
        for key, r in self.rows.items():
            m = self.modules.get(key)
            if not m or not r["var"].get():
                continue
            if str(r["cb"].cget("state")) == "disabled":
                continue
            out.append(m)
        return out

    def _update_estimate(self):
        mods = self._selected()
        total = sum(m.total for m in mods)
        n = len([m for m in mods if m.actions])
        if not self.modules:
            self.lbl_total.configure(text="尚未扫描")
            self.lbl_est.configure(text="")
            return
        self.lbl_total.configure(text="已选 %d 项 / 可执行 %d 项" % (len(mods), n))
        self.lbl_est.configure(text="预计释放约 %s" % core.fmt_size(total) if total else "")

    # ------------------------------------------------------------ 扫描
    def start_scan(self):
        if self.busy:
            return
        self.stop_event.clear()
        self._set_busy(True, "正在扫描…")
        self._build_rows()
        self.modules.clear()
        self.log("")
        self.log("开始扫描 —— 只读统计，不会删除任何文件", "head")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            for spec in core.module_specs():
                if self.stop_event.is_set():
                    break
                self.queue.put(("log", ("  扫描 %s …" % spec["name"], "dim")))
                try:
                    m = core.scan_module(spec["key"],
                                         lambda s: self.queue.put(("log", ("    " + str(s), "dim"))))
                except Exception as e:
                    self.queue.put(("log", ("  扫描失败 %s: %s" % (spec["name"], e), "err")))
                    continue
                self.queue.put(("module", m))
            self.queue.put(("scan_done", None))
        except Exception:
            self.queue.put(("log", (traceback.format_exc(), "err")))
            self.queue.put(("scan_done", None))

    def _apply_module(self, m):
        self.modules[m.key] = m
        r = self.rows.get(m.key)
        if not r:
            return
        r["size"].configure(text=core.fmt_size(m.total) if m.total else "无需清理",
                            fg=GREEN if m.total else DIM)
        need_admin = m.needs_admin and not core.is_admin()
        if need_admin:
            r["tag"].configure(text="需管理员", fg=ORANGE)
            r["cb"].configure(state="disabled")
            r["var"].set(False)
            r["name"].configure(fg="#8A94A6")
            r["row"].configure(highlightbackground="#EDF1F5")
        elif not m.actions:
            r["tag"].configure(text="", fg=DIM)
            r["var"].set(False)
            r["cb"].configure(state="disabled")
            r["name"].configure(fg="#8A94A6")
            r["row"].configure(highlightbackground="#EDF1F5")
        else:
            r["tag"].configure(text="")
            r["name"].configure(fg=TEXT)
            r["row"].configure(highlightbackground=BORDER)
        self._update_estimate()

    # ------------------------------------------------------------ 清理
    def start_clean(self):
        if self.busy:
            return
        mods = self._selected()
        if not self.modules:
            messagebox.showinfo(APP_TITLE, "还没有扫描。\n\n请先点「扫描体检」，扫描完成后再勾选要清理的项目。")
            return
        if not mods:
            messagebox.showinfo(APP_TITLE, "没有勾选任何可执行的项目。\n勾选左侧复选框后再试。")
            return

        total = sum(m.total for m in mods)
        lines = ["即将执行以下清理，删除后不可恢复（自动备份目录已排除在外）：", ""]
        for m in mods:
            if m.actions:
                lines.append("  · %-16s 约 %s" % (m.name, core.fmt_size(m.total)))
        lines.append("")
        lines.append("合计约 %s" % core.fmt_size(total))
        lines.append("")
        if any(m.key in ("edge", "wps", "quark", "webview") for m in mods):
            lines.append("注意：相关程序正在运行时，被占用的文件会自动跳过，不会强杀进程。")
        if any(m.key == "dism" for m in mods):
            lines.append("注意：DISM 步骤耗时 10~30 分钟，中途界面看似无响应属于正常。")
        lines.append("")
        lines.append("确认开始清理？")

        if not messagebox.askyesno(APP_TITLE + " · 确认", "\n".join(lines), icon="warning"):
            return

        self.stop_event.clear()
        self._set_busy(True, "正在清理…")
        self.log("")
        self.log("开始清理", "head")
        self._log_file("清理前剩余 %s" % core.fmt_size(core.disk_usage()[1]))
        # Tk 变量必须在主线程读取，工作线程里读会抛 RuntimeError
        reset_base = bool(self.var_resetbase.get())
        threading.Thread(target=self._clean_worker, args=(mods, reset_base),
                         daemon=True).start()

    def _clean_worker(self, mods, reset_base):
        try:
            freed = core.clean_modules(
                mods,
                log=lambda s: self.queue.put(("log", (s, None))),
                cancel=self.stop_event.is_set,
                reset_base=reset_base,
                on_module_done=lambda m, got: self.queue.put(("mod_done", (m, got))),
            )
            self.queue.put(("clean_done", freed))
        except Exception:
            self.queue.put(("log", (traceback.format_exc(), "err")))
            self.queue.put(("clean_done", 0))

    def request_stop(self):
        if self.busy:
            self.stop_event.set()
            self.lbl_status.configure(text="正在停止…（当前文件处理完就退出）", fg=ORANGE)

    # ------------------------------------------------------------ 队列
    def _poll(self):
        try:
            for _ in range(80):
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    text, tag = payload
                    self.log(text, tag)
                elif kind == "module":
                    self._apply_module(payload)
                elif kind == "mod_done":
                    m, got = payload
                    r = self.rows.get(m.key)
                    if r:
                        r["size"].configure(text="已释放 %s" % core.fmt_size(got), fg=GREEN)
                        r["var"].set(False)
                elif kind == "scan_done":
                    self._on_scan_done()
                elif kind == "clean_done":
                    self._on_clean_done(payload)
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _on_scan_done(self):
        self._set_busy(False, "扫描完成")
        self._update_estimate()
        total, free = core.disk_usage()
        self._refresh_disk()
        self.log("扫描完成，C 盘剩余 %s" % core.fmt_size(free), "ok")
        health = core.edge_health()
        if health:
            bad = [h for h in health if not h[2]]
            if bad:
                for stub, v, _ok in bad:
                    self.log("  Edge 引导器异常: %s 绑定的 %s 不存在" % (stub, v), "err")
                self.log("  可用 fix-edge-launcher.ps1 修复，或重装 Edge", "warn")
            else:
                self.log("  Edge 引导器健康检查通过（%d 个引导器绑定目标均存在）" % len(health), "dim")

    def _on_clean_done(self, freed):
        self._set_busy(False, "清理完成")
        total, free = core.disk_usage()
        self._refresh_disk()
        self.log("")
        self.log("清理结束，本次释放 %s；C 盘剩余 %s" % (core.fmt_size(freed), core.fmt_size(free)), "ok")
        self._log_file("清理结束释放 %s，剩余 %s" % (core.fmt_size(freed), core.fmt_size(free)))
        messagebox.showinfo(APP_TITLE, "清理完成。\n\n本次释放约 %s\nC 盘当前剩余 %s"
                            % (core.fmt_size(freed), core.fmt_size(free)))
        self.start_scan()

    def _log_file(self, line):
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
        except OSError:
            pass

    # ------------------------------------------------------------ 状态
    def _set_busy(self, busy, status=None):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_scan.configure(state=state)
        self.btn_clean.configure(state=state)
        self.btn_stop.configure(state="normal" if busy else "disabled")
        if status:
            self.lbl_status.configure(text=status, fg=MUTED)

    # ------------------------------------------------------------ 提权
    def relaunch_as_admin(self):
        exe = sys.executable
        for cand in (exe.replace("python.exe", "pythonw.exe"),
                     os.path.join(os.path.dirname(exe), "pythonw.exe")):
            if cand != exe and os.path.isfile(cand):
                exe = cand
                break
        try:
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, '"%s"' % os.path.abspath(__file__), None, 1)
            if rc > 32:
                self.destroy()
            else:
                messagebox.showwarning(APP_TITLE, "提权被取消或失败（代码 %d）" % rc)
        except Exception as e:
            messagebox.showerror(APP_TITLE, "无法提权重启: %s" % e)

    def _bind_shortcuts(self):
        self.bind("<F5>", lambda e: self.start_scan())
        self.bind("<Control-l>", lambda e: self._clear_log())


def _selftest():
    """自检：建窗口、截图、退出（开发用）"""
    if "--as-admin" in sys.argv:
        core.is_admin = lambda: True
    app = CleanApp()
    app.geometry("1000x900+30+15")
    app.deiconify()
    app.attributes("-topmost", True)
    app.lift()
    app.focus_force()
    for _ in range(20):
        app.update()
        time.sleep(0.03)

    deadline = time.time() + 240
    while time.time() < deadline:
        app.attributes("-topmost", True)
        app.update()
        time.sleep(0.05)
        if not app.busy and app.modules:
            break
    # 等窗口完成映射与重绘
    for _ in range(40):
        app.attributes("-topmost", True)
        app.lift()
        app.update()
        time.sleep(0.05)
        if app.winfo_viewable() and app.winfo_ismapped():
            break
    if not app.winfo_ismapped():
        app.withdraw()
        app.update()
        time.sleep(0.3)
        app.deiconify()
        app.update()
        for _ in range(40):
            app.update()
            time.sleep(0.05)
            if app.winfo_ismapped():
                break
    time.sleep(0.6)
    app.update()
    try:
        from PIL import ImageGrab
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        out = os.path.join(HERE, "_selftest.png")
        img.save(out)
        print("screenshot:", out, "size:", img.size,
              "viewable:", app.winfo_viewable(), "mapped:", app.winfo_ismapped())
    except Exception as e:
        print("screenshot failed:", e)
    app.destroy()


def main():
    enable_dpi_awareness()
    if "--selftest" in sys.argv:
        _selftest()
        return
    app = CleanApp()
    app.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        crash_path = os.path.join(DATA_DIR, "crash.log")
        try:
            with open(crash_path, "w", encoding="utf-8") as f:
                f.write(err)
        except OSError:
            crash_path = "（写入失败）"
        try:
            ctypes.windll.user32.MessageBoxW(
                0, err[-1200:] + "\n\n崩溃记录已保存到：\n" + crash_path,
                "C 盘清理工具 · 启动失败", 0x10)
        except Exception:
            pass
        raise
