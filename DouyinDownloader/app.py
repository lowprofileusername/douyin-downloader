# -*- coding: utf-8 -*-
"""抖音视频下载器 —— Windows 图形界面（高分屏适配版）。

高分屏适配要点：
  1. 必须在创建 Tk 根窗口**之前**声明 DPI 感知，否则 Tk 早已按 96 DPI 完成初始化；
  2. 读取系统真实 DPI 算出缩放系数，显式设置 `tk scaling`（像素/点），
     使以"点"为单位的字号按屏幕真实 DPI 渲染，不再受 Tk 启动时的猜测影响；
  3. 内边距、进度条厚度、窗口尺寸等以像素为单位的值，全部按同一系数放大。

下载逻辑在后台线程执行，主线程只负责刷新 UI，抓取与下载过程中窗口不会卡死。
"""
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

BG = "#F4F5F7"
CARD = "#FFFFFF"
BORDER = "#E2E5EA"
TEXT = "#1F2329"
SUB = "#8A9099"
ACCENT = "#2F6BFF"
ACCENT_HOVER = "#2559DB"

QUALITY = {"最高画质（自动）": 99999, "1080P": 1080, "720P": 720, "540P": 540}


def init_dpi():
    """声明 DPI 感知并返回缩放系数（相对 96 DPI）。必须在 tk.Tk() 之前调用。

    返回 1.0 表示不自行缩放：进程仍是无感知状态时 Windows 会整体拉伸窗口，
    此时再乘一次系数会导致双倍放大。
    """
    try:
        from ctypes import windll, wintypes, byref, c_int
        try:
            # 1 = SYSTEM_DPI_AWARE。Tk 不处理 WM_DPICHANGED，故不用 PER_MONITOR(2)，
            # 否则拖到不同缩放的副屏时不会重排。
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        # 复核感知是否真的生效（声明失败时不可自行缩放，否则与系统拉伸叠加）
        aware = 0
        try:
            val = c_int()
            if windll.shcore.GetProcessDpiAwareness(0, byref(val)) == 0:
                aware = val.value
        except Exception:
            aware = 1      # 老系统无此 API，假定 SetProcessDPIAware 已生效
        if aware < 1:
            return 1.0

        dpi = 0
        try:
            get_dpi = windll.user32.GetDpiForSystem          # Win10 1607+
            get_dpi.restype = wintypes.UINT
            dpi = get_dpi()
        except Exception:
            pass
        if not dpi:
            hdc = windll.user32.GetDC(0)
            dpi = windll.gdi32.GetDeviceCaps(hdc, 88)        # LOGPIXELSX
            windll.user32.ReleaseDC(0, hdc)
        if dpi:
            return max(1.0, round(dpi / 96.0, 3))
    except Exception:
        pass
    return 1.0


SCALE = init_dpi()


def px(value):
    """像素尺寸按 DPI 缩放（内边距、窗口宽高、进度条厚度等）。

    字号不在这里放大——字体以“点”为单位，由 tk scaling 统一换算，
    两者用同一个 SCALE，整体比例才不会失真。
    """
    return int(round(value * SCALE))


class App:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.busy = False
        self.result_path = None

        # Tk 的 scaling = 每点多少像素；96 DPI 基准为 4/3。显式设定可保证
        # 无论 Tk 启动时如何猜测，点字号都按屏幕真实 DPI 渲染。
        root.tk.call("tk", "scaling", 4.0 / 3.0 * SCALE)

        root.title("抖音视频下载器")
        root.geometry("%dx%d" % (px(700), px(660)))
        root.minsize(px(620), px(600))
        root.configure(bg=BG)

        self._build_styles()
        self._build_ui()
        root.after(80, self._poll)

    # ------------------------------------------------------------------ 样式
    def _build_styles(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=BG, foreground=TEXT,
                     font=("Microsoft YaHei UI", 10))
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD, relief="flat")
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Card.TLabel", background=CARD, foreground=TEXT)
        st.configure("Sub.TLabel", background=CARD, foreground=SUB,
                     font=("Microsoft YaHei UI", 9))
        st.configure("Head.TLabel", background=BG, foreground=TEXT,
                     font=("Microsoft YaHei UI", 16, "bold"))
        st.configure("Tip.TLabel", background=BG, foreground=SUB,
                     font=("Microsoft YaHei UI", 9))
        st.configure("Field.TLabel", background=CARD, foreground=TEXT,
                     font=("Microsoft YaHei UI", 10, "bold"))
        st.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, padding=px(6))
        st.configure("TCombobox", fieldbackground="#FFFFFF", bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, padding=px(5))
        st.configure("Go.TButton", background=ACCENT, foreground="#FFFFFF",
                     bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                     padding=px(8), font=("Microsoft YaHei UI", 11, "bold"))
        st.map("Go.TButton", background=[("active", ACCENT_HOVER), ("disabled", "#B9C6EE")])
        st.configure("TButton", background="#FFFFFF", bordercolor=BORDER,
                     lightcolor=BORDER, darkcolor=BORDER, padding=px(5))
        st.map("TButton", background=[("active", "#EEF1F6")])
        st.configure("Bar.Horizontal.TProgressbar", background=ACCENT,
                     troughcolor="#E9ECF2", bordercolor="#E9ECF2", thickness=px(6))

    # ------------------------------------------------------------------ 布局
    def _card(self, parent, **kw):
        outer = ttk.Frame(parent, style="Card.TFrame", padding=1)
        inner = ttk.Frame(outer, style="Card.TFrame", padding=kw.pop("padding", px(16)))
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        outer.configure(borderwidth=0)
        return outer, inner

    # ------------------------------------------------------------- 编辑菜单
    @staticmethod
    def _has_sel(w):
        try:
            return bool(w.tag_ranges("sel")) if isinstance(w, tk.Text) else w.selection_present()
        except Exception:
            return False

    def _add_edit_menu(self, w, readonly=False, clean_paste=False):
        """给输入框 / 文本框挂右键菜单（剪切、复制、粘贴、删除、全选）。

        Tk 的 Entry / Text 在 Windows 下默认没有右键菜单，必须自己挂。
        """
        m = tk.Menu(w, tearoff=0, font=("Microsoft YaHei UI", 9),
                    bg="#FFFFFF", fg="#1F2329", activebackground="#E8F0FE",
                    activeforeground="#1F2329", relief="flat", bd=1)

        def copy_text():
            # 只读文本框（日志区）处于 disabled 状态时 <<Copy>> 不生效，手动取文本
            try:
                txt = (w.get("sel.first", "sel.last") if self._has_sel(w)
                       else w.get("1.0", "end-1c"))
                w.clipboard_clear()
                w.clipboard_append(txt)
            except Exception:
                pass

        def do(kind):
            try:
                w.focus_set()
                if kind == "copy":
                    if readonly:
                        copy_text()
                    else:
                        w.event_generate("<<Copy>>")
                elif kind == "cut" and not readonly:
                    w.event_generate("<<Cut>>")
                elif kind == "paste" and not readonly:
                    w.event_generate("<<Paste>>")
                    if clean_paste:
                        w.after(10, lambda: self._clean_entry(w))
                elif kind == "del" and not readonly:
                    if self._has_sel(w):
                        w.delete("sel.first", "sel.last")
                elif kind == "all":
                    if isinstance(w, tk.Text):
                        w.tag_add("sel", "1.0", "end")
                        w.mark_set("insert", "end")
                    else:
                        w.select_range(0, "end")
                        w.icursor("end")
            except Exception:
                pass

        m.add_command(label="剪切", command=lambda: do("cut"))
        m.add_command(label="复制", command=lambda: do("copy"))
        m.add_command(label="粘贴", command=lambda: do("paste"))
        m.add_command(label="删除", command=lambda: do("del"))
        m.add_separator()
        m.add_command(label="全选", command=lambda: do("all"))

        def popup(ev):
            sel = self._has_sel(w)
            try:
                clip = bool(w.clipboard_get())
            except Exception:
                clip = False
            m.entryconfigure(0, state="normal" if sel and not readonly else "disabled")
            m.entryconfigure(1, state="normal" if sel or readonly else "disabled")
            m.entryconfigure(2, state="normal" if clip and not readonly else "disabled")
            m.entryconfigure(3, state="normal" if sel and not readonly else "disabled")
            try:
                m.tk_popup(ev.x_root, ev.y_root)
            finally:
                try:
                    m.grab_release()
                except Exception:
                    pass

        def popup_key(_e=None):
            class _Ev:
                x_root = w.winfo_rootx() + px(12)
                y_root = w.winfo_rooty() + max(px(12), w.winfo_height() // 2)
            popup(_Ev())

        w.bind("<Button-3>", popup)
        if hasattr(w, "bind"):
            w.bind("<App>", popup_key)
        for seq in ("<Control-a>", "<Control-A>"):
            w.bind(seq, lambda _e: (do("all"), "break")[1])
        return m

    @staticmethod
    def _clean_entry(w):
        """粘贴整段分享文字时干掉换行与多余空白，保证链接可解析。"""
        try:
            v = w.get()
            nv = re.sub(r"[\r\n\t]+", " ", v).strip()
            if nv != v:
                w.delete(0, "end")
                w.insert(0, nv)
        except Exception:
            pass

    def _build_ui(self):
        root = self.root
        pad = px(20)

        top = ttk.Frame(root, padding=(pad, px(18), pad, 0))
        top.pack(fill="x")
        ttk.Label(top, text="抖音视频下载器", style="Head.TLabel").pack(anchor="w")
        ttk.Label(top, text="粘贴分享链接即可下载，自动选取最高画质、无水印版本",
                  style="Tip.TLabel").pack(anchor="w", pady=(px(3), 0))

        body = ttk.Frame(root, padding=(pad, px(14), pad, px(14)))
        body.pack(fill="both", expand=True)

        outer, c = self._card(body)
        outer.pack(fill="x")

        ttk.Label(c, text="视频链接", style="Field.TLabel").pack(anchor="w")
        self.var_url = tk.StringVar()
        e = ttk.Entry(c, textvariable=self.var_url)
        e.pack(fill="x", pady=(px(6), px(12)))
        e.bind("<Return>", lambda _e: self.on_download())
        self._add_edit_menu(e, clean_paste=True)

        ttk.Label(c, text="保存到", style="Field.TLabel").pack(anchor="w")
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x", pady=(px(6), px(12)))
        self.var_out = tk.StringVar(value=os.path.join(
            os.path.expanduser("~"), "Downloads", "DouyinDownloader"))
        e_out = ttk.Entry(row, textvariable=self.var_out)
        e_out.pack(side="left", fill="x", expand=True)
        self._add_edit_menu(e_out)
        ttk.Button(row, text="浏览…", width=8,
                   command=self.on_browse).pack(side="left", padx=(px(8), 0))

        ttk.Label(c, text="画质", style="Field.TLabel").pack(anchor="w")
        self.var_q = tk.StringVar(value="最高画质（自动）")
        ttk.Combobox(c, textvariable=self.var_q, values=list(QUALITY),
                     state="readonly", width=18).pack(anchor="w", pady=(px(6), px(2)))

        self.btn = ttk.Button(root, text="下  载", style="Go.TButton", command=self.on_download)
        self.btn.pack(fill="x", padx=pad, pady=(px(4), px(10)))

        self.var_status = tk.StringVar(value="就绪")
        ttk.Label(root, textvariable=self.var_status, style="Tip.TLabel").pack(anchor="w", padx=pad)
        self.pb = ttk.Progressbar(root, style="Bar.Horizontal.TProgressbar",
                                  mode="determinate", maximum=100)
        self.pb.pack(fill="x", padx=pad, pady=(px(4), 0))

        outer2, r = self._card(body, padding=px(14))
        outer2.pack(fill="x", pady=(px(12), 0))
        self.var_info = tk.StringVar(value="还没有下载任务")
        ttk.Label(r, textvariable=self.var_info, style="Sub.TLabel",
                  wraplength=px(620), justify="left").pack(anchor="w")
        btns = ttk.Frame(r, style="Card.TFrame")
        btns.pack(fill="x", pady=(px(10), 0))
        self.btn_open = ttk.Button(btns, text="打开文件", width=12,
                                   command=lambda: self.on_open(False), state="disabled")
        self.btn_open.pack(side="left")
        self.btn_dir = ttk.Button(btns, text="打开文件夹", width=12,
                                  command=lambda: self.on_open(True), state="disabled")
        self.btn_dir.pack(side="left", padx=(px(8), 0))

        logf = ttk.Frame(root, padding=(pad, px(10), pad, px(16)))
        logf.pack(fill="both", expand=True)
        ttk.Label(logf, text="运行日志", style="Tip.TLabel").pack(anchor="w")
        wrap = ttk.Frame(logf, style="Card.TFrame", padding=1)
        wrap.pack(fill="both", expand=True, pady=(px(4), 0))
        self.log = tk.Text(wrap, height=6, wrap="word", relief="flat", bd=0,
                           bg=CARD, fg=SUB, font=("Consolas", 9),
                           insertbackground=SUB, highlightthickness=0,
                           padx=px(6), pady=px(6))
        self.log.pack(fill="both", expand=True, padx=1, pady=1)
        self.log.tag_config("err", foreground="#D93026")
        self.log.tag_config("ok", foreground="#1A7F37")
        self.log.insert("end", "等待任务…\n")
        self.log.configure(state="disabled")
        self._add_edit_menu(self.log, readonly=True)

    # ------------------------------------------------------------------ 交互
    def on_browse(self):
        d = filedialog.askdirectory(initialdir=self.var_out.get() or None)
        if d:
            self.var_out.set(d)

    def on_open(self, is_dir):
        p = self.result_path
        if not p or not os.path.exists(p):
            return
        try:
            if is_dir:
                os.startfile(os.path.dirname(p))
            else:
                os.startfile(p)
        except Exception:
            subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])

    def _log(self, msg, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_download(self):
        if self.busy:
            return
        src = self.var_url.get().strip()
        out = self.var_out.get().strip()
        if not src:
            messagebox.showwarning("缺少链接", "请先粘贴抖音分享链接或作品 ID。")
            return
        if not out:
            messagebox.showwarning("缺少目录", "请选择保存位置。")
            return
        os.makedirs(out, exist_ok=True)
        self.busy = True
        self.btn.configure(state="disabled", text="下载中…")
        self.btn_open.configure(state="disabled")
        self.btn_dir.configure(state="disabled")
        self.result_path = None
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.pb.configure(mode="indeterminate")
        self.pb.start(12)
        self.var_status.set("正在解析链接…")
        threading.Thread(target=self._worker, args=(src, out, QUALITY[self.var_q.get()]),
                         daemon=True).start()

    # ------------------------------------------------------------------ 后台
    def _worker(self, src, out, max_h):
        def put(kind, **kw):
            self.q.put((kind, kw))

        def log(msg):
            put("log", msg=msg)

        try:
            aweme_id = core.resolve_aweme_id(src)
            log("作品 ID：%s" % aweme_id)
            put("status", msg="正在获取视频信息…")

            aw = core.capture_detail(aweme_id, core.default_profile(), timeout=45, log=log)
            author = core.safe_name((aw.get("author") or {}).get("nickname"), 24) or "douyin"
            desc = core.safe_name(aw.get("desc"), 40)
            v = aw.get("video") or {}
            dur = (v.get("duration") or 0) / 1000

            cands = core.list_qualities(aw)
            if not cands:
                raise RuntimeError("没找到可下载的地址（该作品可能需要登录才能观看）")

            best, fallback = core.pick_quality(cands, max_h)
            log("可用档位 %d 个，最高 %dx%d" % (len(cands), cands[0]["width"], cands[0]["height"]))
            if fallback:
                log("注意：源片最高只有 %dP，已按实际最高画质下载" % fallback)
            put("status", msg="正在下载 %dx%d …" % (best["width"], best["height"]))
            put("mode", value="determinate")

            base = "%s_%s_%s" % (author, desc, aweme_id)
            path = os.path.join(out, core.safe_name(base, 80) + ".mp4")
            last = [0.0]

            def prog(done, total):
                import time as _t
                now = _t.time()
                if total and (now - last[0] > 0.15 or done == total):
                    last[0] = now
                    put("progress", done=done, total=total)
                elif not total and now - last[0] > 0.15:
                    last[0] = now
                    put("status", msg="已下载 %.1f MB" % (done / 1048576))

            size = None
            for cand in [best] + [c for c in cands if c is not best]:
                log("尝试 %s（%dx%d）…" % (cand["gear"], cand["width"], cand["height"]))
                try:
                    size = core.download_file(cand["url"], path, prog)
                except Exception as e:
                    log("  失败：%s" % e)
                    continue
                if not core.is_valid_mp4(path):
                    log("  文件无效（%d 字节），换下一个源" % size)
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    continue
                best = cand
                break
            else:
                raise RuntimeError("所有候选地址都下载失败，可能是签名过期，请重试一次")

            info = core.probe_mp4(path)
            put("done", path=path, size=size, width=info["width"], height=info["height"],
                duration=info["duration"] or dur, author=author, desc=aw.get("desc") or "",
                has_audio=info["has_audio"], fallback=fallback)
        except Exception as e:
            put("error", msg=str(e))

    # ------------------------------------------------------------------ 刷新
    def _poll(self):
        try:
            while True:
                kind, kw = self.q.get_nowait()
                if kind == "log":
                    self._log(kw["msg"])
                elif kind == "status":
                    self.var_status.set(kw["msg"])
                elif kind == "mode":
                    self.pb.stop()
                    self.pb.configure(mode=kw["value"], value=0)
                elif kind == "progress":
                    if kw.get("total"):
                        self.pb["value"] = min(100, kw["done"] * 100.0 / kw["total"])
                        self.var_status.set("下载中 %.1f / %.1f MB" %
                                            (kw["done"] / 1048576, kw["total"] / 1048576))
                elif kind == "done":
                    self._on_done(kw)
                elif kind == "error":
                    self._on_error(kw["msg"])
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _on_done(self, d):
        self.pb.stop()
        self.pb.configure(mode="determinate", value=100)
        self.busy = False
        self.btn.configure(state="normal", text="下  载")
        self.btn_open.configure(state="normal")
        self.btn_dir.configure(state="normal")
        self.result_path = d["path"]
        note = "（源片最高仅 %dP）" % d["fallback"] if d.get("fallback") else ""
        self.var_info.set(
            "%s\n%s · %.0f:%02.0f · %dx%d · %.1f MB · %s%s" % (
                d["desc"].replace("\n", " ") or "(无文案)", d["author"],
                d["duration"] // 60, d["duration"] % 60,
                d["width"], d["height"], d["size"] / 1048576,
                "含音频" if d["has_audio"] else "⚠ 无音轨", note))
        self.var_status.set("下载完成")
        self._log("完成：%s（%.1f MB）" % (d["path"], d["size"] / 1048576), "ok")

    def _on_error(self, msg):
        self.pb.stop()
        self.pb.configure(mode="determinate", value=0)
        self.busy = False
        self.btn.configure(state="normal", text="下  载")
        self.var_status.set("出错了")
        self.var_info.set(msg)
        self._log("错误：" + msg, "err")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
