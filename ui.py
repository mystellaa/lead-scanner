# -*- coding: utf-8 -*-
"""Tkinter 桌面界面。

布局：
    ① 选择来源视频号
    ② 上传截图 / 选择文件夹
    ③ 开始识别（后台线程 + 进度条）
    ④ 识别结果表格（可导出 CSV）
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

import config
import database as db
import ocr
import phone_extract as pe
import roster

LOG = logging.getLogger("lead_scanner")  # run_app() 里会补上文件日志

# 配色（浅色主题）
BG = "#eef1f5"        # 页面底色，比卡片稍深一点才能显出层次
CARD = "#ffffff"
FG = "#1f2329"
MUTED = "#8c939d"
PRIMARY = "#07c160"
PRIMARY_HOVER = "#06ad56"
PRIMARY_DISABLED = "#a8e6c4"
DANGER = "#e34d59"
WARN = "#ff8f1f"
BORDER = "#e3e6eb"


class LeadScannerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        config.ensure_dirs()
        # 总库：真正落盘的 leads.csv
        self.db = db.LeadDatabase()
        # 临时库：纯内存，接口和总库一模一样，但一个字节都不写文件
        # 临时模式 = 「分批视图」：线索照样写总库，只是结果表只显示这一批，
        # 方便复制走。这里存的是本批新增的记录对象本身（用身份判断，刷新时重算索引）。
        self.batch_records: list[db.LeadRecord] = []
        self.temp_mode: bool = bool(config.TEMP_MODE_DEFAULT)
        self.sources: list[str] = config.load_sources()
        self.default_source: str = config.load_default_source()
        self.files: list[Path] = []
        self._editor = None            # 单元格编辑时盖在上面的 Entry
        self._ctx_cell = None          # 右键点到的单元格 (iid, '#n')
        self.msg_queue: "queue.Queue[tuple]" = queue.Queue()
        self.worker: threading.Thread | None = None
        self._row_images: dict[str, str] = {}
        self._preview_ref = None
        self._pil_ok = _check_pil()

        # 本次批次统计
        self.batch = self._fresh_batch()

        self.title(config.APP_TITLE)
        self.geometry(config.WINDOW_SIZE)          # 半屏就能看全，方便双开
        self.minsize(config.WINDOW_MIN_WIDTH, config.WINDOW_MIN_HEIGHT)
        self.configure(bg=BG)

        self.font_family = _pick_font(self)
        self._setup_style()
        self._build_ui()

        self._load_existing_rows()
        self._refresh_source_combo()
        self._refresh_stats()
        self._update_mode_state()
        self._poll_queue()

        # Ctrl+V 直接粘贴截图（绑在 toplevel 上，焦点在任何子控件都能触发）
        self.bind("<Control-v>", self._paste_image)
        self.bind("<Control-V>", self._paste_image)

        self._install_shortcuts()          # Enter = 开始识别，T = 清空面板

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ============================================================ 样式
    def _setup_style(self) -> None:
        f = self.font_family
        size = config.UI_FONT_SIZE
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except Exception:
            pass

        s.configure(".", font=(f, size), background=BG, foreground=FG)
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Card.TLabel", background=CARD, foreground=FG)
        s.configure("CardHint.TLabel", background=CARD, foreground=MUTED)
        s.configure("Hint.TLabel", background=BG, foreground=MUTED)
        s.configure("Warn.TLabel", background=BG, foreground=WARN)
        s.configure("Ok.TLabel", background=BG, foreground=PRIMARY)
        s.configure("Stat.TLabel", background=BG, foreground=FG, font=(f, size + 1, "bold"))

        # 分区做成「白卡片 + 上方灰色小标题」，比原来的粗黑边框清爽
        s.configure("Card.TLabelframe", background=CARD, bordercolor=BORDER,
                    borderwidth=1, relief="solid", padding=(10, 6))
        s.configure("Card.TLabelframe.Label", background=BG, foreground=MUTED,
                    font=(f, size - 1, "bold"))
        s.configure("TLabelframe", background=BG, bordercolor=BORDER,
                    borderwidth=1, relief="solid", padding=8)
        s.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                    font=(f, size - 1, "bold"))

        s.configure("TButton", padding=(9, 4), background="#ffffff", foreground=FG)
        try:      # bordercolor / focuscolor 是 clam 主题专有选项，换主题就可能不认
            s.configure("TButton", bordercolor=BORDER, focuscolor=BG)
        except tk.TclError:
            pass
        s.map("TButton",
              background=[("active", "#eef0f2"), ("disabled", "#f5f6f8")],
              foreground=[("disabled", MUTED)])

        s.configure("Primary.TButton", padding=(16, 6), background=PRIMARY,
                    foreground="#ffffff", font=(f, size + 1, "bold"))
        try:
            s.configure("Primary.TButton", borderwidth=0)
        except tk.TclError:
            pass
        s.map("Primary.TButton",
              background=[("active", PRIMARY_HOVER), ("disabled", PRIMARY_DISABLED)],
              foreground=[("disabled", "#ffffff")])

        s.configure("TCombobox", padding=3)
        s.configure("TEntry", padding=3)
        s.configure("TCheckbutton", background=CARD)
        s.map("TCheckbutton", background=[("active", CARD)])
        s.configure("TProgressbar", background=PRIMARY, troughcolor="#e8eaed")
        try:
            s.configure("TProgressbar", bordercolor=BORDER,
                        lightcolor=PRIMARY, darkcolor=PRIMARY)
        except tk.TclError:
            pass

        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG,
                    rowheight=24, borderwidth=0)
        try:
            s.configure("Treeview", bordercolor=BORDER)
        except tk.TclError:
            pass
        s.configure("Treeview.Heading", background="#f8f9fb", foreground=MUTED,
                    font=(f, size - 1, "bold"), relief="flat", padding=(4, 5))
        s.map("Treeview.Heading", background=[("active", "#eef0f2")])
        s.map("Treeview", background=[("selected", "#e3f5ea")],
              foreground=[("selected", FG)])

    # ============================================================ UI 组装
    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)          # 结果区撑开

        self._build_source_bar(row=0)
        self._build_image_panel(row=1)
        self._build_action_bar(row=2)
        self._build_result_panel(row=3)
        self._build_status_bar(row=4)

    # -------------------------------------------------- ① 来源
    def _build_source_bar(self, row: int) -> None:
        box = ttk.Labelframe(self, text=" ① 登记方式 ",
                             style="Card.TLabelframe")
        box.grid(row=row, column=0, sticky="ew", padx=10, pady=(10, 5))
        box.columnconfigure(3, weight=1)
        self.entry_mode = tk.StringVar(value="delegate")
        modes = ttk.Frame(box)
        modes.grid(row=1, column=0, columnspan=7, sticky="w", pady=(6, 2))
        ttk.Radiobutton(modes, text="代他人登记", variable=self.entry_mode,
                        value="delegate", command=self._change_entry_mode).pack(side="left")
        ttk.Radiobutton(modes, text="同平台批量导入", variable=self.entry_mode,
                        value="platform", command=self._change_entry_mode).pack(side="left", padx=16)

        ttk.Label(box, text="视频号：", style="Card.TLabel").grid(
            row=0, column=0, padx=(2, 5), pady=1)
        self.source_var = tk.StringVar()
        # state="normal" —— 允许直接输入，配合 <KeyRelease> 做实时筛选
        self.source_combo = ttk.Combobox(box, textvariable=self.source_var,
                                         state="disabled", width=26,
                                         font=(self.font_family, config.UI_FONT_SIZE))
        self.source_combo.grid(row=0, column=1, padx=(0, 6), pady=1, sticky="w")
        self.source_combo.bind("<<ComboboxSelected>>", lambda e: self._on_source_change())
        self.source_combo.bind("<KeyRelease>", self._on_source_key)
        self.source_combo.bind("<Return>", lambda e: self._on_source_change())
        self.source_combo.bind("<FocusOut>", lambda e: self._on_source_change())

        ttk.Button(box, text="＋ 新增", command=self._add_source).grid(
            row=0, column=2, padx=2, pady=1)

        self.source_hint = ttk.Label(box, text="", style="CardHint.TLabel")
        self.source_hint.grid(row=0, column=3, sticky="w", padx=6)

        ttk.Button(box, text="设为默认", command=self._set_default_source).grid(
            row=0, column=4, padx=2, pady=1)
        ttk.Button(box, text="重命名", command=self._rename_source).grid(
            row=0, column=5, padx=2, pady=1)
        ttk.Button(box, text="删除", command=self._delete_source).grid(
            row=0, column=6, padx=(2, 1), pady=1)

    # -------------------------------------------------- ② 图片
    def _build_image_panel(self, row: int) -> None:
        box = ttk.Labelframe(self, text=" ② 粘贴截图（Ctrl+V）或选择图片 ",
                             style="Card.TLabelframe")
        box.grid(row=row, column=0, sticky="ew", padx=10, pady=5)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)

        bar = ttk.Frame(box, style="Card.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Button(bar, text="粘贴 (Ctrl+V)",
                   command=self._paste_image).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="选择图片",
                   command=self._add_files).pack(side="left", padx=4)
        ttk.Button(bar, text="选择文件夹", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(bar, text="移除选中", command=self._remove_selected).pack(side="left", padx=4)
        ttk.Button(bar, text="清空", command=self._clear_files).pack(side="left", padx=4)

        self.file_hint = ttk.Label(bar, text="", style="CardHint.TLabel")
        self.file_hint.pack(side="right")

        body = ttk.Frame(box, style="Card.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        self.file_list = tk.Listbox(
            body, selectmode="extended", height=5, activestyle="none",
            font=(self.font_family, config.UI_FONT_SIZE),
            bg=CARD, fg=FG, selectbackground="#e3f5ea", selectforeground=FG,
            highlightthickness=1, highlightbackground=BORDER, relief="flat")
        self.file_list.grid(row=0, column=0, sticky="nsew")
        self.file_list.bind("<<ListboxSelect>>", self._on_file_select)

        sb = ttk.Scrollbar(body, orient="vertical", command=self.file_list.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=sb.set)

        self.preview = tk.Canvas(body, width=190, height=108, bg="#fafbfc",
                                 highlightthickness=1, highlightbackground=BORDER)
        self.preview.grid(row=0, column=2, padx=(8, 0), sticky="n")
        self.preview.create_text(95, 54, text="选中图片可预览", fill=MUTED,
                                 font=(self.font_family, 9))

    # -------------------------------------------------- ③ 操作
    def _build_action_bar(self, row: int) -> None:
        box = ttk.Frame(self)
        box.grid(row=row, column=0, sticky="ew", padx=10, pady=5)
        box.columnconfigure(3, weight=1)

        self.btn_start = ttk.Button(box, text="▶  开始识别 (Enter)", style="Primary.TButton",
                                    command=self._start_scan)
        self.btn_start.grid(row=0, column=0, sticky="w")

        # 模式开关：勾上 = 列表只看本批（线索照样写入总库）
        self.temp_var = tk.BooleanVar(value=self.temp_mode)
        self.temp_check = tk.Checkbutton(
            box, text="临时模式（只看本批）", variable=self.temp_var,
            command=self._toggle_temp_mode,
            bg=BG, fg=FG, activebackground=BG, activeforeground=FG,
            selectcolor="#ffffff", font=(self.font_family, config.UI_FONT_SIZE))
        self.temp_check.grid(row=0, column=1, padx=(12, 0), sticky="w")

        self.progress = ttk.Progressbar(box, mode="determinate", length=150)
        self.progress.grid(row=0, column=2, padx=10, sticky="w")

        self.stat_label = ttk.Label(box, text="", style="Stat.TLabel")
        self.stat_label.grid(row=0, column=3, sticky="w", padx=8)

    # -------------------------------------------------- ④ 结果
    def _build_result_panel(self, row: int) -> None:
        box = ttk.Labelframe(self, text=" ④ 识别结果 ",
                             style="Card.TLabelframe")
        box.grid(row=row, column=0, sticky="nsew", padx=10, pady=5)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)
        self.result_box = box

        bar = ttk.Frame(box, style="Card.TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        ttk.Button(bar, text="改为当前来源",
                   command=self._reassign_source).pack(side="left")
        ttk.Button(bar, text="删除选中行",
                   command=self._delete_rows).pack(side="left", padx=4)
        ttk.Button(bar, text="补全线索人",
                   command=self._fill_person_by_source).pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=4, pady=1)
        self.btn_clear_batch = ttk.Button(bar, text="清空面板 (T)",
                                          command=self._reset_panel)
        self.btn_clear_batch.pack(side="left", padx=4)
        ttk.Button(bar, text="复制本批",
                   command=self._copy_batch).pack(side="left", padx=4)
        ttk.Button(bar, text="复制设置",
                   command=self._copy_settings).pack(side="left")
        self.result_hint = ttk.Label(bar, text="", style="CardHint.TLabel")
        self.result_hint.pack(side="right")

        # 列宽按「980 宽窗口一屏放得下」配的，不用拉横向滚动条
        widths = {
            "phone": 88, "source_platform": 112, "source_person": 68,
            "business": 86, "content": 140, "customer_id": 64,
            "note": 82, "discovered_at": 100, "image_file": 90,
        }
        left_align = ("content", "note", "image_file")
        cols = tuple(config.CSV_FIELDS)

        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=8)
        for c in cols:
            self.tree.heading(c, text=config.CSV_HEADERS[c])
            self.tree.column(c, width=widths.get(c, 90), minwidth=55,
                             anchor="w" if c in left_align else "center",
                             stretch=(c == "content"))
        self.tree.grid(row=1, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        vsb.grid(row=1, column=1, sticky="ns")
        hsb = ttk.Scrollbar(box, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=2, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.tag_configure("new", background="#f2fbf6")
        self.tree.tag_configure("bad", foreground=WARN)
        self.tree.bind("<Double-1>", self._on_cell_double_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)

        self.tree_menu = tk.Menu(self, tearoff=0)
        self.tree_menu.add_command(label="修改此单元格", command=self._edit_context_cell)
        self.tree_menu.add_command(label="改为当前来源", command=self._reassign_source)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="打开原图", command=self._open_row_image)
        self.tree_menu.add_command(label="删除选中行", command=self._delete_rows)

    # -------------------------------------------------- 底部
    def _build_status_bar(self, row: int) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=row, column=0, sticky="ew", padx=10, pady=(0, 10))
        bar.columnconfigure(1, weight=1)

        ttk.Button(bar, text="导出 CSV…", command=self._export_csv).grid(
            row=0, column=0, sticky="w")
        ttk.Button(bar, text="名单管理", command=self._open_roster).grid(
            row=0, column=2, padx=5)
        ttk.Button(bar, text="更新日志", command=self._open_changelog).grid(
            row=0, column=3, padx=5)
        ttk.Button(bar, text="打开数据目录", command=self._open_data_dir).grid(
            row=0, column=4, padx=5)

        self.status = ttk.Label(bar, text="就绪", style="Hint.TLabel")
        self.status.grid(row=0, column=1, sticky="w", padx=10)

    # ============================================================ 数据加载
    def _current_rows(self) -> list[tuple[int, list[str]]]:
        """表格要显示的行：``(库记录索引, 行数据)``。

        临时模式下**只给本批新增的那些** —— 这就是「方便复制」的核心，
        列表里不会混进历史数据。判断身份用对象 id，所以中途改字段、
        删别的行都不会让它认错记录。
        """
        rows = self.db.rows_for_ui()
        if not self.temp_mode:
            return rows
        wanted = {id(r) for r in self.batch_records}
        out: list[tuple[int, list[str]]] = []
        for index, values in rows:
            rec = self.db.get(index)
            if rec is not None and id(rec) in wanted:
                out.append((index, values))
        return out

    def _load_existing_rows(self) -> None:
        for index, values in self._current_rows():
            self._insert_row(str(index), values, tag="")
        if self.temp_mode:
            self._set_status(f"临时模式：表里只显示本批。总库现有 {len(self.db)} 条（照样会写入）",
                             "hint")
        else:
            self._set_status(f"已载入历史线索 {len(self.db)} 条", "hint")
        if getattr(self.db, "migration_failed", False):
            self.after(900, self._warn_migration_failed)

    def _warn_migration_failed(self) -> None:
        """列名没整理成标准格式时提醒一句 —— 光写日志用户是看不见的。"""
        messagebox.showwarning(
            "线索库的表头没整理成功",
            "leads.csv 现在被其它程序打开着（多半是 Excel / WPS），"
            "所以程序没法把它整理成标准列格式。\n\n"
            "不影响使用：数据照样读得出来、识别也照跑。\n"
            "关掉那个程序，下次启动程序时会自动整理好。\n\n"
            f"{config.LEADS_CSV}",
            parent=self)

    def _insert_row(self, iid: str, values: list[str], tag: str = "new") -> None:
        """iid 用记录在库里的索引 —— 双击改值 / 删行 / 改来源都靠它定位。"""
        self.tree.insert("", 0, iid=iid, values=values, tags=(tag,) if tag else ())
        idx = config.CSV_FIELDS.index("image_file")
        if len(values) > idx:
            self._row_images[iid] = values[idx]

    def _refresh_table(self) -> None:
        """整表重刷。删行 / 改来源 / 切模式之后记录索引会重排，必须全刷才准。"""
        self.tree.delete(*self.tree.get_children())
        self._row_images.clear()
        if self.batch_records:                  # 已经被删掉的记录别再留在本批里
            alive = {id(r) for r in self.db.records}
            self.batch_records = [r for r in self.batch_records if id(r) in alive]
        if self._editor is not None:            # 切模式时可能正开着编辑器
            try:
                self._editor.destroy()
            except Exception:
                pass
            self._editor = None
        for index, values in self._current_rows():
            self._insert_row(str(index), values, tag="")
        self._refresh_stats()
        self._update_mode_state()

    # ============================================================ 来源管理
    def _refresh_source_combo(self) -> None:
        """刷新候选列表。

        启动时不再预选第一个 —— 没设过默认就留空，逼你主动确认一次来源，
        避免"默认某个服务商"导致线索张冠李戴。
        """
        self.source_combo["values"] = self.sources
        cur = self.source_var.get().strip()
        if not cur and self.default_source and self.default_source in self.sources:
            self.source_var.set(self.default_source)
        self._update_source_hint()

    def _current_source(self) -> str:
        if self.entry_mode.get() == "delegate":
            return ""
        return (self.source_var.get() or "").strip()

    def _change_entry_mode(self) -> None:
        self.source_var.set("")
        self.source_combo.configure(values=self.sources,
                                    state="disabled" if self.entry_mode.get() == "delegate" else "normal")
        self._update_source_hint()

    def _on_source_change(self) -> None:
        self._update_source_hint()
        src = self._current_source()
        if src:
            self._set_status(f"当前来源：{src}", "hint")

    def _on_source_key(self, event) -> None:
        """边打字边筛选候选 —— 来源多了以后翻列表太累。"""
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab",
                            "Left", "Right", "Home", "End",
                            "Shift_L", "Shift_R", "Control_L", "Control_R"):
            return
        typed = self.source_var.get().strip()
        if not typed:
            self.source_combo["values"] = self.sources
            self._set_status(f"共 {len(self.sources)} 个来源，输入关键字可筛选", "hint")
            return
        low = typed.lower()
        matched = [s for s in self.sources if low in s.lower()]
        self.source_combo["values"] = matched or self.sources
        if matched:
            tail = "…" if len(matched) > 5 else ""
            self._set_status(f"筛出 {len(matched)} 个：{'、'.join(matched[:5])}{tail}", "hint")
        else:
            self._set_status("没有匹配的来源 —— 按「＋ 新增来源」可以把它加进去", "warn")

    def _update_source_hint(self) -> None:
        if self.entry_mode.get() == "delegate":
            self.source_hint.configure(text="平台待补充")
            return
        src = self._current_source()
        parts: list[str] = []
        if src:
            parts.append(f"当前：{src}")
            op, why = roster.operator_for_source(src)
            parts.append(f"运营：{op}" if op else "运营：未登记")
            if op and why and not why.startswith("账号登记"):
                parts.append(f"（{why}）")
        else:
            parts.append("⚠ 还没选来源，识别会被拦下")
        if self.default_source:
            parts.append(f"默认：{self.default_source}")
        self.source_hint.configure(text="　·　".join(parts))

    def _set_default_source(self) -> None:
        """把当前来源记住为默认（下次启动自动选中）。"""
        src = self._current_source()
        if not src:
            messagebox.showwarning("提示", "先在输入框里填一个来源，再设为默认。", parent=self)
            return
        if src not in self.sources:
            self.sources = config.save_sources(self.sources + [src])
        config.set_default_source(src)
        self.default_source = src
        self._refresh_source_combo()
        self._set_status(f"已把「{src}」设为默认来源，下次启动会自动选中", "ok")

    def _add_source(self) -> None:
        name = simpledialog.askstring("新增来源", "请输入视频号名称（例如：视频号-桂林服务商）",
                                      parent=self)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in self.sources:
            messagebox.showinfo("提示", "该来源已存在。", parent=self)
            self.source_var.set(name)
            return
        self.sources.append(name)
        self.sources = config.save_sources(self.sources)
        self._refresh_source_combo()
        self.source_var.set(name)
        self._set_status(f"已新增来源：{name}", "ok")

    def _rename_source(self) -> None:
        old = self._current_source()
        if not old:
            messagebox.showwarning("提示", "请先选择要重命名的来源。", parent=self)
            return
        new = simpledialog.askstring("重命名来源", "新的名称：", initialvalue=old, parent=self)
        if not new or not new.strip():
            return
        new = new.strip()
        if new == old:
            return
        idx = self.sources.index(old)
        self.sources[idx] = new
        self.sources = config.save_sources(self.sources)
        self._refresh_source_combo()
        self.source_var.set(new)
        used = sum(1 for r in self.db.records if r.source_platform == old)
        if used:
            messagebox.showinfo(
                "提示",
                f"来源已重命名。\n注意：历史数据中仍有 {used} 条线索记录的是旧名称「{old}」，"
                "如需统一请手工编辑 leads.csv。",
                parent=self)
        self._set_status(f"来源已重命名为：{new}", "ok")

    def _delete_source(self) -> None:
        name = self._current_source()
        if not name:
            return
        if len(self.sources) <= 1:
            messagebox.showwarning("提示", "至少需要保留一个来源。", parent=self)
            return
        used = sum(1 for r in self.db.records if r.source_platform == name)
        tip = f"\n\n历史数据中有 {used} 条线索使用该来源（数据不会被删除）。" if used else ""
        if not messagebox.askyesno("删除来源", f"确定删除「{name}」？{tip}", parent=self):
            return
        self.sources.remove(name)
        self.sources = config.save_sources(self.sources)
        self._refresh_source_combo()
        self._set_status(f"已删除来源：{name}", "hint")

    # ============================================================ 文件管理
    def _paste_image(self, event=None):
        """从剪贴板读截图（Ctrl+V）。

        两种情况都支持：
          * 截图工具截的图（剪贴板里是位图）-> 存到 data/paste/paste_时间戳.png
          * 在资源管理器里复制了图片文件（剪贴板里是文件路径列表）
        """
        result = "break" if event is not None else None
        if not self._pil_ok:
            messagebox.showwarning("提示",
                                   "粘贴功能需要 Pillow，请先安装：\n.venv\\Scripts\\python.exe -m pip install Pillow",
                                   parent=self)
            return result

        from PIL import ImageGrab
        try:
            data = ImageGrab.grabclipboard()
        except Exception as exc:
            LOG.warning("读取剪贴板失败", exc_info=True)
            self._set_status(f"读取剪贴板失败：{exc}", "warn")
            return result

        paths: list[Path] = []
        if data is None:
            self._set_status("剪贴板里没有图片 —— 先用截图工具（Win+Shift+S）截图，再按 Ctrl+V", "warn")
        elif isinstance(data, list):
            paths = [Path(p) for p in data if Path(p).suffix.lower() in config.SUPPORTED_EXTS]
            if not paths:
                self._set_status("剪贴板里是文件，但没有图片文件", "warn")
        else:
            config.ensure_dirs()
            out = self._unique_paste_path()
            try:
                data.save(out)
            except Exception as exc:
                LOG.error("保存粘贴图片失败", exc_info=True)
                self._set_status(f"保存图片失败：{exc}", "warn")
                return result
            paths = [out]

        if paths:
            self._extend_files(paths)
            self.file_list.see("end")
            self._set_status(f"已从剪贴板粘贴 {len(paths)} 张截图，可直接点「开始识别」", "ok")
            self._show_preview(paths[-1])
        return result

    @staticmethod
    def _unique_paste_path() -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = config.PASTE_DIR / f"paste_{stamp}.png"
        n = 2
        while out.exists():
            out = config.PASTE_DIR / f"paste_{stamp}_{n}.png"
            n += 1
        return out

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(title="选择截图", filetypes=config.IMAGE_FILETYPES)
        self._extend_files([Path(p) for p in paths])

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择存放截图的文件夹")
        if not folder:
            return
        found = sorted(
            p for p in Path(folder).iterdir()
            if p.is_file() and p.suffix.lower() in config.SUPPORTED_EXTS
        )
        if not found:
            messagebox.showinfo("提示", "该文件夹下没有找到 jpg / png / jpeg 图片。", parent=self)
            return
        self._extend_files(found)

    def _extend_files(self, paths: list[Path]) -> None:
        added = 0
        for p in paths:
            if p.suffix.lower() not in config.SUPPORTED_EXTS:
                continue
            if p in self.files:
                continue
            self.files.append(p)
            self.file_list.insert("end", f"  {p.name}    —  {p.parent}")
            added += 1
        skipped = len(paths) - added
        msg = f"已添加 {added} 张"
        if skipped:
            msg += f"，跳过 {skipped} 张（重复或格式不支持）"
        self.file_hint.configure(text=f"待识别图片：{len(self.files)} 张")
        self._set_status(msg, "ok" if added else "hint")

    def _remove_selected(self) -> None:
        sel = list(self.file_list.curselection())
        if not sel:
            return
        for i in reversed(sel):
            self.file_list.delete(i)
            del self.files[i]
        self.file_hint.configure(text=f"待识别图片：{len(self.files)} 张")
        self._clear_preview()

    def _clear_files(self) -> None:
        self.files.clear()
        self.file_list.delete(0, "end")
        self.file_hint.configure(text="")
        self._clear_preview()

    def _on_file_select(self, _event=None) -> None:
        sel = self.file_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.files):
            self._show_preview(self.files[idx])

    # -------------------------------------------------- 预览
    def _clear_preview(self) -> None:
        self.preview.delete("all")
        self.preview.create_text(95, 54, text="选中图片可预览", fill=MUTED,
                                 font=(self.font_family, 9))

    def _show_preview(self, path: Path) -> None:
        self.preview.delete("all")
        if not self._pil_ok:
            self.preview.create_text(95, 54, text="安装 Pillow 后可预览",
                                     fill=MUTED, font=(self.font_family, 9))
            return
        try:
            from PIL import Image, ImageTk
            img = Image.open(path)
            img.thumbnail((186, 104))
            self._preview_ref = ImageTk.PhotoImage(img)
            self.preview.create_image(95, 54, image=self._preview_ref)
        except Exception as exc:
            self.preview.create_text(95, 54, text=f"无法预览\n{exc}", fill=DANGER,
                                     font=(self.font_family, 9))

    # ============================================================ 识别流程
    def _start_scan(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("提示", "正在识别中，请稍候…", parent=self)
            return
        source = self._current_source()
        if not source and self.entry_mode.get() == "platform":
            messagebox.showwarning("提示",
                                   "请先选择来源视频号。\n（顶部输入框可以直接打字筛选）",
                                   parent=self)
            self.source_combo.focus_set()
            return
        if source and source not in self.sources:          # 手输的新来源，自动登记下来
            self.sources = config.save_sources(self.sources + [source])
            self._refresh_source_combo()
            self.source_var.set(source)
            self._set_status(f"已把「{source}」登记为新来源", "ok")
        if not self.files:
            messagebox.showwarning("提示", "请先上传截图或选择图片文件夹。", parent=self)
            return
        if not ocr.pick_engine_key():
            messagebox.showerror(
                "缺少 OCR 引擎",
                "未检测到可用的 OCR 引擎，请任选一条命令安装后重启程序：\n\n"
                "① pip install paddlepaddle paddleocr   （需求指定）\n"
                "② pip install rapidocr-onnxruntime     （轻量推荐）",
                parent=self)
            return

        self.batch = self._fresh_batch()
        self.batch["source"] = source          # 快照来源，识别中途改选也不影响本批
        self.progress.configure(value=0, maximum=len(self.files))
        self.btn_start.configure(state="disabled", text="识别中…")
        files = list(self.files)

        self.worker = threading.Thread(target=self._scan_worker, args=(files, source), daemon=True)
        self.worker.start()

    def _scan_worker(self, files: list[Path], source: str) -> None:
        """后台线程：只做 OCR + 文本提取，入库交给主线程。"""
        try:
            self.msg_queue.put(("status", "正在加载 OCR 引擎（首次运行需下载模型，请耐心等待）…"))
            ocr.get_engine()
            name, _ = ocr.engine_status()
            self.msg_queue.put(("status", f"OCR 引擎就绪：{name}"))

            for i, f in enumerate(files, 1):
                self.msg_queue.put(("status", f"[{i}/{len(files)}] 识别中：{f.name}"))
                try:
                    res = ocr.recognize(f)
                    if not res.ok:
                        self.msg_queue.put(("item", i, len(files), f, [], res.error))
                        continue
                    # 带上来源平台：截图里认不出是谁发的时，用账号所属运营兜底填线索人
                    leads = pe.extract_leads(res.full_text, source_platform=source)
                    err = "" if leads else "未识别到手机号"
                    self.msg_queue.put(("item", i, len(files), f, leads, err))
                except Exception as exc:
                    self.msg_queue.put(("item", i, len(files), f, [],
                                        f"{type(exc).__name__}: {exc}"))
                    LOG.error("处理失败 %s", f, exc_info=True)
            self.msg_queue.put(("done", source))
        except Exception as exc:
            LOG.error("识别线程异常", exc_info=True)
            self.msg_queue.put(("fatal", f"{type(exc).__name__}: {exc}\n\n"
                                         f"{traceback.format_exc(limit=3)}"))

    # -------------------------------------------------- 队列轮询
    def _poll_queue(self) -> None:
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        except Exception:
            LOG.error("界面消息处理异常", exc_info=True)
        self.after(80, self._poll_queue)

    def _handle_msg(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "status":
            self._set_status(msg[1], "hint")

        elif kind == "item":
            _, idx, total, path, leads, err = msg
            self.progress.configure(value=idx)
            self.batch["images"] += 1
            if leads:
                source = self.batch.get("source", "")
                records = [
                    db.LeadRecord(
                        customer_id=lead.customer_id,
                        phone=lead.phone,
                        content=lead.content,
                        source_person=lead.source_person,
                        business=lead.business,
                        note=lead.note,
                        source_platform=source,
                        discovered_at=config.now_str(),
                        image_file=path.name,
                    ) for lead in leads
                ]
                self.batch["phones"] += len(leads)
                for lead in leads:
                    if lead.customer_id:
                        self.batch["customers"].add(lead.customer_id)
                # 线索**一律写入总库** —— 临时模式只是让列表只显示本批，
                # 不影响落库。
                try:
                    result = self.db.add(records)
                except PermissionError:
                    # 总库被 Excel 之类占着 —— 说清楚，别让界面看起来像卡住了
                    self.batch["failed"] += 1
                    self._set_status(
                        "写入失败：leads.csv 正被其它程序打开（多半是 Excel）。"
                        "关闭它后重新识别即可。", "warn")
                    self._refresh_stats()
                    return
                self.batch["new"] += result.added_count
                self.batch["dup"] += result.dup_count
                self.batch_records.extend(result.added)  # 本批 = 这次真正新增的
                base = len(self.db) - len(result.added)  # 新记录的索引是末尾连续一段
                for i, rec in enumerate(result.added):
                    self._insert_row(str(base + i), rec.to_row(), tag="new")
            else:
                if "未识别到手机号" in (err or ""):
                    self.batch["nophone"] += 1
                else:
                    self.batch["failed"] += 1
                    self._set_status(f"{path.name} 识别失败：{err}", "warn")
            self._refresh_stats()

        elif kind == "done":
            self._finish_scan()

        elif kind == "fatal":
            self._finish_scan()
            messagebox.showerror("识别失败", msg[1], parent=self)

    def _finish_scan(self) -> None:
        self.btn_start.configure(state="normal", text="▶  开始识别")
        b = self.batch
        parts = [f"完成：图片 {b['images']} 张",
                 f"新增线索 {b['new']} 条"]
        if b["dup"]:
            parts.append(f"重复跳过 {b['dup']} 条")
        if b["nophone"]:
            parts.append(f"{b['nophone']} 张图未识别到号码")
        if b["failed"]:
            parts.append(f"{b['failed']} 张失败")
        if self.temp_mode and self.batch_records:
            parts.append(f"本批 {len(self.batch_records)} 条已在表里，可直接「复制本批」")
        self._set_status("，".join(parts), "ok" if b["new"] else "hint")
        self._refresh_stats()
        self._narrow_sources_by_batch()

    def _narrow_sources_by_batch(self) -> None:
        """A completed batch must not restrict the next batch's platform choices."""
        self.source_combo["values"] = self.sources

    # -------------------------------------------------- 统计
    def _fresh_batch(self) -> dict:
        return {"images": 0, "phones": 0, "new": 0, "dup": 0,
                "failed": 0, "nophone": 0, "customers": set(), "source": ""}

    def _refresh_stats(self) -> None:
        b = self.batch
        tail = (f"本批 {len(self.batch_records)} 条（已写入总库）" if self.temp_mode
                else f"库内共 {len(self.db)} 条")
        self.stat_label.configure(
            text=f"识别到：手机号 {b['phones']} 个 · 客户 {len(b['customers'])} 位"
                 f"　|　本次新增 {b['new']} 条，{tail}")

    # ============================================================ 其它操作
    def _export_csv(self) -> None:
        # 导出「当前表里显示的那些」：总库模式 = 全部；临时模式 = 本批
        records = [rec for _, rec in self._visible_records()]
        if not records:
            messagebox.showinfo("提示", "当前表里没有可导出的线索。", parent=self)
            return

        dlg = ExportDialog(self, len(records))
        self.wait_window(dlg)          # 用户关掉对话框才继续
        if not dlg.result:
            return
        fields, with_header = dlg.result

        # 默认名带时间戳：以前默认就叫 leads.csv、默认目录就是主库所在目录，
        # 一路回车就直接把总库覆盖了 —— 这是原始数据，覆盖后找不回来。
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        dest = filedialog.asksaveasfilename(
            title="导出 CSV", defaultextension=".csv",
            initialfile=f"线索导出_{stamp}.csv",
            initialdir=str(config.DATA_DIR), filetypes=[("CSV 文件", "*.csv")])
        if not dest:
            return

        try:
            same = Path(dest).resolve() == Path(config.LEADS_CSV).resolve()
        except Exception:
            same = False
        if same:
            messagebox.showerror(
                "不能覆盖主库",
                f"这个路径就是线索主库：\n{config.LEADS_CSV}\n\n"
                f"覆盖它等于把原始数据冲掉（而且总库一旦被改成别的列数，"
                f"下次启动还得迁移）。\n\n"
                f"请换个文件名再保存，比如「线索导出_{stamp}.csv」。",
                parent=self)
            return
        try:
            path = self.db.export(dest, fields=fields, with_header=with_header,
                                  records=records)
        except PermissionError:
            messagebox.showerror("导出失败",
                                 "文件被占用（可能正被 Excel 打开），请关闭后重试。", parent=self)
            return
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return

        col_names = "、".join(config.CSV_HEADERS[f] for f in fields)
        scope = "本批" if self.temp_mode else "全部"
        self._set_status(f"已导出{scope} {len(records)} 条（{len(fields)} 列）：{path}", "ok")
        if messagebox.askyesno(
                "导出成功",
                f"已导出{scope} {len(records)} 条线索到：\n{path}\n\n"
                f"包含列：{col_names}\n"
                f"表头：{'有' if with_header else '无'}\n\n"
                f"是否打开所在文件夹？",
                parent=self):
            _open_path(path.parent)

    def _open_data_dir(self) -> None:
        _open_path(config.DATA_DIR)

    def _open_roster(self) -> None:
        RosterWindow(self)

    def _open_changelog(self) -> None:
        ChangelogWindow(self)

    # ============================================================ 表格编辑
    def _on_cell_double_click(self, event) -> str:
        """双击单元格 -> 原地编辑。"""
        if self.tree.identify("region", event.x, event.y) != "cell":
            return ""
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not iid or not col:
            return ""
        self._start_cell_edit(iid, col)
        return "break"

    def _on_tree_right_click(self, event) -> str:
        iid = self.tree.identify_row(event.y)
        if not iid:
            return ""
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        self._ctx_cell = (iid, self.tree.identify_column(event.x))
        try:
            self.tree_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.tree_menu.grab_release()
        return "break"

    def _edit_context_cell(self) -> None:
        if not self._ctx_cell:
            return
        iid, col = self._ctx_cell
        if col:
            self._start_cell_edit(iid, col)

    def _start_cell_edit(self, iid: str, col: str) -> None:
        """按字段类型挑编辑器：能选的给下拉，多人的给勾选，其余才手输。"""
        if self._editor is not None:
            return
        try:
            field_index = int(str(col).lstrip("#")) - 1
        except ValueError:
            return
        if not (0 <= field_index < len(config.CSV_FIELDS)):
            return
        field = config.CSV_FIELDS[field_index]

        if field == "business":                    # 任务负责人是多人 -> 勾选窗口
            self._edit_business(iid)
            return

        choices = self._choices_for(field, iid)
        if choices:
            self._start_combo_edit(iid, field, choices)
        else:
            self._start_text_edit(iid, field)

    def _choices_for(self, field: str, iid: str | None = None) -> list[str]:
        """哪些字段适合给下拉候选（其余字段还是自由输入）。"""
        if field == "source_platform":
            return self._platform_choices(iid)
        if field == "source_person":
            return roster.operator_names()
        if field == "image_file":
            return [p.name for p in self.files]
        return []

    def _platform_choices(self, iid: str | None) -> list[str]:
        """来源平台的候选：优先只给「这行线索人自己负责的账号」。

        发布人一定是运营，而运营名下就那几个账号（彭曦 → 视频号-彭曦 / 本地推）。
        所以双击来源平台时直接把这几个摆出来让他挑，比在十几个来源里翻快得多。
        线索人不是运营（或为空）时，退回全部来源。
        """
        if iid:
            person = (self.tree.set(iid, "source_person") or "").strip()
            if person:
                accs = roster.accounts_of(person)
                if accs:
                    return accs
        return list(self.sources)

    def _start_combo_edit(self, iid: str, field: str, choices: list[str]) -> None:
        """下拉式编辑：既能选，也能手输。"""
        bbox = self.tree.bbox(iid, field)
        if not bbox:
            return
        x, y, w, h = bbox
        old = self.tree.set(iid, field)

        var = tk.StringVar(value=old)
        combo = ttk.Combobox(self.tree, textvariable=var, values=choices,
                             state="normal",
                             font=(self.font_family, config.UI_FONT_SIZE))
        combo.place(x=x, y=y, width=max(w, 150), height=h)
        combo.focus_set()
        combo.select_range(0, "end")
        self._editor = combo
        self.result_hint.configure(
            text=f"修改「{config.CSV_HEADERS[field]}」—— 点箭头选，也可手输；回车保存")
        try:                                       # 自动把下拉展开
            combo.tk.call("ttk::combobox::Post", combo)
        except Exception:
            pass

        def finish(commit: bool) -> None:
            if self._editor is None:
                return
            self._editor = None
            new = var.get().strip()
            combo.destroy()
            self.result_hint.configure(text="")
            if commit and new != old:
                self._commit_cell_edit(iid, field, new)

        combo.bind("<<ComboboxSelected>>", lambda e: finish(True))
        combo.bind("<Return>", lambda e: finish(True))
        combo.bind("<FocusOut>", lambda e: finish(True))
        combo.bind("<Escape>", lambda e: finish(False))

    def _start_text_edit(self, iid: str, field: str) -> None:
        """文本式编辑：回车 / 点别处 = 保存，Esc = 取消。"""
        bbox = self.tree.bbox(iid, field)
        if not bbox:
            return
        x, y, w, h = bbox
        old = self.tree.set(iid, field)

        var = tk.StringVar(value=old)
        entry = ttk.Entry(self.tree, textvariable=var,
                          font=(self.font_family, config.UI_FONT_SIZE))
        entry.place(x=x, y=y, width=max(w, 100), height=h)
        entry.focus_set()
        entry.select_range(0, "end")
        self._editor = entry
        self.result_hint.configure(
            text=f"修改「{config.CSV_HEADERS[field]}」—— 回车保存，Esc 取消")

        def finish(commit: bool) -> None:
            if self._editor is None:
                return
            self._editor = None
            new = var.get().strip()
            entry.destroy()
            self.result_hint.configure(text="")
            if commit and new != old:
                self._commit_cell_edit(iid, field, new)

        entry.bind("<Return>", lambda e: finish(True))
        entry.bind("<FocusOut>", lambda e: finish(True))
        entry.bind("<Escape>", lambda e: finish(False))

    def _edit_business(self, iid: str) -> None:
        """任务负责人可能好几个人 -> 弹勾选窗口，选完自动排序（带教垫后）。"""
        current = [x.strip() for x in self.tree.set(iid, "business").split(",") if x.strip()]
        dlg = BusinessPickDialog(self, current)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self._commit_cell_edit(iid, "business", ",".join(dlg.result))

    def _commit_cell_edit(self, iid: str, field: str, value: str) -> None:
        try:
            index = int(iid)
        except ValueError:
            return
        ok = self._write_guard(self.db.update_field, index, field, value)
        if ok:
            self.tree.set(iid, field, value)
            self._set_status(
                f"已修改 {config.CSV_HEADERS[field]}：{value or '(清空)'}（已写入 leads.csv）",
                "ok")
            self._refresh_stats()
        elif ok is None:
            return                       # 文件被占用，已经弹过提示了
        else:
            self._set_status("修改失败：没找到这条记录", "warn")

    def _write_guard(self, fn, *args, **kwargs):
        """执行会写文件的动作，顺手把「文件被占用」翻译成人话。

        leads.csv 常被 Excel / WPS 打开着，这时写盘会抛 PermissionError。
        不拦的话界面看起来只是「点了没反应」，用户根本不知道发生了什么。
        """
        try:
            return fn(*args, **kwargs)
        except PermissionError:
            messagebox.showerror(
                "写不进去：文件正被占用",
                "leads.csv 现在被其它程序打开着（多半是 Excel / WPS）。\n\n"
                "请先关掉那个程序，再重试这次操作。\n\n"
                f"{config.LEADS_CSV}",
                parent=self)
            return None

    # ============================================================ 批量操作
    def _selected_indexes(self) -> list[int]:
        out: list[int] = []
        for iid in self.tree.selection():
            try:
                out.append(int(iid))
            except ValueError:
                continue
        return out

    def _reassign_source(self) -> None:
        """把选中行的来源平台改成当前来源 —— 导入时选错就靠这个救回来。"""
        indexes = self._selected_indexes()
        if not indexes:
            messagebox.showinfo("提示",
                                "先在表格里选中要改的行（按住 Ctrl 可以多选）。", parent=self)
            return
        source = self._current_source()
        if not source:
            messagebox.showwarning("提示", "先在顶部选择或输入目标来源。", parent=self)
            return
        n = self._write_guard(self.db.rebind_source, indexes, source)
        if n is None:
            return
        op, why = roster.operator_for_source(source)
        hint = f"（该平台运营：{op}）" if op else ""
        if n:
            self._refresh_table()
            self._set_status(f"已把 {n} 条线索的来源改为「{source}」{hint}", "ok")
        else:
            self._set_status(f"选中的行来源已经是「{source}」，无需改动{hint}", "hint")

    def _delete_rows(self) -> None:
        indexes = self._selected_indexes()
        if not indexes:
            messagebox.showinfo("提示", "先在表格里选中要删除的行。", parent=self)
            return
        if not messagebox.askyesno(
                "删除线索",
                f"确定删除选中的 {len(indexes)} 条线索？\n\n"
                f"会从总库 leads.csv 里真正删掉，无法撤销。\n"
                f"（想留底稿可以先点「导出 CSV…」备份一份）",
                parent=self):
            return
        n = self._write_guard(self.db.delete_at, indexes)
        if n is None:
            return
        self._refresh_table()
        self._set_status(f"已删除 {n} 条线索", "ok")

    # ============================================================ 模式 / 剪贴板
    def _update_mode_state(self) -> None:
        """按当前模式刷新标题和提示语。

        「清空面板」按钮始终可用 —— 它只清显示，不删总库里的任何数据。
        """
        if self.temp_mode:
            self.result_box.configure(
                text=" ④ 识别结果 · 临时模式（只显示本批，数据照常入库） ")
            self.result_hint.configure(text="复制走，按 T 清空面板开始下一批")
        else:
            self.result_box.configure(text=" ④ 识别结果 · 总库（双击单元格可修改） ")
            self.result_hint.configure(text="")

    def _toggle_temp_mode(self) -> None:
        """切换「只看本批」—— 只影响列表显示，不影响数据写入。

        线索**始终写进总库**。这个开关的作用是：表里只留这一批新增的，
        方便一次性复制走，不被历史数据搅在一起。
        """
        want = bool(self.temp_var.get())
        if want == self.temp_mode:
            return
        self.temp_mode = want
        self._refresh_table()
        if self.temp_mode:
            self._set_status(
                f"已切到临时模式：表里只显示本批（{len(self.batch_records)} 条）。"
                f"线索照样写入总库。", "ok")
        else:
            self._set_status(f"已切回总库模式，显示全部 {len(self.db)} 条", "ok")

    def _visible_records(self) -> list[tuple[int, db.LeadRecord]]:
        """表格当前显示的行：``(库记录索引, 记录)``，顺序与表格一致（最新在上）。"""
        out: list[tuple[int, db.LeadRecord]] = []
        for index, _row in self._current_rows():
            rec = self.db.get(index)
            if rec is not None:
                out.append((index, rec))
        return out

    def _pick_records(self) -> list[db.LeadRecord]:
        """取要操作的行：有选中就取选中的，没选中就取当前全部（按表格顺序）。"""
        rows = self._visible_records()
        sel = set(self._selected_indexes())
        if sel:
            rows = [t for t in rows if t[0] in sel]
        return [rec for _, rec in rows]

    # ============================================================ 快捷键
    def _install_shortcuts(self) -> None:
        """装窗口级快捷键：Enter = 开始识别，T = 清空面板。

        绑定在窗口上（焦点在任何地方都能触发），但**焦点在输入框里时不响应**
        —— 否则改单元格、输来源名、在图片列表里按 T 跳转都会被误吃掉。
        """
        for seq in ("<Return>", "<KP_Enter>"):
            self.bind(seq, self._hotkey_scan)
        for seq in ("<Key-t>", "<Key-T>"):
            self.bind(seq, self._hotkey_clear)

    def _typing_somewhere(self) -> bool:
        """焦点是不是在可输入/可打字的地方（那时快捷键要让路）。"""
        if self._editor is not None:            # 正在编辑单元格
            return True
        w = self.focus_get()
        if w is None:
            return False
        try:
            return w.winfo_class() in ("Entry", "TEntry", "TCombobox",
                                       "Text", "Listbox", "TSpinbox")
        except Exception:
            return False

    def _hotkey_scan(self, _event=None) -> str:
        if self._typing_somewhere():
            return ""                           # 让输入框正常收到回车
        if self.btn_start.instate(["!disabled"]):   # 识别中就不重复触发
            self._start_scan()
        return "break"

    def _hotkey_clear(self, _event=None) -> str:
        if self._typing_somewhere():
            return ""                           # 用户是在打字，别抢
        self._reset_panel()
        return "break"

    def _reset_panel(self) -> None:
        """清空当前面板：待识别图片列表 + 结果列表的显示（T 键）。

        **总库数据一条都不会删** —— 那要用「删除选中行」。
        这个动作的意义是「干净地开始处理下一批」。
        """
        n_imgs = len(self.files)
        n_rows = len(self.tree.get_children())
        if not n_imgs and not n_rows:
            self._set_status("面板已经是空的", "hint")
            return

        self._clear_files()
        self.batch_records.clear()
        self.batch = self._fresh_batch()
        self._refresh_table()

        if self.temp_mode:
            self._set_status(
                f"已清空面板：{n_imgs} 张待识别图片、{n_rows} 条列表显示"
                f"（总库数据未动，仍有 {len(self.db)} 条）", "ok")
        else:
            self._set_status(
                f"已清空 {n_imgs} 张待识别图片。总库模式下列表显示的是全部 "
                f"{len(self.db)} 条线索，没有清空 —— 只想看本批请勾选「临时模式」",
                "ok")

    def _copy_batch(self) -> None:
        """按保存好的复制设置，把线索复制成制表符分隔的文本。

        用制表符分隔是刻意的：粘进飞书多维表格 / Excel / WPS 会自动分列，
        不用先导一份文件。默认**不带表头**，因为粘过去表头会占掉一行数据。
        """
        picked = self._pick_records()
        if not picked:
            messagebox.showinfo("提示", "当前表里没有可复制的线索。", parent=self)
            return
        fields, with_header = config.load_copy_prefs()
        if not self._copy_records(picked, fields, with_header):
            return
        scope = "选中" if self.tree.selection() else "当前"
        cols = "、".join(config.CSV_HEADERS[fd] for fd in fields)
        head = "含表头" if with_header else "不含表头"
        self._set_status(
            f"已复制{scope} {len(picked)} 条（{head}：{cols}）　→ 去多维表格 Ctrl+V", "ok")

    def _copy_records(self, records: list[db.LeadRecord],
                      fields: list[str], with_header: bool) -> bool:
        """真正往系统剪贴板写 TSV。"""
        lines: list[str] = []
        if with_header:
            lines.append("\t".join(config.CSV_HEADERS[fd] for fd in fields))
        lines += ["\t".join(rec.to_row(fields, for_export=True)) for rec in records]
        try:
            self.clipboard_clear()
            self.clipboard_append("\n".join(lines))
            self.update()        # 让内容真正落到系统剪贴板，别等程序退出才生效
        except Exception as exc:
            messagebox.showerror("复制失败", str(exc), parent=self)
            return False
        return True

    def _copy_settings(self) -> None:
        """调整复制到剪贴板的列与表头（选择和导出时是同一套方式）。"""
        dlg = CopyDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return
        fields, with_header = dlg.result
        config.save_copy_prefs(fields, with_header)
        cols = "、".join(config.CSV_HEADERS[fd] for fd in fields)
        self._set_status(
            f"复制设置已保存（{cols}｜{'含表头' if with_header else '不含表头'}），"
            f"点「复制本批」就按这个输出", "ok")

    def _fill_person_by_source(self) -> None:
        """把空的「线索人」按来源平台对应的运营补上。"""
        active = self.db
        rows = self._visible_records()
        sel = set(self._selected_indexes())
        targets = [(i, r) for i, r in rows
                   if (not sel or i in sel) and not (r.source_person or "").strip()]
        if not targets:
            self._set_status("没有需要补全的行（线索人都不为空）", "hint")
            return

        changes: list[tuple[int, str, str]] = []
        unknown: list[str] = []
        for index, rec in targets:
            op, _why = roster.operator_for_source(rec.source_platform)
            if op:
                changes.append((index, "source_person", op))
            else:
                unknown.append(rec.source_platform or "(来源为空)")
        filled = self._write_guard(active.update_many, changes)
        if filled is None:
            return
        self._refresh_table()

        msg = f"已按来源平台补全 {filled} 条线索人"
        if unknown:
            uniq = sorted({u for u in unknown if u})
            msg += ("　|　{} 条没补上（来源未登记运营：{}）".format(
                len(unknown), "、".join(uniq[:3])))
        self._set_status(msg, "ok" if filled else "hint")

    def _open_row_image(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        name = self._row_images.get(sel[0], "")
        if not name:
            return
        for cand in self.files:
            if cand.name == name and cand.exists():
                _open_path(cand)
                return
        messagebox.showinfo("提示",
                            f"图片来源记录为「{name}」，但原文件不在当前已添加列表中。\n"
                            f"数据目录：{config.DATA_DIR}", parent=self)

    # -------------------------------------------------- 状态
    def _set_status(self, text: str, level: str = "hint") -> None:
        style = {"hint": "Hint.TLabel", "ok": "Ok.TLabel",
                 "warn": "Warn.TLabel"}.get(level, "Hint.TLabel")
        self.status.configure(text=text, style=style)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("退出", "识别任务仍在进行中，确定退出吗？", parent=self):
                return
        self.destroy()


# ================================================================ 导出对话框
class ExportDialog(tk.Toplevel):
    """导出选项：勾选要导出的列 + 是否包含表头。

    确认后 ``self.result`` 为 ``(fields, with_header)``；取消则为 None。
    """

    def __init__(self, master: "LeadScannerApp", total: int) -> None:
        super().__init__(master)
        self.withdraw()
        self.result: tuple[list[str], bool] | None = None
        self.vars: dict[str, tk.BooleanVar] = {}

        f = master.font_family
        self.title("导出选项")
        self.resizable(False, False)
        self.configure(bg=CARD)
        self.transient(master)

        top = tk.Frame(self, bg=CARD)
        top.pack(fill="x", padx=18, pady=(16, 4))
        tk.Label(top, text=f"共 {total} 条线索", bg=CARD, fg=FG,
                 font=(f, config.UI_FONT_SIZE + 1, "bold")).pack(anchor="w")
        tk.Label(top, text="勾选要导出的列，没勾的列不会写进 CSV",
                 bg=CARD, fg=MUTED, font=(f, config.UI_FONT_SIZE)).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self, bg=CARD)
        body.pack(fill="x", padx=18, pady=(8, 4))
        cols = list(config.EXPORT_FIELDS)
        saved_fields, saved_header = config.load_export_prefs()   # 上次保存的默认
        half = (len(cols) + 1) // 2
        for i, field in enumerate(cols):
            var = tk.BooleanVar(value=field in saved_fields)
            self.vars[field] = var
            tk.Checkbutton(
                body, text=config.CSV_HEADERS[field], variable=var,
                bg=CARD, fg=FG, activebackground=CARD, activeforeground=FG,
                selectcolor="#ffffff", anchor="w",
                font=(f, config.UI_FONT_SIZE),
            ).grid(row=i % half, column=i // half, sticky="w", padx=(0, 28), pady=1)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=18, pady=(10, 0))

        opt = tk.Frame(self, bg=CARD)
        opt.pack(fill="x", padx=18, pady=8)
        self.header_var = tk.BooleanVar(value=saved_header)
        tk.Checkbutton(
            opt, text="包含表头（第一行写列名）", variable=self.header_var,
            bg=CARD, fg=FG, activebackground=CARD, activeforeground=FG,
            selectcolor="#ffffff", anchor="w", font=(f, config.UI_FONT_SIZE),
        ).pack(anchor="w")

        self.remember_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opt, text="保存为默认导出选项（下次打开自动带出这次的勾选）",
            variable=self.remember_var,
            bg=CARD, fg=PRIMARY, activebackground=CARD, activeforeground=PRIMARY,
            selectcolor="#ffffff", anchor="w", font=(f, config.UI_FONT_SIZE),
        ).pack(anchor="w", pady=(4, 0))

        btns = tk.Frame(self, bg=CARD)
        btns.pack(fill="x", padx=18, pady=(4, 16))
        ttk.Button(btns, text="全选", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(btns, text="全不选", command=lambda: self._set_all(False)).pack(side="left", padx=6)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="确定导出", style="Primary.TButton",
                   command=self._confirm).pack(side="right")

        self._center(master)
        self.deiconify()
        self.grab_set()
        self.bind("<Escape>", lambda e: self.destroy())

    def _set_all(self, value: bool) -> None:
        for var in self.vars.values():
            var.set(value)

    def _center(self, master: tk.Misc) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - w) // 2
            y = master.winfo_rooty() + (master.winfo_height() - h) // 3
        except Exception:
            return
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _confirm(self) -> None:
        fields = [fd for fd in config.EXPORT_FIELDS if self.vars[fd].get()]
        if not fields:
            messagebox.showwarning("提示", "至少要勾选一列。", parent=self)
            return
        header = bool(self.header_var.get())
        if self.remember_var.get():
            config.save_export_prefs(fields, header)
        self.result = (fields, header)
        self.destroy()


# ================================================================ 复制设置
class CopyDialog(tk.Toplevel):
    """复制到剪贴板的设置：勾选要复制的列 + 是否带表头。

    交互和导出对话框一样，但**默认不带表头** —— 粘到多维表格时，
    表头会变成一行数据。
    确认后 ``self.result`` 为 ``(fields, with_header)``；取消则为 None。
    """

    def __init__(self, master: "LeadScannerApp") -> None:
        super().__init__(master)
        self.withdraw()
        self.result: tuple[list[str], bool] | None = None
        self.vars: dict[str, tk.BooleanVar] = {}

        f = master.font_family
        self.title("复制设置")
        self.resizable(False, False)
        self.configure(bg=CARD)
        self.transient(master)

        top = tk.Frame(self, bg=CARD)
        top.pack(fill="x", padx=18, pady=(16, 4))
        tk.Label(top, text="复制到剪贴板时输出哪些列", bg=CARD, fg=FG,
                 font=(f, config.UI_FONT_SIZE + 1, "bold")).pack(anchor="w")
        tk.Label(top, text="制表符分隔，粘到多维表格 / Excel 会自动分列",
                 bg=CARD, fg=MUTED,
                 font=(f, config.UI_FONT_SIZE)).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self, bg=CARD)
        body.pack(fill="x", padx=18, pady=(8, 4))
        cols = list(config.COPY_FIELDS)
        saved_fields, _ = config.load_copy_prefs()
        half = (len(cols) + 1) // 2
        for i, field in enumerate(cols):
            var = tk.BooleanVar(value=field in saved_fields)
            self.vars[field] = var
            tk.Checkbutton(
                body, text=config.CSV_HEADERS[field], variable=var,
                bg=CARD, fg=FG, activebackground=CARD, activeforeground=FG,
                selectcolor="#ffffff", anchor="w",
                font=(f, config.UI_FONT_SIZE),
            ).grid(row=i % half, column=i // half, sticky="w", padx=(0, 28), pady=1)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=18, pady=(10, 0))

        opt = tk.Frame(self, bg=CARD)
        opt.pack(fill="x", padx=18, pady=8)
        tk.Label(opt, text="点「确定」即存为默认，之后「复制本批」直接按这个输出",
                 bg=CARD, fg=PRIMARY,
                 font=(f, config.UI_FONT_SIZE)).pack(anchor="w", pady=(4, 0))

        btns = tk.Frame(self, bg=CARD)
        btns.pack(fill="x", padx=18, pady=(4, 16))
        ttk.Button(btns, text="全选", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(btns, text="全不选", command=lambda: self._set_all(False)).pack(
            side="left", padx=6)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="确定", style="Primary.TButton",
                   command=self._confirm).pack(side="right")

        self._center(master)
        self.deiconify()
        self.grab_set()
        self.bind("<Escape>", lambda e: self.destroy())

    def _set_all(self, value: bool) -> None:
        for var in self.vars.values():
            var.set(value)

    def _center(self, master: tk.Misc) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - w) // 2
            y = master.winfo_rooty() + (master.winfo_height() - h) // 3
        except Exception:
            return
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _confirm(self) -> None:
        fields = [fd for fd in config.COPY_FIELDS if self.vars[fd].get()]
        if not fields:
            messagebox.showwarning("提示", "至少要勾选一列。", parent=self)
            return
        self.result = (fields, False)
        self.destroy()


# ================================================================ 名单 / 更新日志
class RosterWindow(tk.Toplevel):
    """名单管理：查运营-账号对照与业务名单，并能导出成一行一条的 CSV。"""

    def __init__(self, master: "LeadScannerApp") -> None:
        super().__init__(master)
        self.withdraw()
        roster.load()

        self.title("名单管理")
        self.configure(bg=BG)
        self.geometry("780x580")
        self.transient(master)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=12, pady=(12, 8))

        page1 = tk.Frame(nb, bg=CARD)
        nb.add(page1, text="  运营 & 账号  ")
        self._make_tree(page1, ("运营", "账号"),
                        [(op, acc) for op, accs in roster.OPERATORS for acc in accs],
                        widths=(180, 540))

        page2 = tk.Frame(nb, bg=CARD)
        nb.add(page2, text="  业务名单  ")
        self._make_tree(page2, ("分组", "姓名"),
                        [(g, n) for g, members in roster.SALES for n in members],
                        widths=(180, 540))

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(bar, text="导出「运营-账号」表",
                   command=lambda: self._export("op")).pack(side="left")
        ttk.Button(bar, text="导出「业务名单」表",
                   command=lambda: self._export("sales")).pack(side="left", padx=8)
        ttk.Label(bar, text="导出格式为一行一条，可直接导入多维表格",
                  style="Hint.TLabel").pack(side="left", padx=12)
        ttk.Button(bar, text="关闭", command=self.destroy).pack(side="right")

        _center_window(self, master)
        self.deiconify()
        self.grab_set()

    def _make_tree(self, parent: tk.Frame, heads: tuple, rows: list,
                   widths: tuple) -> ttk.Treeview:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        cols = [f"c{i}" for i in range(len(heads))]
        tree = ttk.Treeview(parent, columns=cols, show="headings", height=14)
        for c, h, w in zip(cols, heads, widths):
            tree.heading(c, text=h)
            tree.column(c, width=w, anchor="w")
        for r in rows:
            tree.insert("", "end", values=list(r))
        tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        tree.configure(yscrollcommand=sb.set)
        return tree

    def _export(self, kind: str) -> None:
        if kind == "op":
            title, initial = "导出运营-账号对照表", "运营账号对照表.csv"
            func = roster.export_operators_csv
        else:
            title, initial = "导出业务名单", "业务名单.csv"
            func = roster.export_sales_csv

        dest = filedialog.asksaveasfilename(
            title=title, defaultextension=".csv", initialfile=initial,
            initialdir=str(config.DATA_DIR), filetypes=[("CSV 文件", "*.csv")], parent=self)
        if not dest:
            return
        try:
            path = func(dest)
        except PermissionError:
            messagebox.showerror("导出失败", "文件被占用（可能正被 Excel 打开）。", parent=self)
            return
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        messagebox.showinfo(
            "导出成功",
            f"已导出到：\n{path}\n\n这个文件是一行一条的格式，直接导入多维表格就是分开的记录。",
            parent=self)


class ChangelogWindow(tk.Toplevel):
    """更新日志。"""

    def __init__(self, master: "LeadScannerApp") -> None:
        super().__init__(master)
        self.withdraw()
        f = master.font_family
        self.title("更新日志")
        self.configure(bg=BG)
        self.geometry("680x600")
        self.transient(master)

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(16, 6))
        tk.Label(head, text=config.APP_NAME, bg=BG, fg=FG,
                 font=(f, config.UI_FONT_SIZE + 3, "bold")).pack(anchor="w")
        tk.Label(head, text=f"当前版本 {config.APP_VERSION}", bg=BG, fg=MUTED,
                 font=(f, config.UI_FONT_SIZE)).pack(anchor="w")

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(6, 0))
        text = tk.Text(body, wrap="word", bg=CARD, fg=FG, relief="flat",
                       font=(f, config.UI_FONT_SIZE), padx=16, pady=12,
                       highlightthickness=1, highlightbackground=BORDER)
        text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        sb.pack(side="right", fill="y")
        text.configure(yscrollcommand=sb.set)

        text.tag_configure("ver", font=(f, config.UI_FONT_SIZE + 2, "bold"),
                           foreground=PRIMARY, spacing1=12, spacing3=2)
        text.tag_configure("date", foreground=MUTED, font=(f, config.UI_FONT_SIZE - 1))
        text.tag_configure("item", spacing1=3, lmargin1=18, lmargin2=32)

        for entry in config.CHANGELOG:
            text.insert("end", f"{entry.get('version', '')}  ", "ver")
            text.insert("end", f"{entry.get('date', '')}\n", "date")
            for it in entry.get("items", []):
                text.insert("end", f"· {it}\n", "item")
            text.insert("end", "\n")
        text.configure(state="disabled")

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=18, pady=14)
        ttk.Button(bar, text="关闭", command=self.destroy).pack(side="right")

        _center_window(self, master)
        self.deiconify()
        self.grab_set()


class BusinessPickDialog(tk.Toplevel):
    """任务负责人多选：按 老业务 / 新人 / 带教 分组列出，勾选即可。

    确认时会自动处理业务规则：有新人就补上带教，顺序排成 新人 → 其它 → 带教。
    """

    def __init__(self, master: "LeadScannerApp", current: list[str]) -> None:
        super().__init__(master)
        self.withdraw()
        self.result: list[str] | None = None
        self.vars: dict[str, tk.BooleanVar] = {}

        roster.load()
        f = master.font_family
        self.title("选择任务负责人")
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.transient(master)

        top = tk.Frame(self, bg=CARD)
        top.pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(top, text="勾选要指派的人（可以多选）", bg=CARD, fg=FG,
                 font=(f, config.UI_FONT_SIZE + 1, "bold")).pack(anchor="w")
        tk.Label(top, text="分给新人的线索会自动带上带教，导出时带教排在最后",
                 bg=CARD, fg=MUTED, font=(f, config.UI_FONT_SIZE - 1)).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self, bg=CARD)
        body.pack(fill="both", expand=True, padx=18, pady=(6, 0))
        for group, members in roster.SALES:
            block = tk.Frame(body, bg=CARD)
            block.pack(fill="x", pady=(0, 8))
            tk.Label(block, text=group, bg=CARD, fg=MUTED,
                     font=(f, config.UI_FONT_SIZE - 1, "bold")).pack(anchor="w")
            row = tk.Frame(block, bg=CARD)
            row.pack(fill="x")
            for i, name in enumerate(members):
                var = tk.BooleanVar(value=name in current)
                self.vars[name] = var
                tk.Checkbutton(row, text=name, variable=var, bg=CARD, fg=FG,
                               activebackground=CARD, activeforeground=FG,
                               selectcolor="#ffffff", anchor="w",
                               font=(f, config.UI_FONT_SIZE)).grid(
                    row=i // 5, column=i % 5, sticky="w", padx=(0, 16), pady=1)

        bar = tk.Frame(self, bg=CARD)
        bar.pack(fill="x", padx=18, pady=(6, 14))
        ttk.Button(bar, text="全不选", command=lambda: self._set_all(False)).pack(side="left")
        ttk.Button(bar, text="取消", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="确定", style="Primary.TButton",
                   command=self._confirm).pack(side="right")

        _center_window(self, master)
        self.deiconify()
        self.grab_set()
        self.bind("<Escape>", lambda e: self.destroy())

    def _set_all(self, value: bool) -> None:
        for var in self.vars.values():
            var.set(value)

    def _confirm(self) -> None:
        picked = [n for n in roster.sales_names()
                  if self.vars.get(n) is not None and self.vars[n].get()]
        if not picked:
            if not messagebox.askyesno("清空", "一个人都没勾，确定把「任务负责人」清空吗？",
                                       parent=self):
                return
            self.result = []
            self.destroy()
            return
        self.result = roster.order_responsibles(roster.with_mentor(picked))
        self.destroy()


def _center_window(win: tk.Toplevel, master: tk.Misc) -> None:
    """把弹窗摆到主窗口中间偏上的位置。"""
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    try:
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 3
    except Exception:
        return
    win.geometry(f"+{max(0, x)}+{max(0, y)}")


# ================================================================ 工具函数
def _check_pil() -> bool:
    try:
        import PIL
        return bool(PIL.__version__)
    except Exception:
        return False


def _pick_font(root: tk.Misc) -> str:
    try:
        families = set(tkfont.families(root))
    except Exception:
        return "Arial"
    for f in config.UI_FONT_CANDIDATES:
        if f in families:
            return f
    return "Arial"


def _open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))            # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
    except Exception:
        pass


def run_app() -> None:
    global LOG
    config.setup_logging()
    LOG = config.LOGGER
    app = LeadScannerApp()
    app.mainloop()
