#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stock-quant 图形界面。

界面风格参考 stock_predict/stock_gui.py 的深色桌面布局；功能上不重复实现
分析算法，而是把 main.py 的全部 CLI 能力封装成表单，后台调用同一套命令，
因此 GUI 与命令行行为、参数、报告完全一致。

支持：
- 分析 analyze（股票池/热门股/指数模式/多持仓模式/信号过滤/预测/钉钉通知）
- 扫描 scan（排序/随机抽样/PE/ST/低市值过滤/并行）
- 回测 backtest（单股/批量/随机/单持仓轮动/固定资金）
- 策略对比 compare
- 因子指标 indicators
- 环境检查、报告/输出目录、CLI 帮助

命令行仍然可用：
    python main.py analyze -s 000725 -g macd
    python main.py --help
不带子命令运行 `python main.py` 时启动本界面。
"""

import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from _version import __version__
except Exception:
    __version__ = 'unknown'

try:
    from core.config import app_root
    APP_ROOT = app_root()
except Exception:
    APP_ROOT = ROOT

MAIN_PATH = os.path.join(APP_ROOT, 'main.py')

# ---- 配色：取自 stock_predict/stock_gui.py，保持同一套深色桌面观感 ----
AXIS_TXT = '#8fa0ad'
TITLE_TXT = '#aebccb'
DARK_BG = '#101418'
PANEL_BG = '#171c22'
FIELD_BG = '#1c232b'
FG_MAIN = '#d7dee6'
BTN_BG = '#222a33'
BTN_FG = '#d7dee6'
BTN_HOVER = '#2b3540'
BTN_BORDER = '#333e4a'
BORDER = '#2a3340'
SEL_BG = '#2b3a4d'
ACCENT = '#4da3ff'
GREEN = '#7ee787'
YELLOW = '#e5c07b'
RED = '#ff6b6b'
CYAN = '#56d4dd'
LOG_BG = '#0c1117'

UI_FONT = ('TkDefaultFont', 9)
UI_FONT_BOLD = ('TkDefaultFont', 9, 'bold')
MONO_FONT = ('TkFixedFont', 9)
TITLE_FONT = ('TkDefaultFont', 17, 'bold')

SOURCES = ['auto', 'sqlite', 'sina', 'baostock', 'eastmoney', 'akshare']
SOURCES_LABEL = {
    'auto': 'auto（SQLite 优先）',
    'sqlite': 'sqlite（本地 stock_predict 库）',
    'sina': 'sina 新浪',
    'baostock': 'baostock',
    'eastmoney': 'eastmoney 东方财富',
    'akshare': 'akshare',
}
SOURCE_LABELS = list(SOURCES_LABEL.values())
SOURCE_BY_LABEL = {v: k for k, v in SOURCES_LABEL.items()}

STOCK_STRATEGIES = [
    ('boll', 'BOLL 布林带'),
    ('macd', 'MACD'),
    ('macd1', 'MACD1 6/13/5'),
    ('kdj', 'KDJ'),
    ('sar', 'SAR'),
    ('ma1030', 'MA10/30'),
    ('ema1030', 'EMA10/30'),
    ('ma310', 'MA3/10'),
    ('ma520', 'MA5/20'),
    ('ema520', 'EMA5/20'),
    ('quality_value', '质价多因子'),
    ('hold', '持有基准'),
]
INDEX_STRATEGIES = [
    ('momentum', '动量分层'),
    ('volatility', '波动率择时'),
    ('breadth', '涨跌比确认'),
]
SORT_OPTIONS = [
    ('random', '随机抽样（板块比例）'),
    ('changepercent', '涨跌幅'),
    ('turnoverratio', '换手率'),
    ('amount', '成交额'),
    ('mktcap', '总市值'),
    ('pe', '市盈率'),
]
MODES = [
    ('0', '0 常规综合分析'),
    ('1', '1 资金利用最大化轮动'),
    ('2', '2 多持仓资金利用最大化'),
    ('3', '3 多持仓强化'),
    ('4', '4 多持仓强化（趋势择股卖出）'),
]
FILTERS = [('', '不过滤'), ('b', '仅买入'), ('s', '仅卖出'), ('w', '仅观望')]
ONESTOCK = [('', '不启用'), ('0', '0 持仓时忽略新买点'), ('1', '1 RSI>75 时换股')]


# ============================================================================
# 轻量控件层
#
# 这台机器的 Tk 在 XWayland 下创建 ttk 控件非常慢（60 个 ttk.Label 约 8 秒），
# 而原生 tk 控件不到 1 秒。这里用 tk 控件实现 ttk 同名子集的观感，保留
# ttk.Notebook / Combobox / Progressbar，避免启动时几百个 ttk 控件卡住。
# ============================================================================

def _padding_kwargs(padding):
    if padding is None:
        return {}
    if isinstance(padding, (int, float)):
        p = int(padding)
        return {'padx': p, 'pady': p}
    try:
        vals = list(padding)
    except TypeError:
        return {}
    if len(vals) == 2:
        return {'padx': int(vals[0]), 'pady': int(vals[1])}
    if len(vals) >= 4:
        return {'padx': int(vals[0]) + int(vals[2]),
                'pady': int(vals[1]) + int(vals[3])}
    return {}


def _label_style(style):
    if style == 'Title.TLabel':
        return PANEL_BG, FG_MAIN, TITLE_FONT
    if style == 'Sub.TLabel':
        return PANEL_BG, AXIS_TXT, (UI_FONT[0], 9)
    if style == 'Header.TLabel':
        return PANEL_BG, TITLE_TXT, UI_FONT_BOLD
    if style == 'Dim.TLabel':
        return DARK_BG, AXIS_TXT, UI_FONT
    if style == 'Hint.TLabel':
        return DARK_BG, AXIS_TXT, (UI_FONT[0], 8)
    return DARK_BG, FG_MAIN, UI_FONT


class _TkFrame(tk.Frame):
    def __init__(self, master=None, padding=None, style=None, **kw):
        kw.setdefault('bg', PANEL_BG if style == 'Header.TFrame' else DARK_BG)
        kw.setdefault('bd', 0)
        kw.setdefault('highlightthickness', 0)
        kw.update(_padding_kwargs(padding))
        super().__init__(master, **kw)


class _TkLabel(tk.Label):
    def __init__(self, master=None, style=None, padding=None, **kw):
        bg, fg, font = _label_style(style)
        kw.setdefault('bg', bg)
        kw.setdefault('fg', fg)
        kw.setdefault('font', font)
        kw.setdefault('bd', 0)
        kw.setdefault('highlightthickness', 0)
        kw.update(_padding_kwargs(padding))
        super().__init__(master, **kw)


class _TkButton(tk.Button):
    def __init__(self, master=None, style=None, padding=None, **kw):
        if style == 'Accent.TButton':
            bg, fg, active = ACCENT, '#ffffff', '#3b8de0'
            font = UI_FONT_BOLD
        elif style == 'Danger.TButton':
            bg, fg, active = '#5c2b2b', '#ffd6d6', '#7a3a3a'
            font = UI_FONT
        else:
            bg, fg, active = BTN_BG, BTN_FG, BTN_HOVER
            font = UI_FONT
        kw.setdefault('bg', bg)
        kw.setdefault('fg', fg)
        kw.setdefault('activebackground', active)
        kw.setdefault('activeforeground', fg)
        kw.setdefault('disabledforeground', AXIS_TXT)
        kw.setdefault('font', font)
        kw.setdefault('relief', 'flat')
        kw.setdefault('bd', 0)
        kw.setdefault('highlightthickness', 0)
        kw.setdefault('cursor', 'hand2')
        pad = _padding_kwargs(padding)
        kw.setdefault('padx', pad.get('padx', 8))
        kw.setdefault('pady', pad.get('pady', 3))
        super().__init__(master, **kw)


class _TkCheckbutton(tk.Checkbutton):
    def __init__(self, master=None, style=None, **kw):
        kw.setdefault('bg', DARK_BG)
        kw.setdefault('fg', FG_MAIN)
        kw.setdefault('activebackground', DARK_BG)
        kw.setdefault('activeforeground', FG_MAIN)
        kw.setdefault('selectcolor', FIELD_BG)
        kw.setdefault('font', (UI_FONT[0], 8)
                      if style == 'Small.TCheckbutton' else UI_FONT)
        kw.setdefault('bd', 0)
        kw.setdefault('highlightthickness', 0)
        kw.setdefault('anchor', 'w')
        kw.setdefault('justify', 'left')
        super().__init__(master, **kw)


class _TkEntry(tk.Entry):
    def __init__(self, master=None, style=None, **kw):
        kw.setdefault('bg', FIELD_BG)
        kw.setdefault('fg', FG_MAIN)
        kw.setdefault('insertbackground', FG_MAIN)
        kw.setdefault('relief', 'flat')
        kw.setdefault('bd', 0)
        kw.setdefault('highlightthickness', 1)
        kw.setdefault('highlightbackground', BORDER)
        kw.setdefault('highlightcolor', ACCENT)
        if style == 'Preview.TEntry':
            kw.setdefault('bg', LOG_BG)
            kw.setdefault('fg', '#9cdcfe')
            kw.setdefault('font', MONO_FONT)
        kw.setdefault('readonlybackground', FIELD_BG)
        kw.setdefault('font', UI_FONT)
        super().__init__(master, **kw)


class _TkSpinbox(tk.Spinbox):
    def __init__(self, master=None, style=None, **kw):
        kw.setdefault('bg', FIELD_BG)
        kw.setdefault('fg', FG_MAIN)
        kw.setdefault('insertbackground', FG_MAIN)
        kw.setdefault('buttonbackground', BTN_BG)
        kw.setdefault('relief', 'flat')
        kw.setdefault('bd', 0)
        kw.setdefault('highlightthickness', 1)
        kw.setdefault('highlightbackground', BORDER)
        kw.setdefault('highlightcolor', ACCENT)
        kw.setdefault('font', UI_FONT)
        super().__init__(master, **kw)


class _TkLabelframe(tk.LabelFrame):
    def __init__(self, master=None, padding=None, **kw):
        kw.setdefault('bg', DARK_BG)
        kw.setdefault('fg', TITLE_TXT)
        kw.setdefault('font', UI_FONT_BOLD)
        kw.setdefault('bd', 1)
        kw.setdefault('relief', 'solid')
        kw.setdefault('labelanchor', 'nw')
        kw.setdefault('highlightthickness', 0)
        kw.update(_padding_kwargs(padding))
        super().__init__(master, **kw)


class _TkScrollbar(tk.Scrollbar):
    def __init__(self, master=None, **kw):
        kw.setdefault('bg', BTN_BG)
        kw.setdefault('troughcolor', DARK_BG)
        kw.setdefault('activebackground', BTN_HOVER)
        kw.setdefault('relief', 'flat')
        kw.setdefault('bd', 0)
        kw.setdefault('highlightthickness', 0)
        super().__init__(master, **kw)


class _TkSeparator(tk.Frame):
    def __init__(self, master=None, orient='horizontal', **kw):
        kw.setdefault('bg', BORDER)
        kw.setdefault('bd', 0)
        kw.setdefault('highlightthickness', 0)
        if orient == 'horizontal':
            kw.setdefault('height', 1)
        else:
            kw.setdefault('width', 1)
        super().__init__(master, **kw)


class _TkPanedwindow(tk.PanedWindow):
    def __init__(self, master=None, orient=tk.VERTICAL, **kw):
        kw.setdefault('bg', DARK_BG)
        kw.setdefault('sashwidth', 5)
        kw.setdefault('sashrelief', 'flat')
        kw.setdefault('bd', 0)
        super().__init__(master, orient=orient, **kw)

    def add(self, child, weight=1, minsize=0, **kw):
        stretch = 'always' if weight else 'never'
        super().add(child, minsize=minsize, stretch=stretch, **kw)


# 仅替换本项目用到的 ttk 控件；Notebook/Combobox/Progressbar/Style 保留。
# 注意：不能替换 ttk.Entry / ttk.Spinbox —— ttk.Combobox 内部会调用模块级
# Entry.__init__，替换后会导致 Combobox 初始化报错。Entry 数量不多，保留 ttk。
for _name, _cls in (
    ('Frame', _TkFrame), ('Label', _TkLabel), ('Button', _TkButton),
    ('Checkbutton', _TkCheckbutton), ('Labelframe', _TkLabelframe),
    ('Scrollbar', _TkScrollbar), ('Separator', _TkSeparator),
    ('Panedwindow', _TkPanedwindow),
):
    setattr(ttk, _name, _cls)


# ============================================================================
# 基础组件
# ============================================================================

def _pick_fonts(root):
    """按系统可用字体选择中文字体/等宽字体。"""
    global UI_FONT, UI_FONT_BOLD, MONO_FONT, TITLE_FONT
    try:
        import tkinter.font as tkfont
        families = set(tkfont.families(root))
    except Exception:
        families = set()

    def pick(candidates, fallback):
        for name in candidates:
            if name in families:
                return name
        return fallback

    ui = pick(('Microsoft YaHei UI', 'Microsoft YaHei', 'Noto Sans CJK SC',
               'Noto Sans CJK JP', 'WenQuanYi Micro Hei', 'Droid Sans Fallback',
               'DejaVu Sans'), 'TkDefaultFont')
    mono = pick(('Consolas', 'Cascadia Mono', 'DejaVu Sans Mono',
                 'Noto Sans Mono CJK SC', 'Liberation Mono'), 'TkFixedFont')
    UI_FONT = (ui, 9)
    UI_FONT_BOLD = (ui, 9, 'bold')
    MONO_FONT = (mono, 9)
    TITLE_FONT = (ui, 17, 'bold')


class ScrollFrame(ttk.Frame):
    """带纵向滚动条的标签页内容容器。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=DARK_BG, bd=0,
                                highlightthickness=0)
        self.vsb = ttk.Scrollbar(self, orient='vertical',
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)

        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body,
                                              anchor='nw')
        self.body.bind('<Configure>', self._on_body_configure)
        self.canvas.bind('<Configure>', self._on_canvas_configure)
        self.canvas.bind('<Enter>', self._bind_wheel)
        self.canvas.bind('<Leave>', self._unbind_wheel)

    def _on_body_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _bind_wheel(self, _event=None):
        self.canvas.bind_all('<MouseWheel>', self._on_wheel)
        self.canvas.bind_all('<Button-4>', self._on_wheel)
        self.canvas.bind_all('<Button-5>', self._on_wheel)

    def _unbind_wheel(self, _event=None):
        for seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            try:
                self.canvas.unbind_all(seq)
            except Exception:
                pass

    def _on_wheel(self, event):
        if getattr(event, 'num', None) == 4:
            delta = -1
        elif getattr(event, 'num', None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, 'units')


class StrategySelector(ttk.Frame):
    """策略多选：股票策略与指数策略两组，按指数模式切换。"""

    def __init__(self, parent, allow_index=False, default='boll,macd'):
        super().__init__(parent)
        self.allow_index = allow_index
        self.stock_vars = {k: tk.BooleanVar(value=False)
                           for k, _ in STOCK_STRATEGIES}
        self.index_vars = {k: tk.BooleanVar(value=False)
                           for k, _ in INDEX_STRATEGIES}
        self.stock_all = tk.BooleanVar(value=False)
        self.index_all = tk.BooleanVar(value=False)
        self._index_mode = False

        self._stock_frame = ttk.Frame(self)
        self._index_frame = ttk.Frame(self)
        self._build_group(self._stock_frame, STOCK_STRATEGIES,
                          self.stock_vars, self.stock_all, columns=3)
        self._build_group(self._index_frame, INDEX_STRATEGIES,
                          self.index_vars, self.index_all, columns=3)
        self._stock_frame.pack(fill='x')
        self.set(default)
        # 指数组默认全选，切换过去即可用
        for _var in self.index_vars.values():
            _var.set(True)
        self.index_all.set(True)

    def _build_group(self, parent, items, vars_map, all_var, columns=3):
        for i, (key, label) in enumerate(items):
            var = vars_map[key]
            # 单独勾选任意策略时自动取消「全部」，避免 get() 永远返回 all。
            var.trace_add('write',
                          lambda *_a, av=all_var, vv=var:
                          av.set(False) if vv.get() else None)
            ttk.Checkbutton(parent, text=label, variable=var,
                            style='Small.TCheckbutton').grid(
                row=i // columns, column=i % columns, sticky='w',
                padx=(0, 12), pady=2)
        bottom = ttk.Frame(parent)
        bottom.grid(row=(len(items) + columns - 1) // columns, column=0,
                    columnspan=columns, sticky='w', pady=(4, 0))
        ttk.Checkbutton(bottom, text='全部（all）', variable=all_var,
                        style='Small.TCheckbutton').pack(side='left')
        ttk.Button(bottom, text='全选', width=5,
                   command=lambda: self._select_all(vars_map, all_var, True)
                   ).pack(side='left', padx=(8, 2))
        ttk.Button(bottom, text='清空', width=5,
                   command=lambda: self._select_all(vars_map, all_var, False)
                   ).pack(side='left')

    def _select_all(self, vars_map, all_var, value):
        all_var.set(False)
        for var in vars_map.values():
            var.set(value)

    def set_mode(self, is_index):
        self._index_mode = bool(is_index and self.allow_index)
        self._stock_frame.pack_forget()
        self._index_frame.pack_forget()
        if self._index_mode:
            self._index_frame.pack(fill='x')
        else:
            self._stock_frame.pack(fill='x')

    def get(self):
        vars_map = self.index_vars if self._index_mode else self.stock_vars
        all_var = self.index_all if self._index_mode else self.stock_all
        if all_var.get():
            return 'all'
        return ','.join(k for k, v in vars_map.items() if v.get())

    def set(self, value):
        value = str(value or '').strip().lower()
        stock_keys = {k for k, _ in STOCK_STRATEGIES}
        index_keys = {k for k, _ in INDEX_STRATEGIES}
        if value == 'all':
            for var in self.stock_vars.values():
                var.set(True)
            for var in self.index_vars.values():
                var.set(True)
            self.stock_all.set(True)
            self.index_all.set(True)
            return
        keys = {x.strip() for x in re.split(r'[,|]', value) if x.strip()}
        has_stock = bool(keys & stock_keys)
        has_index = bool(keys & index_keys)
        for k, var in self.stock_vars.items():
            var.set(k in keys)
        for k, var in self.index_vars.items():
            var.set(k in keys)
        # 未涉及的另一组默认回到「全部」，切换指数/个股模式时不会出现空策略。
        if keys and not has_stock:
            for var in self.stock_vars.values():
                var.set(True)
        if keys and not has_index:
            for var in self.index_vars.values():
                var.set(True)
        self.stock_all.set(bool(keys) and not has_stock)
        self.index_all.set(bool(keys) and not has_index)


# ============================================================================
# 主窗口
# ============================================================================

class StockQuantGUI:
    def __init__(self, root):
        self.root = root
        self.state = {}
        self.proc = None
        self.reader_thread = None
        self.msg_queue = queue.Queue()
        self.last_report = ''
        self._running = False
        self._stop_requested = False
        self._closing = False
        self._build_after = None

        _pick_fonts(root)
        self._setup_root()
        self._setup_style()
        self._build_vars()
        self._build_header()
        self._build_body()
        self._build_statusbar()
        self._load_state()
        self._sync_index_mode()
        self._refresh_env_async()
        self._update_preview()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self._poll_after = self.root.after(100, self._poll_queue)
        self._preview_after = self.root.after(500, self._preview_loop)

    # ---------- 窗口与样式 ----------

    def _setup_root(self):
        self.root.title(f'stock-quant 量化工作台 · v{__version__}')
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        w = min(1400, max(1120, int(screen_w * 0.86)))
        h = min(960, max(720, int(screen_h * 0.84)))
        x = max(0, (screen_w - w) // 2)
        y = max(0, (screen_h - h) // 3)
        self.root.geometry(f'{w}x{h}+{x}+{y}')
        self.root.minsize(1000, 680)
        self.root.configure(bg=DARK_BG)
        try:
            self.root.attributes('-zoomed', True)
        except tk.TclError:
            pass
        self.root.bind('<F5>', lambda _e: self._run_current())
        self.root.bind('<Control-Return>', lambda _e: self._run_current())
        self.root.bind('<Control-l>', lambda _e: self._clear_log())
        self.root.bind('<Control-s>', lambda _e: self._save_state())

    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('.', background=DARK_BG, foreground=FG_MAIN,
                        fieldbackground=FIELD_BG, bordercolor=BORDER,
                        lightcolor=PANEL_BG, darkcolor=DARK_BG,
                        troughcolor=DARK_BG, focuscolor=ACCENT,
                        selectbackground=SEL_BG, selectforeground=FG_MAIN,
                        font=UI_FONT)
        style.configure('TFrame', background=DARK_BG)
        style.configure('Card.TFrame', background=PANEL_BG)
        style.configure('Header.TFrame', background=PANEL_BG)
        style.configure('TLabelframe', background=DARK_BG, bordercolor=BORDER,
                        relief='solid', borderwidth=1)
        style.configure('TLabelframe.Label', background=DARK_BG,
                        foreground=TITLE_TXT, font=UI_FONT_BOLD)
        style.configure('TLabel', background=DARK_BG, foreground=FG_MAIN)
        style.configure('Dim.TLabel', background=DARK_BG, foreground=AXIS_TXT)
        style.configure('Hint.TLabel', background=DARK_BG, foreground=AXIS_TXT,
                        font=(UI_FONT[0], 8))
        style.configure('Title.TLabel', background=PANEL_BG,
                        foreground=FG_MAIN, font=TITLE_FONT)
        style.configure('Sub.TLabel', background=PANEL_BG,
                        foreground=AXIS_TXT, font=(UI_FONT[0], 9))
        style.configure('Header.TLabel', background=PANEL_BG,
                        foreground=TITLE_TXT, font=UI_FONT_BOLD)
        style.configure('TButton', background=BTN_BG, foreground=BTN_FG,
                        bordercolor=BTN_BORDER, padding=(10, 4),
                        font=UI_FONT)
        style.map('TButton',
                  background=[('pressed', BTN_HOVER), ('active', BTN_HOVER),
                              ('disabled', DARK_BG)],
                  foreground=[('disabled', AXIS_TXT)],
                  bordercolor=[('focus', ACCENT)])
        style.configure('Accent.TButton', background=ACCENT,
                        foreground='#ffffff', bordercolor=ACCENT,
                        padding=(16, 5), font=UI_FONT_BOLD)
        style.map('Accent.TButton',
                  background=[('pressed', '#2f7bc7'), ('active', '#3b8de0'),
                              ('disabled', BTN_BG)],
                  foreground=[('disabled', AXIS_TXT)])
        style.configure('Danger.TButton', background='#5c2b2b',
                        foreground='#ffd6d6', bordercolor='#7a3a3a',
                        padding=(12, 4), font=UI_FONT)
        style.map('Danger.TButton',
                  background=[('pressed', '#7a3a3a'), ('active', '#6d3434'),
                              ('disabled', DARK_BG)],
                  foreground=[('disabled', AXIS_TXT)])
        style.configure('Small.TCheckbutton', background=DARK_BG,
                        foreground=FG_MAIN, focuscolor=DARK_BG,
                        font=(UI_FONT[0], 8))
        style.map('Small.TCheckbutton',
                  background=[('active', DARK_BG), ('selected', DARK_BG)],
                  foreground=[('active', FG_MAIN), ('selected', FG_MAIN)])
        style.configure('TEntry', fieldbackground=FIELD_BG, foreground=FG_MAIN,
                        bordercolor=BORDER, padding=4)
        style.map('TEntry', bordercolor=[('focus', ACCENT)])
        style.configure('Preview.TEntry', fieldbackground=LOG_BG,
                        foreground='#9cdcfe', bordercolor=BORDER,
                        padding=(6, 4), font=MONO_FONT)
        style.configure('TCombobox', fieldbackground=FIELD_BG,
                        foreground=FG_MAIN, background=BTN_BG,
                        arrowcolor=AXIS_TXT, bordercolor=BORDER, padding=3)
        style.map('TCombobox',
                  fieldbackground=[('readonly', FIELD_BG)],
                  foreground=[('readonly', FG_MAIN)],
                  arrowcolor=[('active', ACCENT)],
                  bordercolor=[('focus', ACCENT)])
        style.configure('TSpinbox', fieldbackground=FIELD_BG,
                        foreground=FG_MAIN, background=BTN_BG,
                        arrowcolor=AXIS_TXT, bordercolor=BORDER, padding=3)
        style.map('TSpinbox',
                  fieldbackground=[('readonly', FIELD_BG)],
                  foreground=[('readonly', FG_MAIN)],
                  arrowcolor=[('active', ACCENT)],
                  bordercolor=[('focus', ACCENT)])
        style.configure('TScrollbar', background=BTN_BG, troughcolor=DARK_BG,
                        bordercolor=DARK_BG, arrowcolor=AXIS_TXT, gripcount=0)
        style.map('TScrollbar', background=[('active', BTN_HOVER)])
        style.configure('TNotebook', background=DARK_BG, bordercolor=BORDER,
                        lightcolor=DARK_BG, darkcolor=DARK_BG,
                        tabmargins=[6, 6, 6, 0])
        style.configure('TNotebook.Tab', background=DARK_BG,
                        foreground=AXIS_TXT, bordercolor=BORDER,
                        padding=[15, 7], font=(UI_FONT[0], 10))
        style.map('TNotebook.Tab',
                  background=[('selected', PANEL_BG), ('active', PANEL_BG)],
                  foreground=[('selected', TITLE_TXT), ('active', FG_MAIN)])
        style.configure('TSeparator', background=BORDER)
        style.configure('TProgressbar', background=ACCENT,
                        troughcolor=FIELD_BG, bordercolor=BORDER)
        style.configure('TPanedwindow', background=DARK_BG)
        for sub in ('TRadiobutton', 'TCheckbutton'):
            style.configure(sub, background=DARK_BG, foreground=FG_MAIN,
                            focuscolor=DARK_BG, bordercolor=BORDER,
                            indicatorcolor=FIELD_BG)
            style.map(sub,
                      background=[('active', DARK_BG), ('selected', DARK_BG),
                                  ('disabled', DARK_BG)],
                      foreground=[('active', FG_MAIN), ('selected', FG_MAIN),
                                  ('disabled', AXIS_TXT)],
                      indicatorcolor=[('selected', ACCENT),
                                      ('disabled', FIELD_BG)])

    # ---------- 变量 ----------

    def _make_vars(self, prefix, defs):
        """defs: key -> (type, default)，type 为 's' / 'b'。"""
        out = {}
        for key, (kind, default) in defs.items():
            if kind == 'b':
                var = tk.BooleanVar(value=bool(default))
            else:
                var = tk.StringVar(value=str(default))
            self.state[f'{prefix}{key}'] = var
            out[key] = var
        return out

    def _build_vars(self):
        self.an = self._make_vars('an_', {
            'symbol': ('s', ''), 'file': ('s', ''), 'hot': ('s', ''),
            'start': ('s', ''), 'end': ('s', ''),
            'index': ('b', False), 'capital': ('s', '100'),
            'output': ('s', './output'), 'output_file': ('s', ''),
            'threads': ('s', '6'), 'mode': ('s', '0'), 'filter': ('s', ''),
            'chart': ('b', False), 'forecast': ('b', False),
            'notify': ('b', False), 'plan_time': ('s', ''),
            'src': ('s', 'auto'), 'seqnum': ('s', '0'),
            'maxholddays': ('s', ''),
        })
        self.sc = self._make_vars('sc_', {
            'top': ('s', '10'), 'offset': ('s', '0'),
            'min_volume': ('s', ''), 'sort': ('s', 'random'),
            'peplus': ('b', False), 'nost': ('b', False),
            'nolowcap': ('b', False), 'threads': ('s', '6'),
            'forecast': ('b', False), 'notify': ('b', False),
            'src': ('s', 'auto'), 'seqnum': ('s', '0'),
            'maxholddays': ('s', ''),
        })
        self.bt = self._make_vars('bt_', {
            'symbol': ('s', ''), 'file': ('s', ''), 'random': ('b', False),
            'start': ('s', ''), 'end': ('s', ''), 'capital': ('s', '100'),
            'output': ('s', './output'), 'chart': ('b', False),
            'onestock': ('s', ''), 'fixcapital': ('b', False),
            'verbose': ('b', False), 'src': ('s', 'auto'),
            'seqnum': ('s', '0'), 'maxholddays': ('s', ''),
        })
        self.cp = self._make_vars('cp_', {
            'symbol': ('s', ''), 'start': ('s', ''), 'end': ('s', ''),
            'capital': ('s', '100'), 'chart': ('b', False),
            'src': ('s', 'auto'), 'seqnum': ('s', '0'),
            'maxholddays': ('s', ''),
        })
        self.ind = self._make_vars('ind_', {
            'symbol': ('s', ''), 'start': ('s', ''), 'end': ('s', ''),
            'output': ('s', ''), 'chart': ('b', False), 'src': ('s', 'auto'),
        })
        # 策略选择值独立保存：标签页懒加载时未构建的页面也能生成命令/保存状态。
        self.strategy_vars = {
            'analyze': tk.StringVar(value='boll,macd'),
            'scan': tk.StringVar(value='boll,macd'),
            'backtest': tk.StringVar(value='boll,macd'),
        }
        self._strategy_attrs = {
            'analyze': 'an_sel', 'scan': 'sc_sel', 'backtest': 'bt_sel',
        }

    # ---------- 顶部 ----------

    def _build_header(self):
        header = ttk.Frame(self.root, style='Header.TFrame',
                           padding=(14, 10))
        header.pack(fill='x')
        left = ttk.Frame(header, style='Header.TFrame')
        left.pack(side='left', fill='x', expand=True)
        ttk.Label(left, text='stock-quant 量化工作台',
                  style='Title.TLabel').pack(anchor='w')
        ttk.Label(left,
                  text='分析 · 扫描 · 回测 · 对比 · 因子  |  与命令行共用同一套策略与数据源',
                  style='Sub.TLabel').pack(anchor='w', pady=(2, 0))

        right = ttk.Frame(header, style='Header.TFrame')
        right.pack(side='right')
        self.sqlite_badge = tk.Label(
            right, text='SQLite: 检测中…', bg=FIELD_BG, fg=AXIS_TXT,
            padx=8, pady=3, font=(UI_FONT[0], 8))
        self.sqlite_badge.pack(side='left', padx=(0, 6))
        self.mysql_badge = tk.Label(
            right, text='MySQL: 检测中…', bg=FIELD_BG, fg=AXIS_TXT,
            padx=8, pady=3, font=(UI_FONT[0], 8))
        self.mysql_badge.pack(side='left')

        bar = ttk.Frame(self.root, padding=(10, 6, 10, 2))
        bar.pack(fill='x')
        self.btn_run = ttk.Button(bar, text='▶  运行当前任务',
                                  style='Accent.TButton',
                                  command=self._run_current)
        self.btn_run.pack(side='left')
        self.btn_stop = ttk.Button(bar, text='■  停止', style='Danger.TButton',
                                   command=self._stop, state='disabled')
        self.btn_stop.pack(side='left', padx=(6, 0))
        ttk.Button(bar, text='复制命令', command=self._copy_command).pack(
            side='left', padx=(12, 0))
        ttk.Button(bar, text='打开报告', command=self._open_last_report).pack(
            side='left', padx=(6, 0))
        ttk.Button(bar, text='报告目录', command=self._open_report_dir).pack(
            side='left', padx=(6, 0))
        ttk.Button(bar, text='输出目录', command=self._open_output_dir).pack(
            side='left', padx=(6, 0))
        ttk.Button(bar, text='清空日志', command=self._clear_log).pack(
            side='right')

    # ---------- 主体 ----------

    def _build_body(self):
        paned = ttk.Panedwindow(self.root, orient=tk.VERTICAL)
        paned.pack(fill='both', expand=True, padx=10, pady=(0, 4))

        self.notebook = ttk.Notebook(paned)
        paned.add(self.notebook, weight=4)

        self.tab_names = ['analyze', 'scan', 'backtest', 'compare',
                          'indicators', 'tools']
        self.tab_labels = {
            'analyze': '  分析 Analyze  ',
            'scan': '  扫描 Scan  ',
            'backtest': '  回测 Backtest  ',
            'compare': '  策略对比 Compare  ',
            'indicators': '  因子指标 Indicators  ',
            'tools': '  环境 / 工具  ',
        }
        # 懒加载：先只构建第一个标签页，其余页面首次点击时再创建。
        # 在 XWayland 高分屏下可把启动时间从几十秒降到几秒。
        self.tab_frames = {}
        self.loading_labels = {}
        for name in self.tab_names:
            tab = ScrollFrame(self.notebook)
            self.notebook.add(tab, text=self.tab_labels[name])
            self.tab_frames[name] = tab
            lbl = tk.Label(tab.body, text='正在加载页面…', bg=DARK_BG,
                           fg=AXIS_TXT, font=(UI_FONT[0], 10))
            lbl.pack(pady=40)
            self.loading_labels[name] = lbl
        self.built_tabs = set()
        self.notebook.bind('<<NotebookTabChanged>>', self._on_tab_changed)
        # 先让窗口显示出来，再构建表单；避免高分屏 XWayland 下白屏数秒。
        self._build_after = self.root.after(150, self._build_initial_tab)

        bottom = ttk.Frame(paned)
        paned.add(bottom, weight=1)
        self._build_command_bar(bottom)
        self._build_log_panel(bottom)

    def _build_command_bar(self, parent):
        frame = ttk.Frame(parent, padding=(0, 2))
        frame.pack(fill='x')
        ttk.Label(frame, text='命令预览', style='Dim.TLabel').pack(side='left')
        self.cmd_var = tk.StringVar()
        self.cmd_entry = _TkEntry(frame, textvariable=self.cmd_var,
                                  style='Preview.TEntry', state='readonly')
        self.cmd_entry.pack(side='left', fill='x', expand=True, padx=(8, 6))
        ttk.Button(frame, text='复制', width=5,
                   command=self._copy_command).pack(side='left')

    def _build_log_panel(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True, pady=(4, 0))
        head = ttk.Frame(frame)
        head.pack(fill='x')
        ttk.Label(head, text='运行日志', style='Dim.TLabel').pack(side='left')
        ttk.Button(head, text='全选复制', command=self._copy_log).pack(
            side='right', padx=(4, 0))
        ttk.Button(head, text='清空', command=self._clear_log).pack(
            side='right')

        wrap = ttk.Frame(frame)
        wrap.pack(fill='both', expand=True)
        self.log = tk.Text(wrap, bg=LOG_BG, fg=FG_MAIN, insertbackground=FG_MAIN,
                           relief='flat', bd=0, wrap='none', font=MONO_FONT,
                           padx=8, pady=6, height=10, undo=True)
        ysb = ttk.Scrollbar(wrap, orient='vertical', command=self.log.yview)
        xsb = ttk.Scrollbar(wrap, orient='horizontal', command=self.log.xview)
        self.log.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        ysb.pack(side='right', fill='y')
        xsb.pack(side='bottom', fill='x')
        self.log.pack(side='left', fill='both', expand=True)
        self.log.tag_configure('info', foreground=FG_MAIN)
        self.log.tag_configure('cmd', foreground=ACCENT,
                               font=(MONO_FONT[0], 9, 'bold'))
        self.log.tag_configure('ok', foreground=GREEN)
        self.log.tag_configure('warn', foreground=YELLOW)
        self.log.tag_configure('error', foreground=RED)
        self.log.tag_configure('dim', foreground=AXIS_TXT)
        self.log.tag_configure('cyan', foreground=CYAN)
        self.log.bind('<Button-3>', self._log_menu)
        self.log_menu = tk.Menu(self.root, tearoff=0, bg=PANEL_BG,
                                fg=FG_MAIN, activebackground=SEL_BG,
                                activeforeground=FG_MAIN)
        self.log_menu.add_command(label='复制选中', command=self._copy_log)
        self.log_menu.add_command(label='全选', command=self._select_log)
        self.log_menu.add_separator()
        self.log_menu.add_command(label='清空', command=self._clear_log)

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, padding=(10, 4))
        bar.pack(fill='x')
        self.status_var = tk.StringVar(value='就绪')
        ttk.Label(bar, textvariable=self.status_var,
                  style='Dim.TLabel').pack(side='left')
        self.progress = ttk.Progressbar(bar, mode='determinate', length=120,
                                        maximum=100, value=0)
        self.progress.pack(side='right', padx=(8, 0))
        self.env_var = tk.StringVar(value='')
        ttk.Label(bar, textvariable=self.env_var,
                  style='Dim.TLabel').pack(side='right', padx=(0, 12))

    # ---------- 表单辅助 ----------

    def _lf(self, parent, title, row, col, colspan=1, pady=6, padx=6):
        frame = ttk.Labelframe(parent, text=title, padding=(10, 8))
        frame.grid(row=row, column=col, columnspan=colspan, sticky='nsew',
                   padx=padx, pady=pady)
        return frame

    def _label(self, parent, text, row, col=0, dim=True):
        ttk.Label(parent, text=text,
                  style='Dim.TLabel' if dim else 'TLabel').grid(
            row=row, column=col, sticky='w', padx=(0, 6), pady=3)

    def _entry(self, parent, var, row, col=1, colspan=1, width=20):
        entry = _TkEntry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=col, columnspan=colspan, sticky='ew',
                   pady=3)
        return entry

    def _combo(self, parent, var, values, row, col=1, colspan=1, width=18):
        combo = ttk.Combobox(parent, textvariable=var, values=values,
                             state='readonly', width=width)
        combo.grid(row=row, column=col, columnspan=colspan, sticky='ew',
                   pady=3)
        return combo

    def _mapped_combo(self, parent, var, options, row, col=1, colspan=1,
                      width=22):
        """下拉框显示中文标签，实际保存 key（options: [(key, label)]）。"""
        display = tk.StringVar()
        labels = [label for _, label in options]
        key_by_label = {label: key for key, label in options}
        label_by_key = {key: label for key, label in options}
        combo = ttk.Combobox(parent, textvariable=display, values=labels,
                             state='readonly', width=width)
        combo.grid(row=row, column=col, columnspan=colspan, sticky='ew',
                   pady=3)

        def sync_display(*_args):
            display.set(label_by_key.get(var.get(), labels[0]))

        def on_select(_event=None):
            var.set(key_by_label.get(display.get(), options[0][0]))

        combo.bind('<<ComboboxSelected>>', on_select)
        var.trace_add('write', sync_display)
        sync_display()
        return combo

    def _spin(self, parent, var, row, col=1, colspan=1, width=10,
              from_=0, to=1000000):
        spin = _TkSpinbox(parent, textvariable=var, width=width,
                          from_=from_, to=to)
        spin.grid(row=row, column=col, columnspan=colspan, sticky='w',
                  pady=3)
        # tk.Spinbox 在变量为空时会自动写入 from_ 的值；可选字段要恢复为空，
        # 否则会误生成 -h 1 / --maxholddays 1 这类参数。
        if not str(var.get()).strip():
            try:
                spin.delete(0, 'end')
            except Exception:
                var.set('')
        return spin

    def _check(self, parent, text, var, row, col=0, colspan=1):
        chk = ttk.Checkbutton(parent, text=text, variable=var,
                              style='Small.TCheckbutton')
        chk.grid(row=row, column=col, columnspan=colspan, sticky='w',
                 padx=(0, 12), pady=3)
        return chk

    def _file_entry(self, parent, var, row, col=1, colspan=1, mode='open',
                    title='选择文件', filetypes=None):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=col, columnspan=colspan, sticky='ew',
                   pady=3)
        _TkEntry(frame, textvariable=var).pack(side='left', fill='x',
                                               expand=True)

        def browse():
            if mode == 'dir':
                path = filedialog.askdirectory(initialdir=APP_ROOT,
                                               title=title)
            elif mode == 'save':
                path = filedialog.asksaveasfilename(
                    initialdir=APP_ROOT, title=title,
                    defaultextension='.csv',
                    filetypes=filetypes or [('CSV', '*.csv'),
                                            ('所有文件', '*.*')])
            else:
                path = filedialog.askopenfilename(
                    initialdir=APP_ROOT, title=title,
                    filetypes=filetypes or [('文本', '*.txt'),
                                            ('所有文件', '*.*')])
            if path:
                var.set(path)

        ttk.Button(frame, text='浏览…', width=7, command=browse).pack(
            side='left', padx=(4, 0))
        return frame

    def _date_controls(self, parent, start_var, end_var, row):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=4, sticky='ew', pady=3)
        ttk.Label(frame, text='开始', style='Dim.TLabel').pack(side='left')
        _TkEntry(frame, textvariable=start_var, width=11).pack(
            side='left', padx=(4, 8))
        ttk.Label(frame, text='结束', style='Dim.TLabel').pack(side='left')
        _TkEntry(frame, textvariable=end_var, width=11).pack(
            side='left', padx=(4, 10))
        ttk.Button(frame, text='近半年', width=6,
                   command=lambda: self._set_dates(start_var, end_var, 183)
                   ).pack(side='left', padx=1)
        ttk.Button(frame, text='近1年', width=5,
                   command=lambda: self._set_dates(start_var, end_var, 365)
                   ).pack(side='left', padx=1)
        ttk.Button(frame, text='今年', width=4,
                   command=lambda: self._set_ytd(start_var, end_var)
                   ).pack(side='left', padx=1)
        ttk.Button(frame, text='清空', width=4,
                   command=lambda: (start_var.set(''), end_var.set(''))
                   ).pack(side='left', padx=1)
        ttk.Label(frame, text='YYYYMMDD，留空用命令默认',
                  style='Hint.TLabel').pack(side='left', padx=(8, 0))

    @staticmethod
    def _set_dates(start_var, end_var, days):
        end = date.today()
        start = end - timedelta(days=days)
        start_var.set(start.strftime('%Y%m%d'))
        end_var.set(end.strftime('%Y%m%d'))

    @staticmethod
    def _set_ytd(start_var, end_var):
        today = date.today()
        start_var.set(date(today.year, 1, 1).strftime('%Y%m%d'))
        end_var.set(today.strftime('%Y%m%d'))

    def _source_row(self, parent, var, row, colspan=1):
        self._label(parent, '数据源 --src', row, 0)
        options = [(s, SOURCES_LABEL[s]) for s in SOURCES]
        return self._mapped_combo(parent, var, options, row, 1,
                                  colspan=colspan, width=22)

    # ---------- 分析 ----------

    def _build_analyze_tab(self):
        tab = self.tab_frames['analyze']
        body = tab.body
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        src = self._lf(body, '股票来源', 0, 0, colspan=2)
        src.columnconfigure(1, weight=1)
        self._label(src, '代码/名称 -s', 0)
        self._entry(src, self.an['symbol'], 0, 1, colspan=3)
        ttk.Label(src, text='多个用空格/逗号分隔，如：000725 京东方A 000021',
                  style='Hint.TLabel').grid(row=0, column=4, sticky='w',
                                            padx=(8, 0))
        self._label(src, '股票池文件 -sf', 1)
        self._file_entry(src, self.an['file'], 1, 1, colspan=3)
        self._label(src, '热门股 -h', 2)
        self._spin(src, self.an['hot'], 2, 1, width=8, from_=1, to=100)
        ttk.Label(src, text='按热度取前 N 只（存在时忽略 -s/-sf）',
                  style='Hint.TLabel').grid(row=2, column=2, columnspan=3,
                                            sticky='w', padx=(8, 0))
        ttk.Label(src, text='全部留空 = 默认股票池 input/pool.txt',
                  style='Hint.TLabel').grid(row=3, column=0, columnspan=5,
                                            sticky='w', pady=(2, 0))

        strat = self._lf(body, '策略与区间', 1, 0)
        self.an_sel = StrategySelector(
            strat, allow_index=True,
            default=self.strategy_vars['analyze'].get())
        self.an_sel.grid(row=0, column=0, columnspan=4, sticky='ew')
        self.an_sel.set_mode(bool(self.an['index'].get()))
        self._check(strat, '指数模式 -i（使用指数专属策略）', self.an['index'], 1)
        self.an['index'].trace_add('write',
                                   lambda *_: self._sync_index_mode())
        ttk.Separator(strat, orient='horizontal').grid(
            row=2, column=0, columnspan=4, sticky='ew', pady=6)
        self._date_controls(strat, self.an['start'], self.an['end'], 3)

        params = self._lf(body, '运行参数', 1, 1)
        params.columnconfigure(1, weight=1)
        self._source_row(params, self.an['src'], 0)
        self._label(params, '初始资金 -c（万元）', 1)
        self._spin(params, self.an['capital'], 1, width=10, from_=1,
                   to=1000000)
        self._label(params, '图表目录 -o', 2)
        self._file_entry(params, self.an['output'], 2, mode='dir',
                         title='选择图表输出目录')
        self._label(params, '报告文件名 -of', 3)
        self._entry(params, self.an['output_file'], 3)
        self._label(params, '并行进程 -x', 4)
        self._spin(params, self.an['threads'], 4, width=6, from_=1, to=6)
        self._label(params, '分析模式 -m', 5)
        self._mapped_combo(params, self.an['mode'], MODES, 5, 1, width=30)
        self._label(params, '信号过滤 -f', 6)
        self._mapped_combo(params, self.an['filter'], FILTERS, 6, 1, width=12)

        adv = self._lf(body, '高级 / 通知', 2, 0, colspan=2)
        adv.columnconfigure(1, weight=1)
        self._check(adv, '生成全部图表 --chart', self.an['chart'], 0, 0)
        self._check(adv, '预测次日触发价 --forecast', self.an['forecast'],
                    0, 1)
        self._check(adv, '钉钉通知 --notify', self.an['notify'], 0, 2)
        self._label(adv, '定时执行 --plan-time', 1, 0)
        self._entry(adv, self.an['plan_time'], 1, 1, width=10)
        ttk.Label(adv, text='HHMM，如 1430；当日已过顺延次日',
                  style='Hint.TLabel').grid(row=1, column=2, sticky='w',
                                            padx=(8, 0))
        self._label(adv, '买入序号 -sn', 2, 0)
        self._spin(adv, self.an['seqnum'], 2, 1, width=8, from_=0, to=1000)
        ttk.Label(adv, text='仅 BOLL 生效：0 不过滤，1 首次',
                  style='Hint.TLabel').grid(row=2, column=2, sticky='w',
                                            padx=(8, 0))
        self._label(adv, '最大持股 --maxholddays', 3, 0)
        self._spin(adv, self.an['maxholddays'], 3, 1, width=8,
                   from_=1, to=100000)
        ttk.Label(adv, text='超过 N 个交易日且跌破 BOLL 中线/回撤>10% 卖出',
                  style='Hint.TLabel').grid(row=3, column=2, sticky='w',
                                            padx=(8, 0))

    # ---------- 扫描 ----------

    def _build_scan_tab(self):
        tab = self.tab_frames['scan']
        body = tab.body
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        strat = self._lf(body, '策略', 0, 0, colspan=2)
        self.sc_sel = StrategySelector(
            strat, allow_index=False,
            default=self.strategy_vars['scan'].get())
        self.sc_sel.pack(fill='x')

        cond = self._lf(body, '筛选条件', 1, 0)
        cond.columnconfigure(1, weight=1)
        self._label(cond, 'Top N -t', 0)
        self._spin(cond, self.sc['top'], 0, width=8, from_=1, to=100)
        self._label(cond, '偏移 -o', 1)
        self._spin(cond, self.sc['offset'], 1, width=8, from_=0,
                   to=1000000)
        self._label(cond, '最小成交量', 2)
        self._entry(cond, self.sc['min_volume'], 2, width=12)
        self._label(cond, '排序方式', 3)
        self._mapped_combo(cond, self.sc['sort'], SORT_OPTIONS, 3, 1,
                           width=22)
        self._label(cond, '并行进程 -x', 4)
        self._spin(cond, self.sc['threads'], 4, width=6, from_=1, to=6)

        opt = self._lf(body, '过滤与通知', 1, 1)
        opt.columnconfigure(1, weight=1)
        self._check(opt, '排除亏损股 --peplus', self.sc['peplus'], 0, 0)
        self._check(opt, '排除 ST --nost', self.sc['nost'], 0, 1)
        self._check(opt, '排除市值<50亿 --nolowcap', self.sc['nolowcap'],
                    1, 0)
        self._check(opt, '次日触发价 --forecast', self.sc['forecast'], 1, 1)
        self._check(opt, '钉钉通知 --notify', self.sc['notify'], 2, 0)
        self._source_row(opt, self.sc['src'], 3)
        self._label(opt, '买入序号 -sn', 4)
        self._spin(opt, self.sc['seqnum'], 4, width=8, from_=0, to=1000)
        self._label(opt, '最大持股天数', 5)
        self._spin(opt, self.sc['maxholddays'], 5, width=8, from_=1,
                   to=100000)
        ttk.Label(opt, text='未指定排序时默认随机抽样；随机按板块比例 6:5:5:2',
                  style='Hint.TLabel').grid(row=6, column=0, columnspan=2,
                                            sticky='w', pady=(4, 0))

    # ---------- 回测 ----------

    def _build_backtest_tab(self):
        tab = self.tab_frames['backtest']
        body = tab.body
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        src = self._lf(body, '股票来源', 0, 0, colspan=2)
        src.columnconfigure(1, weight=1)
        self._label(src, '代码 -s', 0)
        self._entry(src, self.bt['symbol'], 0, 1, colspan=3)
        self._label(src, '股票池 -sf', 1)
        self._file_entry(src, self.bt['file'], 1, 1, colspan=3)
        self._check(src, '随机抽样 100 只 --random（忽略 -s/-sf）',
                    self.bt['random'], 2, 0, colspan=4)
        ttk.Label(src, text='全部留空 = 默认 input/pool.txt',
                  style='Hint.TLabel').grid(row=3, column=0, columnspan=5,
                                            sticky='w')

        strat = self._lf(body, '策略与区间', 1, 0)
        self.bt_sel = StrategySelector(
            strat, allow_index=False,
            default=self.strategy_vars['backtest'].get())
        self.bt_sel.grid(row=0, column=0, columnspan=4, sticky='ew')
        ttk.Separator(strat, orient='horizontal').grid(
            row=1, column=0, columnspan=4, sticky='ew', pady=6)
        self._date_controls(strat, self.bt['start'], self.bt['end'], 2)

        params = self._lf(body, '资金与输出', 1, 1)
        params.columnconfigure(1, weight=1)
        self._label(params, '初始资金 -c（万元）', 0)
        self._spin(params, self.bt['capital'], 0, width=10, from_=1,
                   to=1000000)
        self._label(params, '图表目录 -o', 1)
        self._file_entry(params, self.bt['output'], 1, mode='dir',
                         title='选择图表输出目录')
        self._check(params, '生成图表 --chart', self.bt['chart'], 2, 0)
        self._check(params, '固定资金 --fixcapital', self.bt['fixcapital'],
                    3, 0)
        self._check(params, '逐股报告 --verbose', self.bt['verbose'], 4, 0)

        adv = self._lf(body, '高级', 2, 0, colspan=2)
        adv.columnconfigure(1, weight=1)
        self._label(adv, '单持仓轮动 --onestock', 0, 0)
        self._mapped_combo(adv, self.bt['onestock'], ONESTOCK, 0, 1,
                           width=26)
        self._source_row(adv, self.bt['src'], 1)
        self._label(adv, '买入序号 -sn', 2, 0)
        self._spin(adv, self.bt['seqnum'], 2, width=8, from_=0, to=1000)
        self._label(adv, '最大持股 --maxholddays', 3, 0)
        self._spin(adv, self.bt['maxholddays'], 3, width=8, from_=1,
                   to=100000)

    # ---------- 对比 ----------

    def _build_compare_tab(self):
        tab = self.tab_frames['compare']
        body = tab.body
        body.columnconfigure(0, weight=1)

        params = self._lf(body, '参数', 0, 0)
        params.columnconfigure(1, weight=1)
        self._label(params, '股票代码 -s（必需）', 0)
        self._entry(params, self.cp['symbol'], 0, 1, colspan=3)
        self._date_controls(params, self.cp['start'], self.cp['end'], 1)
        self._label(params, '初始资金 -c（万元）', 2)
        self._spin(params, self.cp['capital'], 2, width=10, from_=1,
                   to=1000000)
        self._source_row(params, self.cp['src'], 3)
        self._label(params, '买入序号 -sn', 4)
        self._spin(params, self.cp['seqnum'], 4, width=8, from_=0, to=1000)
        self._label(params, '最大持股 --maxholddays', 5)
        self._spin(params, self.cp['maxholddays'], 5, width=8, from_=1,
                   to=100000)
        self._check(params, '生成对比图 --chart', self.cp['chart'], 6, 0,
                    colspan=2)
        ttk.Label(params, text='对比会同时运行所有个股策略，输出夏普/收益/回撤排名',
                  style='Hint.TLabel').grid(row=7, column=0, columnspan=4,
                                            sticky='w', pady=(4, 0))

    # ---------- 指标 ----------

    def _build_indicators_tab(self):
        tab = self.tab_frames['indicators']
        body = tab.body
        body.columnconfigure(0, weight=1)

        params = self._lf(body, '参数', 0, 0)
        params.columnconfigure(1, weight=1)
        self._label(params, '股票代码 -s（必需）', 0)
        self._entry(params, self.ind['symbol'], 0, 1, colspan=3)
        self._date_controls(params, self.ind['start'], self.ind['end'], 1)
        self._label(params, '导出 CSV -o', 2)
        self._file_entry(params, self.ind['output'], 2, 1, colspan=3,
                         mode='save', title='导出因子 CSV')
        self._source_row(params, self.ind['src'], 3)
        self._check(params, '生成全因子 K 线图 --chart', self.ind['chart'],
                    4, 0, colspan=2)
        ttk.Label(params, text='不指定 CSV 时直接打印到运行日志；'
                              '--chart 输出 HTML 到 report/',
                  style='Hint.TLabel').grid(row=5, column=0, columnspan=4,
                                            sticky='w', pady=(4, 0))

    # ---------- 工具 ----------

    def _build_tools_tab(self):
        tab = self.tab_frames['tools']
        body = tab.body
        body.columnconfigure(0, weight=1)

        env = self._lf(body, '环境状态', 0, 0)
        env.columnconfigure(1, weight=1)
        self.env_labels = {}
        rows = [
            ('version', '版本'),
            ('root', '项目目录'),
            ('sqlite', 'SQLite 数据源'),
            ('mysql', 'MySQL'),
        ]
        for i, (key, label) in enumerate(rows):
            self._label(env, label, i, 0)
            lbl = tk.Label(env, text='检测中…', bg=DARK_BG, fg=AXIS_TXT,
                           anchor='w', justify='left', font=UI_FONT)
            lbl.grid(row=i, column=1, sticky='ew', pady=2)
            self.env_labels[key] = lbl
        self.env_labels['version'].config(text=f'v{__version__}')
        self.env_labels['root'].config(text=APP_ROOT)
        ttk.Button(env, text='刷新状态', command=self._refresh_env_async).grid(
            row=len(rows), column=1, sticky='w', pady=(8, 0))

        shortcuts = self._lf(body, '快捷操作', 1, 0)
        ttk.Button(shortcuts, text='打开 report/ 报告目录',
                   command=self._open_report_dir).grid(row=0, column=0,
                                                       sticky='w', padx=2,
                                                       pady=3)
        ttk.Button(shortcuts, text='打开 output/ 图表目录',
                   command=self._open_output_dir).grid(row=0, column=1,
                                                       sticky='w', padx=2,
                                                       pady=3)
        ttk.Button(shortcuts, text='打开股票池 input/pool.txt',
                   command=lambda: self._open_path(
                       os.path.join(APP_ROOT, 'input', 'pool.txt'))
                   ).grid(row=1, column=0, sticky='w', padx=2, pady=3)
        ttk.Button(shortcuts, text='打开配置 squant.toml',
                   command=lambda: self._open_path(
                       os.path.join(APP_ROOT, 'squant.toml'))
                   ).grid(row=1, column=1, sticky='w', padx=2, pady=3)
        ttk.Button(shortcuts, text='清空运行日志',
                   command=self._clear_log).grid(row=2, column=0, sticky='w',
                                                 padx=2, pady=3)

        help_box = self._lf(body, '命令行速查（CLI 仍然完整可用）', 2, 0)
        help_box.columnconfigure(0, weight=1)
        text = tk.Text(help_box, height=11, bg=LOG_BG, fg=FG_MAIN,
                       insertbackground=FG_MAIN, relief='flat', bd=0,
                       wrap='none', font=MONO_FONT, padx=8, pady=6)
        ysb = ttk.Scrollbar(help_box, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=ysb.set)
        ysb.pack(side='right', fill='y')
        text.pack(side='left', fill='both', expand=True)
        text.insert('1.0', CLI_HELP_TEXT)
        text.configure(state='disabled')

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, sticky='w', pady=(4, 0))
        for cmd in ('analyze', 'scan', 'backtest', 'compare', 'indicators'):
            ttk.Button(buttons, text=f'{cmd} --help', width=16,
                       command=lambda c=cmd: self._show_help(c)).pack(
                side='left', padx=(0, 4))

    # ---------- 命令构建 ----------

    def _build_initial_tab(self):
        if self._closing:
            return
        self._ensure_tab(self._current_tab())

    def _on_tab_changed(self, _event=None):
        name = self._current_tab()
        self._ensure_tab(name)
        self._update_preview()

    def _ensure_tab(self, name):
        if name in self.built_tabs:
            return
        builder = getattr(self, f'_build_{name}_tab', None)
        if builder is None:
            return
        lbl = self.loading_labels.pop(name, None)
        if lbl is not None:
            try:
                lbl.destroy()
            except Exception:
                pass
        # tk.Spinbox 在变量为空时会自动填入 from_ 的值；记录构建前的空值，
        # 构建后恢复，避免可选参数被误写成 1/0。
        empty_before = {
            k: v for k, v in self.state.items()
            if isinstance(v, tk.StringVar) and not str(v.get()).strip()
        }
        builder()
        for key, var in empty_before.items():
            if str(var.get()).strip() in ('0', '1'):
                var.set('')
        self.built_tabs.add(name)
        if name == 'tools' and hasattr(self, 'env_labels'):
            self._apply_env(getattr(self, '_env_info', {}) or {})

    def _strategy_value(self, tab):
        attr = self._strategy_attrs.get(tab)
        sel = getattr(self, attr, None) if attr else None
        if sel is not None:
            value = sel.get()
            self.strategy_vars[tab].set(value)
            return value
        return self.strategy_vars[tab].get()

    def _current_tab(self):
        try:
            return self.tab_names[self.notebook.index(self.notebook.select())]
        except Exception:
            return 'analyze'

    def _current_args(self):
        tab = self._current_tab()
        if tab == 'analyze':
            return self._args_analyze()
        if tab == 'scan':
            return self._args_scan()
        if tab == 'backtest':
            return self._args_backtest()
        if tab == 'compare':
            return self._args_compare()
        if tab == 'indicators':
            return self._args_indicators()
        return []

    @staticmethod
    def _append(args, flag, value, default=None):
        text = str(value or '').strip()
        if not text:
            return
        if default is not None and text == str(default):
            return
        args.extend([flag, text])

    @staticmethod
    def _append_flag(args, flag, value):
        if value:
            args.append(flag)

    def _args_analyze(self):
        v = self.an
        a = ['analyze']
        self._append(a, '-s', v['symbol'].get())
        self._append(a, '-sf', v['file'].get())
        self._append(a, '-h', v['hot'].get())
        self._append(a, '-st', v['start'].get())
        self._append(a, '-e', v['end'].get())
        strat = self._strategy_value('analyze')
        if strat:
            a.extend(['-g', strat])
        self._append_flag(a, '-i', v['index'].get())
        self._append(a, '-c', v['capital'].get(), default='100')
        self._append(a, '-o', v['output'].get(), default='./output')
        self._append(a, '-of', v['output_file'].get())
        self._append(a, '-x', v['threads'].get(), default='6')
        self._append(a, '-m', v['mode'].get(), default='0')
        self._append(a, '-f', v['filter'].get())
        self._append_flag(a, '--chart', v['chart'].get())
        self._append_flag(a, '--forecast', v['forecast'].get())
        self._append_flag(a, '--notify', v['notify'].get())
        self._append(a, '--plan-time', v['plan_time'].get())
        self._append(a, '--src', v['src'].get(), default='auto')
        self._append(a, '-sn', v['seqnum'].get(), default='0')
        self._append(a, '--maxholddays', v['maxholddays'].get())
        return a

    def _args_scan(self):
        v = self.sc
        a = ['scan']
        strat = self._strategy_value('scan')
        if strat:
            a.extend(['-g', strat])
        a.extend(['-t', v['top'].get().strip() or '10'])
        a.extend(['-o', v['offset'].get().strip() or '0'])
        self._append(a, '--min-volume', v['min_volume'].get())
        sort = v['sort'].get() or 'random'
        if sort == 'random':
            a.append('--random')
        else:
            a.append('--' + sort)
        self._append_flag(a, '--peplus', v['peplus'].get())
        self._append_flag(a, '--nost', v['nost'].get())
        self._append_flag(a, '--nolowcap', v['nolowcap'].get())
        self._append(a, '-x', v['threads'].get(), default='6')
        self._append_flag(a, '--forecast', v['forecast'].get())
        self._append_flag(a, '--notify', v['notify'].get())
        self._append(a, '--src', v['src'].get(), default='auto')
        self._append(a, '-sn', v['seqnum'].get(), default='0')
        self._append(a, '--maxholddays', v['maxholddays'].get())
        return a

    def _args_backtest(self):
        v = self.bt
        a = ['backtest']
        if v['random'].get():
            a.append('--random')
        else:
            self._append(a, '-s', v['symbol'].get())
            self._append(a, '-sf', v['file'].get())
        strat = self._strategy_value('backtest')
        if strat:
            a.extend(['-g', strat])
        self._append(a, '-st', v['start'].get())
        self._append(a, '-e', v['end'].get())
        self._append(a, '-c', v['capital'].get(), default='100')
        self._append(a, '-o', v['output'].get(), default='./output')
        self._append_flag(a, '--chart', v['chart'].get())
        self._append(a, '--onestock', v['onestock'].get())
        self._append_flag(a, '--fixcapital', v['fixcapital'].get())
        self._append_flag(a, '--verbose', v['verbose'].get())
        self._append(a, '--src', v['src'].get(), default='auto')
        self._append(a, '-sn', v['seqnum'].get(), default='0')
        self._append(a, '--maxholddays', v['maxholddays'].get())
        return a

    def _args_compare(self):
        v = self.cp
        a = ['compare']
        self._append(a, '-s', v['symbol'].get())
        self._append(a, '-st', v['start'].get())
        self._append(a, '-e', v['end'].get())
        self._append(a, '-c', v['capital'].get(), default='100')
        self._append_flag(a, '--chart', v['chart'].get())
        self._append(a, '--src', v['src'].get(), default='auto')
        self._append(a, '-sn', v['seqnum'].get(), default='0')
        self._append(a, '--maxholddays', v['maxholddays'].get())
        return a

    def _args_indicators(self):
        v = self.ind
        a = ['indicators']
        self._append(a, '-s', v['symbol'].get())
        self._append(a, '-st', v['start'].get())
        self._append(a, '-e', v['end'].get())
        self._append(a, '-o', v['output'].get())
        self._append_flag(a, '--chart', v['chart'].get())
        self._append(a, '--src', v['src'].get(), default='auto')
        return a

    def _cmd_text(self):
        args = self._current_args()
        if not args:
            return ''
        if getattr(sys, 'frozen', False):
            exe = 'squant'
        else:
            exe = 'python main.py'
        return exe + ' ' + ' '.join(shlex.quote(x) for x in args)

    def _update_preview(self):
        try:
            self.cmd_var.set(self._cmd_text())
        except Exception as exc:
            self.cmd_var.set(f'命令构建失败: {exc}')

    def _preview_loop(self):
        if self._closing:
            return
        self._update_preview()
        self._preview_after = self.root.after(500, self._preview_loop)

    # ---------- 校验与运行 ----------

    def _symbol_value(self, tab):
        if tab == 'analyze':
            return self.an['symbol'].get().strip()
        if tab == 'backtest':
            return self.bt['symbol'].get().strip()
        if tab == 'compare':
            return self.cp['symbol'].get().strip()
        if tab == 'indicators':
            return self.ind['symbol'].get().strip()
        return ''

    def _date_pairs(self, tab):
        if tab == 'analyze':
            return [('开始日期', self.an['start']), ('结束日期', self.an['end'])]
        if tab == 'backtest':
            return [('开始日期', self.bt['start']), ('结束日期', self.bt['end'])]
        if tab == 'compare':
            return [('开始日期', self.cp['start']), ('结束日期', self.cp['end'])]
        if tab == 'indicators':
            return [('开始日期', self.ind['start']), ('结束日期', self.ind['end'])]
        return []

    def _validate(self, tab):
        if tab in ('compare', 'indicators') and not self._symbol_value(tab):
            messagebox.showwarning('缺少参数', '该功能必须填写股票代码 -s。')
            return False
        pairs = self._date_pairs(tab)
        values = []
        for label, var in pairs:
            text = var.get().strip()
            if text and not re.fullmatch(r'\d{8}', text):
                messagebox.showwarning('日期格式', f'{label} 应为 YYYYMMDD，'
                                                  f'例如 20260101。')
                return False
            values.append(text)
        if len(values) == 2 and values[0] and values[1] and values[0] > values[1]:
            messagebox.showwarning('日期范围', '开始日期不能晚于结束日期。')
            return False
        if tab == 'analyze':
            hot = self.an['hot'].get().strip()
            if hot and not hot.isdigit():
                messagebox.showwarning('参数错误', '热门股数量必须是整数。')
                return False
            plan = self.an['plan_time'].get().strip()
            if plan and not re.fullmatch(r'\d{4}', plan):
                messagebox.showwarning('参数错误', '--plan-time 应为 HHMM，'
                                                  '例如 1430。')
                return False
        if tab == 'scan':
            for label, var in (('Top N', self.sc['top']),
                               ('偏移', self.sc['offset']),
                               ('并行进程', self.sc['threads'])):
                text = var.get().strip()
                if text:
                    try:
                        int(text)
                    except ValueError:
                        messagebox.showwarning('参数错误', f'{label} 必须是整数。')
                        return False
        for label, var in (('并行进程', self.an['threads']),
                           ('分析模式', self.an['mode'])):
            text = var.get().strip()
            if text:
                try:
                    int(text)
                except ValueError:
                    messagebox.showwarning('参数错误', f'{label} 必须是整数。')
                    return False
        return True

    def _run_current(self):
        tab = self._current_tab()
        if tab == 'tools':
            messagebox.showinfo('提示', '当前是工具页，请切换到分析/扫描/回测/对比/指标页运行。')
            return
        if self.proc is not None:
            messagebox.showwarning('任务运行中', '已有任务正在运行，请先点击“停止”。')
            return
        if not self._validate(tab):
            return
        args = self._current_args()
        if not args:
            return
        self._start_command(args)

    def _start_command(self, args):
        cmd = self._build_subprocess(args)
        self._append_log('\n' + '─' * 88 + '\n', 'dim')
        self._append_log('$ ' + ' '.join(shlex.quote(x) for x in cmd) + '\n',
                         'cmd')
        self._set_running(True)
        self.status_var.set('正在运行…')
        self.env = dict(os.environ)
        self.env['PYTHONUNBUFFERED'] = '1'
        self.env['PYTHONIOENCODING'] = 'utf-8'
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=APP_ROOT, env=self.env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                errors='replace', bufsize=1)
        except Exception as exc:
            self.proc = None
            self._set_running(False)
            self._append_log(f'启动失败: {exc}\n', 'error')
            messagebox.showerror('启动失败', str(exc))
            return
        self._stop_requested = False
        self.reader_thread = threading.Thread(
            target=self._reader_worker, args=(self.proc,), daemon=True)
        self.reader_thread.start()

    def _build_subprocess(self, args):
        if getattr(sys, 'frozen', False):
            return [sys.executable] + args
        return [sys.executable, '-u', MAIN_PATH] + args

    def _reader_worker(self, proc):
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    self.msg_queue.put(('line', line.rstrip('\n')))
            code = proc.wait()
            self.msg_queue.put(('done', code))
        except Exception as exc:
            self.msg_queue.put(('error', str(exc)))

    def _stop(self):
        if self.proc is None:
            return
        self._stop_requested = True
        self._append_log('正在停止任务…\n', 'warn')
        try:
            self.proc.terminate()
        except Exception:
            pass
        proc = self.proc
        self.root.after(2500, lambda: self._kill_if_running(proc))

    def _kill_if_running(self, proc):
        try:
            if proc.poll() is None:
                proc.kill()
                self._append_log('任务未在 2.5 秒内退出，已强制结束。\n', 'warn')
        except Exception:
            pass

    def _poll_queue(self):
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == 'line':
                    self._handle_line(payload)
                elif kind == 'done':
                    self._handle_done(payload)
                elif kind == 'env':
                    self._apply_env(payload)
                elif kind == 'error':
                    self._append_log(f'运行异常: {payload}\n', 'error')
                    self._handle_done(-1)
        except queue.Empty:
            pass
        self._poll_after = self.root.after(100, self._poll_queue)

    def _handle_line(self, line):
        clean = re.sub(r'\x1b\[[0-9;]*m', '', line)
        tag = 'info'
        if any(x in clean for x in ('错误', '失败', 'Traceback', 'Error')):
            tag = 'error'
        elif any(x in clean for x in ('警告', 'WARN', 'Warning')):
            tag = 'warn'
        elif any(x in clean for x in ('完成', '已保存', '成功')):
            tag = 'ok'
        elif clean.startswith('[') or '数据源' in clean:
            tag = 'dim'
        self._append_log(clean + '\n', tag)
        stripped = clean.strip()
        if stripped:
            self.status_var.set(stripped[:120])
        self._detect_report(clean)

    def _handle_done(self, code):
        if not self._running and self.proc is None:
            return
        self.proc = None
        self.reader_thread = None
        self._set_running(False)
        if self._stop_requested:
            self.status_var.set('已停止')
            self._append_log('任务已停止。\n', 'warn')
        elif code == 0:
            self.status_var.set('完成')
            self._append_log('任务完成（退出码 0）。\n', 'ok')
            try:
                self.root.bell()
            except Exception:
                pass
        else:
            self.status_var.set(f'失败（退出码 {code}）')
            self._append_log(f'任务结束，退出码 {code}。\n', 'error')

    def _set_running(self, running):
        self._running = bool(running)
        state_run = 'disabled' if running else 'normal'
        state_stop = 'normal' if running else 'disabled'
        try:
            self.btn_run.configure(state=state_run)
            self.btn_stop.configure(state=state_stop)
            if running:
                self.progress.configure(mode='indeterminate')
                self.progress.start(12)
            else:
                self.progress.stop()
                self.progress.configure(mode='determinate', value=0)
        except Exception:
            pass

    # ---------- 日志与报告 ----------

    def _append_log(self, text, tag='info'):
        try:
            self.log.insert('end', text, tag)
            # 限制日志长度，避免长时间运行占用内存
            if int(self.log.index('end-1c').split('.')[0]) > 6000:
                self.log.delete('1.0', '1000.0')
            self.log.see('end')
        except Exception:
            pass

    def _clear_log(self):
        try:
            self.log.delete('1.0', 'end')
        except Exception:
            pass

    def _select_log(self):
        try:
            self.log.tag_add('sel', '1.0', 'end')
            self.log.mark_set('insert', '1.0')
        except Exception:
            pass

    def _copy_log(self):
        try:
            text = self.log.get('sel.first', 'sel.last')
        except tk.TclError:
            text = self.log.get('1.0', 'end-1c')
        self._copy_text(text)

    def _copy_command(self):
        self._copy_text(self.cmd_var.get())

    def _copy_text(self, text):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.status_var.set('已复制到剪贴板')
        except Exception as exc:
            messagebox.showerror('复制失败', str(exc))

    def _log_menu(self, event):
        try:
            self.log_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.log_menu.grab_release()

    def _detect_report(self, line):
        for match in re.findall(r'[^\s\'"<>]+\.html', line):
            path = match.strip().rstrip('.,;)')
            if not os.path.isabs(path):
                path = os.path.join(APP_ROOT, path)
            if os.path.exists(path):
                self.last_report = path

    def _open_last_report(self):
        if self.last_report and os.path.exists(self.last_report):
            self._open_path(self.last_report)
        else:
            self._open_dir(os.path.join(APP_ROOT, 'report'),
                           '还没有生成报告，已打开 report/ 目录。')

    def _open_report_dir(self):
        self._open_dir(os.path.join(APP_ROOT, 'report'))

    def _open_output_dir(self):
        self._open_dir(os.path.join(APP_ROOT, 'output'))

    def _open_dir(self, path, hint=''):
        os.makedirs(path, exist_ok=True)
        if hint:
            self.status_var.set(hint)
        self._open_path(path)

    def _open_path(self, path):
        path = os.path.abspath(os.path.expanduser(str(path)))
        if not os.path.exists(path):
            messagebox.showinfo('文件不存在', path)
            return
        try:
            if os.name == 'nt':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                subprocess.Popen(['xdg-open', path],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        except Exception as exc:
            messagebox.showerror('打开失败', f'{path}\n{exc}')

    def _show_help(self, command):
        if self.proc is not None:
            messagebox.showwarning('任务运行中', '请先停止当前任务。')
            return
        self._start_command([command, '--help'])

    # ---------- 环境状态 ----------

    def _refresh_env_async(self):
        self.env_var.set('环境检测中…')
        threading.Thread(target=self._refresh_env_work, daemon=True).start()

    def _refresh_env_work(self):
        info = {'version': f'v{__version__}', 'root': APP_ROOT,
                'sqlite': '未找到', 'sqlite_ok': False,
                'mysql': '未连接', 'mysql_ok': False}
        try:
            from core.sqlite_source import (sqlite_source_available,
                                            sqlite_source_path)
            path = sqlite_source_path()
            if path and sqlite_source_available():
                info['sqlite'] = path
                info['sqlite_ok'] = True
            elif path:
                info['sqlite'] = f'{path}（不可用）'
            else:
                info['sqlite'] = '未配置/未找到 stock_cache.db'
        except Exception as exc:
            info['sqlite'] = f'检测失败: {exc}'
        try:
            from core.db import check_db_connection
            ok = check_db_connection(timeout=2)
            info['mysql_ok'] = bool(ok)
            info['mysql'] = '已连接' if ok else '未连接（自动回退 SQLite/网络）'
        except Exception as exc:
            info['mysql'] = f'检测失败: {exc}'
        self.msg_queue.put(('env', info))

    def _apply_env(self, info):
        self._env_info = info
        if not info:
            return
        if info:
            for key, lbl in getattr(self, 'env_labels', {}).items():
                text = info.get(key, '')
                lbl.configure(text=text,
                              fg=GREEN if (key == 'sqlite' and info.get('sqlite_ok'))
                              else (GREEN if (key == 'mysql' and info.get('mysql_ok'))
                                    else AXIS_TXT))
        if info.get('sqlite_ok'):
            self.sqlite_badge.configure(text='SQLite: 已连接', fg=GREEN)
        else:
            self.sqlite_badge.configure(text='SQLite: 未连接', fg=YELLOW)
        if info.get('mysql_ok'):
            self.mysql_badge.configure(text='MySQL: 已连接', fg=GREEN)
        else:
            self.mysql_badge.configure(text='MySQL: 未连接', fg=AXIS_TXT)
        self.env_var.set(
            'SQLite: ' + ('就绪' if info.get('sqlite_ok') else '不可用') +
            '  |  MySQL: ' + ('就绪' if info.get('mysql_ok') else '未连接'))

    # ---------- 状态保存 ----------

    @staticmethod
    def _state_path():
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(
            os.path.expanduser('~'), '.config')
        return os.path.join(base, 'stock-quant', 'gui.json')

    def _save_state(self):
        data = {'version': __version__,
                'geometry': self.root.geometry()}
        for key, var in self.state.items():
            try:
                data[key] = var.get()
            except Exception:
                pass
        try:
            data['strategy'] = {
                'analyze': self._strategy_value('analyze'),
                'scan': self._strategy_value('scan'),
                'backtest': self._strategy_value('backtest'),
            }
        except Exception:
            pass
        try:
            path = self._state_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_state(self):
        try:
            path = self._state_path()
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return
        for key, var in self.state.items():
            if key not in data:
                continue
            try:
                if isinstance(var, tk.BooleanVar):
                    var.set(bool(data[key]))
                else:
                    var.set(str(data[key]))
            except Exception:
                pass
        strategy = data.get('strategy') or {}
        for key in ('analyze', 'scan', 'backtest'):
            if key not in strategy:
                continue
            value = str(strategy[key])
            self.strategy_vars[key].set(value)
            sel = getattr(self, self._strategy_attrs.get(key, ''), None)
            if sel is not None:
                try:
                    sel.set(value)
                except Exception:
                    pass
        geometry = data.get('geometry')
        if isinstance(geometry, str) and geometry:
            try:
                self.root.geometry(geometry)
            except Exception:
                pass

    def _sync_index_mode(self):
        sel = getattr(self, 'an_sel', None)
        if sel is None:
            return
        try:
            sel.set_mode(bool(self.an['index'].get()))
        except Exception:
            pass

    def _shutdown(self):
        self._closing = True
        for attr in ('_poll_after', '_preview_after', '_build_after'):
            after_id = getattr(self, attr, None)
            if after_id:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            self.root.destroy()
        except Exception:
            pass

    def _on_close(self):
        if self.proc is not None:
            if not messagebox.askyesno('任务运行中', '任务仍在运行，确定退出吗？'):
                return
            try:
                self.proc.terminate()
            except Exception:
                pass
        self._save_state()
        self._shutdown()


CLI_HELP_TEXT = """\
python main.py analyze    -s 000725 -g boll,macd --chart --forecast
python main.py analyze    -sf input/pool.txt -g all -m 2
python main.py scan       --random --nost --peplus -t 10 -g macd,boll
python main.py backtest   -sf input/pool.txt -g all --chart
python main.py backtest   -s 002920 --onestock 1 --fixcapital
python main.py compare    -s 002920 --chart
python main.py indicators -s 000725 --chart

python main.py --help                    查看 CLI 总帮助
python main.py <command> --help          查看子命令全部参数
python main.py                            启动本图形界面

数据源：
  --src auto      本地 SQLite 优先，缺失自动回退网络
  --src sqlite    强制优先 stock_predict 缓存
  --src sina      新浪 / baostock / eastmoney / akshare
"""


def launch_gui():
    try:
        root = tk.Tk(className='stock-quant')
    except Exception as exc:
        raise RuntimeError(f'无法创建图形窗口（可能没有图形环境）: {exc}') from exc
    app = StockQuantGUI(root)
    if os.environ.get('STOCK_QUANT_GUI_TEST'):
        root.after(1500, app._shutdown)
    root.mainloop()
    return True


if __name__ == '__main__':
    try:
        launch_gui()
    except Exception as exc:
        print(f'启动图形界面失败: {exc}', file=sys.stderr)
        sys.exit(1)
