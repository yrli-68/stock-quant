# 股票量化分析软件 - CLI 主入口
# 提供命令行交互界面，支持股票分析、回测、策略对比等功能

import click
from click.core import ParameterSource
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
import sys
import os
import json
import io
import contextlib
import fnmatch
import warnings
import re
import shlex
import time
import unicodedata

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入核心模块
from core.data_fetcher import (DataFetcher, set_primary_source,
                                preload_market_trade_dates,
                                set_market_trade_dates_override)
from core.indicators import (add_all_indicators, classify_trend,
                             calc_rsi, calc_bollinger, calc_macd, calc_adxr,
                             calc_atr, update_atr_trailing_stop,
                             adjust_atr_stop_multiplier)
from core.backtest import BacktestEngine
from core.risk import risk_report
from core.strategy import Strategy
from core.thresholds import get_threshold
from core.config import load_config, app_root
from core.db import check_db_connection, fetch_valuation, infer_asset_type
from core.constants import TRADING_DAYS_PER_YEAR
from core.market_trend import (analyze_market_trend, analyze_stock_trend,
                               classify_stock_trend_series,
                               market_trend_states,
                               get_market_trend_index, get_index_name)
from _version import __version__
from strategies.ma_cross import MACrossStrategy
from strategies.macd_strategy import MACDStrategy
from strategies.macd1_strategy import MACD1Strategy
from strategies.bollinger_strategy import BollingerStrategy
from strategies.boma_strategy import (BOMAStrategy, SIGNAL_MACD_SELL,
                                      SIGNAL_MACD1_SELL, SIGNAL_MACD2_SELL,
                                      SIGNAL_BOLL1_SELL, SIGNAL_BOLL2_SELL,
                                      _sw_enabled)
from strategies.kdj_strategy import KDJStrategy
from strategies.quality_value_factor import QualityValueFactorStrategy
from strategies.hold_strategy import HoldStrategy
from strategies.sar_strategy import SARStrategy
from visualization.charts import ChartGenerator


def _silence_py_mini_racer_shutdown_error():
    """屏蔽 py_mini_racer 在解释器退出阶段 __del__ 的噪声异常。"""
    try:
        from py_mini_racer.py_mini_racer import MiniRacer
    except Exception:
        return
    if getattr(MiniRacer, '_squant_safe_del', False):
        return
    original_del = MiniRacer.__del__

    def _safe_del(self):
        try:
            original_del(self)
        except Exception:
            pass

    MiniRacer.__del__ = _safe_del
    MiniRacer._squant_safe_del = True


_silence_py_mini_racer_shutdown_error()

# 导入表格格式化库
def _display_width(text):
    """终端显示宽度：中文/全角等宽字符按 2 个字符宽度计算。"""
    text = re.sub(r'\x1b\[[0-9;]*m', '', str(text))
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1
    return width


try:
    import tabulate as _tabulate_module
    from tabulate import tabulate
    HAS_TABULATE = True
    # tabulate 默认依赖 wcwidth 才能正确处理中文宽度；未安装时用标准库兜底
    if not getattr(_tabulate_module, 'WIDE_CHARS_MODE', False):
        class _WcWidthShim:
            @staticmethod
            def wcswidth(s):
                return _display_width(s)

        _tabulate_module.wcwidth = _WcWidthShim
        _tabulate_module.WIDE_CHARS_MODE = True
except ImportError:
    HAS_TABULATE = False

# 忽略非关键警告
warnings.filterwarnings('ignore')

# ============================================================================
# 策略注册表
# ============================================================================

STRATEGY_MAP = {
    'ma1030': ('MA1030', MACrossStrategy),
    'macd': ('MACD', MACDStrategy),
    'macd1': ('MACD1', MACD1Strategy),
    'boll': ('BOLL', BollingerStrategy),
    'boma': ('BOMA', BOMAStrategy),
    'kdj': ('KDJ', KDJStrategy),
    'sar': ('SAR', SARStrategy),
    'quality_value': ('多因子', QualityValueFactorStrategy),
    'hold': ('持有', HoldStrategy),
}

# 指数模式使用通用策略（原指数专属策略已移除）
INDEX_STRATEGY_MAP = dict(STRATEGY_MAP)

ALL_STRATEGIES = ['ma1030', 'macd', 'macd1', 'boll', 'boma', 'kdj', 'sar', 'hold']
# -g all 使用的策略集合（排除 ma1030，仍可用 -g 单独指定）
ALL_SELECTION = [k for k in ALL_STRATEGIES if k != 'ma1030']
ALL_INDEX_STRATEGIES = list(ALL_SELECTION)

# scan 多策略汇总信号表使用的短栏名（仅用于汇总表列头，不影响其它输出）
_SCAN_SHORT_NAMES = {
    'ma1030': 'MA1030',
    'macd': 'MACD',
    'macd1': 'MACD1',
    'boll': 'BOLL',
    'boma': 'BOMA',
    'kdj': 'KDJ',
    'sar': 'SAR',
    'quality_value': '多因子',
    'hold': '持有',
}

# ETF/指数基金专用策略（适配低波动、趋势跟随特性）
ETF_STRATEGY_MAP = {
    'ma1030': ('ETF均线', lambda: MACrossStrategy(fast_period=10, slow_period=40, name='ETF MACross')),
    'macd': ('ETF MACD', lambda: MACDStrategy(fast=16, slow=32, signal=12, name='ETF MACD')),
    'macd1': ('ETF MACD1', lambda: MACD1Strategy(name='ETF MACD1')),
    'boll': ('ETF BOLL', lambda: BollingerStrategy(period=20, std=2.5, name='ETF Bollinger')),
    'boma': ('ETF BOMA', lambda: BOMAStrategy(boll_period=20, boll_std=2.5, macd_fast=16, macd_slow=32, macd_signal=12, name='ETF BOMA')),
    'kdj': ('ETF KDJ', lambda: KDJStrategy(n=9, m1=3, m2=3, oversold=20, overbought=80, name='ETF KDJ')),
    'sar': ('ETF SAR', lambda: SARStrategy(name='ETF SAR')),
    'quality_value': ('ETF多因子', lambda: QualityValueFactorStrategy(stock_type='auto', name='ETF QualityValue')),
    'hold': ('ETF持有', lambda: HoldStrategy(name='ETF Hold')),
}
ALL_ETF_STRATEGIES = ['ma1030', 'macd', 'macd1', 'boll', 'boma', 'kdj', 'sar', 'quality_value', 'hold']
ALL_ETF_SELECTION = [k for k in ALL_ETF_STRATEGIES if k != 'ma1030']

# 策略 key 到类的映射
_STRATEGY_CLASS_MAP = {
    'ma1030': MACrossStrategy,
    'macd': MACDStrategy,
    'macd1': MACD1Strategy,
    'boll': BollingerStrategy,
    'boma': BOMAStrategy,
    'kdj': KDJStrategy,
    'sar': SARStrategy,
    'quality_value': QualityValueFactorStrategy,
    'hold': HoldStrategy,
}

_quant_config = None


def _load_quant_config():
    """加载 squant.toml 的 [tool.stock-quant] 配置，失败时返回空字典"""
    global _quant_config
    if _quant_config is None:
        _quant_config = load_config()
    return _quant_config


# 固定资金回测模式：由 --fixcapital 选项开启；开启后每次买入均按初始资金额度买入，
# 上一轮交易的盈亏不影响本轮交易资金
_FIX_CAPITAL = False


def _set_fix_capital(v):
    global _FIX_CAPITAL
    _FIX_CAPITAL = bool(v)


_DOWNLOSS_SELL_THRESHOLD = 0.02
_DOWNLOSS_DAILY_DROP_THRESHOLD = 0.05
_DOWNLOSS_RANGE_MAX_DRAWDOWN = 0.10

_ATR_STOP_ENABLED = True
_ATR_STOP_MULTIPLIER = 2.0


def _set_maxdrawdown(ratio):
    global _ATR_STOP_ENABLED, _ATR_STOP_MULTIPLIER
    try:
        _ATR_STOP_MULTIPLIER = max(0.1, float(ratio))
    except (TypeError, ValueError):
        _ATR_STOP_MULTIPLIER = 2.0
    _ATR_STOP_ENABLED = True


_DOWNLOSS_SELL = False


def _set_downloss_sell(v):
    global _DOWNLOSS_SELL
    _DOWNLOSS_SELL = bool(v)


def _market_states_for_df(df):
    """获取与 df 日期对齐、按个股市值选择指数的大盘趋势状态。"""
    if df is None or len(df) == 0:
        return None
    try:
        start = pd.Timestamp(df.index[0])
        end = pd.Timestamp(df.index[-1])
        symbol = str(df.attrs.get('symbol') or '').strip()
        index_code = _symbol_index_code(symbol)
        states = _TREND_STATES_PRECOMPUTED.get(index_code)
        if states is None:
            states = market_trend_states(f'{start.year}-01-01',
                                         end.strftime('%Y-%m-%d'), index_code)
        if states is None or len(states) == 0:
            return None
        return states.reindex(df.index, method='ffill')
    except Exception:
        return None


def _apply_market_regime_to_signals(signals, strategy, df, market_states=None,
                                    no_force_signals=None):
    """将跌势规则应用到策略原始信号。

    - 跌势：买入信号置 0。
    """
    if signals is None or len(signals) == 0:
        return signals
    states = market_states if market_states is not None else _market_states_for_df(df)
    if states is None or len(states) == 0:
        return signals
    try:
        aligned = states.reindex(signals.index, method='ffill')
    except Exception:
        return signals

    down_block = (aligned == '跌势')
    if not bool(down_block.any()):
        return signals

    out = signals.copy()
    mask = (out > 0) & down_block
    if mask.any():
        out[mask] = 0.0
    out.attrs = dict(getattr(signals, 'attrs', {}))
    return out


def _apply_downloss_sell_to_signals(signals, df, market_states=None):
    """在策略信号上模拟持仓，执行跌势亏损/单日大跌止损。"""
    if not _DOWNLOSS_SELL or signals is None or len(signals) == 0:
        return signals
    states = market_states if market_states is not None else _market_states_for_df(df)
    if states is None or len(states) == 0:
        return signals
    try:
        aligned = states.reindex(signals.index, method='ffill')
        close = pd.to_numeric(df['close'], errors='coerce').reindex(signals.index)
    except Exception:
        return signals

    out = signals.copy()
    in_pos = False
    entry = None
    peak = None
    prev = None
    for i in range(len(out)):
        c = close.iloc[i]
        if pd.isna(c):
            continue
        c = float(c)
        val = float(out.iloc[i])
        state = aligned.iloc[i]
        if not in_pos:
            if val > 0:
                in_pos = True
                entry = c
                peak = c
        else:
            if val < 0:
                in_pos = False
                entry = None
                peak = None
            elif state == '震荡':
                if peak is None or c > peak:
                    peak = c
                if peak > 0 and (peak - c) / peak > _DOWNLOSS_RANGE_MAX_DRAWDOWN:
                    out.iloc[i] = -1.0
                    in_pos = False
                    entry = None
                    peak = None
            elif state == '跌势' and entry is not None and entry > 0:
                loss = c / entry - 1.0
                day_ret = (c / prev - 1.0) if prev is not None and prev > 0 else None
                if (loss < -_DOWNLOSS_SELL_THRESHOLD
                        or (day_ret is not None
                            and day_ret < -_DOWNLOSS_DAILY_DROP_THRESHOLD)):
                    out.iloc[i] = -1.0
                    in_pos = False
                    entry = None
                    peak = None
        prev = c
    out.attrs = dict(getattr(signals, 'attrs', {}))
    return out


def _generate_effective_signals(strategy, df, apply_market_regime=True,
                                market_states=None, apply_downloss_signal=True):
    """生成最终交易信号

    - BOLL/BOMA：在策略内部同步 -sn 与ATR 移动止损；
    - MACD：内部同步ATR 移动止损；
    - 其余策略：直接生成信号，忽略 -sn；
    - 最后应用大盘趋势：跌势日禁止买入。
    """
    if apply_market_regime and market_states is None:
        market_states = _market_states_for_df(df)
    if market_states is not None:
        df.attrs['market_states'] = market_states

    if getattr(strategy, 'integrates_buy_filter', False):
        signals = strategy.generate_signals(df)
        signals = _mask_signal_warmup(signals)
    else:
        signals = _mask_signal_warmup(strategy.generate_signals(df))
    if apply_market_regime:
        signals = _apply_market_regime_to_signals(
            signals, strategy, df, market_states=market_states)
    signals = _apply_stock_limit(signals, df)
    return signals


def _get_position_style():
    """从 squant.toml 读取回测持仓模式（position_style），默认 'full'

    合法值：'full'（全仓）/ 'fraction'（按信号比例），非法时回退默认并告警。
    """
    style = str(_load_quant_config().get('position_style', 'full')).strip().lower()
    if style not in ('full', 'fraction'):
        click.echo(click.style(f'  警告: 配置 position_style="{style}" 非法，使用默认 full', fg='yellow'))
        style = 'full'
    return style


# 判断是否为 ETF/指数基金（5开头上海ETF / 159开头深圳ETF / 16开头LOF等）
def _is_etf(symbol):
    return symbol.startswith(('5', '15', '16', '51', '58', '588')) or symbol[:3] == '159'


def _is_market_open():
    """判断当前是否处于 A 股交易时段（9:30-11:30, 13:00-15:00，周一至周五）"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    morning_start = datetime.strptime('09:30', '%H:%M').time()
    morning_end = datetime.strptime('11:30', '%H:%M').time()
    afternoon_start = datetime.strptime('13:00', '%H:%M').time()
    afternoon_end = datetime.strptime('15:00', '%H:%M').time()
    return (morning_start <= t <= morning_end) or (afternoon_start <= t <= afternoon_end)

# ============================================================================
# 辅助函数
# ============================================================================


def _print_header(title, symbol=''):
    """打印格式化的报告头部"""
    click.echo()
    click.echo(click.style('=' * 56, fg='cyan', bold=True))
    click.echo(click.style(f'  {title}', fg='cyan', bold=True))
    if symbol:
        click.echo(click.style(f'  股票: {symbol}', fg='cyan', bold=True))
    click.echo(click.style('=' * 56, fg='cyan', bold=True))
    click.echo()


def _print_section(title):
    """打印章节标题"""
    click.echo(click.style(f'\n  {title}', fg='yellow', bold=True))
    click.echo(click.style('  ' + '-' * 40, fg='yellow'))


def _looks_numeric(text):
    """判断表格单元格是否为可右对齐的数值（允许千分位与百分号）。"""
    t = re.sub(r'\x1b\[[0-9;]*m', '', str(text)).strip()
    t = t.replace(',', '').replace('%', '')
    if not t or t in ('—', '-', '/'):
        return False
    try:
        float(t)
        return True
    except ValueError:
        return False


def _print_table(headers, rows, tablefmt='grid', floatfmt='.4f',
                 disable_numparse=False):
    """使用 tabulate 格式化打印表格（中文按 2 字符宽度对齐）"""
    if HAS_TABULATE:
        kwargs = {}
        if disable_numparse and rows and headers:
            ncol = len(headers)
            colalign = []
            for i in range(ncol):
                vals = [r[i] for r in rows if i < len(r)
                        and str(r[i]).strip() not in ('', '—', '-', '/')]
                colalign.append('right' if vals and all(
                    _looks_numeric(v) for v in vals) else 'left')
            kwargs['colalign'] = tuple(colalign)
        click.echo(tabulate(rows, headers=headers, tablefmt=tablefmt,
                            floatfmt=floatfmt, disable_numparse=disable_numparse,
                            **kwargs))
    else:
        _print_simple_table(headers, rows)


def _print_simple_table(headers, rows):
    """无 tabulate 时的 CJK 宽度感知表格（grid 风格）。"""
    headers = [str(h) for h in headers]
    ncol = len(headers)
    norm_rows = []
    for row in rows:
        cells = [str(c) for c in row]
        if len(cells) < ncol:
            cells += [''] * (ncol - len(cells))
        norm_rows.append(cells[:ncol])

    widths = [_display_width(h) for h in headers]
    for cells in norm_rows:
        for i, c in enumerate(cells):
            widths[i] = max(widths[i], _display_width(c))

    def _is_num(s):
        t = s.replace(',', '').replace('%', '').strip()
        if not t:
            return False
        try:
            float(t)
            return True
        except ValueError:
            return False

    aligns = []
    for i in range(ncol):
        vals = [r[i] for r in norm_rows
                if r[i].strip() not in ('', '—', '-', '/')]
        aligns.append('right' if vals and all(_is_num(v) for v in vals)
                      else 'left')

    def _cell(text, width, align):
        pad = max(0, width - _display_width(text))
        if align == 'right':
            return ' ' * pad + text
        return text + ' ' * pad

    def _border(ch='-'):
        return '+' + '+'.join(ch * (w + 2) for w in widths) + '+'

    def _line(cells):
        return '| ' + ' | '.join(_cell(cells[i], widths[i], aligns[i])
                                for i in range(ncol)) + ' |'

    sep = _border('-')
    click.echo(sep)
    click.echo(_line(headers))
    click.echo(_border('='))
    for cells in norm_rows:
        click.echo(_line(cells))
        click.echo(sep)


def _print_metric(name, value, color='white'):
    """打印单个指标"""
    click.echo(f'  {name:<20s} {click.style(str(value), fg=color, bold=True)}')


_MARKET_TREND = None


def _set_market_trend(trend):
    global _MARKET_TREND
    _MARKET_TREND = trend


def _get_market_trend():
    return _MARKET_TREND


_HS300_INDEX_CODE = '000300.SH'
_ZZ1000_INDEX_CODE = '000852.SH'
_LARGE_CAP_THRESHOLD_WAN = 2_000_000.0  # 200亿 = 2,000,000 万元
_SYMBOL_CAP_MAP = {}
_SYMBOL_NAME_MAP = {}
_SYMBOL_PE_MAP = {}
_SYMBOL_PRICE_MAP = {}
_CAP_LIST_LOADED = False
_TREND_STATES_PRECOMPUTED = {}

# --limit：命中条件时限制买入
_LIMIT_ENABLED = False
_LIMIT_CFG = {}
_LIMIT_CACHE = {}
_LIMIT_FETCHER = None


def _preload_trend_states(start_date, end_date):
    """在个股数据计算前一次性预计算两个指数的趋势状态。"""
    global _TREND_STATES_PRECOMPUTED
    _TREND_STATES_PRECOMPUTED = {
        _HS300_INDEX_CODE: market_trend_states(
            start_date, end_date, _HS300_INDEX_CODE),
        _ZZ1000_INDEX_CODE: market_trend_states(
            start_date, end_date, _ZZ1000_INDEX_CODE),
    }


def _update_symbol_cap_map(stock_list):
    """从股票列表 DataFrame 更新 symbol -> 总市值（万元）/名称/PE/现价 映射。"""
    global _SYMBOL_CAP_MAP
    if stock_list is None or len(stock_list) == 0:
        return
    if 'symbol' not in stock_list.columns:
        return
    try:
        syms = stock_list['symbol'].astype(str)
        caps = (pd.to_numeric(stock_list['mktcap'], errors='coerce')
                if 'mktcap' in stock_list.columns else None)
        pes = (pd.to_numeric(stock_list['pe'], errors='coerce')
               if 'pe' in stock_list.columns else None)
        prices = (pd.to_numeric(stock_list['price'], errors='coerce')
                  if 'price' in stock_list.columns else None)
        names = stock_list['name'] if 'name' in stock_list.columns else None
        for i, sym in enumerate(syms):
            sym = str(sym)
            if caps is not None:
                cap = caps.iloc[i]
                if pd.notna(cap) and float(cap) > 0:
                    _SYMBOL_CAP_MAP[sym] = float(cap)
            if names is not None:
                nm = names.iloc[i]
                if pd.notna(nm) and str(nm).strip():
                    _SYMBOL_NAME_MAP[sym] = str(nm).strip()
            if pes is not None:
                pe = pes.iloc[i]
                if pd.notna(pe):
                    _SYMBOL_PE_MAP[sym] = float(pe)
            if prices is not None:
                price = prices.iloc[i]
                if pd.notna(price) and float(price) > 0:
                    _SYMBOL_PRICE_MAP[sym] = float(price)
    except Exception:
        pass


def _ensure_symbol_cap_map(symbols=None):
    """确保目标股票的总市值已加载；首选源列表不含总市值时回退新浪列表；仍无法获取时按中证1000处理。"""
    global _CAP_LIST_LOADED
    need = [str(s).strip() for s in (symbols or []) if str(s).strip()]
    if need and all(s in _SYMBOL_CAP_MAP for s in need):
        return
    if _CAP_LIST_LOADED:
        return
    _CAP_LIST_LOADED = True
    pages = 60 if _LIMIT_ENABLED else 20
    fetcher = None
    try:
        fetcher = DataFetcher()
        df = fetcher.get_stock_list(sort_key='mktcap', max_pages=pages)
        _update_symbol_cap_map(df)
    except Exception:
        pass
    # 首选源（baostock/eastmoney/akshare）的股票列表可能不含总市值，改用新浪列表补充
    if need and any(s not in _SYMBOL_CAP_MAP for s in need):
        try:
            if fetcher is None:
                fetcher = DataFetcher()
            df_sina = fetcher._get_stock_list_sina('mktcap', pages)
            _update_symbol_cap_map(df_sina)
        except Exception:
            pass


def _get_limit_fetcher():
    global _LIMIT_FETCHER
    if _LIMIT_FETCHER is None:
        _LIMIT_FETCHER = DataFetcher()
    return _LIMIT_FETCHER


def _set_limit(enabled):
    """设置 --limit 开关，并从 squant.toml 的 [tool.stock-quant.limit] 读取边界值。"""
    global _LIMIT_ENABLED, _LIMIT_CFG
    _LIMIT_ENABLED = bool(enabled)
    _LIMIT_CACHE.clear()
    cfg = {'LIMIT_ST': 1.0, 'LIMIT_PRICE': 2.0,
           'LIMIT_PE': 2.0, 'LIMIT_CAPITAL': 40.0}
    if _LIMIT_ENABLED:
        try:
            sec = (_load_quant_config() or {}).get('limit') or {}
        except Exception:
            sec = {}
        for k in cfg:
            if k in sec and sec[k] is not None:
                try:
                    cfg[k] = float(sec[k])
                except (TypeError, ValueError):
                    pass
    _LIMIT_CFG = cfg


def _stock_limit_reasons(symbol, name=None, ref_date=None):
    """返回 --limit 命中条件的原因列表（空列表表示不限制买入）。

    条件：ST；当前价 >= 一年前价 × LIMIT_PRICE；当前 PE 为负或 >= 一年前正 PE × LIMIT_PE；
    当前总市值 < LIMIT_CAPITAL 亿元。数据缺失的条件跳过。结果按 (symbol, ref_date) 缓存。
    """
    if not _LIMIT_ENABLED:
        return []
    sym = str(symbol or '').strip()
    if not sym:
        return []
    ref = str(ref_date or datetime.now().strftime('%Y-%m-%d'))[:10]
    key = (sym, ref)
    if key in _LIMIT_CACHE:
        return _LIMIT_CACHE[key]

    cfg = _LIMIT_CFG or {}

    def _num(k, default):
        try:
            return float(cfg.get(k, default) or 0)
        except (TypeError, ValueError):
            return 0.0

    st_limit = _num('LIMIT_ST', 1.0)
    price_limit = _num('LIMIT_PRICE', 2.0)
    pe_limit = _num('LIMIT_PE', 2.0)
    cap_limit = _num('LIMIT_CAPITAL', 40.0)

    reasons = []
    nm = name if name is not None else _SYMBOL_NAME_MAP.get(sym, '')
    today = datetime.now().strftime('%Y-%m-%d')
    is_today = (ref == today)
    start = (pd.Timestamp(ref) - pd.Timedelta(days=365)).strftime('%Y-%m-%d')

    close_series = None
    if price_limit > 0 or pe_limit > 0:
        try:
            kdf = _get_limit_fetcher().get_stock_data(sym, start, ref)
            if kdf is not None and len(kdf) > 0 and 'close' in kdf.columns:
                c = pd.to_numeric(kdf['close'], errors='coerce').dropna()
                if len(c) > 0:
                    close_series = c
        except Exception:
            close_series = None

    # 1. ST
    if st_limit >= 1 and 'ST' in str(nm or '').upper():
        reasons.append('ST')

    # 2. 价格翻倍
    if price_limit > 0 and close_series is not None and len(close_series) >= 2:
        first = float(close_series.iloc[0])
        last = float(close_series.iloc[-1])
        if first > 0 and last >= first * price_limit:
            reasons.append(f'价格{last / first:.2f}倍')

    # 3. PE 为负或翻倍
    if pe_limit > 0:
        try:
            rows = fetch_valuation(sym, start_date=start,
                                   asset_type=infer_asset_type(sym), force=True)
        except Exception:
            rows = []
        end_ts = pd.Timestamp(ref)
        latest = None
        positives = []
        for row in rows or []:
            try:
                d = pd.Timestamp(row[0])
                v = float(row[1])
            except (TypeError, ValueError, IndexError):
                continue
            if d > end_ts or pd.isna(v):
                continue
            latest = v
            if v > 0:
                positives.append(v)
        current = latest
        if current is None and is_today:
            current = _SYMBOL_PE_MAP.get(sym)
        if current is not None:
            if current <= 0:
                reasons.append('PE为负')
            elif positives and current >= positives[0] * pe_limit:
                reasons.append(f'PE{current / positives[0]:.2f}倍')

    # 4. 总市值低于阈值（市值单位：万元）
    if cap_limit > 0:
        cap = _SYMBOL_CAP_MAP.get(sym)
        if cap is not None:
            cap_ref = float(cap)
            if not is_today and close_series is not None and len(close_series) > 0:
                cur_price = _SYMBOL_PRICE_MAP.get(sym)
                if cur_price and float(cur_price) > 0:
                    cap_ref = (float(cap) * float(close_series.iloc[-1])
                               / float(cur_price))
            if cap_ref < cap_limit * 10000.0:
                reasons.append(f'市值{cap_ref / 10000.0:.1f}亿')

    _LIMIT_CACHE[key] = reasons
    return reasons


def _apply_stock_limit(signals, df):
    """--limit：命中限制条件时禁止买入（买入信号置 0，保留卖出）。"""
    if not _LIMIT_ENABLED or signals is None or len(signals) == 0:
        return signals
    attrs = getattr(df, 'attrs', {}) or {}
    if attrs.get('limit_skip'):
        return signals
    symbol = str(attrs.get('symbol') or '').strip()
    if not symbol:
        return signals
    reasons = _stock_limit_reasons(
        symbol, name=attrs.get('stock_name'), ref_date=attrs.get('ref_date'))
    if not reasons:
        return signals
    out = signals.copy()
    mask = out > 0
    if mask.any():
        out[mask] = 0.0
    out.attrs = dict(getattr(signals, 'attrs', {}))
    out.attrs['limit_reasons'] = reasons
    return out


def _symbol_index_code(symbol):
    """按总市值选择个股对应的大盘指数。"""
    cap = _SYMBOL_CAP_MAP.get(str(symbol or '').strip())
    if cap is not None and cap > _LARGE_CAP_THRESHOLD_WAN:
        return _HS300_INDEX_CODE
    return _ZZ1000_INDEX_CODE


def _preferred_strategy_key():
    """历史优先策略（当前不再启用）。"""
    if not _DOWNLOSS_SELL:
        return None
    trend = _get_market_trend()
    if isinstance(trend, (list, tuple)):
        default_code = get_market_trend_index()
        trend = next((t for t in trend if t.get('index_code') == default_code),
                     trend[0] if trend else {})
    state = str((trend or {}).get('state') or '')
    if state == '涨势':
        return 'macd'
    if state == '震荡':
        return 'boll'
    return None


def _market_trend_color(state):
    return {'涨势': 'green', '跌势': 'red', '震荡': 'yellow',
            '过渡': 'cyan'}.get(state, 'white')


def _format_market_trend(trend):
    if isinstance(trend, (list, tuple)):
        return '\n  '.join(_format_market_trend(t) for t in trend)
    if not trend:
        return '大盘趋势(指数): 震荡'
    state = str(trend.get('state') or '震荡')
    index_name = str(trend.get('index_name') or trend.get('index_code') or '指数')
    date = trend.get('date')
    reason = str(trend.get('reason') or '').strip()
    reason_txt = f'；原因: {reason}' if reason else ''
    return f'大盘趋势({index_name}, {date}): {state}{reason_txt}'


def _print_market_trend(trend):
    if isinstance(trend, (list, tuple)):
        for t in trend:
            _print_market_trend(t)
        return
    state = str((trend or {}).get('state') or '震荡')
    click.echo(click.style('  ' + _format_market_trend(trend),
                           fg=_market_trend_color(state), bold=True))


def _market_trend_html(trend):
    if isinstance(trend, (list, tuple)):
        return '<br>'.join(_market_trend_html(t) for t in trend)
    if not trend:
        return ''
    state = str(trend.get('state') or '震荡')
    index_name = str(trend.get('index_name') or trend.get('index_code') or '指数')
    date = trend.get('date')
    color = {'涨势': '#27ae60', '跌势': '#e74c3c', '震荡': '#f39c12',
             '过渡': '#2980b9'}.get(state, '#7f8c8d')
    date_html = f' <small>({date})</small>' if date else ''
    reason = str(trend.get('reason') or '').strip()
    reason_html = f'<br><small>原因: {reason}</small>' if reason else ''
    return (f'<span><strong>大盘趋势({index_name}):</strong> '
            f'<span style="color:{color};font-weight:bold">{state}</span>'
            f'{date_html}{reason_html}</span>')


def _stock_trend_html(trend, label=''):
    if not trend:
        return ''
    state = str(trend.get('state') or '震荡')
    name = str(label or trend.get('stock_name') or trend.get('symbol') or '个股').strip()
    date = trend.get('date')
    color = {'涨势': '#27ae60', '跌势': '#e74c3c', '震荡': '#f39c12'}.get(state, '#7f8c8d')
    date_html = f' <small>({date})</small>' if date else ''
    reason = str(trend.get('reason') or '').strip()
    reason_html = f'<br><small>原因: {reason}</small>' if reason else ''
    return (f'<span><strong>个股趋势({name}):</strong> '
            f'<span style="color:{color};font-weight:bold">{state}</span>'
            f'{date_html}{reason_html}</span>')


def _run_strategy(strategy_key, df, capital=100000, is_index=False, enhance=0):
    """运行单个策略并返回回测结果"""
    strategy_map = INDEX_STRATEGY_MAP if is_index else STRATEGY_MAP
    strategy_name = _strat_label(strategy_key, strategy_map)
    strategy = _build_strategy_instance(strategy_key, strategy_map, enhance)
    signals = _generate_effective_signals(strategy, df)
    raw_signals = signals
    engine = BacktestEngine(initial_capital=capital, fix_capital=_FIX_CAPITAL)
    result = engine.run(df, signals, position_style=_get_position_style())
    result['strategy_name'] = strategy_name
    result['strategy_key'] = strategy_key
    result['enhance'] = 0
    result['signals'] = signals
    result['raw_signals'] = raw_signals

    # 运行风险分析
    risk = risk_report(result['daily_returns'].dropna(), result['equity_curve'])
    # 合并风险指标到结果中
    result.update(risk)

    return result


def _run_single_analysis(df, strategy_key, capital, chart_gen, prefix='', strategy_map=None, enhance=0):
    """运行单个策略的完整分析流程（图表在外部统一生成）"""
    if strategy_map is None:
        strategy_map = STRATEGY_MAP
    strategy_name = _strat_label(strategy_key, strategy_map)
    strategy = _build_strategy_instance(strategy_key, strategy_map, enhance)

    signals = _generate_effective_signals(strategy, df)
    raw_signals = signals

    engine = BacktestEngine(initial_capital=capital, fix_capital=_FIX_CAPITAL)
    result = engine.run(df, signals, position_style=_get_position_style())
    result['raw_signals'] = raw_signals

    risk = risk_report(result['daily_returns'].dropna(), result['equity_curve'])
    result.update(risk)

    return result, {}, strategy_name, signals


# 未指定 -st 时的默认分析时段长度（日历天，start = end 前推 180 天）
_DEFAULT_PERIOD_DAYS = 180

# 数据获取预热天数：在 start 前再多取 N 天，供因子/指标正确计算
_ANALYSIS_WARMUP_DAYS = 90


def _warmup_fetch_start(start_date, warmup_days=_ANALYSIS_WARMUP_DAYS):
    """计算带预热期的数据获取起始日（start 前推 warmup_days 天）"""
    return (pd.Timestamp(start_date) - pd.Timedelta(days=warmup_days)).strftime('%Y-%m-%d')


def _truncate_analysis_df(df, start_date):
    """忽略预热期数据：仅保留 start 日期及之后的行情行（信号分析/报告从 start 开始）"""
    if df is None or len(df) == 0:
        return df
    return df[df.index >= pd.Timestamp(start_date)]


def _parse_dates(start, end, default_days=_DEFAULT_PERIOD_DAYS,
                 duration_explicit=False):
    """解析日期字符串，返回 (start_date, end_date)

    - 未指定 -st：开始日期 = 结束日期前 default_days 天（-e 缺省为今天）。
    - 指定 -st 且指定 -e：以 -e 为准。
    - 指定 -st 且显式指定 -d（duration_explicit=True）且缺省 -e：
      结束日期 = 开始日期 + default_days 天。
    - 指定 -st 但未显式指定 -d 且缺省 -e：结束日期默认为今天。
    对 start/end 做格式校验并统一为 YYYY-MM-DD，非法时抛出明确错误。
    start/end 输入格式为 YYYYMMDD（如 20240815）。
    """
    def _normalize_date(d):
        d = str(d).strip()
        for fmt in ('%Y%m%d', '%Y-%m-%d'):
            try:
                return datetime.strptime(d, fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
        raise click.ClickException(f'日期格式不正确: {d}，期望格式 YYYYMMDD')

    if start is not None:
        start_date = _normalize_date(start)
        if end is not None:
            end_date = _normalize_date(end)
        elif duration_explicit:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_date = (start_dt + timedelta(days=default_days)).strftime('%Y-%m-%d')
        else:
            end_date = datetime.now().strftime('%Y-%m-%d')
    else:
        if end is not None:
            end_date = _normalize_date(end)
        else:
            end_date = datetime.now().strftime('%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        start_date = (end_dt - timedelta(days=default_days)).strftime('%Y-%m-%d')
    return start_date, end_date


def _build_date_tag(start_date, end_date):
    """构建文件名日期标签: yyyymmdd_天数d（天数 = 起始到结束的日期差）"""
    tag = end_date.replace('-', '') if end_date else datetime.now().strftime('%Y%m%d')
    if start_date and end_date:
        try:
            days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
            tag = f'{tag}_{days}d'
        except Exception:
            pass
    return tag


def _backtest_report_filename(symbol, strat_names, start_date, end_date):
    """单只股票回测报告文件名：<symbol>_<start>_<end>_<策略名>_<mmddHHMM>.html

    - symbol: 股票代码
    - strat_names: 策略名列表，多个策略名直接连续拼接
    - start/end: 回测区间（统一为 YYYYMMDD）
    - mmddHHMM: 当前时间（月、日、时、分）
    """
    strat_part = ''.join(str(s) for s in (strat_names or []))
    start_part = str(start_date).replace('-', '') if start_date else ''
    end_part = str(end_date).replace('-', '') if end_date else ''
    time_part = datetime.now().strftime('%m%d%H%M')
    parts = [str(symbol), start_part, end_part]
    if strat_part:
        parts.append(strat_part)
    parts.append(time_part)
    return '_'.join(parts) + '.html'


def _format_sources(sources):
    """将数据源集合格式化为报告展示文本（如 'sina, baostock'）。"""
    vals = sorted({str(s) for s in sources if s})
    return ', '.join(vals) if vals else 'N/A'


def _calc_hold_annual(avg_return, hold_days, trade_count=None):
    """持股年化收益率 = 累计收益率 * 250 / 累计持股天数

    持股天数按实际交易日计；无法计算时返回 None。
    """
    if hold_days is None or avg_return is None or hold_days <= 0:
        return None
    return avg_return * TRADING_DAYS_PER_YEAR / hold_days


def _profit_int(return_value):
    """收益率转百分数整数（0.675 -> 67），None 返回 None"""
    if return_value is None:
        return None
    return int(return_value * 100)


def _calc_sharpe_drawdown(equity_curve):
    """从权益曲线计算夏普比率与最大回撤（返回 (sharpe, max_drawdown)）"""
    eq = pd.Series([float(v) for v in equity_curve])
    if len(eq) < 2:
        return 0.0, 0.0
    rets = eq.pct_change().dropna()
    if len(rets) > 1 and np.std(rets, ddof=1) > 0:
        excess = rets - 0.03 / TRADING_DAYS_PER_YEAR
        sharpe = float(np.mean(excess) / np.std(rets, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
    else:
        sharpe = 0.0
    cummax = eq.cummax()
    drawdowns = (eq - cummax) / cummax
    max_dd = float(np.abs(np.min(drawdowns))) if len(drawdowns) else 0.0
    return sharpe, max_dd


def _build_report_filename(source, end_date, days, strategy, mode, profit=None):
    """构建 analyze 报告文件名: anlyze_<file>_<strategy>_<mmddHHMM>.html"""
    file_part = str(source or 'analysis')
    strat_part = re.sub(r'[\s,|]+', '', str(strategy or 'all'))
    return f'anlyze_{file_part}_{strat_part}_{datetime.now().strftime("%m%d%H%M")}.html'


def _command_options_str():
    """当前进程的完整命令行选项（不含解释器与脚本路径）"""
    args = sys.argv[1:]
    return shlex.join(args) if args else 'N/A'


def _format_cmd_params(symbol, symbol_file, hot, start, end, strategy, is_index,
                       capital, output, output_file, threads,
                       filter_signal=None, chart=False):
    """构建命令选项参数摘要字符串，用于在报告中标注本次运行所用的选项"""
    parts = ['analyze']
    if symbol:
        parts.append(f'-s "{symbol}"')
    if symbol_file:
        parts.append(f'-sf "{symbol_file}"')
    if hot:
        parts.append(f'-h {hot}')
    if start:
        parts.append(f'-st {start}')
    if end:
        parts.append(f'-e {end}')
    parts.append(f'-g {strategy}')
    if is_index:
        parts.append('-i')
    parts.append(f'-c {capital / 10000:g}')
    parts.append(f'-o "{output}"')
    if output_file:
        parts.append(f'-of "{output_file}"')
    parts.append(f'-x{threads}')
    if filter_signal:
        parts.append(f'-f {filter_signal}')
    if chart:
        parts.append('--chart')
    return ' '.join(parts)


def _format_risk_report_rows(risk):
    """格式化风险报告为表格行"""
    rows = []

    def _safe_pct(v):
        if v is None:
            return 'N/A'
        return f'{v * 100:.2f}%'

    def _safe_float(v, fmt='.2f'):
        if v is None:
            return 'N/A'
        return f'{v:{fmt}}'

    rows.append(['总收益率', _safe_pct(risk.get('total_return'))])
    rows.append(['年化收益率', _safe_pct(risk.get('annual_return'))])
    rows.append(['最大回撤', _safe_pct(risk.get('max_drawdown'))])
    rows.append(['夏普比率', _safe_float(risk.get('sharpe_ratio'))])
    rows.append(['胜率', _safe_pct(risk.get('win_rate'))])
    rows.append(['交易次数', _fmt_trade_count(risk)])
    rows.append(['盈利因子', _safe_float(risk.get('profit_factor'))])

    return rows


def _get_signal_text(sig_series):
    """从信号序列中提取最新信号文本"""
    if sig_series is None or len(sig_series) == 0:
        return 'N/A'
    last_sig = sig_series.iloc[-1] if hasattr(sig_series, 'iloc') else sig_series[-1]
    sig_map = {1.0: '买入', 0.0: '观望', -1.0: '卖出'}
    return sig_map.get(float(last_sig), str(last_sig))


def _get_signal_color(sig_text):
    """获取信号对应的颜色"""
    return {'买入': 'green', '买': 'green', '卖出': 'red', '卖': 'red', '观望': 'yellow'}.get(sig_text, 'white')


def _fmt_trade_count(risk, *args, **kwargs):
    """交易次数（精简为单一数值）"""
    return str(risk.get('total_trades', 'N/A'))


def _signal_reason(strategy_key, signal, enhance=0):
    """按策略与信号方向给出买卖原因的文字描述（signal: >0 买 / <0 卖）"""
    base = _strat_base(strategy_key)
    buy = signal is not None and signal > 0
    if base == 'ma1030':
        return 'MA快线上穿慢线（金叉）' if buy else 'MA快线下穿慢线（死叉）'
    if base in ('macd', 'macd1'):
        return 'DIF 上穿 DEA（金叉）' if buy else 'DIF 下穿 DEA（死叉）'
    if base == 'boll':
        return '跌破下轨后反弹' if buy else '触及上轨后回落或ATR 移动止损'
    if base == 'kdj':
        return 'K 上穿 D（金叉）' if buy else 'K 下穿 D（死叉）'
    if base == 'quality_value':
        return '综合得分 Z 值≥阈值（买）' if buy else '综合得分 Z 值≤阈值（卖）'
    if base == 'hold':
        return '买入持有'
    return '买入信号' if buy else '卖出信号'


def _trades_summary_row(headers, trades_df, pnl_spec=',.2f'):
    """构建交易明细表汇总行：合计 持仓天数/收益率/盈亏额；首列标注「汇总」。"""
    if trades_df is None or len(trades_df) == 0:
        return None

    def _num(col):
        if col not in trades_df.columns:
            return pd.Series([0.0] * len(trades_df))
        return pd.to_numeric(trades_df[col], errors='coerce').fillna(0.0)

    total_hd = int(_num('holding_days').sum())
    total_ret = float(_num('return').sum())
    total_pnl = float(((_num('exit_price') - _num('entry_price')) * _num('position')).sum())
    row = []
    for i, h in enumerate(headers):
        if i == 0:
            row.append('汇总')
        elif h == '持仓天数':
            row.append(total_hd)
        elif h == '收益率':
            row.append(f'{total_ret * 100:.2f}%')
        elif h == '盈亏额':
            row.append(f'{total_pnl:{pnl_spec}}')
        else:
            row.append('')
    return row


def _build_trades_detail_table(trades_df):
    """由回测交易记录构建「买卖时间与盈亏」明细表，返回 (headers, rows)

    每笔（完整买入→卖出）记录：买入日期/卖出日期/买入价/卖出价/持股数/持仓天数/
    收益率/盈亏额/退出原因。entry_price 为持仓加权平均成本。
    """
    headers = ['买入日期', '卖出日期', '买入价', '卖出价', '持股数', '持仓天数', '收益率', '盈亏额', '退出原因']
    rows = []
    if trades_df is None or len(trades_df) == 0:
        return headers, rows
    reason_map = {'signal': '信号', 'end': '期末平仓', 'replace': '换股',
                  'downloss': '大盘下跌/震荡止损'}
    for _, t in trades_df.iterrows():
        ed = t.get('entry_date')
        xd = t.get('exit_date')
        ed = ed.strftime('%Y-%m-%d') if hasattr(ed, 'strftime') else str(ed)
        xd = xd.strftime('%Y-%m-%d') if hasattr(xd, 'strftime') else str(xd)
        ep = float(t.get('entry_price', 0) or 0)
        xp = float(t.get('exit_price', 0) or 0)
        pos = float(t.get('position', 0) or 0)
        ret = float(t.get('return', 0) or 0)
        hd = int(t.get('holding_days', 0) or 0)
        pnl = (xp - ep) * pos
        reason = reason_map.get(t.get('exit_reason'), t.get('exit_reason'))
        rows.append([ed, xd, f'{ep:.4f}', f'{xp:.4f}', f'{pos:.0f}',
                     hd, f'{ret * 100:.2f}%', f'{pnl:,.2f}', reason])
    # 汇总行：统计持仓天数/收益率/盈亏额合计
    summary = _trades_summary_row(headers, trades_df)
    if summary:
        rows.append(summary)
    return headers, rows


# 买卖点分析：忽略时间段最前面 N 天（根数）的信号，统一标注为观望
# 已按要求改为 0：信号从 start 日期起即可考虑，不再屏蔽前 N 天
_SIGNAL_WARMUP_DAYS = 0


def _mask_signal_warmup(signals):
    """将信号序列最前面 _SIGNAL_WARMUP_DAYS 个交易日（根）置为 0（观望）

    _SIGNAL_WARMUP_DAYS 目前为 0，即不屏蔽任何信号（信号从 start 起即可考虑）。
    保留该函数以便需要时恢复屏蔽。
    返回新的信号序列（不改动原序列）。
    """
    if signals is None or len(signals) == 0:
        return signals
    signals = signals.copy()
    n = min(_SIGNAL_WARMUP_DAYS, len(signals))
    signals.iloc[:n] = 0
    return signals


# 信号过滤映射：-f 选项字符 -> 信号文本
_FILTER_SIGNAL_TEXT = {'b': '买入', 's': '卖出', 'w': '观望'}


def _has_actionable_signal(all_signals):
    """判断是否存在至少一个策略的最新信号不是观望（买/卖）"""
    for sig in all_signals.values():
        if sig is None or len(sig) == 0:
            continue
        last = float(sig.iloc[-1]) if hasattr(sig, 'iloc') else float(sig[-1])
        if last != 0.0:
            return True
    return False


def _get_primary_signal_value(all_signals, all_risks=None):
    """确定股票的主信号值：选最佳收益策略的信号

    Args:
        all_signals: {策略key: 信号序列}
        all_risks: {策略key: 风险指标dict}，用于选出最佳策略

    Returns:
        float: 主信号值 1/0/-1，无法确定时返回 None
    """
    if not all_signals:
        return None
    best_sk = None
    best_ret = -999
    for key in all_signals:
        r = (all_risks or {}).get(key, {}).get('total_return')
        if r is not None and (r or 0) > best_ret:
            best_ret = r or 0
            best_sk = key
    sk = best_sk if best_sk is not None else next(iter(all_signals))
    sig = all_signals.get(sk)
    if sig is None or len(sig) == 0:
        return None
    return float(sig.iloc[-1]) if hasattr(sig, 'iloc') else float(sig[-1])


def _match_filter(filter_signal, all_signals, all_risks=None):
    """判断股票是否满足 -f 信号过滤条件（未设置过滤时恒为 True）

    b=买入(1)，s=卖出(-1)，w=观望(0)。
    """
    if not filter_signal:
        return True
    val = _get_primary_signal_value(all_signals, all_risks)
    if val is None:
        return False
    if filter_signal == 'b':
        return val > 0
    if filter_signal == 's':
        return val < 0
    return val == 0


def _get_webhook_config():
    """读取钉钉机器人 Webhook 配置（squant.toml 的 WEBHOOK 节）"""
    config = _load_quant_config().get('WEBHOOK', {})
    url = config.get('URL')
    key = config.get('key', 'quant')
    return url, key


def _send_dingtalk_notify(content):
    """通过钉钉机器人发送一条文本消息（content 为完整内容，可含换行）"""
    url, _ = _get_webhook_config()
    if not url:
        click.echo(click.style('  通知: 未配置 WEBHOOK.URL，跳过发送', fg='yellow'))
        return False
    payload = {
        'msgtype': 'text',
        'text': {'content': content},
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        ok = resp.status_code == 200 and resp.json().get('errcode', -1) == 0
        preview = content if len(content) <= 80 else content[:80] + '...'
        if ok:
            click.echo(click.style(f'  钉钉通知已发送: {preview}', fg='green'))
        else:
            click.echo(click.style(f'  钉钉通知失败: HTTP {resp.status_code} {resp.text[:200]}', fg='yellow'))
        return ok
    except Exception as e:
        click.echo(click.style(f'  钉钉通知发送异常: {e}', fg='yellow'))
        return False


def _market_trend_notify_line():
    """构建钉钉消息中的大盘趋势行，如 '沪深300：跌势；中证1000：涨势'。"""
    trend = _get_market_trend()
    if not trend:
        return ''
    if isinstance(trend, dict):
        trend = [trend]
    parts = []
    for t in trend:
        if not isinstance(t, dict):
            continue
        name = str(t.get('index_name') or t.get('index_code') or '').strip()
        if not name:
            continue
        state = str(t.get('state') or '震荡').strip()
        parts.append(f'{name}：{state}')
    if not parts:
        return ''
    return '；'.join(parts)


def _notify_signals(all_stock_data):
    """对最新信号的股票发送钉钉通知：多条股票消息合成一条消息发出

    第一行（title）：key: 1.<策略1> 2.<策略2> 3.<策略3> ...
    第二行：大盘趋势，如 "沪深300：跌势；中证1000：涨势"
    后续行（data）：<股票代码><股票名称>: <策略编号><买/卖> ...（仅显示买/卖信号，观望跳过）
    （如 "quant: 1.MACD 2.KDJ 3.均线" / "600089 特变电工: 1买 3卖"）
    """
    _, key = _get_webhook_config()
    sig_text_map = {1.0: '买', -1.0: '卖'}

    # 汇总所有策略（按出现顺序去重）作为列顺序（持有 hold 为基准对照，不进入通知）
    strategy_keys = []
    for d in all_stock_data:
        for sk in d.get('strategies_to_run', []):
            if sk == 'hold':
                continue
            if sk not in strategy_keys:
                strategy_keys.append(sk)

    if not strategy_keys:
        return

    # 策略显示名（取第一只含有该策略的股票的名称）
    names = {}
    for d in all_stock_data:
        smap = d.get('strategy_map', {})
        for sk in strategy_keys:
            if sk not in names and sk in smap:
                names[sk] = smap[sk]

    lines = [f'{key}: ' + ' '.join(f'{i}.{names.get(sk, sk)}' for i, sk in enumerate(strategy_keys, 1))]
    score_lines = []
    watch_candidates = []

    def _label(d):
        symbol = str(d.get('symbol') or '')
        name = str(d.get('stock_name') or '').strip()
        return symbol if not name or name == symbol else f'{symbol} {name}'

    for d in all_stock_data:
        symbol = d.get('symbol')
        if not symbol:
            continue
        sigs = d.get('all_signals', {})
        sig_cells = []
        latest_values = []
        for i, sk in enumerate(strategy_keys, 1):
            sig = sigs.get(sk)
            if sig is None or len(sig) == 0:
                continue
            try:
                last = float(sig.iloc[-1]) if hasattr(sig, 'iloc') else float(sig[-1])
            except Exception:
                continue
            if pd.isna(last):
                continue
            latest_values.append(last)
            text = sig_text_map.get(last)
            if text is not None:
                sig_cells.append(f'{i}{text}')

        total = d.get('signal_score_total')
        if total is not None and latest_values and all(v == 0 for v in latest_values):
            watch_candidates.append((int(total), d))

        if not sig_cells:
            continue
        label = _label(d)
        lines.append(f'{label}: ' + ' '.join(sig_cells))
        score_text = str(d.get('signal_score') or '').strip()
        if score_text:
            score_lines.append(f'{symbol}: {score_text}')

    if score_lines:
        lines.append('综合打分:')
        lines.extend(score_lines)
    if watch_candidates:
        hi = max(watch_candidates, key=lambda x: x[0])
        lo = min(watch_candidates, key=lambda x: x[0])
        lines.append('观望综合打分最高: ' + _label(hi[1]) + ' ' + str(hi[1].get('signal_score') or ''))
        if lo[1] is not hi[1]:
            lines.append('观望综合打分最低: ' + _label(lo[1]) + ' ' + str(lo[1].get('signal_score') or ''))

    if len(lines) <= 1:
        return
    trend_line = _market_trend_notify_line()
    if trend_line:
        lines.insert(1, trend_line)
    _send_dingtalk_notify('\n'.join(lines))


def _sleep_until(plan_time):
    """阻塞直到指定时间 hhmm（时分）才继续运行

    当日该时刻已过时顺延到次日同一时刻。

    Args:
        plan_time: 格式 hhmm（如 '0900' 表示 09:00）
    """
    if not plan_time:
        return
    plan_time = str(plan_time).strip()
    try:
        if len(plan_time) != 4:
            raise ValueError
        hour, minute = int(plan_time[:2]), int(plan_time[2:4])
        if hour > 23 or minute > 59:
            raise ValueError
    except ValueError:
        raise click.ClickException(f'--plan-time 格式错误: {plan_time}，期望 hhmm（如 0900 表示 09:00）')

    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    wait_seconds = (target - now).total_seconds()
    hours, rem = divmod(int(wait_seconds), 3600)
    minutes = rem // 60
    click.echo(click.style(
        f'  定时执行(--plan-time): 等待至 {target.strftime("%Y-%m-%d %H:%M")}（还需 {hours} 小时 {minutes} 分钟）', fg='cyan'))
    time.sleep(wait_seconds)


def _generate_html_report(symbol, sname, start_date, end_date, capital, is_index,
                          strategy_map, strategies_to_run, all_results, all_risks, all_charts,
                          all_signals=None, latest_close=None, latest_date=None,
                          trading_days=None, report_filename=None, cmd_params='',
                          strategy=None, mode=0, market_trend=None,
                          signal_reasons=None, stock_trend=None):
    """生成 HTML 分析报告"""
    import base64
    from datetime import datetime as dt

    if all_signals is None:
        all_signals = {}
    if signal_reasons is None:
        signal_reasons = {}

    def _safe_pct_html(v):
        if v is None:
            return 'N/A'
        return f'{v * 100:.2f}%'

    def _safe_float_html(v, fmt='.2f'):
        if v is None:
            return 'N/A'
        return f'{v:{fmt}}'

    def _img_to_b64(path):
        if not path or not os.path.exists(path):
            return ''
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode()

    mode_label = '指数（通用策略）' if is_index else '个股通用'
    report_time = dt.now().strftime('%Y-%m-%d %H:%M:%S')

    # 拼排名表
    rankings = []
    for sk in strategies_to_run:
        r = all_risks.get(sk, {})
        sig_text = _get_signal_text(all_signals.get(sk))
        sig_display = _get_signal_text(all_signals.get(sk))
        rankings.append((
            _strat_label(sk, strategy_map),
            r.get('total_return'),
            r.get('sharpe_ratio'),
            r.get('max_drawdown'),
            r.get('total_trades'),
            r.get('annual_return'),
            r.get('win_rate'),
            r.get('profit_factor'),
            sig_text,
            sig_display,
            (signal_reasons.get(sk, '') if sig_text in ('买入', '卖出') else ''),
        ))
    rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

    rank_rows_html = ''
    for i, (name, tr, sh, dd, nt, ar, wr, pf, sig, sig_display, reason) in enumerate(rankings, 1):
        rank_rows_html += f'''<tr>
            <td>{i}</td><td>{name}</td>
            <td>{_safe_pct_html(tr)}</td><td>{_safe_float_html(sh)}</td>
            <td>{_safe_pct_html(dd)}</td><td>{nt if nt is not None else 'N/A'}</td>
            <td>{_safe_pct_html(ar)}</td><td>{_safe_pct_html(wr)}</td>
            <td>{_safe_float_html(pf)}</td>
            <td style="font-weight:bold;color:{'#27ae60' if sig == '买入' else '#e74c3c' if sig == '卖出' else '#f39c12'}">{sig_display}</td>
            <td>{reason}</td>
        </tr>'''

    # 各策略详情
    detail_html = ''
    # 已嵌入的图表路径（合并后的多策略图表只需展示一次）
    embedded_chart_paths = set()

    for sk in strategies_to_run:
        name = _strat_label(sk, strategy_map)
        risk = all_risks.get(sk, {})
        charts = all_charts.get(sk, {})

        detail_html += f'<h3>{name}</h3>'

        # 最新信号
        sig_text = _get_signal_text(all_signals.get(sk))
        sig_display = _get_signal_text(all_signals.get(sk))
        sig_color_html = {'买入': '#27ae60', '卖出': '#e74c3c', '观望': '#f39c12'}.get(sig_text, '#2c3e50')
        detail_html += f'<div class="signal" style="color:{sig_color_html};font-weight:bold;font-size:16px;margin:10px 0;">最新信号: {sig_display}</div>'
        if sig_text in ('买入', '卖出'):
            reason = signal_reasons.get(sk, '')
            if reason:
                detail_html += f'<div class="signal-reason" style="color:#666;margin:-4px 0 10px 0;">原因: {reason}</div>'

        detail_html += '<table class="detail"><tr><th>指标</th><th>数值</th></tr>'
        for label, func in [
            ('总收益率', lambda: _safe_pct_html(risk.get('total_return'))),
            ('年化收益率', lambda: _safe_pct_html(risk.get('annual_return'))),
            ('最大回撤', lambda: _safe_pct_html(risk.get('max_drawdown'))),
            ('夏普比率', lambda: _safe_float_html(risk.get('sharpe_ratio'))),
            ('胜率', lambda: _safe_pct_html(risk.get('win_rate'))),
            ('交易次数', lambda: _fmt_trade_count(risk)),
            ('盈利因子', lambda: _safe_float_html(risk.get('profit_factor'))),
        ]:
            detail_html += f'<tr><td>{label}</td><td>{func()}</td></tr>'
        detail_html += '</table>'

        for cname, cpath in charts.items():
            if cpath in embedded_chart_paths:
                continue  # 合并后的多策略图表已在前面策略中展示，跳过重复
            embedded_chart_paths.add(cpath)
            b64 = _img_to_b64(cpath)
            if b64:
                ext = os.path.splitext(cpath)[1].lstrip('.')
                detail_html += f'<div class="chart"><img src="data:image/{ext};base64,{b64}" alt="{cname}"></div>'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>量化分析报告 - {symbol} {sname}</title>
<style>
body {{ font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #fafafa; color: #2c3e50; }}
h1 {{ border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
h2 {{ border-bottom: 2px solid #bdc3c7; padding-bottom: 6px; margin-top: 30px; }}
h3 {{ color: #2980b9; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: center; }}
th {{ background: #3498db; color: white; }}
tr:nth-child(even) {{ background: #f2f2f2; }}
.info {{ background: #ecf0f1; padding: 15px; border-radius: 5px; margin: 15px 0; }}
.info span {{ margin-right: 30px; }}
.chart {{ margin: 20px 0; text-align: center; }}
.chart img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; }}
.footer {{ text-align: center; color: #95a5a6; margin-top: 40px; font-size: 12px; }}
</style>
</head>
<body>
<h1>股票量化分析报告</h1>
<div class="info">
    <span><strong>股票:</strong> {symbol} {sname}</span>
    <span><strong>区间:</strong> {start_date} ~ {end_date}</span>
    <span><strong>初始资金:</strong> {capital:,.0f}</span>
    <span><strong>模式:</strong> {mode_label}</span>
    <span><strong>分析周期:</strong> {start_date} ~ {end_date}</span>
    <span><strong>生成时间:</strong> {report_time}</span>
    {_market_trend_html(market_trend)}
    {_stock_trend_html(stock_trend, label=f'{symbol} {sname}')}
</div>
<div class="info">
    <span><strong>最新价格:</strong> {f'{latest_close:.2f}' if latest_close is not None else 'N/A'} <small>({str(latest_date.date()) if latest_date is not None else 'N/A'})</small></span>
</div>
<div class="info">
    <span><strong>命令参数:</strong> {cmd_params}</span>
    <span><strong>命令选项:</strong> {_command_options_str()}</span>
    <span><strong>软件版本:</strong> {__version__}</span>
</div>

<h2>策略对比排名</h2>
<table class="rank">
<tr><th>排名</th><th>策略</th><th>总收益率</th><th>夏普比率</th><th>最大回撤</th><th>交易次数</th><th>年化收益率</th><th>胜率</th><th>盈利因子</th><th>最新信号</th><th>信号原因</th></tr>
{rank_rows_html}
</table>

<h2>各策略详情</h2>
{detail_html}

<div class="footer"><p>报告由 stock-quant 自动生成 | {report_time}</p></div>
</body>
</html>'''

    # 最佳策略收益率（用于文件名）
    best_return = None
    for sk in strategies_to_run:
        tr = all_risks.get(sk, {}).get('total_return')
        if tr is not None and (best_return is None or tr > best_return):
            best_return = tr

    if report_filename:
        report_path = report_filename
    else:
        days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
        report_path = _build_report_filename(symbol, end_date, days, strategy, mode,
                                             profit=_profit_int(best_return))
    if not os.path.dirname(report_path):
        report_path = os.path.join('report', report_path)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


def _generate_scan_stock_charts(top_results, strat_keys, name_map, prefix_tag='', strat_enhance=None):
    """为 scan 的 top N 股票生成个股图表，返回可嵌入 HTML 的片段

    每只股票仅生成一个综合K线图：各策略指标子图合并到一个K线图，
    各策略权益曲线合并到一个权益子图（不同颜色曲线）。

    top_results: 扫描结果列表（含 symbol 等字段）
    strat_keys: 策略 key 列表
    name_map: symbol -> 名称
    返回: dict {symbol: [base64图html片段...]}，生成失败返回空
    """
    import base64
    from datetime import datetime as dt

    charts_by_symbol = {}
    fetcher = DataFetcher()

    def _img_b64(path):
        if not path or not os.path.exists(path):
            return ''
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode()

    for r in top_results:
        symbol = r.get('symbol')
        if not symbol:
            continue
        sn = name_map.get(symbol, symbol)
        try:
            start_date = (dt.now() - timedelta(days=_DEFAULT_PERIOD_DAYS)).strftime('%Y-%m-%d')
            end_date = dt.now().strftime('%Y-%m-%d')
            df = fetcher.get_stock_data(symbol, _warmup_fetch_start(start_date), end_date)
            if df is None or len(df) < 100:
                continue
            df, _, _, _ = _apply_realtime_to_df(df, symbol, fetcher, end_date, verbose=False)
            df = add_all_indicators(df)
            df = _truncate_analysis_df(df, start_date)
            df.attrs['symbol'] = symbol

            # 运行各策略，收集信号与权益曲线
            strategies_data = []
            if strat_enhance is None:
                strat_enhance = {}
            for sk in strat_keys:
                try:
                    result = _run_strategy(sk, df, capital=100000, enhance=strat_enhance.get(sk, 0))
                    signals = result.get('signals')
                    if signals is None or len(signals) == 0:
                        continue
                    strategies_data.append({
                        'key': sk,
                        'name': _strat_label(sk),
                        'signals': signals,
                        'equity_curve': result.get('equity_curve'),
                    })
                except Exception:
                    continue

            if not strategies_data:
                continue

            # 每只股票仅生成一个综合图表
            cg = ChartGenerator(output_dir='output', prefix=f'scan_{prefix_tag}_{symbol}', date_tag='')
            chart_path = cg.plot_multi_strategy_composite(
                df, strategies_data,
                title=f'多策略回测效果 ({symbol} {sn})'.replace('  ', ' '),
            )
            b64 = _img_b64(chart_path)
            if b64:
                ext = os.path.splitext(chart_path)[1].lstrip('.')
                charts_by_symbol[symbol] = [
                    f'<div class="chart"><img src="data:image/{ext};base64,{b64}" '
                    f'style="max-width:100%;border:1px solid #ddd;border-radius:4px;"></div>'
                ]
        except Exception:
            continue
    return charts_by_symbol


def _generate_scan_html_report(headers, table_rows, strat_names, strat_cols, charts_by_symbol=None, name_map=None, report_filename=None, market_trend=None):
    """生成 scan 扫描结果的 HTML 报告（含 top N 表格 + 个股图表）"""
    from datetime import datetime as dt
    report_time = dt.now().strftime('%Y-%m-%d %H:%M:%S')

    html_rows = ''
    for row in table_rows:
        cells = ''.join(f'<td>{v}</td>' for v in row)
        html_rows += f'<tr>{cells}</tr>\n'

    strat_label = ', '.join(strat_cols if strat_cols else strat_names)

    # 个股图表区
    detail_html = ''
    if charts_by_symbol:
        for symbol, charts in charts_by_symbol.items():
            sn = (name_map or {}).get(symbol, symbol)
            detail_html += f'<h2>{symbol} {sn}</h2>'
            for ch in charts:
                detail_html += ch

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>股票扫描报告</title>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; margin: 20px; color: #333; }}
h1 {{ color: #1a5276; }}
h2 {{ color: #1a5276; border-bottom: 1px solid #ccc; padding-bottom: 5px; margin-top: 30px; }}
.info {{ color: #666; margin-bottom: 10px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 15px; font-size: 14px; }}
th, td {{ border: 1px solid #ccc; padding: 8px; text-align: center; }}
th {{ background: #1a5276; color: #fff; }}
tr:nth-child(even) {{ background: #f5f6fa; }}
.chart {{ margin: 15px 0; text-align: center; }}
.chart h3 {{ color: #333; margin-bottom: 5px; }}
</style>
</head>
<body>
<h1>股票扫描报告</h1>
<div class="info">
    <span><strong>策略:</strong> {strat_label}</span>
    <span style="margin-left:20px;"><strong>生成时间:</strong> {report_time}</span>
    <span style="margin-left:20px;"><strong>命令选项:</strong> {_command_options_str()}</span>
    <span style="margin-left:20px;"><strong>软件版本:</strong> {__version__}</span>
    {_market_trend_html(market_trend)}
</div>
<h2>扫描结果 - Top {len(table_rows)}</h2>
<table>
<tr>{''.join(f'<th>{h}</th>' for h in headers)}</tr>
{html_rows}
</table>
{detail_html}
</body>
</html>'''

    if not report_filename:
        report_filename = f'scan_{dt.now().strftime("%m%d%H%M")}.html'
    os.makedirs('report', exist_ok=True)
    report_path = os.path.join('report', report_filename)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


def _img_to_b64(path):
    """将图片文件转为 base64 data URI，失败返回空串"""
    import base64
    if not path or not os.path.exists(path):
        return ''
    try:
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return ''


def _generate_backtest_html(symbol_label, start_date, end_date, capital, tables, charts_html='',
                            report_filename=None, source_label=None):
    """生成回测结果的 HTML 报告

    Args:
        symbol_label: 标题（如 "600089 特变电工" 或 "批量回测(5只)"）
        start_date / end_date: 分析区间
        capital: 初始资金
        tables: [(表格标题, headers, rows), ...]
        charts_html: 已拼接好的图表 <img> 片段（可空）
        report_filename: 输出文件名（默认自动生成）
        source_label: 数据来源展示文本（如 "sina" / "baostock"）
    Returns:
        报告文件路径
    """
    from datetime import datetime as dt
    report_time = dt.now().strftime('%Y-%m-%d %H:%M:%S')

    # 概要备注：回测使用的阈值参数（读取 squant.toml 的 thresholds）
    def _fmt_th(v):
        return 'N/A' if v is None else f'{v:g}'

    _remark = (
        f'ATRStop:2*ATR14; '
        f'Max/MinRSI:{_fmt_th(get_threshold("MAX_RSI_THRESHOLD"))}/'
        f'{_fmt_th(get_threshold("MIN_RSI_THRESHOLD"))}; '
        f'MaxPE:{_fmt_th(get_threshold("PE_THRESHOLD"))}'
    )

    tables_html = ''
    for title, headers, rows in tables:
        tr = ''
        for row in rows:
            cells = ''.join(f'<td>{v}</td>' for v in row)
            tr += f'<tr>{cells}</tr>\n'
        th = ''.join(f'<th>{h}</th>' for h in headers)
        tables_html += f'<h2>{title}</h2><table><tr>{th}</tr>{tr}</table>'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>回测报告 - {symbol_label}</title>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; margin: 20px; color: #333; }}
h1 {{ color: #1a5276; }}
h2 {{ color: #1a5276; border-bottom: 1px solid #ccc; padding-bottom: 5px; margin-top: 30px; }}
.info {{ color: #666; margin-bottom: 10px; }}
.info span {{ margin-right: 25px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 15px; font-size: 14px; }}
th, td {{ border: 1px solid #ccc; padding: 8px; text-align: center; }}
th {{ background: #1a5276; color: #fff; }}
tr:nth-child(even) {{ background: #f5f6fa; }}
.chart {{ margin: 15px 0; text-align: center; }}
.chart img {{ max-width: 100%; border: 1px solid #ddd; border-radius: 4px; }}
</style>
</head>
<body>
<h1>策略回测报告</h1>
<div class="info">
    <span><strong>标的:</strong> {symbol_label}</span>
    <span><strong>区间:</strong> {start_date} ~ {end_date}</span>
    <span><strong>初始资金:</strong> {capital:,.0f}</span>
    <span><strong>数据来源:</strong> {source_label or 'N/A'}</span>
    <span><strong>生成时间:</strong> {report_time}</span>
    <span><strong>备注:</strong> {_remark}</span>
    <span><strong>命令选项:</strong> {_command_options_str()}</span>
    <span><strong>软件版本:</strong> {__version__}</span>
</div>
{tables_html}
<div class="chart">{charts_html}</div>
</body>
</html>'''

    if not report_filename:
        report_filename = f'backtest_{dt.now().strftime("%m%d_%H%M")}.html'
    os.makedirs('report', exist_ok=True)
    report_path = os.path.join('report', report_filename)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


def _build_signal_table(all_stock_data, include_all=False, sort_by_signal=False,
                        preferred_key=None, include_signal_score=False):
    """构建最新信号表数据，返回 (strategy_keys, strategy_names, rows, excluded)

    rows 每行为 [股票标签, 最新价格, 各策略最新信号文本(买/卖/-/N/A)...]（include_signal_score 时末尾附加综合打分）
    include_all=False 时仅列示至少有一项策略信号不是"观望"（买/卖）的股票，
    include_all=True 时列出所有股票（不隐藏全部观望的股票）。
    excluded 为被过滤掉的股票数。
    sort_by_signal=True 时按信号排序：买入信号多的排前面，卖出信号多的排中间，
    所有策略均为观望的排后面。
    """
    strategy_keys = []
    for d in all_stock_data:
        for sk in d.get('strategies_to_run', []):
            if sk == 'hold':
                continue  # 持有为基准对照策略，不进入最新信号表
            if sk not in strategy_keys:
                strategy_keys.append(sk)

    strategy_names = {}
    for d in all_stock_data:
        smap = d['strategy_map']
        for sk in strategy_keys:
            if sk not in strategy_names and sk in smap:
                strategy_names[sk] = smap[sk]

    def _signal_text(sig_series):
        if sig_series is None or len(sig_series) == 0:
            return 'N/A'
        last = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
        if last > 0:
            return '买'
        if last < 0:
            return '卖'
        return '-'

    data = list(all_stock_data)
    if preferred_key:
        data.sort(key=lambda d: 0 if (
            (d.get('all_signals') or {}).get(preferred_key) or 0) > 0 else 1)

    rows = []
    watch_rows = []
    excluded = 0
    for d in data:
        s = d['symbol']
        sn = d['stock_name']
        label = f'{s} {sn}'.strip()
        lc = d.get('latest_close')
        price_str = f'{lc:.2f}' if lc is not None else 'N/A'
        sigs = d.get('all_signals', {})
        cells = [_signal_text(sigs.get(sk)) for sk in strategy_keys]
        is_watch = not any(c in ('买', '卖') for c in cells)
        trend = str((d.get('stock_trend') or {}).get('state') or '—')
        row = [label, price_str, trend] + cells
        if include_signal_score:
            row.append(d.get('signal_score') or '—')
        if is_watch:
            excluded += 1
            total = d.get('signal_score_total')
            if include_signal_score and total is not None:
                watch_rows.append((int(total), row))
            if not include_all:
                continue
        rows.append(row)

    if sort_by_signal:
        pref_pos = (strategy_keys.index(preferred_key)
                    if preferred_key in strategy_keys else None)

        def _sort_key(row):
            cells = row[3:3 + len(strategy_keys)]
            buy = sum(1 for c in cells if c == '买')
            sell = sum(1 for c in cells if c == '卖')
            if buy > 0:
                group = 0  # 买入信号 → 排前面
            elif sell > 0:
                group = 1  # 卖出信号 → 排中间
            else:
                group = 2  # 全部观望 → 排后面
            pref_group = 0
            if pref_pos is not None:
                pref_group = 0 if cells[pref_pos] == '买' else 1
            return (pref_group, group, -buy, -sell)

        rows.sort(key=_sort_key)

    if include_signal_score and not include_all and watch_rows:
        ordered = sorted(watch_rows, key=lambda x: x[0], reverse=True)
        top = ordered[:3]
        bottom = ordered[-3:]
        picked = []
        seen = set()
        for item in top + bottom:
            key = id(item[1])
            if key not in seen:
                seen.add(key)
                picked.append(item)
        for _, row in picked:
            rows.append(row)
        excluded = max(0, excluded - len(picked))

    return strategy_keys, strategy_names, rows, excluded


def _print_signal_table(all_stock_data, include_all=False,
                        include_signal_score=False):
    """在屏幕打印最新信号表；include_all=True 时保留全部观望的股票。"""
    strategy_keys, strategy_names, rows, excluded = _build_signal_table(
        all_stock_data, include_all=include_all,
        preferred_key=_preferred_strategy_key(),
        include_signal_score=include_signal_score)
    headers = ['股票代码名称', '最新价格', '个股趋势'] + [strategy_names.get(sk, sk) for sk in strategy_keys]
    if include_signal_score:
        headers.append('综合打分')
    _print_section('最新信号表')
    _print_table(headers, rows, disable_numparse=True)
    if include_signal_score:
        click.echo(click.style(
            '  备注: 综合打分排位：DMI|MACD|OBV|BIAS| RSI|BOLL|WR|ATR| KDJ|SAR',
            fg='yellow'))
    if excluded and not include_all:
        click.echo(click.style(f'  备注: 未列示股票 {excluded} 只，其全部策略信号均为"观望"。', fg='yellow'))


def _generate_multi_html_report(all_stock_data, strategy, is_index, capital, report_filename=None, end_date=None, start_date=None, cmd_params='', source=None, mode=0, include_all_watch=False, market_trend=None, include_signal_score=False):
    """生成多股票汇总 HTML 报告"""
    import base64
    from datetime import datetime as dt

    report_time = dt.now().strftime('%Y-%m-%d %H:%M:%S')
    mode_label = '指数（通用策略）' if is_index else '个股通用'

    def _is_all_watch(d):
        """判断某股票是否所有（非 hold）策略的最新信号均为观望"""
        sigs = d.get('all_signals', {})
        for sk in d.get('strategies_to_run', []):
            if sk == 'hold':
                continue  # 持有为基准对照策略，不参与判断
            series = sigs.get(sk)
            if series is None or len(series) == 0:
                continue
            last = float(series.iloc[-1]) if hasattr(series, 'iloc') else float(series[-1])
            if last != 0:
                return False
        return True

    # 默认删除全部策略均为观望信号的股票；include_all_watch=True 时保留
    if include_all_watch:
        report_data = list(all_stock_data)
        removed_count = 0
    else:
        report_data = [d for d in all_stock_data if not _is_all_watch(d)]
        removed_count = len(all_stock_data) - len(report_data)

    def _safe_pct(v):
        return f'{v * 100:.2f}%' if v is not None else 'N/A'

    def _safe_float(v, fmt='.2f'):
        return f'{v:{fmt}}' if v is not None else 'N/A'

    def _img_to_b64(path):
        if not path or not os.path.exists(path):
            return ''
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode()

    # 最新信号表：每行一只股票，每列一个策略，单元格为该股票该策略的最新信号
    strategy_keys, strategy_names, signal_table_rows, excluded = _build_signal_table(
        report_data, include_all=True, sort_by_signal=True,
        preferred_key=_preferred_strategy_key(),
        include_signal_score=include_signal_score)
    excluded = 0  # 全部观望的股票已在 report_data 中剔除，不再显示隐藏备注

    def _signal_cell(text):
        if text.startswith('买') and '≥' not in text:
            return f'<td style="color:#e74c3c;font-weight:bold">{text}</td>'
        if text.startswith('卖') and '≤' not in text:
            return f'<td style="color:#27ae60;font-weight:bold">{text}</td>'
        if text == 'N/A':
            return '<td style="color:#000000">N/A</td>'
        if text.startswith('观(') or ('≥' in text or '≤' in text):
            return f'<td style="color:#f39c12;font-weight:bold">{text}</td>'
        return f'<td style="color:#000000">{text}</td>'

    signal_rows = ''
    n_strat = len(strategy_keys)
    for row in signal_table_rows:
        label = row[0]
        price = row[1]
        trend = row[2]
        cells = row[3:3 + n_strat]
        sig_score = row[-1] if include_signal_score else None
        _trend_color = {'涨势': '#e74c3c', '跌势': '#27ae60',
                        '震荡': '#f39c12'}.get(trend, '#000000')
        signal_rows += (f'<tr><td>{label}</td><td>{price}</td>'
                        f'<td style="color:{_trend_color};font-weight:bold">{trend}</td>')
        for cell in cells:
            signal_rows += _signal_cell(cell)
        if include_signal_score:
            signal_rows += f'<td>{sig_score}</td>'
        signal_rows += '</tr>'

    signal_table_html = ('<table><tr><th>股票代码名称</th><th>最新价格</th>'
                          '<th>个股趋势</th>')
    for sk in strategy_keys:
        signal_table_html += f'<th>{strategy_names.get(sk, sk)}</th>'
    if include_signal_score:
        signal_table_html += '<th>综合打分</th>'
    signal_table_html += '</tr>' + signal_rows + '</table>'
    signal_remark_html = f'<p style="color:#f39c12;font-size:12px;">备注: 未列示股票 {excluded} 只，其全部策略信号均为"观望"。</p>' if excluded else ''

    # 各股票详情
    detail_sections = ''
    for d in report_data:
        s = d['symbol']
        sn = d['stock_name']
        lc = d.get('latest_close')
        ldt = d.get('latest_date')
        price_str = f'{lc:.2f}' if lc is not None else ''
        date_str = f'({ldt.date()})' if ldt is not None else ''
        label = f'{s} {sn}'.strip()
        smap = d['strategy_map']
        risks = d.get('all_risks', {})
        sigs = d.get('all_signals', {})
        charts = d.get('all_charts', {})

        detail_sections += f'<h2>{label} <small style="color:#666;font-weight:normal">{price_str} {date_str}</small></h2>'
        if include_signal_score and d.get('signal_score'):
            detail_sections += f'<div style="color:#666;margin:4px 0 10px 0;">综合打分: {d.get("signal_score")}</div>'

        # 排名表
        rankings = []
        reasons = d.get('signal_reasons', {})
        for sk in d['strategies_to_run']:
            r = risks.get(sk, {})
            sg = _get_signal_text(sigs.get(sk))
            rankings.append((
                smap[sk], r.get('total_return'), r.get('sharpe_ratio'),
                r.get('max_drawdown'), r.get('total_trades'),
                sg,
                _get_signal_text(sigs.get(sk)),
                (reasons.get(sk, '') if sg in ('买入', '卖出') else ''),
            ))
        rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

        rank_rows = ''
        for i, (name, tr, sh, dd, nt, sg, sg_display, reason) in enumerate(rankings, 1):
            rank_rows += f'''<tr>
                <td>{i}</td><td>{name}</td>
                <td>{_safe_pct(tr)}</td><td>{_safe_float(sh)}</td>
                <td>{_safe_pct(dd)}</td><td>{nt if nt is not None else 'N/A'}</td>
                <td style="font-weight:bold;color:{'#27ae60' if sg == '买入' else '#e74c3c' if sg == '卖出' else '#f39c12'}">{sg_display}</td>
                <td>{reason}</td>
            </tr>'''
        detail_sections += f'''<table>
            <tr><th>排名</th><th>策略</th><th>总收益率</th><th>夏普比率</th><th>最大回撤</th><th>交易次数</th><th>最新信号</th><th>信号原因</th></tr>
            {rank_rows}
        </table>'''

        # 策略权益曲线对比图
        # 嵌入各策略图表（合并后的多策略图表每股票只展示一次）
        embedded_chart_paths = set()
        for sk in d['strategies_to_run']:
            ch = charts.get(sk, {})
            for cname, cpath in ch.items():
                if cpath in embedded_chart_paths:
                    continue
                embedded_chart_paths.add(cpath)
                b64 = _img_to_b64(cpath)
                if b64:
                    ext = os.path.splitext(cpath)[1].lstrip('.')
                    detail_sections += f'<div class="chart"><img src="data:image/{ext};base64,{b64}" alt="{cname}"></div>'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>量化分析报告 - 批量汇总</title>
<style>
body {{ font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #fafafa; color: #2c3e50; }}
h1 {{ border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
h2 {{ border-bottom: 2px solid #bdc3c7; padding-bottom: 6px; margin-top: 30px; }}
h3 {{ color: #2980b9; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: center; }}
th {{ background: #3498db; color: white; }}
tr:nth-child(even) {{ background: #f2f2f2; }}
.info {{ background: #ecf0f1; padding: 15px; border-radius: 5px; margin: 15px 0; }}
.info span {{ margin-right: 30px; }}
.chart {{ margin: 20px 0; text-align: center; }}
.chart img {{ max-width: 100%; height: auto; border: 1px solid #ddd; border-radius: 4px; }}
.footer {{ text-align: center; color: #95a5a6; margin-top: 40px; font-size: 12px; }}
</style>
</head>
<body>
<h1>股票量化分析报告（批量）</h1>
<div class="info">
    <span><strong>股票数:</strong> {len(report_data)}</span>
    <span><strong>初始资金:</strong> {capital:,.0f}</span>
    <span><strong>模式:</strong> {mode_label}</span>
    <span><strong>分析周期:</strong> {report_data[0]['start_date'] if report_data else 'N/A'} ~ {report_data[0]['end_date'] if report_data else 'N/A'}</span>
    <span><strong>生成时间:</strong> {report_time}</span>
    {_market_trend_html(market_trend)}
    {'<span><strong>已剔除全部观望信号股票:</strong> ' + str(removed_count) + ' 只</span>' if removed_count else ''}
</div>
<div class="info">
    <span><strong>命令参数:</strong> {cmd_params}</span>
    <span><strong>命令选项:</strong> {_command_options_str()}</span>
    <span><strong>软件版本:</strong> {__version__}</span>
</div>

<h2>最新信号表</h2>
{signal_table_html}
{signal_remark_html}

{detail_sections}

<div class="footer"><p>报告由 stock-quant 自动生成 | {report_time}</p></div>
</body>
</html>'''

    # 平均收益率（各股票最佳策略收益率的均值，用于文件名）
    best_returns = []
    for d in report_data:
        risks = d.get('all_risks', {})
        best_ret = None
        for sk in d.get('strategies_to_run', []):
            r = risks.get(sk, {}).get('total_return')
            if r is not None and (best_ret is None or r > best_ret):
                best_ret = r
        if best_ret is not None:
            best_returns.append(best_ret)
    avg_return = (sum(best_returns) / len(best_returns)) if best_returns else None

    if report_filename:
        report_path = report_filename
    else:
        days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
        report_path = _build_report_filename(source, end_date, days, strategy, mode,
                                             profit=_profit_int(avg_return))
    if not os.path.dirname(report_path):
        report_path = os.path.join('report', report_path)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


# ============================================================================
# CLI 命令组
# ============================================================================


def _format_elapsed(seconds: float) -> str:
    """将秒数格式化为易读的耗时文本。"""
    if seconds < 60:
        return f'{seconds:.2f} 秒'
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f'{int(minutes)} 分 {sec:.2f} 秒'
    hours, minutes = divmod(minutes, 60)
    return f'{int(hours)} 时 {int(minutes)} 分 {sec:.2f} 秒'


def _print_total_elapsed(seconds: float) -> None:
    """在命令执行结束后打印总耗时。"""
    click.echo()
    click.echo(click.style(f'  总耗时: {_format_elapsed(seconds)}', fg='cyan', bold=True))


class _TimedGroup(click.Group):
    """命令组：每个子命令正常执行完毕后打印总耗时。"""

    def invoke(self, ctx):
        start = time.perf_counter()
        result = super().invoke(ctx)
        if ctx.invoked_subcommand:
            _print_total_elapsed(time.perf_counter() - start)
        return result


@click.group(cls=_TimedGroup)
@click.version_option(version=__version__, prog_name='stock_quant')
def cli():
    """股票量化分析软件 - 命令行工具

    提供股票技术分析、策略回测、策略对比、股票扫描等功能。
    """
    ctx = click.get_current_context(silent=True)
    if ctx is not None and ctx.invoked_subcommand:
        if not check_db_connection():
            click.echo(click.style(
                '  警告: 数据库连接失败，本次运行将仅从网络获取数据', fg='yellow'))


def _exchange_from_token(token):
    """从代码文本中提取交易所（SH/SZ/BJ），无则返回 ''。"""
    raw = str(token or '').strip().upper()
    if '.' in raw:
        left, right = raw.split('.', 1)
        if right in ('SH', 'SZ', 'BJ'):
            return right
        if left in ('SH', 'SZ', 'BJ'):
            return left
    if len(raw) > 2 and raw[:2] in ('SH', 'SZ', 'BJ') and raw[2:].isdigit():
        return raw[:2]
    return ''


def _sina_realtime_code(code, exchange=''):
    """按交易所（或代码前缀）生成新浪实时行情代码，如 sz000159 / sh600036。"""
    ex = str(exchange or '').strip().upper()
    if ex not in ('SH', 'SZ', 'BJ'):
        if code.startswith(('6', '9')):
            ex = 'SH'
        elif code.startswith(('0', '3', '2', '15', '16', '18')):
            ex = 'SZ'
        elif code.startswith(('4', '8')):
            ex = 'BJ'
        else:
            ex = 'SH'
    return f'{ex.lower()}{code}'


def _resolve_symbol(input_str, exchange=None):
    """将股票代码或名称解析为 (纯数字代码, 股票名称)

    纯代码优先用实时行情（按交易所/代码前缀）取名称，避免搜索接口把同号指数
    （如 000159 沪股通）当作股票返回。

    Args:
        input_str: 股票代码或名称（如 '000159.SZ'、'京东方A'）
        exchange: 可选交易所（SH/SZ/BJ），传入时优先使用

    Returns:
        (code, name) 或 (None, None)
    """
    import re as _re

    raw = str(input_str or '').strip()
    code = _parse_code_token(raw)

    if code:
        ex = (exchange or _exchange_from_token(raw) or '').upper()
        if ex not in ('SH', 'SZ', 'BJ'):
            ex = ''
        # 1) 实时行情：交易所已知则用该交易所，否则按代码前缀推断
        try:
            sina_code = _sina_realtime_code(code, ex)
            rt_url = f'https://hq.sinajs.cn/list={sina_code}'
            rt_headers = {'User-Agent': 'Mozilla/5.0',
                          'Referer': 'https://finance.sina.com.cn/'}
            rt_resp = requests.get(rt_url, timeout=10, headers=rt_headers)
            rt_match = _re.search(r'"([^"]*)"', rt_resp.text)
            if rt_match:
                rt_parts = rt_match.group(1).split(',')
                if rt_parts and rt_parts[0]:
                    return code, rt_parts[0]
        except Exception:
            pass
        # 2) 回退：Sina 搜索接口
        try:
            url = f'https://suggest3.sinajs.cn/suggest/type=11&key={code}'
            headers = {'User-Agent': 'Mozilla/5.0',
                       'Referer': 'https://finance.sina.com.cn/'}
            resp = requests.get(url, timeout=10, headers=headers)
            match = _re.search(r'"([^"]*)"', resp.text)
            if match:
                for item in match.group(1).split(';'):
                    fields = item.split(',')
                    if len(fields) >= 4 and fields[2].isdigit():
                        name = fields[4] if len(fields) > 4 and fields[4] else fields[0]
                        return code, name
        except Exception:
            pass
        return code, ''

    # 名称输入：Sina 搜索接口
    try:
        url = f'https://suggest3.sinajs.cn/suggest/type=11&key={raw}'
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}
        resp = requests.get(url, timeout=10, headers=headers)
        match = _re.search(r'"([^"]*)"', resp.text)
        if match:
            for item in match.group(1).split(';'):
                fields = item.split(',')
                if len(fields) >= 4 and fields[2].isdigit():
                    code = fields[2]
                    name = fields[4] if len(fields) > 4 and fields[4] else fields[0]
                    return code, name
    except Exception:
        pass

    # 备选：akshare 全量股票列表
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        matches = df[df['名称'].str.contains(raw, na=False)]
        if matches.empty:
            return None, None
        exact = matches[matches['名称'] == raw]
        if not exact.empty:
            return str(exact.iloc[0]['代码']), str(exact.iloc[0]['名称'])
        return str(matches.iloc[0]['代码']), str(matches.iloc[0]['名称'])
    except Exception:
        return None, None


# ============================================================================
# analyze 命令 - 单只股票综合分析
# ============================================================================


def _split_strategies(s):
    """按逗号或竖线分隔策略字符串，返回去空白后的非空列表（兼容 | 与 , 两种写法）"""
    if not s:
        return []
    return [x.strip() for x in re.split(r'[,|]', s) if x.strip()]


def _parse_strategy_spec(spec):
    """返回策略 key（不再支持增强级别后缀）。"""
    return str(spec).strip(), 0


def _strat_base(key):
    """策略键（不再去掉数字后缀）。"""
    return str(key).strip()


def _strat_enh(key):
    """兼容接口：增强级别已取消，始终返回 0。"""
    return 0


def _strat_label(key, strategy_map=None):
    """策略显示名"""
    smap = strategy_map if strategy_map is not None else STRATEGY_MAP
    base, _ = _parse_strategy_spec(str(key))
    return smap[base][0]


def _strat_short(key):
    """scan 汇总表短栏名"""
    base, _ = _parse_strategy_spec(str(key))
    return _SCAN_SHORT_NAMES.get(base, STRATEGY_MAP[base][0])


def _build_report_strategy_map(strategy_map, tokens):
    """构建报告用的 {键: 显示名} 映射"""
    m = {k: v[0] for k, v in strategy_map.items()}
    for t in tokens:
        m[t] = _strat_label(t, strategy_map)
    return m


def _build_strategy_instance(base_key, strategy_map, enhance=0):
    """构建策略实例（enhance 仅为兼容旧调用，已忽略）。"""
    base, _ = _parse_strategy_spec(str(base_key))
    strategy_name, strategy_class = strategy_map[base]
    strategy = strategy_class()
    if hasattr(strategy, 'use_atr_stop'):
        strategy.use_atr_stop = _ATR_STOP_ENABLED
        strategy.atr_stop_multiplier = _ATR_STOP_MULTIPLIER
    return strategy


def _parse_code_token(token):
    """从代码文本中提取 6 位股票代码；非代码返回 None。

    支持 600036 / sh600036 / SH600036 / 600036.SH / SH.600036 等写法。
    """
    raw = str(token or '').strip().upper()
    if not raw:
        return None
    if '.' in raw:
        left, right = raw.split('.', 1)
        if right in ('SH', 'SZ', 'BJ') and left.isdigit():
            return left.zfill(6)
        if left in ('SH', 'SZ', 'BJ') and right.isdigit():
            return right.zfill(6)
    if len(raw) > 2 and raw[:2] in ('SH', 'SZ', 'BJ') and raw[2:].isdigit():
        return raw[2:].zfill(6)
    if raw.isdigit() and len(raw) <= 6:
        return raw.zfill(6)
    return None


def _normalize_symbol_tokens(tokens):
    """将代码 token 规范为 6 位代码；名称等非代码 token 原样保留。"""
    out = []
    for t in tokens:
        t = t.strip()
        if not t:
            continue
        code = _parse_code_token(t)
        out.append(code if code else t)
    return out


def _read_symbol_names(path):
    """从股票池文件解析 代码 -> 名称（每行形如 '000333 美的集团'）；无名称的代码不返回。"""
    names = {}
    if not os.path.isfile(path):
        candidates = []
        if not os.path.isabs(path):
            candidates.append(os.path.join('input', path))
            candidates.append(os.path.join('input', os.path.basename(path)))
        found = next((p for p in candidates if os.path.isfile(p)), None)
        if not found:
            return names
        path = found
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                code = None
                name_parts = []
                for tok in re.split(r'[,\s]+', line):
                    if not tok:
                        continue
                    c = _parse_code_token(tok)
                    if c and code is None:
                        code = c
                    elif c is None:
                        name_parts.append(tok)
                nm = ''.join(name_parts).strip()
                if code and nm:
                    names[code] = nm
    except Exception:
        pass
    return names


def _read_symbols_file(path):
    """从文件读取股票代码列表（空格/逗号/换行分隔）

    仅保留有效股票代码（6 位数字，可带 sh/sz/bj 前缀或 .SH/.SZ/.BJ 后缀），忽略股票名称等其它内容。
    若 path 不存在，则尝试在 input/ 目录下查找同名文件。
    """
    if not os.path.isfile(path):
        candidates = []
        if not os.path.isabs(path):
            candidates.append(os.path.join('input', path))
            candidates.append(os.path.join('input', os.path.basename(path)))
        candidates = list(dict.fromkeys(candidates))
        found = next((p for p in candidates if os.path.isfile(p)), None)
        if found:
            path = found
        else:
            searched = ', '.join([path] + candidates)
            click.echo(click.style(f'  错误: 文件不存在（已尝试: {searched}）', fg='red'))
            return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        symbols = []
        seen = set()
        for token in re.split(r'[,\s]+', text):
            code = _parse_code_token(token)
            if not code or code in seen:
                continue
            seen.add(code)
            symbols.append(code)
        return symbols
    except Exception as e:
        click.echo(click.style(f'  错误: 无法读取 {path}: {e}', fg='red'))
        return []


def _apply_realtime_to_df(df, symbol, fetcher, end_date, verbose=True):
    """将当日实时行情合并进 df（盘中处理）：

    1) 历史K线 df 已获取；
    2) -e 缺省或为今日才继续，否则返回 df 最后一根收盘价；
    3) -e 为今日且 df 最后一条日期已是今日，跳过（K线已含今日）；
    4) 最后一条非今日时获取实时行情，price<=0 或 datetime 非今日则跳过；
    5) price>0 且 datetime==今日，把实时行情追加到 df（close 取实时价）。

    Args:
        df: 历史K线 DataFrame（date 索引）
        symbol: 股票代码
        fetcher: DataFetcher 实例
        end_date: 请求结束日期（YYYY-MM-DD）
        verbose: 是否打印价格提示（批量/并行场景可关闭）

    Returns:
        (df, latest_close, latest_date, is_realtime): 合并后的 df、最新价、
        最新日期（datetime）、是否为实时价。
    """
    latest_close = df['close'].iloc[-1]
    latest_date = df.index[-1]
    is_realtime = False
    today = datetime.now()
    today_str = today.strftime('%Y-%m-%d')

    if end_date == today_str:
        last_row_date = df.index[-1]
        if last_row_date.date() != today.date():
            # 步骤4：获取实时行情
            try:
                rt = fetcher.get_realtime_quote(symbol)
            except Exception:
                rt = None
            if rt:
                rt_price = rt.get('price', 0)
                rt_ts = rt.get('datetime') or ''
                try:
                    rt_date = pd.to_datetime(rt_ts).date()
                except Exception:
                    rt_date = None
                if rt_price > 0 and rt_date == today.date():
                    # 步骤5：追加实时行情到 df
                    latest_close = rt_price
                    latest_date = today
                    is_realtime = True
                    today_idx = pd.Timestamp(today.date())
                    rt_row = pd.DataFrame({
                        'open':  [rt.get('open', rt_price)],
                        'high':  [rt.get('high', rt_price)],
                        'low':   [rt.get('low', rt_price)],
                        'close': [rt_price],
                        'volume': [rt.get('volume', 0)],
                    }, index=[today_idx])
                    for col in rt_row.columns:
                        rt_row[col] = rt_row[col].astype(df[col].dtype)
                    df = pd.concat([df, rt_row])
                    df = df.sort_index()

    if verbose:
        if is_realtime:
            click.echo(click.style(f'  实时价格: {latest_close:.2f} (已纳入分析)', fg='yellow'))
        else:
            click.echo(click.style(f'  最新价格: {latest_close:.2f} ({latest_date.date()})', fg='green'))

    return df, latest_close, latest_date, is_realtime


def _src_option(f):
    """通用 --src 选项：数据源（auto/sina/baostock/eastmoney/akshare），auto 按测速自动选择"""
    return click.option(
        '--src', default='auto',
        type=click.Choice(['auto', 'sina', 'baostock', 'eastmoney', 'akshare']),
        show_default=True,
        help='数据源 [auto|sina|baostock|eastmoney|akshare]，默认 auto；auto 启动时测速并按速度选择'
             '（起始日早于2023-01-01时优先 baostock/eastmoney/akshare），失败自动回退')(f)


def _maxdrawdown_option(f):
    """ATR 动态止损倍数（始终启用）。"""
    return click.option(
        '--maxdrawdown', 'atr_stop_ratio',
        type=float, default=2.0, show_default=True,
        help='ATR 移动止损倍数：stop=max(前一日stop, close-ratio*ATR14)，默认2，始终启用')(f)


def _limit_option(f):
    """限制买入：ST/近一年价格翻倍/PE 翻倍或为负/总市值过低。"""
    return click.option(
        '--limit', is_flag=True, default=False,
        help='限制买入：ST、当前价超一年前 LIMIT_PRICE 倍、当前PE为负或超一年前 LIMIT_PE 倍、'
             '总市值低于 LIMIT_CAPITAL 亿元（阈值见 squant.toml [tool.stock-quant.limit]）')(f)


@cli.command('analyze')
@_src_option
@_maxdrawdown_option
@_limit_option
@click.option('--symbol', '-s', default=None, help='股票代码或名称，可多个，以空格/逗号分隔（如 "000725 京东方A 000021" 或 "000021,002594"）')
@click.option('--symbol-file', '-sf', default=None, help='从指定文件读取股票代码（空格/逗号/换行分隔），仅处理有效 6 位代码并忽略名称，与 -s 同时使用时合并')
@click.option('--hot', '-h', default=None, type=int, help='获取热门股票数量（按HotScore热度分排序，存在时忽略 -s 和 -sf）')
@click.option('--start', '-st', default=None, help='开始日期（默认180天前），格式: YYYYMMDD')
@click.option('--end', '-e', default=None, help='结束日期（默认今天），格式: YYYYMMDD')
@click.option('--strategy', '-g', default='boma',     help='策略选择 [ma1030|macd|macd1|boll|boma|kdj|sar|quality_value|hold|all]，多个用逗号分隔（默认boma）')
@click.option('--index', '-i', 'is_index', is_flag=True, default=False, help='指数分析模式（使用通用策略）')
@click.option('--capital', '-c', default=100, type=float, help='初始资金（单位：万元，默认100万元）')
@click.option('--output', '-o', default='./output', help='图表输出目录（默认./output）')
@click.option('--output-file', '-of', default=None, help='指定HTML报告文件名，默认自动生成')
@click.option('--threads', '-x', default=5, type=click.IntRange(1, 6), help='并行进程数 -xN（N=1~6，默认5）')
@click.option('--filter', '-f', 'filter_signal', default=None, type=click.Choice(['b', 's', 'w']), help='信号过滤，仅输出符合指定信号的股票报告 [b=买入|s=卖出|w=观望]')
@click.option('--chart', is_flag=True, default=False, help='生成所有股票的K线图等图形并嵌入报告（默认仅生成至少有一个策略信号非观望的股票的图表）')
@click.option('--verbose', is_flag=True, default=False, help='输出每日信号综合打分（10指标）')
@click.option('--notify', is_flag=True, default=False, help='对最新信号非观望的股票通过钉钉机器人发送通知（含综合打分及观望极值；读取 squant.toml 的 WEBHOOK 配置）')
@click.option('--plan-time', default=None, help='定时执行：到指定时间(hhmm，如 0900=09:00)才开始运行，当日已过则顺延到次日')
def analyze_cmd(symbol, symbol_file, hot, start, end, strategy, is_index, capital, output, output_file, threads, filter_signal, chart, notify, plan_time, src, atr_stop_ratio, verbose, limit):
    """量化分析：重点输出结束日当天的买卖信号

    流程：获取数据 -> 计算指标 -> 运行策略 -> 回测 -> 风险分析 -> 生成图表 -> 打印报告

    使用 --index/-i 参数可切换到指数分析模式（使用通用策略），适用于分析大盘指数。
    -s 支持多个股票代码或名称，以空格/逗号分隔；报告文件名格式为 anlyze_<file>_<strategy>_<mmddHHMM>.html。
    -s 和 -sf 可同时使用，股票列表会自动合并去重。
    都不指定时默认采用 -sf input/pool.txt。
    使用 -h 指定数量时，忽略 -s/-sf，从网络获取最热门股票。
    缺少 -st 时默认分析周期 180 天，缺少 -e 时默认今天。
    """
    # 定时执行：--plan-time hhmm，先 sleep 到指定时间再继续
    _sleep_until(plan_time)

    mode = 0  # 固定常规分析流程
    set_primary_source(src)
    _set_maxdrawdown(atr_stop_ratio)
    _set_limit(limit)
    capital = capital * 10000  # -c 单位为万元，内部换算为元

    # 命令选项参数摘要（用于在报告中标注）
    cmd_params = _format_cmd_params(symbol, symbol_file, hot, start, end, strategy,
                                    is_index, capital, output, output_file, threads,
                                    filter_signal, chart)

    # 确定要分析的股票列表
    symbols = []
    sources = []
    s_multi = False

    if hot:
        # 热门股票模式：忽略 -s 和 -sf
        click.echo(click.style(f'\n  正在获取热门股票列表 ({hot} 只)...', fg='blue'))
        fetcher = DataFetcher()
        hot_df = fetcher.get_hot_stocks(count=hot)
        if hot_df.empty:
            click.echo(click.style('  错误: 获取热门股票失败', fg='red'))
            return
        for _, row in hot_df.iterrows():
            symbols.append(row['symbol'])
        sources.append(f'-h 热门股 ({len(symbols)}只, 按热度分排序)')
    else:
        if symbol:
            symbol_list = _normalize_symbol_tokens(re.split(r'[,\s，]+', symbol))
            has_wildcard = any(any(ch in s for ch in '*?') for s in symbol_list)
            if has_wildcard:
                # 展开通配符（如 60* 表示所有 60 开头的股票）
                click.echo(click.style('  检测到通配符，正在获取全量股票列表...', fg='blue'))
                fetcher = DataFetcher()
                all_codes = fetcher.get_all_stock_codes()
                expanded = []
                for s in symbol_list:
                    if any(ch in s for ch in '*?'):
                        matched = [c for c in all_codes if fnmatch.fnmatch(c, s)]
                        if not matched:
                            click.echo(click.style(f'  警告: 通配符 "{s}" 未匹配到任何股票', fg='yellow'))
                        expanded.extend(matched)
                    else:
                        expanded.append(s)
                # 最多查询 500 只
                if len(expanded) > 500:
                    click.echo(click.style(f'  通配符匹配 {len(expanded)} 只，超过 500 只上限，仅取前 500 只', fg='yellow'))
                    expanded = expanded[:500]
                symbol_list = expanded
                symbols = symbol_list
                s_multi = True
                sources.append(f'-s ({len(symbol_list)}只, 含通配符)')
                click.echo(click.style('  查询到的股票: ' + '，'.join(symbol_list), fg='cyan'))
            else:
                symbols = symbol_list
                s_multi = len(symbol_list) > 1
                sources.append(f'-s ({len(symbol_list)}只)')

        if symbol_file:
            file_symbols = _read_symbols_file(symbol_file)
            if file_symbols:
                symbols.extend(file_symbols)
                sources.append(f'-sf ({len(file_symbols)}只)')

        if not symbols:
            # 未指定 -s/-sf 时，默认采用 -sf input/pool.txt
            pool_path = os.path.join(app_root(), 'input', 'pool.txt')
            file_symbols = _read_symbols_file(pool_path)
            if not file_symbols:
                click.echo(click.style(
                    '  错误: 未指定 -s/-sf，且 input/pool.txt 无有效股票代码', fg='red'))
                return
            symbols = file_symbols
            sources.append('input/pool.txt')

    # 去重并保持顺序
    seen = set()
    deduped = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    symbols = deduped
    _ensure_symbol_cap_map(symbols)

    click.echo(click.style(f'\n  股票来源: {", ".join(sources)} → 共 {len(symbols)} 只（已去重）', fg='blue'))

    # 确定股票源标签
    if hot:
        source_tag = f'hot{hot}'
    elif symbol_file:
        source_tag = os.path.splitext(os.path.basename(symbol_file))[0]
    elif len(symbols) > 1:
        source_tag = 'multi'
    elif len(symbols) == 1:
        source_tag = symbols[0]
    else:
        source_tag = 'pool'

    # 解析日期：缺少 -st 默认180天前，缺少 -e 默认今天
    start_date_str, end_date_str = _parse_dates(start, end)
    _preload_trend_states(start_date_str, end_date_str)
    _market_dates = preload_market_trade_dates(
        _warmup_fetch_start(start_date_str), end_date_str) or None
    set_market_trade_dates_override(_market_dates)

    market_trends = [analyze_market_trend(end_date_str, _HS300_INDEX_CODE),
                     analyze_market_trend(end_date_str, _ZZ1000_INDEX_CODE)]
    _set_market_trend(market_trends)
    _print_market_trend(market_trends)

    all_stock_data = []
    if len(symbols) > 1 and threads > 1:
        n_workers = min(threads, len(symbols))
        click.echo(click.style(f'\n  使用 {n_workers} 个进程并行分析...', fg='blue'))

        tasks = [(s, start_date_str, end_date_str, strategy, is_index, capital, output,
                  output_file, True, cmd_params, mode, filter_signal, chart,
                  _market_dates, verbose or notify) for s in symbols]
        # 单只股票独立计时，超时只回收对应进程，不影响其它 worker
        all_stock_data = _run_analyze_tasks(tasks, symbols, n_workers, per_stock_timeout=180)
    else:
        for idx, raw_symbol in enumerate(symbols):
            if len(symbols) > 1:
                click.echo(click.style(f'\n  ── [{idx+1}/{len(symbols)}] ──', fg='cyan', bold=True))
            data = _analyze_single(raw_symbol, start_date_str, end_date_str, strategy, is_index, capital, output, output_file,
                                   batch_mode=len(symbols) > 1, cmd_params=cmd_params, mode=mode,
                                   filter_signal=filter_signal, chart=chart,
                                   verbose=verbose or notify)
            if data is not None:
                all_stock_data.append(data)

    if len(symbols) > 1:
        click.echo(click.style(f'\n  批量分析完成，共 {len(symbols)} 只股票', fg='green', bold=True))

    if filter_signal:
        click.echo(click.style(
            f'  信号过滤: 共 {len(symbols)} 只股票，符合 "{_FILTER_SIGNAL_TEXT[filter_signal]}" 条件的 {len(all_stock_data)} 只', fg='cyan'))

    # 打印最新信号表到屏幕
    if all_stock_data:
        _print_signal_table(all_stock_data, include_all=bool(symbol),
                            include_signal_score=verbose)

    # 生成汇总 HTML 报告
    if all_stock_data:
        try:
            html_path = _generate_multi_html_report(
                all_stock_data, strategy, is_index, capital,
                report_filename=output_file, end_date=end_date_str,
                start_date=start_date_str, cmd_params=cmd_params,
                source=source_tag, mode=mode,
                include_all_watch=True,
                market_trend=_get_market_trend(),
                include_signal_score=verbose)
            click.echo(click.style(f'\n  汇总报告: {html_path}', fg='green'))
        except Exception as e:
            click.echo(click.style(f'\n  汇总报告生成失败: {e}', fg='yellow'))

    # 钉钉通知：发送买卖信号（文本）
    if notify and all_stock_data:
        click.echo(click.style('\n  发送钉钉通知...', fg='blue'))
        _notify_signals(all_stock_data)


def _analyze_single(raw_symbol, start, end, strategy, is_index, capital, output, output_file=None, batch_mode=False, cmd_params='', mode=0, filter_signal=None, chart=False, verbose=False):
    """分析单只股票"""
    symbol = _parse_code_token(raw_symbol) or raw_symbol.strip()
    exchange = _exchange_from_token(raw_symbol)

    try:
        # 股票代码/名称解析
        stock_name = ''
        if not symbol.isdigit():
            click.echo(click.style(f'\n  正在搜索股票: {raw_symbol}...', fg='blue'))
            code, name = _resolve_symbol(raw_symbol, exchange=exchange)
            if code is None:
                click.echo(click.style(f'  错误: 未找到匹配 "{raw_symbol}" 的股票', fg='red'))
                return
            symbol = code
            stock_name = name
            click.echo(click.style(f'  找到: {symbol} {stock_name}', fg='green'))
        else:
            click.echo(click.style(f'\n  正在查找股票名称: {symbol}...', fg='blue'))
            _, name = _resolve_symbol(symbol, exchange=exchange)
            if name:
                stock_name = name
                click.echo(click.style(f'  找到: {symbol} {stock_name}', fg='green'))

        # 解析日期
        start_date, end_date = _parse_dates(start, end)

        # 打印头部
        header_display = f'{symbol} {stock_name}'.strip()
        _print_header('股票量化分析报告' if not is_index else '指数量化分析报告', header_display)

        click.echo(click.style(f'  分析区间: {start_date} ~ {end_date}', fg='white'))
        click.echo(click.style(f'  初始资金: {capital:,.0f}', fg='white'))

        # 获取数据：在 start 日期前推 _ANALYSIS_WARMUP_DAYS 天开始获取（供因子/指标正确计算）
        click.echo(click.style('\n  正在获取股票数据...', fg='blue'))
        fetcher = DataFetcher()
        df = fetcher.get_stock_data(symbol, _warmup_fetch_start(start_date), end_date)

        if df is None or len(df) == 0:
            click.echo(click.style(f'  错误: 未能获取股票 {symbol} 的数据', fg='red'))
            return

        click.echo(click.style(f'  获取到 {len(df)} 条数据记录（含前推 {_ANALYSIS_WARMUP_DAYS} 天预热）', fg='green'))

        # 最新价格与盘中实时数据处理（合并当日实时行情，见 _apply_realtime_to_df）
        df, latest_close, latest_date, is_realtime = _apply_realtime_to_df(
            df, symbol, fetcher, end_date)

        # 判断 ETF/指数基金
        is_etf = not is_index and _is_etf(symbol)

        # 计算技术指标（完全基于网络数据本地计算）
        click.echo(click.style('  正在计算技术指标...', fg='blue'))
        df = add_all_indicators(df)
        click.echo(click.style(f'  已计算 {len(df.columns)} 项指标', fg='green'))

        # 个股趋势：使用含预热期的完整个股日线（不含 ADR）
        stock_trend = None
        if not is_index:
            stock_trend = analyze_stock_trend(df, end_date=end_date)
            stock_trend['symbol'] = symbol
            stock_trend['stock_name'] = stock_name

        # 信号分析与报告输出从 start 日期开始：忽略前推的预热期数据
        df = _truncate_analysis_df(df, start_date)
        click.echo(click.style(f'  分析期 {len(df)} 条（预热期数据已忽略）', fg='green'))

        # 初始化图表生成器
        date_tag = _build_date_tag(start_date, end_date)
        chart_gen = ChartGenerator(output_dir=output, prefix=symbol, date_tag=date_tag)

        # 注入股票代码供策略使用
        df.attrs['symbol'] = symbol
        df.attrs['stock_name'] = stock_name
        df.attrs['ref_date'] = end_date
        df.attrs['limit_skip'] = bool(is_index or is_etf)

        stock_label = f'{symbol}({stock_name})' if stock_name else symbol

        # 确定要运行的策略列表和模式标签
        if is_index:
            strategy_map = INDEX_STRATEGY_MAP
            all_strategies_list = ALL_INDEX_STRATEGIES
            mode_label = '指数（通用策略）'
        elif is_etf:
            strategy_map = ETF_STRATEGY_MAP
            all_strategies_list = ALL_ETF_SELECTION
            mode_label = 'ETF专用'
        else:
            strategy_map = STRATEGY_MAP
            all_strategies_list = ALL_SELECTION
            mode_label = '个股通用'

        click.echo(click.style(f'  策略模式: {mode_label}', fg='white'))

        if strategy == 'all':
            strategies_to_run = all_strategies_list
            strategy_enhance = {sk: 0 for sk in strategies_to_run}
        else:
            strategies_to_run = []
            strategy_enhance = {}
            for s in _split_strategies(strategy):
                base_key, _ = _parse_strategy_spec(s)
                if base_key not in strategy_map:
                    avail = ', '.join(strategy_map.keys())
                    click.echo(click.style(f'  错误: 未知策略 "{base_key}"，可选: {avail}, all', fg='red'))
                    return
                token = base_key
                if token not in strategies_to_run:
                    strategies_to_run.append(token)
                strategy_enhance[token] = 0

        # 运行策略
        all_results = {}
        all_risks = {}
        all_charts = {}
        all_signals = {}

        for sk in strategies_to_run:
            click.echo(click.style(f'  正在运行策略: {_strat_label(sk, strategy_map)}...', fg='blue'))
            result, chart_paths, sname, signals = _run_single_analysis(
                df, sk, capital, chart_gen, prefix=stock_label, strategy_map=strategy_map,
                enhance=strategy_enhance.get(sk, 0)
            )
            all_results[sk] = result
            all_risks[sk] = result
            all_charts[sk] = chart_paths
            all_signals[sk] = signals

        # 报告展示仅包含 -g 所选策略
        strategies_to_report = strategies_to_run

        # 生成信号图表：所有股票都生成回测效果图表（含信号为观望的股票）
        if all_signals is not None:
            # 单个策略：生成独立图表；多个策略：合并为一个综合图表
            if len(strategies_to_run) == 1:
                sk = strategies_to_run[0]
                signals = all_signals.get(sk)
                if signals is not None and len(signals) > 0:
                    try:
                        sk_chart_gen = ChartGenerator(output_dir=chart_gen.output_dir, prefix=f'{symbol}_{sk}', date_tag=date_tag)
                        strategy_name = _strat_label(sk, strategy_map)
                        equity_curve = all_risks[sk].get('equity_curve') if all_risks.get(sk) else None
                        signal_path = sk_chart_gen.plot_signal_composite(
                            df, signals, strategy_key=sk,
                            title=f'{strategy_name}-回测效果 ({symbol} {stock_name})'.replace('  ', ' '),
                            equity_curve=equity_curve,
                        )
                        all_charts[sk]['signals'] = signal_path
                    except Exception:
                        all_charts[sk]['signals'] = ''
            else:
                # 多策略：合并为一个K线图（各策略指标子图 + 权益曲线汇总子图）
                strategies_data = []
                for sk in strategies_to_run:
                    signals = all_signals.get(sk)
                    if signals is None or len(signals) == 0:
                        continue
                    strategies_data.append({
                        'key': sk,
                        'name': _strat_label(sk, strategy_map),
                        'signals': signals,
                        'equity_curve': all_risks[sk].get('equity_curve') if all_risks.get(sk) else None,
                    })
                if strategies_data:
                    try:
                        multi_chart_gen = ChartGenerator(output_dir=chart_gen.output_dir, prefix=f'{symbol}_multi', date_tag=date_tag)
                        multi_path = multi_chart_gen.plot_multi_strategy_composite(
                            df, strategies_data,
                            title=f'多策略回测效果 ({symbol} {stock_name})'.replace('  ', ' '),
                        )
                        for sk in strategies_to_run:
                            all_charts[sk]['signals'] = multi_path
                    except Exception:
                        for sk in strategies_to_run:
                            all_charts[sk]['signals'] = ''

        # 输出每个策略的回测结果
        for sk in strategies_to_report:
            sname = _strat_label(sk, strategy_map)
            risk = all_risks[sk]
            charts = all_charts[sk]
            sig = all_signals[sk]

            _print_section(f'策略: {sname}')

            # 最新信号
            sig_text = _get_signal_text(sig)
            sig_display = _get_signal_text(sig)
            sig_color = _get_signal_color(sig_text)
            click.echo(click.style('    最新信号: ', fg='white', bold=True) +
                       click.style(sig_display, fg=sig_color, bold=True))

            headers = ['指标', '数值']
            rows = _format_risk_report_rows(risk)
            _print_table(headers, rows)

            if charts:
                click.echo(click.style(f'\n    生成的图表:', fg='white'))
                for chart_name, chart_path in charts.items():
                    click.echo(click.style(f'      - {chart_name}: {chart_path}', fg='green'))

        # 如果运行了多个策略，输出对比
        if len(strategies_to_report) > 1:
            _print_section('策略对比排名')
            rankings = []
            for sk in strategies_to_report:
                rank_total_return = all_risks[sk].get('total_return')
                rank_sharpe = all_risks[sk].get('sharpe_ratio')
                rank_drawdown = all_risks[sk].get('max_drawdown')
                rank_trades = all_risks[sk].get('total_trades')
                rank_signal = _get_signal_text(all_signals.get(sk))
                rankings.append((_strat_label(sk, strategy_map), rank_total_return, rank_sharpe,
                                 rank_drawdown, rank_trades, rank_signal))
            rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

            headers = ['排名', '策略', '总收益率', '夏普比率', '最大回撤', '交易次数', '最新信号']
            table_rows = []
            for i, (name, total_ret, sharpe, drawdown, trades, signal) in enumerate(rankings, 1):
                ret_str = f'{total_ret * 100:.2f}%' if total_ret is not None else 'N/A'
                shp_str = f'{sharpe:.2f}' if sharpe is not None else 'N/A'
                dd_str = f'{drawdown * 100:.2f}%' if drawdown is not None else 'N/A'
                tr_str = str(trades) if trades is not None else 'N/A'
                table_rows.append([i, name, ret_str, shp_str, dd_str, tr_str, signal])
            _print_table(headers, table_rows)

        signal_score = ''
        signal_score_total = None
        if verbose:
            try:
                from core.signal_score import compute_signal_score, combine_signals
                comp = combine_signals(
                    {sk: all_signals.get(sk) for sk in strategies_to_report})
                info = compute_signal_score(df, comp)
                signal_score = info['text']
                signal_score_total = info['total']
            except Exception:
                signal_score = ''

        signal_reasons = {}
        for sk in strategies_to_report:
            sig = all_signals.get(sk)
            if sig is None or len(sig) == 0:
                continue
            try:
                val = float(sig.iloc[-1]) if hasattr(sig, 'iloc') else float(sig[-1])
            except Exception:
                continue
            if val != 0:
                signal_reasons[sk] = _latest_signal_reason_detail(
                    sk, df, sig)

        # 信号过滤：仅当主信号符合 -f 指定条件时才输出报告
        if filter_signal and not _match_filter(filter_signal, all_signals, all_risks):
            click.echo(click.style(
                f'  信号过滤: 主信号不符合 "{_FILTER_SIGNAL_TEXT[filter_signal]}" 条件，已跳过该股票', fg='yellow'))
            return None

        # 生成 HTML 报告（单只股票时）
        if not batch_mode:
            try:
                html_path = _generate_html_report(
                    symbol, stock_name, start_date, end_date, capital,
                    is_index, strategy_map, strategies_to_report,
                    all_results, all_risks, all_charts,
                    all_signals=all_signals,
                    latest_close=latest_close,
                    latest_date=latest_date,
                    trading_days=len(df),
                    report_filename=output_file,
                    cmd_params=cmd_params,
                    strategy=strategy,
                    mode=mode,
                    market_trend=_get_market_trend(),
                    signal_reasons=signal_reasons,
                    stock_trend=stock_trend,
                )
                click.echo(click.style(f'\n  HTML报告: {html_path}', fg='green'))
            except Exception as e:
                click.echo(click.style(f'\n  HTML报告生成失败: {e}', fg='yellow'))

        click.echo()
        click.echo(click.style('  分析完成!', fg='green', bold=True))
        click.echo()

        # 返回数据供批量报告使用
        return {
            'symbol': symbol,
            'stock_name': stock_name,
            'latest_close': latest_close,
            'latest_date': latest_date,
            'start_date': start_date,
            'end_date': end_date,
            'trading_days': len(df),
            'capital': capital,
            'is_index': is_index,
            # 仅保留策略名（类/闭包不可跨进程 pickle）
            'strategy_map': _build_report_strategy_map(strategy_map, strategies_to_report),
            'strategies_to_run': strategies_to_report,
            'all_results': all_results,
            'all_risks': all_risks,
            'all_charts': all_charts,
            'all_signals': all_signals,
            'signal_reasons': signal_reasons,
            'signal_score': signal_score,
            'signal_score_total': signal_score_total,
            'stock_trend': stock_trend,
        }

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


def _analyze_single_worker(args):
    """多进程 worker：分析单只股票，捕获其控制台输出，返回 (raw_symbol, data, 输出文本)"""
    (raw_symbol, start, end, strategy, is_index, capital, output, output_file,
     batch_mode, cmd_params, mode, filter_signal, chart,
     market_dates, verbose) = args
    set_market_trade_dates_override(market_dates)
    buf = io.StringIO()
    data = None
    try:
        with contextlib.redirect_stdout(buf):
            data = _analyze_single(raw_symbol, start, end, strategy, is_index, capital,
                                   output, output_file, batch_mode=batch_mode, cmd_params=cmd_params,
                                   mode=mode, filter_signal=filter_signal, chart=chart,
                                   verbose=verbose)
    except Exception as e:
        buf.write(f'\n  异常: {e}\n')
    return raw_symbol, data, buf.getvalue()


def _analyze_pool_worker(worker_id, task_queue, result_queue):
    """长期运行的 worker：从自己的任务队列取任务，完成后写回结果队列。"""
    while True:
        item = task_queue.get()
        if item is None:
            break
        idx, task = item
        try:
            _, data, out = _analyze_single_worker(task)
        except BaseException as e:
            data, out = None, f'  异常: {e}'
        result_queue.put((worker_id, idx, data, out))


def _run_analyze_tasks(tasks, symbols, n_workers, per_stock_timeout=180):
    """按 worker 独立分配任务，并对每个任务单独计时。

    每个任务在分配给 worker 时开始计时；超过 per_stock_timeout 秒只终止
    该任务对应的 worker，并启动替代进程继续处理剩余任务。
    """
    import multiprocessing as mp
    import queue as _queue
    from collections import deque

    total = len(tasks)
    if total == 0:
        return []

    methods = mp.get_all_start_methods()
    ctx = mp.get_context('fork') if 'fork' in methods else mp.get_context()
    result_queue = ctx.Queue()
    workers = {}   # wid -> (process, task_queue)
    idle = set()
    active = {}    # wid -> (task_idx, start_monotonic)
    done = set()
    results = [None] * total
    progress = {'dots': 1}
    problems = []

    click.echo(click.style('  分析进度: ', fg='blue'), nl=False)
    click.echo('.', nl=False)
    sys.stdout.flush()

    def _report(idx, data, out, timed_out=False):
        if idx in done:
            return
        done.add(idx)
        results[idx] = data

        target_dots = max(1, int(len(done) / total * 20))
        if target_dots > progress['dots']:
            click.echo('.' * (target_dots - progress['dots']), nl=False)
            sys.stdout.flush()
            progress['dots'] = target_dots

        if data is None:
            label = str(symbols[idx])
            if '信号过滤' in (out or ''):
                problems.append((f'  {label} 已跳过（信号过滤）', 'yellow'))
            elif timed_out:
                problems.append((f'  {label} 分析超时', 'yellow'))
            else:
                problems.append((f'  {label} 分析失败', 'red'))

    def _start_worker(wid):
        task_q = ctx.Queue()
        proc = ctx.Process(target=_analyze_pool_worker,
                           args=(wid, task_q, result_queue),
                           daemon=True)
        proc.start()
        workers[wid] = (proc, task_q)
        idle.add(wid)

    def _kill_worker(wid):
        idle.discard(wid)
        item = workers.pop(wid, None)
        if item is None:
            return
        proc, task_q = item
        try:
            task_q.close()
        except Exception:
            pass
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=1)

    for wid in range(n_workers):
        _start_worker(wid)

    pending = deque(enumerate(tasks))
    try:
        while pending or active:
            # 分发任务：每个 worker 一次只处理一个任务
            while pending and idle:
                wid = idle.pop()
                idx, task = pending.popleft()
                proc, task_q = workers[wid]
                try:
                    task_q.put((idx, task))
                except Exception as e:
                    _kill_worker(wid)
                    _report(idx, None, f'  异常: 无法提交任务: {e}')
                    if pending:
                        _start_worker(wid)
                    continue
                active[wid] = (idx, time.monotonic())

            # 收集已完成任务
            got_result = False
            while True:
                try:
                    wid, idx, data, out = result_queue.get_nowait()
                except _queue.Empty:
                    break
                got_result = True
                active.pop(wid, None)
                if idx not in done:
                    _report(idx, data, out)
                proc, _ = workers.get(wid, (None, None))
                if proc is not None and proc.is_alive() and wid not in idle:
                    idle.add(wid)

            # 独立的单任务超时检查
            now = time.monotonic()
            for wid, (idx, started) in list(active.items()):
                if now - started <= per_stock_timeout:
                    continue
                # 超时前再确认一次结果是否刚好到达，避免误杀已完成任务
                try:
                    msg = result_queue.get(timeout=0.2)
                except _queue.Empty:
                    msg = None
                if msg is not None:
                    rwid, ridx, rdata, rout = msg
                    active.pop(rwid, None)
                    if ridx not in done:
                        _report(ridx, rdata, rout)
                    proc, _ = workers.get(rwid, (None, None))
                    if proc is not None and proc.is_alive():
                        idle.add(rwid)
                if idx in done:
                    continue

                _kill_worker(wid)
                active.pop(wid, None)
                _report(idx, None,
                        f'  超时: 分析超过 {per_stock_timeout} 秒，已回收对应进程',
                        timed_out=True)
                if pending:
                    _start_worker(wid)

            # worker 意外退出处理
            for wid, (proc, task_q) in list(workers.items()):
                if proc.is_alive():
                    continue
                if wid in active:
                    idx, _ = active.pop(wid)
                    _report(idx, None,
                            f'  异常: 工作进程意外退出 (exitcode={proc.exitcode})')
                idle.discard(wid)
                try:
                    task_q.close()
                except Exception:
                    pass
                workers.pop(wid, None)
                if pending:
                    _start_worker(wid)

            if (pending or active) and not got_result:
                time.sleep(0.1)
    finally:
        for wid in list(workers):
            try:
                workers[wid][1].put(None)
            except Exception:
                pass
        for wid in list(workers):
            proc, task_q = workers[wid]
            proc.join(timeout=1)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1)
                if proc.is_alive():
                    proc.kill()
                    proc.join(timeout=1)
            try:
                task_q.close()
            except Exception:
                pass
        try:
            result_queue.close()
        except Exception:
            pass

    click.echo()
    for message, color in problems:
        click.echo(click.style(message, fg=color))

    return [d for d in results if d is not None]


def _run_rotation_analysis(symbols, start, end, strategy, capital, output, output_file, cmd_params='',
                           source=None, mode=0, chart=False):
    """模式 1：资金利用最大化轮动选股

    每天先检查所持股票是否有卖出信号，有则全部卖出；再遍历股票池，
    对出现买入信号(1)的股票用全部剩余资金买入；若剩余资金 < 总资金/10
    且有持仓权益 > 总资金/3，则卖出该持仓的一半释放资金后再买入。
    分析周期内持仓股票可能变化。支持 1 只及以上股票，报告格式与模式 2/3/4 一致。
    """
    if len(symbols) < 1:
        click.echo(click.style('  错误: 轮动模式至少需要 1 只股票', fg='red'))
        return

    start_date, end_date = _parse_dates(start, end)

    # 确定轮动所用策略
    if strategy == 'all':
        strategy_key = 'ma1030'
        strategy_enhance = 0
    else:
        strategy_key, strategy_enhance = _parse_strategy_spec(_split_strategies(strategy)[0])
    if strategy_key in STRATEGY_MAP:
        strat = _build_strategy_instance(strategy_key, STRATEGY_MAP, strategy_enhance)
        strategy_name = STRATEGY_MAP[strategy_key][0]
    else:
        click.echo(click.style(f'  错误: 未知策略 "{strategy_key}"', fg='red'))
        return

    click.echo(click.style(f'\n  轮动策略: {strategy_name}', fg='white'))
    click.echo(click.style(f'  股票池: {len(symbols)} 只  区间: {start_date} ~ {end_date}', fg='white'))

    # 获取每只股票的数据与信号
    fetcher = DataFetcher()
    stock_data = {}  # symbol -> (df, signals)
    for idx, symbol in enumerate(symbols):
        click.echo(click.style(f'  [{idx+1}/{len(symbols)}] 计算 {symbol} 信号...', fg='blue'))
        try:
            df = fetcher.get_stock_data(symbol, _warmup_fetch_start(start_date), end_date)
            if df is None or df.empty:
                continue
            df = add_all_indicators(df)
            df = _truncate_analysis_df(df, start_date)
            df.attrs['symbol'] = symbol
            sig = _generate_effective_signals(strat, df)
            stock_data[symbol] = (df, sig)
        except Exception as e:
            click.echo(click.style(f'  {symbol} 信号计算失败: {e}', fg='yellow'))

    if len(stock_data) < 1:
        click.echo(click.style('  错误: 有效股票数据不足', fg='red'))
        return

    # 日期对齐（取并集）
    all_dates = sorted(set().union(*[set(df.index) for df, _ in stock_data.values()]))

    total = float(capital)          # 总资金（固定为初始资金）
    cash = float(capital)
    cash_by_date = {}               # 交易日 -> 当日剩余资金（用于按日历日累计闲置资金）
    positions = {}                  # symbol -> {'shares','entry_date','entry_price'}
    events = []                     # (date, sell_symbol, sell_amount, buy_symbol, buy_amount, equity, hold_count, cleared_symbols, cash)
    trade_stats = {}                # symbol -> {'buy_amount','sell_amount','first_buy','last_sell'}
    daily_eq_sum = {}               # symbol -> 持股期间每日市值之和（用于计算日平均权益）
    daily_eq_count = {}             # symbol -> 持股天数
    equity_curve = []               # 每日权益（用于计算夏普比率与最大回撤）

    def _close_price(symbol, d):
        edf = stock_data[symbol][0]
        p = edf['close'].asof(d)
        if pd.isna(p):
            p = edf['close'].iloc[-1]
        return float(p)

    def _equity(d):
        eq = cash
        for sym, pos in positions.items():
            eq += pos['shares'] * _close_price(sym, d)
        return eq

    def _accumulate_daily_equity(d):
        for sym, pos in positions.items():
            mv = pos['shares'] * _close_price(sym, d)
            daily_eq_sum[sym] = daily_eq_sum.get(sym, 0.0) + mv
            daily_eq_count[sym] = daily_eq_count.get(sym, 0) + 1
        cash_by_date[d.strftime('%Y-%m-%d')] = cash
        equity_curve.append(_equity(d))

    for date in all_dates:
        # 步骤0：卖出检查 —— 持仓中信号为 -1 的全部清仓
        sold_list = []
        sold_amount = 0.0
        cleared_list = []
        for symbol in list(positions.keys()):
            df, sig = stock_data[symbol]
            if date in df.index and sig.loc[date] < 0:
                pos = positions[symbol]
                price = float(df['close'].loc[date])
                amount = pos['shares'] * price
                cash += amount
                sold_amount += amount
                del positions[symbol]
                sold_list.append(symbol)
                cleared_list.append(symbol)
                st = trade_stats.setdefault(symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                st['sell_amount'] += amount
                st['last_sell'] = date

        # 步骤1：买入检查 —— 遍历股票池，对买入信号(1)的股票用全部剩余资金买入；
        # 若有多个股票满足条件，只买入第一只符合条件的股票
        buy_symbols = []
        buy_amount_total = 0.0
        sell_symbols = list(sold_list)
        sell_amount_total = sold_amount
        for symbol in symbols:
            if symbol not in stock_data or symbol in positions:
                continue
            df, sig = stock_data[symbol]
            if date in df.index and sig.loc[date] > 0:
                price = float(df['close'].loc[date])
                # 剩余资金不足时，若有持仓权益 > 总资金/3，卖出该持仓的一半释放资金
                if cash < total / 10.0:
                    sell_target = None
                    sell_value = 0.0
                    for held in positions:
                        held_value = positions[held]['shares'] * _close_price(held, date)
                        if held_value > total / 3.0 and held_value > sell_value:
                            sell_target = held
                            sell_value = held_value
                    if sell_target is not None:
                        pos = positions[sell_target]
                        held_price = _close_price(sell_target, date)
                        sell_shares = pos['shares'] / 2.0
                        pos['shares'] -= sell_shares
                        cash += sell_shares * held_price
                        sell_symbols.append(sell_target)
                        sell_amount_total += sell_shares * held_price
                        st = trade_stats.setdefault(sell_target, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                        st['sell_amount'] += sell_shares * held_price
                        st['last_sell'] = date
                buy_amount = cash
                if buy_amount <= 0:
                    continue
                shares = buy_amount / price
                cash -= buy_amount
                positions[symbol] = {'shares': shares, 'entry_date': date, 'entry_price': price}
                buy_symbols.append(symbol)
                buy_amount_total += buy_amount
                st = trade_stats.setdefault(symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                st['buy_amount'] += buy_amount
                break

        # 步骤2：记录当日交易事件（有买卖行为时）
        if buy_symbols or sell_symbols:
            sell_str = '/'.join(sell_symbols) if sell_symbols else None
            buy_str = '/'.join(buy_symbols) if buy_symbols else None
            events.append((date, sell_str, sell_amount_total, buy_str, buy_amount_total,
                           _equity(date), len(positions), cleared_list, cash))
        _accumulate_daily_equity(date)

    # 期末市值（不强制平仓，保留期末持仓）
    final_value = cash
    for symbol, pos in positions.items():
        edf = stock_data[symbol][0]
        final_value += pos['shares'] * float(edf['close'].iloc[-1])

    total_return = (final_value - total) / total if total else 0.0
    total_shares = sum(pos['shares'] for pos in positions.values())
    equity_curve.append(final_value)
    sharpe, max_dd = _calc_sharpe_drawdown(equity_curve)

    # 闲置资金天数 = 分析周期内每日剩余资金累计 / 资金总额
    period_days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days + 1
    cum_cash = 0.0
    carry = float(capital)
    day0 = pd.Timestamp(start_date)
    for i in range(period_days):
        day_key = (day0 + pd.Timedelta(days=i)).strftime('%Y-%m-%d')
        if day_key in cash_by_date:
            carry = cash_by_date[day_key]
        cum_cash += carry
    idle_days = (cum_cash / total) if total else 0.0

    # 解析持仓股票名称
    name_map = {}
    held = set(positions.keys()) | set(trade_stats.keys())
    for e in events:
        if e[1]:
            held.update(str(e[1]).split('/'))
        if e[3]:
            held.update(str(e[3]).split('/'))
    for s in held:
        code, name = _resolve_symbol(s)
        name_map[s] = name if name else s

    # 个股收益统计
    stock_stats = []
    end_dt = all_dates[-1] if all_dates else None
    for sym, st in trade_stats.items():
        buy_amt = st['buy_amount']
        sell_amt = st['sell_amount']
        final_mv = 0.0
        if sym in positions:
            edf = stock_data[sym][0]
            final_mv = positions[sym]['shares'] * float(edf['close'].iloc[-1])
        total_profit = sell_amt + final_mv - buy_amt
        hold_cnt = daily_eq_count.get(sym, 0)
        avg_eq = (daily_eq_sum.get(sym, 0.0) / hold_cnt) if hold_cnt > 0 else 0.0
        ret = (total_profit / avg_eq) if avg_eq else 0.0
        exit_date = st['last_sell'] if st['last_sell'] is not None else end_dt
        hold_days = hold_cnt
        stock_stats.append((sym, buy_amt, sell_amt, final_mv, total_profit, ret, hold_days))
    stock_stats.sort(key=lambda x: x[4], reverse=True)

    # 打印结果
    click.echo()
    _print_section('多持仓组合结果')
    click.echo(click.style(f'  总权益: {final_value:,.2f}', fg='green', bold=True))
    click.echo(click.style(f'  总收益率: {total_return * 100:.2f}%', fg='green' if total_return >= 0 else 'red', bold=True))
    click.echo(click.style(f'  夏普比率: {sharpe:.2f}', fg='white'))
    click.echo(click.style(f'  最大回撤: {max_dd * 100:.2f}%', fg='white'))
    click.echo(click.style(f'  持股数量: {total_shares:,.0f} 股', fg='white'))
    click.echo(click.style(f'  交易次数: {len(events)}', fg='white'))
    click.echo(click.style(f'  闲置资金天数/分析周期天数: {idle_days:.2f} / {period_days}', fg='white'))

    _print_section('个股收益统计')
    headers2 = ['股票代码', '股票名称', '买入金额', '卖出金额', '期末市值', '总收益', '收益率', '持股天数']
    rows2 = []
    for sym, buy_amt, sell_amt, final_mv, total_profit, ret, hold_days in stock_stats:
        rows2.append([sym, name_map.get(sym, sym), f'{buy_amt:,.0f}', f'{sell_amt:,.0f}',
                      f'{final_mv:,.0f}', f'{total_profit:,.0f}', f'{ret * 100:.2f}%', str(hold_days)])
    _print_table(headers2, rows2)

    # 生成 HTML 报告
    try:
        html_path = _generate_portfolio_html(
            symbols, strategy_name, start_date, end_date, capital, final_value,
            total_return, events, positions, stock_data, name_map, stock_stats,
            idle_days, period_days, output_file, cmd_params,
            source=source, strategy=strategy, mode=mode, sharpe=sharpe, max_drawdown=max_dd,
            output=output, chart=chart
        )
        click.echo(click.style(f'\n  组合报告: {html_path}', fg='green'))
    except Exception as e:
        click.echo(click.style(f'\n  组合报告生成失败: {e}', fg='yellow'))


def _run_portfolio_analysis(symbols, start, end, strategy, capital, output, output_file,
                            weak_trend_cancel=False, trend_priority_sell=False, cmd_params='',
                            source=None, mode=0, chart=False):
    """模式 2：多持仓资金利用最大化

    每天先按 symbols 顺序找卖出信号（信号 == -1）的持仓并全部卖出；
    再找第一个买入信号（多个则只取第一个）的股票，剩余资金 > 1/10 总资金时
    用剩余资金买入（单只买入不超过 1/3 总资金）；不足则从持仓中选出
    MACD 柱下降（今日 < 昨日）的一只股票，按持仓市值 > 总资金 20% 卖一半、
    否则全卖释放资金买入；若持仓中无 MACD 柱下降股票，则放弃当日买入。
    支持 1 只及以上股票（1 只作为多只的特例处理）。

    weak_trend_cancel: 若为 True，则在现金不足分支中，若买入股当前趋势为弱势，
        取消被动卖出和买入。
    trend_priority_sell: 若为 True，则在多个候选卖出股票中按趋势优先级选择
        （弱势 > 横盘震荡，取消强势股卖出），而非最早买入者。
    """
    if len(symbols) < 1:
        click.echo(click.style('  错误: 该模式至少需要 1 只股票', fg='red'))
        return

    start_date, end_date = _parse_dates(start, end)

    # 确定策略
    if strategy == 'all':
        strategy_key = 'ma1030'
        strategy_enhance = 0
    else:
        strategy_key, strategy_enhance = _parse_strategy_spec(_split_strategies(strategy)[0])
    if strategy_key in STRATEGY_MAP:
        strat = _build_strategy_instance(strategy_key, STRATEGY_MAP, strategy_enhance)
        strategy_name = STRATEGY_MAP[strategy_key][0]
    else:
        click.echo(click.style(f'  错误: 未知策略 "{strategy_key}"', fg='red'))
        return

    click.echo(click.style(f'\n  组合策略: {strategy_name}', fg='white'))
    click.echo(click.style(f'  股票池: {len(symbols)} 只  区间: {start_date} ~ {end_date}', fg='white'))

    # 获取每只股票的数据与信号
    fetcher = DataFetcher()
    stock_data = {}
    for idx, symbol in enumerate(symbols):
        click.echo(click.style(f'  [{idx+1}/{len(symbols)}] 计算 {symbol} 信号...', fg='blue'))
        try:
            df = fetcher.get_stock_data(symbol, _warmup_fetch_start(start_date), end_date)
            if df is None or df.empty:
                continue
            df = add_all_indicators(df)
            df = _truncate_analysis_df(df, start_date)
            df.attrs['symbol'] = symbol
            sig = _generate_effective_signals(strat, df)
            stock_data[symbol] = (df, sig)
        except Exception as e:
            click.echo(click.style(f'  {symbol} 信号计算失败: {e}', fg='yellow'))

    if len(stock_data) < 1:
        click.echo(click.style('  错误: 有效股票数据不足 1 只', fg='red'))
        return

    all_dates = sorted(set().union(*[set(df.index) for df, _ in stock_data.values()]))

    total = float(capital)          # 总资金（固定为初始资金）
    cash = float(capital)
    cash_by_date = {}               # 交易日 -> 当日剩余资金（用于按日历日累计闲置资金）
    positions = {}                  # symbol -> {'shares','entry_date','avg_price'}
    events = []                     # (date, sell_symbol, sell_amount, buy_symbol, buy_amount, equity, hold_count, cleared_symbols, cash)
    trade_stats = {}                # symbol -> {'buy_amount','sell_amount','first_buy','last_sell'}
    daily_eq_sum = {}               # symbol -> 持股期间每日市值之和（用于计算日平均权益）
    daily_eq_count = {}             # symbol -> 持股天数
    equity_curve = []               # 每日权益（用于计算夏普比率与最大回撤）

    def _close_price(symbol, d):
        edf = stock_data[symbol][0]
        p = edf['close'].asof(d)
        if pd.isna(p):
            p = edf['close'].iloc[-1]
        return float(p)

    def _equity(d):
        eq = cash
        for sym, pos in positions.items():
            eq += pos['shares'] * _close_price(sym, d)
        return eq

    def _macd_declining(symbol, d):
        """判断该股在 d 当日（或之前最近交易日）MACD 柱是否较前一交易日下降"""
        edf = stock_data[symbol][0]
        sub = edf.loc[edf.index <= d]
        if len(sub) < 2 or 'MACD_BAR' not in sub.columns:
            return False
        today_bar = sub['MACD_BAR'].iloc[-1]
        yest_bar = sub['MACD_BAR'].iloc[-2]
        if pd.isna(today_bar) or pd.isna(yest_bar):
            return False
        return today_bar < yest_bar

    def _accumulate_daily_equity(d):
        """累计每个持仓股票当日市值，并记录当日剩余资金"""
        for sym, pos in positions.items():
            mv = pos['shares'] * _close_price(sym, d)
            daily_eq_sum[sym] = daily_eq_sum.get(sym, 0.0) + mv
            daily_eq_count[sym] = daily_eq_count.get(sym, 0) + 1
        cash_by_date[d.strftime('%Y-%m-%d')] = cash
        equity_curve.append(_equity(d))

    def _buy_cash(cash_avail):
        """计算实际买入金额：单只买入不超过 1/3 总资金"""
        return min(cash_avail, total / 3.0)

    def _trend_rank(symbol, d):
        """趋势卖出优先级：弱势 0 > 横盘震荡 1（强势不参与被动卖出）"""
        t = classify_trend(stock_data[symbol][0], d)
        return {'弱势': 0, '横盘震荡': 1}.get(t, 1)

    for date in all_dates:
        # 步骤0：找卖出信号 —— 按 symbols 顺序遍历股票池，信号为 -1 的持仓全部卖出
        sold_list = []
        sold_amount = 0.0
        cleared_list = []   # 本日清仓（全部卖出）的股票
        for symbol in symbols:
            if symbol not in stock_data or symbol not in positions:
                continue
            df, sig = stock_data[symbol]
            if date in df.index and sig.loc[date] < 0:
                pos = positions[symbol]
                cur_price = _close_price(symbol, date)
                amount = pos['shares'] * cur_price
                cash += amount
                sold_amount += amount
                del positions[symbol]
                sold_list.append(symbol)
                cleared_list.append(symbol)
                st = trade_stats.setdefault(symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                st['sell_amount'] += amount
                st['last_sell'] = date

        # 步骤1：找第一个买入信号的股票
        buy_symbol = None
        buy_price = None
        for symbol in symbols:
            if symbol not in stock_data:
                continue
            df, sig = stock_data[symbol]
            if date in df.index and sig.loc[date] > 0:
                buy_symbol = symbol
                buy_price = float(df['close'].loc[date])
                break

        if buy_symbol is None:
            if sold_list:
                events.append((date, '/'.join(sold_list), sold_amount, None, 0.0, _equity(date), len(positions), cleared_list, cash))
            _accumulate_daily_equity(date)
            continue

        sell_symbol = None
        sell_amount = 0.0
        buy_amount = 0.0

        if cash > total * 0.1:
            # 剩余资金充足：买入（单只不超过 1/3 总资金）
            buy_amount = _buy_cash(cash)
            buy_shares = buy_amount / buy_price
            cash -= buy_amount
            if buy_symbol in positions:
                p = positions[buy_symbol]
                p['shares'] += buy_shares
                p['avg_price'] = (p['avg_price'] * (p['shares'] - buy_shares) + buy_price * buy_shares) / p['shares']
            else:
                positions[buy_symbol] = {'shares': buy_shares, 'entry_date': date, 'avg_price': buy_price}
            st = trade_stats.setdefault(buy_symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
            st['buy_amount'] += buy_amount
        else:
            # 剩余资金不足：若买入股趋势为弱势则取消被动卖出和买入
            weak_cancel = (weak_trend_cancel
                           and classify_trend(stock_data[buy_symbol][0], date) == '弱势')
            if not weak_cancel:
                # 在持仓中找 MACD 柱下降的股票作为卖出对象
                # 若当日已有主动卖出（信号 == -1），则不再执行被动卖出
                if not sold_list and positions:
                    if trend_priority_sell:
                        # 模式4：取消强势股卖出，仅弱势/横盘震荡参与被动卖出
                        candidates = [s for s in positions if _macd_declining(s, date)
                                      and classify_trend(stock_data[s][0], date) != '强势']
                    else:
                        candidates = [s for s in positions if _macd_declining(s, date)]
                    if candidates:
                        if trend_priority_sell:
                            sell_target = min(candidates, key=lambda s: (_trend_rank(s, date), positions[s]['entry_date']))
                        else:
                            sell_target = min(candidates, key=lambda s: positions[s]['entry_date'])
                        pos = positions[sell_target]
                        cur_price = _close_price(sell_target, date)
                        pos_value = pos['shares'] * cur_price
                        # 被动卖出：持仓市值 > 总资金×20% 卖一半，否则全卖
                        if pos_value > total * 0.2:
                            sell_shares = pos['shares'] / 2.0
                            pos['shares'] -= sell_shares
                            cash += sell_shares * cur_price
                            sell_symbol = sell_target
                            sell_amount = sell_shares * cur_price
                        else:
                            sold_shares = pos['shares']
                            cash += sold_shares * cur_price
                            del positions[sell_target]
                            sell_symbol = sell_target
                            sell_amount = sold_shares * cur_price
                            cleared_list.append(sell_target)
                        st = trade_stats.setdefault(sell_target, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                        st['sell_amount'] += sell_amount
                        st['last_sell'] = date
                # 只有发生卖出释放资金时才买入；否则放弃当日买入
                if sell_symbol is not None and cash > 0:
                    buy_amount = _buy_cash(cash)
                    buy_shares = buy_amount / buy_price
                    cash -= buy_amount
                    if buy_symbol in positions:
                        p = positions[buy_symbol]
                        p['shares'] += buy_shares
                        p['avg_price'] = (p['avg_price'] * (p['shares'] - buy_shares) + buy_price * buy_shares) / p['shares']
                    else:
                        positions[buy_symbol] = {'shares': buy_shares, 'entry_date': date, 'avg_price': buy_price}
                    st = trade_stats.setdefault(buy_symbol, {'buy_amount': 0.0, 'sell_amount': 0.0, 'first_buy': date, 'last_sell': None})
                    st['buy_amount'] += buy_amount

        # 当日买入被放弃（无足够资金且无卖出）时，买入股票置空
        if buy_amount <= 0:
            buy_symbol = None

        # 合并本日所有卖出（卖出信号 + 资金不足时的 MACD 卖出）
        day_sell_symbols = list(sold_list)
        day_sell_amount = sold_amount
        if sell_symbol is not None:
            day_sell_symbols.append(sell_symbol)
            day_sell_amount += sell_amount
        day_sell_str = '/'.join(day_sell_symbols) if day_sell_symbols else None

        events.append((date, day_sell_str, day_sell_amount, buy_symbol, buy_amount, _equity(date), len(positions), cleared_list, cash))
        _accumulate_daily_equity(date)

    # 期末市值
    final_value = cash
    for symbol, pos in positions.items():
        edf = stock_data[symbol][0]
        final_value += pos['shares'] * float(edf['close'].iloc[-1])

    total_return = (final_value - total) / total if total else 0.0
    total_shares = sum(pos['shares'] for pos in positions.values())
    equity_curve.append(final_value)
    sharpe, max_dd = _calc_sharpe_drawdown(equity_curve)

    # 闲置资金天数 = 分析周期内每日剩余资金累计 / 资金总额
    # 按日历日累计：非交易日沿用最近交易日的剩余资金
    period_days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days + 1
    cum_cash = 0.0
    carry = float(capital)
    day0 = pd.Timestamp(start_date)
    for i in range(period_days):
        day_key = (day0 + pd.Timedelta(days=i)).strftime('%Y-%m-%d')
        if day_key in cash_by_date:
            carry = cash_by_date[day_key]
        cum_cash += carry
    idle_days = (cum_cash / total) if total else 0.0

    # 解析持仓股票名称
    name_map = {}
    held = set(positions.keys()) | set(trade_stats.keys())
    for e in events:
        if e[1]:
            held.update(str(e[1]).split('/'))
        if e[3]:
            held.add(e[3])
    for s in held:
        code, name = _resolve_symbol(s)
        name_map[s] = name if name else s

    # 个股收益统计
    stock_stats = []
    end_dt = all_dates[-1] if all_dates else None
    for sym, st in trade_stats.items():
        buy_amt = st['buy_amount']
        sell_amt = st['sell_amount']
        final_mv = 0.0
        if sym in positions:
            edf = stock_data[sym][0]
            final_mv = positions[sym]['shares'] * float(edf['close'].iloc[-1])
        total_profit = sell_amt + final_mv - buy_amt
        hold_cnt = daily_eq_count.get(sym, 0)
        avg_eq = (daily_eq_sum.get(sym, 0.0) / hold_cnt) if hold_cnt > 0 else 0.0
        ret = (total_profit / avg_eq) if avg_eq else 0.0
        exit_date = st['last_sell'] if st['last_sell'] is not None else end_dt
        hold_days = hold_cnt
        stock_stats.append((sym, buy_amt, sell_amt, final_mv, total_profit, ret, hold_days))
    stock_stats.sort(key=lambda x: x[4], reverse=True)

    # 打印结果
    click.echo()
    _print_section('多持仓组合结果')
    click.echo(click.style(f'  总权益: {final_value:,.2f}', fg='green', bold=True))
    click.echo(click.style(f'  总收益率: {total_return * 100:.2f}%', fg='green' if total_return >= 0 else 'red', bold=True))
    click.echo(click.style(f'  夏普比率: {sharpe:.2f}', fg='white'))
    click.echo(click.style(f'  最大回撤: {max_dd * 100:.2f}%', fg='white'))
    click.echo(click.style(f'  持股数量: {total_shares:,.0f} 股', fg='white'))
    click.echo(click.style(f'  交易次数: {len(events)}', fg='white'))
    click.echo(click.style(f'  闲置资金天数/分析周期天数: {idle_days:.2f} / {period_days}', fg='white'))

    _print_section('个股收益统计')
    headers2 = ['股票代码', '股票名称', '买入金额', '卖出金额', '期末市值', '总收益', '收益率', '持股天数']
    rows2 = []
    for sym, buy_amt, sell_amt, final_mv, total_profit, ret, hold_days in stock_stats:
        rows2.append([sym, name_map.get(sym, sym), f'{buy_amt:,.0f}', f'{sell_amt:,.0f}',
                      f'{final_mv:,.0f}', f'{total_profit:,.0f}', f'{ret * 100:.2f}%', str(hold_days)])
    _print_table(headers2, rows2)

    try:
        html_path = _generate_portfolio_html(
            symbols, strategy_name, start_date, end_date, capital, final_value,
            total_return, events, positions, stock_data, name_map, stock_stats,
            idle_days, period_days, output_file, cmd_params,
            source=source, strategy=strategy, mode=mode, sharpe=sharpe, max_drawdown=max_dd,
            output=output, chart=chart
        )
        click.echo(click.style(f'\n  组合报告: {html_path}', fg='green'))
    except Exception as e:
        click.echo(click.style(f'\n  组合报告生成失败: {e}', fg='yellow'))


def _run_portfolio_analysis_v3(symbols, start, end, strategy, capital, output, output_file, cmd_params='',
                               source=None, mode=3, chart=False):
    """模式 3：多持仓强化优化

    在模式 2（多持仓资金利用最大化）基础上，进一步加强对买卖信号
    与股票选择的优化。当前已引入个股趋势判断：现金不足时，若买入股当前
    趋势为弱势，则取消被动卖出和买入。
    """
    _run_portfolio_analysis(symbols, start, end, strategy, capital, output, output_file,
                            weak_trend_cancel=True, cmd_params=cmd_params, source=source, mode=mode,
                            chart=chart)


def _run_portfolio_analysis_v4(symbols, start, end, strategy, capital, output, output_file, cmd_params='',
                               source=None, mode=4, chart=False):
    """模式 4：多持仓强化优化（趋势择股卖出）

    在模式 3 基础上，修改卖出股票的选择方式：当多个持仓同时满足卖出条件时，
    取消强势股卖出，仅在弱势与横盘震荡之间按趋势值优先选择弱势的股票。
    """
    _run_portfolio_analysis(symbols, start, end, strategy, capital, output, output_file,
                            weak_trend_cancel=True, trend_priority_sell=True, cmd_params=cmd_params,
                            source=source, mode=mode, chart=chart)


def _generate_portfolio_html(pool_symbols, strategy_name, start_date, end_date, capital,
                             final_value, total_return, events, positions, stock_data,
                             name_map, stock_stats, idle_days, period_days, output_file,
                             cmd_params='', source=None, strategy=None, mode=0,
                             sharpe=0.0, max_drawdown=0.0, output='./output', chart=False):
    """生成多持仓组合 HTML 报告"""
    if output_file:
        report_path = output_file
    else:
        days = (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days
        report_path = _build_report_filename(source, end_date, days, strategy, mode,
                                             profit=_profit_int(total_return))
    if not os.path.dirname(report_path):
        report_path = os.path.join('report', report_path)

    tx_rows = ''
    for date, sell_symbol, sell_amount, buy_symbol, buy_amount, equity, hold_count, cleared_symbols, cash in events:
        if sell_symbol:
            parts = []
            for s in sell_symbol.split('/'):
                nm = name_map.get(s, s)
                parts.append(f'{s} {nm}(清)' if s in cleared_symbols else f'{s} {nm}')
            sell_str = '<br>'.join(parts)
        else:
            sell_str = '-'
        sell_amt = f'{sell_amount:,.0f}' if sell_amount else '-'
        if buy_symbol:
            buy_str = '<br>'.join(f'{s} {name_map.get(s, s)}' for s in buy_symbol.split('/'))
        else:
            buy_str = '-'
        buy_amt = f'{buy_amount:,.0f}' if buy_amount else '-'
        tx_rows += f'''<tr>
            <td>{date.date()}</td><td>{buy_str}</td><td>{buy_amt}</td>
            <td>{sell_str}</td><td>{sell_amt}</td>
            <td>{equity:,.0f}</td><td>{cash:,.0f}</td><td>{hold_count}</td>
        </tr>'''

    stat_rows = ''
    for sym, buy_amt, sell_amt, final_mv, total_profit, ret, hold_days in stock_stats:
        ret_color = '#27ae60' if total_profit >= 0 else '#e74c3c'
        stat_rows += f'''<tr>
            <td>{sym}</td><td>{name_map.get(sym, sym)}</td>
            <td>{buy_amt:,.0f}</td><td>{sell_amt:,.0f}</td><td>{final_mv:,.0f}</td>
            <td style="color:{ret_color};font-weight:bold">{total_profit:,.0f}</td>
            <td style="color:{ret_color}">{ret * 100:.2f}%</td><td>{hold_days}</td>
        </tr>'''

    profit_map = {s: tp for s, _, _, _, tp, _, _ in stock_stats}
    hold_rows = ''
    total_shares = 0.0
    total_market = 0.0
    for sym, pos in positions.items():
        edf = stock_data[sym][0]
        last_price = float(edf['close'].iloc[-1])
        val = pos['shares'] * last_price
        total_shares += pos['shares']
        total_market += val
        total_profit = profit_map.get(sym, 0.0)
        cost_price = (val - total_profit) / val * last_price if val > 0 else 0.0
        hold_rows += f'<tr><td>{sym}</td><td>{name_map.get(sym, sym)}</td><td>{pos["shares"]:.0f}</td>'
        hold_rows += f'<td>{cost_price:.2f}</td><td>{last_price:.2f}</td><td>{val:,.0f}</td></tr>'
    hold_rows += f'<tr style="font-weight:bold;background:#eaf2f8"><td>合计</td><td>-</td><td>{total_shares:,.0f}</td><td>-</td><td>-</td><td>{total_market:,.0f}</td></tr>'

    pool_str = '，'.join(str(s) for s in pool_symbols)
    ret_color = '#27ae60' if total_return >= 0 else '#e74c3c'

    # 生成所有买入股票的 K 线图（价格+买卖点、成交量、MACD），内嵌到报告末尾
    import base64 as _b64

    def _img_to_b64(path):
        if not path or not os.path.exists(path):
            return ''
        with open(path, 'rb') as f:
            return _b64.b64encode(f.read()).decode()

    date_tag = _build_date_tag(start_date, end_date)
    bought_symbols = [sym for sym, buy_amt, *_ in stock_stats if buy_amt > 0]
    charts_html = ''
    if chart:
        for sym in bought_symbols:
            if sym not in stock_data:
                continue
            df, sig = stock_data[sym]
            label = f'{sym} {name_map.get(sym, sym)}'.strip()
            try:
                cg = ChartGenerator(output_dir=output, prefix=f'{sym}_portfolio', date_tag=date_tag)
                chart_path = cg.plot_signal_composite(
                    df, sig, strategy_key='macd', title=f'{label} K线与买卖点'
                )
                b64 = _img_to_b64(chart_path)
                if b64:
                    ext = os.path.splitext(chart_path)[1].lstrip('.')
                    charts_html += f'<div class="chart"><h3>{label}</h3>'
                    charts_html += f'<img src="data:image/{ext};base64,{b64}" alt="{label}" style="max-width:100%;border:1px solid #ddd;border-radius:4px;"></div>'
            except Exception as e:
                charts_html += f'<div class="chart">图表生成失败 {label}: {e}</div>'

    chart_section = '<h2>买入股票 K 线图</h2>' + charts_html if chart else ''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>多持仓组合报告</title>
<style>
body {{ font-family: 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif; max-width: 1100px; margin: 0 auto; padding: 20px; background: #fafafa; color: #2c3e50; }}
h1 {{ border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
h2 {{ border-bottom: 2px solid #bdc3c7; padding-bottom: 6px; margin-top: 30px; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: center; }}
th {{ background: #3498db; color: white; }}
tr:nth-child(even) {{ background: #f2f2f2; }}
.info {{ background: #ecf0f1; padding: 15px; border-radius: 5px; margin: 15px 0; }}
.info span {{ margin-right: 30px; }}
.chart {{ margin: 20px 0; text-align: center; }}
.footer {{ text-align: center; color: #95a5a6; margin-top: 40px; font-size: 12px; }}
</style>
</head>
<body>
<h1>多持仓资金利用最大化报告</h1>
<div class="info">
    <span><strong>策略:</strong> {strategy_name}</span>
    <span><strong>区间:</strong> {start_date} ~ {end_date}</span>
    <span><strong>初始资金:</strong> {capital:,.0f}</span>
    <span><strong>总权益:</strong> {final_value:,.0f}</span>
    <span><strong>持股数量:</strong> {total_shares:,.0f} 股</span>
    <span><strong>总收益率:</strong> <span style="color:{ret_color};font-weight:bold">{total_return * 100:.2f}%</span></span>
    <span><strong>夏普比率:</strong> {sharpe:.2f}</span>
    <span><strong>最大回撤:</strong> {max_drawdown * 100:.2f}%</span>
    <span><strong>交易次数:</strong> {len(events)}</span>
    <span><strong>闲置资金天数/分析周期:</strong> {idle_days:.2f} / {period_days}</span>
</div>
<div class="info">
    <span><strong>股票池({len(pool_symbols)}只):</strong> {pool_str}</span>
</div>
<div class="info">
    <span><strong>命令参数:</strong> {cmd_params}</span>
    <span><strong>命令选项:</strong> {_command_options_str()}</span>
    <span><strong>软件版本:</strong> {__version__}</span>
</div>

<h2>交易记录</h2>
<table>
<tr><th>日期</th><th>买入股票</th><th>买入资金</th><th>卖出股票</th><th>卖出资金</th><th>总权益</th><th>剩余资金</th><th>持股种类数</th></tr>
{tx_rows}
</table>

<h2>个股收益统计</h2>
<table>
<tr><th>股票代码</th><th>股票名称</th><th>买入金额</th><th>卖出金额</th><th>期末市值</th><th>总收益</th><th>收益率</th><th>持股天数</th></tr>
{stat_rows}
</table>

<h2>期末持仓</h2>
<table>
<tr><th>股票代码</th><th>股票名称</th><th>持股数</th><th>成本价</th><th>最新价</th><th>市值</th></tr>
{hold_rows}
</table>

{chart_section}

<div class="footer"><p>报告由 stock-quant 自动生成</p></div>
</body>
</html>'''

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return report_path


# ============================================================================
# scan 命令 - 股票扫描筛选
# ============================================================================


# 板块划分（--random 随机抽样用）
_BOARD_RATIOS = [('沪市主板', 6), ('深市主板', 5), ('创业板', 5), ('科创板', 2)]


def _classify_board(symbol):
    """按股票代码前缀划分板块：沪市主板/深市主板/创业板/科创板，无法识别返回 None"""
    if symbol.startswith(('600', '601', '603', '605')):
        return '沪市主板'
    if symbol.startswith(('000', '001', '002', '003')):
        return '深市主板'
    if symbol.startswith(('300', '301')):
        return '创业板'
    if symbol.startswith('688'):
        return '科创板'
    return None


def _random_pick_by_board(stock_list, total):
    """按 沪市主板:深市主板:创业板:科创板=6:5:5:2 的比例，随机抽取 total 只股票

    Args:
        stock_list: 股票列表（DataFrame 或 dict 列表）
        total: 抽样总数（通常为 top*30）

    Returns:
        pd.DataFrame: 抽样后的股票列表（顺序已打乱）
    """
    import random

    if hasattr(stock_list, 'to_dict'):
        records = stock_list.to_dict('records')
    else:
        records = list(stock_list)

    # 按板块分组
    boards = {}
    for rec in records:
        sym = rec.get('symbol', '') or rec.get('code', '')
        b = _classify_board(sym)
        if b:
            boards.setdefault(b, []).append(rec)

    # 按比例分配各板块抽样数（最大余数法，保证总和 = total）
    ratio_sum = sum(r for _, r in _BOARD_RATIOS)
    raw_counts = [total * r / ratio_sum for _, r in _BOARD_RATIOS]
    counts = [int(c) for c in raw_counts]
    remainder = total - sum(counts)
    order = sorted(range(len(raw_counts)), key=lambda i: raw_counts[i] - int(raw_counts[i]), reverse=True)
    for i in range(remainder):
        counts[order[i % len(order)]] += 1

    picked = []
    for (name, _), c in zip(_BOARD_RATIOS, counts):
        pool = boards.get(name, [])
        if c > len(pool):
            c = len(pool)
        picked.extend(random.sample(pool, c))

    random.shuffle(picked)
    if hasattr(stock_list, 'to_dict'):
        return pd.DataFrame(picked)
    return picked


def _append_watch_extremes(all_results, selected):
    """在扫描结果末尾追加观望股票中综合打分最高/最低各1只。"""
    existing = {str(r.get('symbol')) for r in selected}
    candidates = []
    for r in all_results:
        total = r.get('signal_score_total')
        if total is None or str(r.get('symbol')) in existing:
            continue
        if 'signals' in r:
            vals = list((r.get('signals') or {}).values())
            is_watch = all(v is None or float(v) == 0 for v in vals)
        else:
            is_watch = r.get('signal') == 0
        if is_watch:
            candidates.append((int(total), r))
    if not candidates:
        return []
    hi = max(candidates, key=lambda x: x[0])
    lo = min(candidates, key=lambda x: x[0])
    picked = [hi]
    if lo[1] is not hi[1]:
        picked.append(lo)
    for _, r in picked:
        selected.append(r)
        existing.add(str(r.get('symbol')))
    return picked


def _scan_single(symbol, strategy, capital=100000, enhance=0, verbose=False):
    """扫描单只股票：获取近一年数据 -> 运行策略 -> 返回信号、最新价及风险指标
    在独立进程中调用时自建 DataFetcher（requests.Session 非线程/进程安全）。
    """
    try:
        fetcher = DataFetcher()
        start_date = (datetime.now() - timedelta(days=_DEFAULT_PERIOD_DAYS)).strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')
        df = fetcher.get_stock_data(symbol, _warmup_fetch_start(start_date), end_date)
        if df is None or len(df) < 100:
            return None
        df, latest_close, _, _ = _apply_realtime_to_df(df, symbol, fetcher, end_date, verbose=False)
        df = add_all_indicators(df)
        df = _truncate_analysis_df(df, start_date)
        df.attrs['symbol'] = symbol
        df.attrs['ref_date'] = end_date
        df.attrs['limit_skip'] = (infer_asset_type(symbol) != 'stock')
        result = _run_strategy(strategy, df, capital=capital, enhance=enhance)
        risk = result  # 结果已包含风险指标

        # 取最新信号值（1=买, 0=观望, -1=卖）
        sig_series = result.get('signals')
        signal_value = None
        if sig_series is not None and len(sig_series) > 0:
            signal_value = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])

        if signal_value is None:
            return None

        signal_score = ''
        signal_score_total = None
        if verbose:
            try:
                from core.signal_score import compute_signal_score
                info = compute_signal_score(df, sig_series)
                signal_score = info['text']
                signal_score_total = info['total']
            except Exception:
                signal_score = ''

        return {
            'symbol': symbol,
            'latest_close': latest_close,
            'signal': signal_value,
            'signal_score': signal_score,
            'signal_score_total': signal_score_total,
            'reason': (_latest_signal_reason_detail(strategy, df, sig_series)
                       if signal_value else ''),
            'total_return': risk.get('total_return'),
            'sharpe': risk.get('sharpe_ratio'),
            'max_dd': risk.get('max_drawdown'),
            'win_rate': risk.get('win_rate'),
            'trades': risk.get('total_trades'),
        }
    except Exception:
        return None


def _scan_single_worker(args):
    """多进程 worker：扫描单只股票"""
    symbol, strategy, capital, enhance, market_dates, verbose = args
    set_market_trade_dates_override(market_dates)
    return _scan_single(symbol, strategy, capital, enhance, verbose)


def _scan_multi(symbol, strategies, capital=100000, strat_enhance=None, verbose=False):
    """扫描单只股票并运行多个策略，返回每个策略的最新信号值、预测阈值及信号合计
    在独立进程中调用时自建 DataFetcher（requests.Session 非线程/进程安全）。
    """
    if strat_enhance is None:
        strat_enhance = {}
    try:
        fetcher = DataFetcher()
        start_date = (datetime.now() - timedelta(days=_DEFAULT_PERIOD_DAYS)).strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')
        df = fetcher.get_stock_data(symbol, _warmup_fetch_start(start_date), end_date)
        if df is None or len(df) < 100:
            return None
        df, latest_close, _, _ = _apply_realtime_to_df(df, symbol, fetcher, end_date, verbose=False)
        df = add_all_indicators(df)
        df = _truncate_analysis_df(df, start_date)
        df.attrs['symbol'] = symbol
        df.attrs['ref_date'] = end_date
        df.attrs['limit_skip'] = (infer_asset_type(symbol) != 'stock')
        sig_map = {}
        sig_series_map = {}
        for sk in strategies:
            try:
                result = _run_strategy(sk, df, capital=capital, enhance=strat_enhance.get(sk, 0))
                sig_series = result.get('signals')
                if sig_series is not None and len(sig_series) > 0:
                    v = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
                else:
                    v = None
                sig_map[sk] = v
                if sig_series is not None:
                    sig_series_map[sk] = sig_series
            except Exception:
                sig_map[sk] = None
        if all(v is None for v in sig_map.values()):
            return None
        total = sum(v for v in sig_map.values() if v is not None)
        signal_score = ''
        signal_score_total = None
        if verbose:
            try:
                from core.signal_score import compute_signal_score, combine_signals
                comp = combine_signals(sig_series_map)
                info = compute_signal_score(df, comp)
                signal_score = info['text']
                signal_score_total = info['total']
            except Exception:
                signal_score = ''
        return {'symbol': symbol, 'latest_close': latest_close, 'signals': sig_map,
                'signal_score': signal_score,
                'signal_score_total': signal_score_total,
                'total': total}
    except Exception:
        return None


def _scan_multi_worker(args):
    """多进程 worker：扫描单只股票（多策略）"""
    symbol, strategies, capital, strat_enhance, market_dates, verbose = args
    set_market_trade_dates_override(market_dates)
    return _scan_multi(symbol, strategies, capital, strat_enhance, verbose)


def _run_scan(scan_symbols, strategies, threads, strat_enhance=None,
              market_dates=None, verbose=False):
    """运行扫描（串行/并行），返回每只股票的结果列表"""
    if strat_enhance is None:
        strat_enhance = {}
    results = []
    if len(scan_symbols) > 1 and threads > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        n_workers = min(threads, len(scan_symbols))
        click.echo(click.style(f'  使用 {n_workers} 个进程并行扫描...', fg='blue'))
        tasks = [(s, strategies, 100000, strat_enhance,
                  market_dates, verbose) for s in scan_symbols]
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_scan_multi_worker, task): i for i, task in enumerate(tasks)}
            for fut in as_completed(futures):
                data = fut.result()
                if data is not None:
                    results.append(data)
    else:
        set_market_trade_dates_override(market_dates)
        for s in scan_symbols:
            data = _scan_multi(s, strategies, 100000, strat_enhance,
                               verbose=verbose)
            if data is not None:
                results.append(data)
    return results


def _scan_single_batch(symbols, sk, sk_enhance, threads,
                       market_dates=None, verbose=False):
    """对一批股票运行单个策略扫描（串行/并行），返回结果列表"""
    results = []
    if len(symbols) > 1 and threads > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        n_workers = min(threads, len(symbols))
        tasks = [(s, sk, 100000, sk_enhance, market_dates,
                  verbose) for s in symbols]
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_scan_single_worker, task): i for i, task in enumerate(tasks)}
            for fut in as_completed(futures):
                data = fut.result()
                if data is not None:
                    results.append(data)
    else:
        set_market_trade_dates_override(market_dates)
        for s in symbols:
            data = _scan_single(s, sk, 100000, sk_enhance,
                                verbose=verbose)
            if data is not None:
                results.append(data)
    return results


def _scan_has_buy(result):
    """判断扫描结果是否至少含一个买入信号（多策略看 signals，单策略看 signal）"""
    if result is None:
        return False
    if 'signals' in result:
        return any(v is not None and v > 0 for v in result['signals'].values())
    return result.get('signal') is not None and result['signal'] > 0


def _scan_sort_key(result):
    """扫描排序：买 > 卖 > 观望；同组内按综合打分从大到小。"""
    if result is None:
        return (1, 2, float('inf'))
    values = (list(result.get('signals', {}).values())
              if 'signals' in result else [result.get('signal')])
    values = [v for v in values if v is not None]
    has_buy = any(v > 0 for v in values)
    has_sell = any(v < 0 for v in values)
    group = 0 if has_buy else (1 if has_sell else 2)
    pref_group = 0
    pref = _preferred_strategy_key()
    if pref and 'signals' in result:
        pv = (result.get('signals') or {}).get(pref)
        pref_group = 0 if (pv is not None and pv > 0) else 1
    score = result.get('signal_score_total')
    score_key = -float(score) if score is not None else float('inf')
    return (pref_group, group, score_key)


def _apply_scan_filters(stock_list, peplus=False):
    """按 PE 过滤股票列表 DataFrame（缺少列时忽略）。

    PE 上限取自 squant.toml 的 PE_THRESHOLD（默认 100），高于阈值视为高估并剔除。
    """
    if stock_list is None or len(stock_list) == 0:
        return stock_list
    out = stock_list
    if 'pe' in out.columns:
        pe = pd.to_numeric(out['pe'], errors='coerce')
        pe_max = get_threshold('PE_THRESHOLD')
        if pe_max is not None:
            out = out[~(pe > pe_max)]
        if peplus:
            out = out[~(pe < 0)]
    return out


@cli.command('scan')
@_src_option
@_maxdrawdown_option
@_limit_option
@click.option('--strategy', '-g', default='boma', help='策略选择，多个策略用逗号分隔，all表示全部（默认boma）')
@click.option('--symbol', '-s', default=None, help='指定股票代码/名称扫描（空格/逗号分隔）；指定 -s 或 -sf 后仅分析这些股票，不再从网络补充，买入信号不足时退出')
@click.option('--symbol-file', '-sf', default=None, help='从指定文件读取股票代码扫描（空格/逗号/换行分隔）；指定 -s 或 -sf 后仅分析这些股票，不再从网络补充，买入信号不足时退出')
@click.option('--top', '-t', default=5, type=click.IntRange(1, 100), help='筛选排名前N只股票 -tN（N=1~100，默认10）')
@click.option('--offset', '-o', default=0, type=click.IntRange(0, 1000000), help='从获取到的股票中第N条记录开始分析 -oN（N=0起始，默认0=第一条）')
@click.option('--min-volume', default=None, type=float, help='最小成交量过滤')
@click.option('--threads', '-x', default=5, type=click.IntRange(1, 6), help='并行进程数 -xN（N=1~6，默认5）')
@click.option('--changepercent', 'sort_key', flag_value='changepercent', default=True, help='获取股票列表排序方式：按涨跌幅（指定后覆盖默认随机）')
@click.option('--turnoverratio', 'sort_key', flag_value='turnoverratio', default=False, help='获取股票列表排序方式：按换手率')
@click.option('--amount', 'sort_key', flag_value='amount', default=False, help='获取股票列表排序方式：按成交额')
@click.option('--mktcap', 'sort_key', flag_value='mktcap', default=False, help='获取股票列表排序方式：按总市值')
@click.option('--pe', 'sort_key', flag_value='pe', default='changepercent', help='获取股票列表排序方式：按市盈率')
@click.option('--random', is_flag=True, default=True, help='按 沪市主板:深市主板:创业板:科创板=6:5:5:2 比例随机抽取 30*N 只股票分析（默认；指定排序选项可覆盖）')
@click.option('--peplus', is_flag=True, default=False, help='排除 PE 为负（亏损）的股票')
@click.option('--notify', is_flag=True, default=False, help='对扫描出的 Top N 股票通过钉钉机器人发送买卖信号通知（含综合打分及观望极值）')
@click.option('--verbose', is_flag=True, default=False, help='输出每日信号综合打分（10指标）')
def scan_cmd(strategy, symbol, symbol_file, top, offset, min_volume, threads, sort_key, random, notify, src, peplus, atr_stop_ratio, verbose, limit):
    """股票筛选：重点输出符合要求或最佳的股票

    流程：获取股票列表 -> 筛选 -> 逐个运行策略（可用 -x 并行）-> 输出结果
    指定多个策略（逗号分隔）或 all 时，输出多策略汇总信号表（每只股票一行，
    列为策略名，值为买/卖/观；排序规则为买 > 卖 > 观望，同组按 score 降序）。
    未指定排序选项时默认按板块比例随机抽样（--random）；
    指定 --changepercent 等排序选项时按对应字段排序。
    指定 --random 时按板块比例随机抽样 30*N 只。
    指定 -s / -sf 时仅分析指定的股票，不再从网络补充；买入信号不足 -tN 时直接退出。
    大盘趋势为跌势时禁止买入。
    """
    ctx = click.get_current_context()
    if (ctx.get_parameter_source('random') != ParameterSource.COMMANDLINE
            and ctx.get_parameter_source('sort_key') == ParameterSource.COMMANDLINE):
        random = False
    try:
        set_primary_source(src)
        _set_maxdrawdown(atr_stop_ratio)
        _set_limit(limit)
        _scan_start = (datetime.now() - timedelta(days=_DEFAULT_PERIOD_DAYS)).strftime('%Y-%m-%d')
        _scan_end = datetime.now().strftime('%Y-%m-%d')
        _market_dates = preload_market_trade_dates(
            _warmup_fetch_start(_scan_start), _scan_end) or None
        set_market_trade_dates_override(_market_dates)
        _preload_trend_states(_scan_start, _scan_end)
        # 解析策略列表
        strat_specs = _split_strategies(strategy)
        if any(k == 'all' for k in strat_specs):
            strat_keys = ALL_SELECTION
            strat_enhance = {k: 0 for k in strat_keys}
        else:
            strat_keys = []
            strat_enhance = {}
            for spec in strat_specs:
                base_key, _ = _parse_strategy_spec(spec)
                if base_key not in STRATEGY_MAP:
                    click.echo(click.style(f'  错误: 未知策略 "{base_key}"，可选: {", ".join(STRATEGY_MAP.keys())}', fg='red'))
                    return
                token = base_key
                if token not in strat_keys:
                    strat_keys.append(token)
                strat_enhance[token] = 0

        # 持有 hold 为基准对照策略，不参与扫描信号表/排序；仅当用户显式只指定 hold 时保留
        if len(strat_keys) > 1:
            strat_keys = [k for k in strat_keys if _strat_base(k) != 'hold']
            strat_enhance = {k: v for k, v in strat_enhance.items() if _strat_base(k) != 'hold'}

        multi = len(strat_keys) > 1
        strat_names = [_strat_label(k) for k in strat_keys]
        # 汇总信号表使用的短栏名（其余策略沿用完整名称）
        strat_cols = [_strat_short(k) for k in strat_keys]

        _print_header('股票扫描筛选')
        _today = datetime.now().strftime('%Y-%m-%d')
        market_trends = [analyze_market_trend(_today, _HS300_INDEX_CODE),
                         analyze_market_trend(_today, _ZZ1000_INDEX_CODE)]
        _set_market_trend(market_trends)
        _print_market_trend(market_trends)
        click.echo(click.style(f'  策略: {", ".join(strat_names)}', fg='cyan', bold=True))
        sort_label = {'changepercent': '涨跌幅', 'turnoverratio': '换手率',
                      'amount': '成交额', 'mktcap': '总市值', 'pe': '市盈率'}.get(sort_key, sort_key)
        if symbol or symbol_file:
            _srcs = []
            if symbol:
                _srcs.append('-s')
            if symbol_file:
                _srcs.append(f'-sf {symbol_file}')
            mode_label = f'指定股票: {" ".join(_srcs)}'
        else:
            mode_label = '随机抽样（板块比例 6:5:5:2）' if random else f'排序: {sort_label}'
        click.echo(click.style(f'  {mode_label}', fg='cyan'))

        fetcher = DataFetcher()

        # 指定 -s / -sf 时仅分析这些股票，不再从网络补充
        provided_only = bool(symbol or symbol_file)
        if provided_only:
            entries = []  # (code, name)
            if symbol:
                for tok in re.split(r'[,\s，]+', symbol):
                    tok = tok.strip()
                    if not tok:
                        continue
                    code = _parse_code_token(tok)
                    if code:
                        _, nm = _resolve_symbol(tok)
                        entries.append((code, nm or ''))
                    else:
                        c, nm = _resolve_symbol(tok)
                        if c:
                            entries.append((c, nm or ''))
            if symbol_file:
                file_symbols = _read_symbols_file(symbol_file)
                file_names = _read_symbol_names(symbol_file)
                for code in file_symbols:
                    entries.append((code, file_names.get(code, '')))
            ordered, seen_names = [], {}
            for code, nm in entries:
                if code not in seen_names:
                    seen_names[code] = nm
                    ordered.append(code)
                elif nm and not seen_names.get(code):
                    seen_names[code] = nm
            if not ordered:
                click.echo(click.style('  错误: -s/-sf 未提供有效股票代码', fg='red'))
                return
            click.echo(click.style(
                f'  指定股票 {len(ordered)} 只（不再从网络补充）', fg='green'))
            if min_volume is not None or peplus:
                click.echo(click.style(
                    '  提示: -s/-sf 模式下按成交量/PE 过滤不生效', fg='yellow'))
            _ensure_symbol_cap_map(ordered)
            random = False
            stock_list = pd.DataFrame({
                'symbol': ordered,
                'name': [seen_names.get(c) or c for c in ordered],
            })
        else:
            click.echo(click.style('  正在获取股票列表...', fg='blue'))
            stock_list = fetcher.get_stock_list(
                sort_key='changepercent' if random else sort_key, max_pages=60)

            if stock_list is None or len(stock_list) == 0:
                click.echo(click.style(
                    f'  错误: 未能获取股票列表（首选源 {src} 及备用源均失败；'
                    f'请检查网络/数据源可用性，或改用 --src 指定其他源）', fg='red'))
                return

            click.echo(click.style(f'  获取到 {len(stock_list)} 只股票', fg='green'))
            _update_symbol_cap_map(stock_list)

            # 按成交量过滤
            if min_volume is not None:
                click.echo(click.style(f'  正在按成交量过滤 (>= {min_volume:,.0f})...', fg='blue'))
                stock_list = fetcher.filter_by_volume(stock_list, min_volume)
                click.echo(click.style(f'  过滤后剩余 {len(stock_list)} 只股票', fg='green'))

            # 按 PE 上限（PE_THRESHOLD）/ ST / 市值过滤
            before = len(stock_list)
            stock_list = _apply_scan_filters(stock_list, peplus)
            click.echo(click.style(
                f'  按条件过滤后剩余 {len(stock_list)} 只股票（原 {before} 只）', fg='green'))
            _pe_missing = ('pe' not in stock_list.columns or
                           pd.to_numeric(stock_list['pe'], errors='coerce').notna().sum() == 0)
            if _pe_missing and (peplus or sort_key == 'pe'):
                click.echo(click.style(
                    '  警告: 当前数据源不提供 PE，PE 排序/--peplus 过滤未生效', fg='yellow'))
            if len(stock_list) == 0:
                click.echo(click.style('  错误: 过滤后无剩余股票', fg='red'))
                return

        if random:
            # 随机模式：按板块比例随机排列全部候选（供增量扩样）
            picked = _random_pick_by_board(stock_list, len(stock_list))
            recs = picked.to_dict('records') if hasattr(picked, 'to_dict') else picked
            click.echo(click.style(f'  按 沪市主板:深市主板:创业板:科创板=6:5:5:2 随机排列 {len(recs)} 只股票', fg='green'))
        else:
            recs = stock_list[offset:].to_dict('records') if hasattr(stock_list, 'to_dict') else list(stock_list[offset:])
            click.echo(click.style(f'  跳过前 {offset} 条，候选 {len(recs)} 只股票', fg='green'))

        pool = [(r.get('symbol', '') or r.get('code', '')) for r in recs]
        pool = [s for s in pool if s]
        # 代码 -> 名称 映射（用于输出展示）
        name_map = {}
        for r in recs:
            sym = r.get('symbol', '') or r.get('code', '')
            name_map[sym] = r.get('name', sym)

        # 扫描候选池：-s/-sf 时仅这些股票（立即扫完，不足即退出）；网络模式按 Top N 分批 + 最多扩容 2 次
        batch = max(1, top * 30)
        if provided_only:
            batch = max(1, len(pool))
        if multi:
            # 多策略：对候选池运行所有策略并输出汇总信号表
            click.echo(click.style(f'  正在扫描股票（策略: {", ".join(strat_names)}）...', fg='blue'))
            results = []
            scanned = 0
            expansions = 0
            buy_count = 0
            while True:
                chunk = pool[scanned:scanned + batch]
                if not chunk:
                    break
                scanned += len(chunk)
                results.extend(_run_scan(chunk, strat_keys, threads, strat_enhance,
                               market_dates=_market_dates, verbose=verbose or notify))
                buy_count = sum(1 for r in results if _scan_has_buy(r))
                if buy_count >= top or scanned >= len(pool):
                    break
                if expansions >= 2:
                    break
                expansions += 1
                click.echo(click.style(
                    f'  含买入信号股票不足（{buy_count}/{top}），已扫描 {scanned} 只，'
                    f'扩大扫描范围（第 {expansions}/2 次）...', fg='yellow'))
            all_scan_results = list(results)
            if buy_count == 0:
                click.echo(click.style('  未找到符合条件的股票，退出', fg='yellow'))
                return
            if provided_only and buy_count < top:
                click.echo(click.style(
                    f'  买入信号不足（{buy_count}/{top}），-s/-sf 指定股票已扫描完，退出', fg='yellow'))
                return
            results = [r for r in results if _scan_has_buy(r)]
        else:
            # 单策略：仅筛选「买入」信号
            sk = strat_keys[0]
            sk_enhance = strat_enhance.get(sk, 0)
            click.echo(click.style(f'  正在扫描股票（策略: {strat_names[0]}，目标买入信号 ≥ {top} 只）...', fg='blue'))
            results = []
            all_scan_results = []
            scanned = 0
            expansions = 0
            while True:
                chunk = pool[scanned:scanned + batch]
                if not chunk:
                    break
                scanned += len(chunk)
                chunk_results = _scan_single_batch(chunk, sk, sk_enhance, threads,
                                        market_dates=_market_dates, verbose=verbose or notify)
                all_scan_results.extend(chunk_results)
                results.extend([r for r in chunk_results if r.get('signal') and r['signal'] > 0])
                if len(results) >= top or scanned >= len(pool):
                    break
                if expansions >= 2:
                    break
                expansions += 1
                click.echo(click.style(
                    f'  买入信号不足（{len(results)}/{top}），已扫描 {scanned} 只，'
                    f'扩大扫描范围（第 {expansions}/2 次）...', fg='yellow'))
            if not results:
                click.echo(click.style('  未找到符合条件的股票，退出', fg='yellow'))
                return
            if provided_only and len(results) < top:
                click.echo(click.style(
                    f'  买入信号不足（{len(results)}/{top}），-s/-sf 指定股票已扫描完，退出', fg='yellow'))
                return

        if not results and not verbose and not notify:
            click.echo(click.style('  未找到符合条件的股票', fg='yellow'))
            return

        if multi:
            # 多策略汇总信号表：买 > 卖 > 观望，同组按综合打分降序
            results.sort(key=_scan_sort_key)
            top_results = results[:top]
            watch_added = (_append_watch_extremes(all_scan_results, top_results)
                           if verbose else [])
            suffix = f'（另含观望极值{len(watch_added)}只）' if watch_added else ''
            _print_section(f'多策略汇总信号表 - Top {top} {suffix}')

            headers = ['代码', '名称', '最新价格'] + strat_cols
            if verbose:
                headers.append('综合打分')
            table_rows = []
            for i, r in enumerate(top_results, 1):
                lc = r.get('latest_close')
                price_str = f'{lc:.2f}' if lc is not None else 'N/A'
                row = [r['symbol'], name_map.get(r['symbol'], r['symbol']),
                       price_str]
                for sk in strat_keys:
                    v = r['signals'].get(sk)
                    if v is not None and v > 0:
                        cell = '买'
                    elif v is not None and v < 0:
                        cell = '卖'
                    elif v == 0:
                        cell = '观'
                    else:
                        cell = 'N/A'
                    row.append(cell)
                if verbose:
                    row.append(r.get('signal_score') or '—')
                table_rows.append(row)

            _print_table(headers, table_rows, disable_numparse=True)
        else:
            # 单策略：剔除观望股票，按买 > 卖 > 观望，同组按综合打分降序
            results = [r for r in results if r.get('signal') not in (None, 0)]
            if not results and not verbose and not notify:
                click.echo(click.style('  未找到非观望信号的股票', fg='yellow'))
                click.echo()
                click.echo(click.style('  扫描完成!', fg='green', bold=True))
                click.echo()
                return
            results.sort(key=_scan_sort_key)
            top_results = results[:top]
            watch_added = (_append_watch_extremes(all_scan_results, top_results)
                           if verbose else [])
            suffix = f'（另含观望极值{len(watch_added)}只）' if watch_added else ''
            _print_section(f'扫描结果 - Top {top} {suffix}')

            headers = ['代码', '名称', '最新价格', '信号', '信号详情']
            if verbose:
                headers.append('综合打分')
            table_rows = []
            for i, r in enumerate(top_results, 1):
                lc = r.get('latest_close')
                price_str = f'{lc:.2f}' if lc is not None else 'N/A'
                sig_text = '买入' if r['signal'] > 0 else '卖出'
                row = [r['symbol'], name_map.get(r['symbol'], r['symbol']),
                       price_str, sig_text, r.get('reason', '')]
                if verbose:
                    row.append(r.get('signal_score') or '—')
                table_rows.append(row)

            _print_table(headers, table_rows, disable_numparse=True)

        # 保存筛选出的股票代码到 scan_yyyymmdd.txt（每行一条；文件已存在时追加到末尾）
        try:
            txt_path = os.path.join('report', f'scan_{datetime.now().strftime("%Y%m%d")}.txt')
            os.makedirs(os.path.dirname(txt_path), exist_ok=True)
            with open(txt_path, 'a', encoding='utf-8') as f:
                # 若已有内容且末尾无换行，先补一个换行，避免与本次代码粘连
                if f.tell() > 0:
                    with open(txt_path, 'rb') as rf:
                        rf.seek(-1, os.SEEK_END)
                        if rf.read(1) != b'\n':
                            f.write('\n')
                for r in top_results:
                    f.write(r['symbol'] + '\n')
            click.echo(click.style(f'  筛选代码已保存到: {txt_path}', fg='green'))
        except Exception as e:
            click.echo(click.style(f'  保存筛选代码失败: {e}', fg='yellow'))

        # 保存结果到 HTML 报告（top N 表格 + 个股图表）
        try:
            strat_tag = '_'.join(strat_keys)
            charts_by_symbol = _generate_scan_stock_charts(top_results, strat_keys, name_map, prefix_tag=strat_tag, strat_enhance=strat_enhance)
            html_path = _generate_scan_html_report(headers, table_rows, strat_names, strat_cols,
                                                   charts_by_symbol=charts_by_symbol, name_map=name_map,
                                                   report_filename=f'scan_{datetime.now().strftime("%m%d%H%M")}.html',
                                                   market_trend=_get_market_trend())
            click.echo(click.style(f'  HTML报告已保存到: {html_path}', fg='green'))
        except Exception as e:
            click.echo(click.style(f'  保存HTML失败: {e}', fg='yellow'))

        # --notify：将扫描 Top N 股票按策略信号合成一条钉钉消息发送
        if notify:
            click.echo(click.style('\n  发送钉钉通知...', fg='blue'))
            try:
                notify_entries = list(top_results)
                _append_watch_extremes(all_scan_results, notify_entries)
                stock_entries = []
                if multi:
                    # 多策略：每只 Top 股票携带各策略最新信号值
                    for r in notify_entries:
                        sigs = r.get('signals') or {}
                        stock_entries.append({
                            'symbol': r['symbol'],
                            'stock_name': name_map.get(r['symbol'], r['symbol']),
                            'signal_score': r.get('signal_score') or '',
                            'signal_score_total': r.get('signal_score_total'),
                            'all_signals': {sk: [v] for sk, v in sigs.items() if v is not None},
                            'all_risks': {},
                            'strategy_map': {sk: _strat_label(sk) for sk in strat_keys},
                            'strategies_to_run': list(strat_keys),
                        })
                else:
                    # 单策略：每只 Top 股票携带该策略最新信号值
                    sk = strat_keys[0]
                    for r in notify_entries:
                        if r.get('signal') is None:
                            continue
                        stock_entries.append({
                            'symbol': r['symbol'],
                            'stock_name': name_map.get(r['symbol'], r['symbol']),
                            'signal_score': r.get('signal_score') or '',
                            'signal_score_total': r.get('signal_score_total'),
                            'all_signals': {sk: [r['signal']]},
                            'all_risks': {},
                            'strategy_map': {sk: _strat_label(sk)},
                            'strategies_to_run': [sk],
                        })
                if stock_entries:
                    _notify_signals(stock_entries)
                else:
                    click.echo(click.style('  没有可通知的信号', fg='yellow'))
            except Exception as e:
                click.echo(click.style(f'  发送钉钉通知失败: {e}', fg='yellow'))

        click.echo()
        click.echo(click.style('  扫描完成!', fg='green', bold=True))
        click.echo()

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


def _sample_random_pool(count=100):
    """随机抽样股票池（沪主板:深主板:创业板:科创板 = 6:5:5:2）"""
    try:
        fetcher = DataFetcher()
        stock_list = fetcher.get_stock_list(sort_key='changepercent', max_pages=60)
        if stock_list is None or len(stock_list) == 0:
            return []
        picked = _random_pick_by_board(stock_list, count)
        recs = picked.to_dict('records') if hasattr(picked, 'to_dict') else picked
        syms = [r.get('symbol', '') or r.get('code', '') for r in recs]
        return [s for s in syms if s]
    except Exception:
        return []


def _build_onestock_breakdown(trades, source_keys, result):
    """构建单持仓回测的「回测结果」统计表，返回 (headers, rows)。

    结果列为全部策略合并后的交易统计。
    """
    n = len(trades)
    rets = [float(t.get('return') or 0.0) for t in trades]
    pnls = [(t['exit_price'] - t['entry_price']) * t['position']
            for t in trades]
    holds = [int(t.get('holding_days') or 0) for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    win_rate = len(wins) / n if n else 0.0
    avg_ret = sum(rets) / n if n else 0.0
    pf = (sum(wins) / abs(sum(losses))) if losses else (
        float('inf') if wins else 0.0)

    def pct(v):
        return f'{v * 100:.2f}%'

    def _dist(vals):
        if not vals:
            return '—'
        return (f'平均{sum(vals) / len(vals):.1f}，中位{np.median(vals):.1f}，'
                f'最高{max(vals):.0f}，最低{min(vals):.0f}')

    if rets:
        ret_dist = (f'总{pct(result["total_return"])},'
                    f'中位{pct(float(np.median(rets)))},'
                    f'最高{pct(max(rets))},最低{pct(min(rets))}')
    else:
        ret_dist = f'总{pct(result["total_return"])},中位—,最高—,最低—'

    headers = ['指标', '结果']
    rows = [
        ['总收益率', ret_dist],
        ['年化收益率', pct(result['annual_return'])],
        ['最大回撤', pct(result['max_drawdown'])],
        ['夏普比率', f"{result['sharpe_ratio']:.2f}"],
        ['交易次数', str(n)],
        ['胜率', pct(win_rate)],
        ['平均收益率', pct(avg_ret)],
        ['盈利因子', 'inf' if pf == float('inf') else f'{pf:.2f}'],
        ['持股天数', _dist(holds)],
        ['总盈亏额', f'{sum(pnls):,.0f}'],
        ['最大单笔盈利', f'{max(pnls):,.0f}' if pnls else '0'],
        ['最大单笔亏损', f'{min(pnls):,.0f}' if pnls else '0'],
    ]
    return headers, rows


def _build_reason_return_table(trades, reason_key, next_key=None,
                               day5_key=None, day10_key=None):
    """按买入/卖出原因分组统计收益。

    - 总收益：该分组交易的盈亏额合计（元）；
    - 总收益率：该分组各笔收益率之和；中位/最高/最低为收益率统计；
    - 平均持仓：该分组交易的平均持仓天数（保留 1 位小数）；
    - 可选输出“次日OK率”（next_key）、“5日OK率”（day5_key）与“10日OK率”（day10_key）：
      买入分组为之后收盘价较信号日上涨的占比，卖出分组为之后收盘价较信号日下跌的占比；
      未来交易日不足的交易不计入对应分母；买入分组不看期间是否提前卖出。
    """
    groups = {}
    for t in trades:
        raw = str(t.get(reason_key) or '—')
        parts = [p.strip() for p in raw.split('/') if p.strip()] or ['—']
        for reason in parts:
            groups.setdefault(reason, []).append(t)

    headers = ['分组', '次数', '平均持仓']
    if next_key:
        headers.append('次日OK率')
    if day5_key:
        headers.append('5日OK率')
    if day10_key:
        headers.append('10日OK率')
    headers += ['总收益', '总收益率', '中位', '最高', '最低']
    rows = []
    for reason, items in groups.items():
        rets = [float(t.get('return') or 0.0) for t in items]
        profits = []
        for t in items:
            p = t.get('profit')
            if p is None:
                p = ((float(t.get('exit_price') or 0.0)
                      - float(t.get('entry_price') or 0.0))
                     * float(t.get('position') or 0.0))
            profits.append(float(p or 0.0))
        row = [reason, len(items)]
        hold_vals = [float(t.get('holding_days')) for t in items
                     if t.get('holding_days') is not None]
        avg_hold = sum(hold_vals) / len(hold_vals) if hold_vals else 0.0
        row.append(f'{avg_hold:.1f}')
        for key in (next_key, day5_key, day10_key):
            if not key:
                continue
            valid = [t for t in items if t.get(key) is not None]
            if valid:
                correct = sum(1 for t in valid if t.get(key))
                row.append(f'{correct / len(valid) * 100:.2f}%')
            else:
                row.append('—')
        row += [
            f'{sum(profits):,.2f}',
            f'{sum(rets) * 100:.2f}%',
            f'{float(np.median(rets)) * 100:.2f}%',
            f'{max(rets) * 100:.2f}%',
            f'{min(rets) * 100:.2f}%',
        ]
        rows.append(row)
    rows.sort(key=lambda r: (-r[1], r[0]))
    return headers, rows


def _future_dir_sign(df, date, offset=1):
    """之后第 offset 个交易日收盘价相对当日的涨跌符号：+1/-1/0；不足返回 None。"""
    try:
        ts = pd.Timestamp(date)
        idx = int(df.index.searchsorted(ts))
        j = idx + offset
        if idx >= len(df.index) or df.index[idx] != ts or j >= len(df.index):
            return None
        c0 = float(df['close'].iloc[idx])
        c1 = float(df['close'].iloc[j])
        if c1 > c0:
            return 1
        if c1 < c0:
            return -1
        return 0
    except Exception:
        return None


def _onestock_signal_exit_detail(sk, df, d, hold, market_state=None):
    """推断 onestock “信号”卖出的具体触发原因。"""
    base = _strat_base(sk)
    try:
        if d not in df.index:
            return '策略卖出信号'
        pos = int(df.index.get_loc(d))
        if not isinstance(pos, (int, np.integer)) or pos < 0:
            return '策略卖出信号'
        close = pd.to_numeric(df['close'], errors='coerce')
        c = float(close.iloc[pos])

        entry_date = pd.Timestamp(hold.get('entry_date'))
        epos_arr = df.index.get_indexer([entry_date], method='ffill')
        epos = int(epos_arr[0]) if len(epos_arr) and epos_arr[0] >= 0 else None

        peak = None
        dd = 0.0
        if epos is not None and epos <= pos:
            peak = float(close.iloc[epos:pos + 1].max())
            dd = (peak - c) / peak if peak > 0 else 0.0

        # 策略内部 ATR 动态止损
        atr_keys = {'boll', 'macd', 'macd1'}
        state_series = df.attrs.get('market_states')
        if state_series is not None and len(state_series) > 0:
            try:
                state_series = state_series.reindex(df.index, method='ffill')
            except Exception:
                state_series = None
        else:
            state_series = None

        def _atr_mult(j):
            st = (state_series.iloc[j] if state_series is not None
                  else market_state)
            return adjust_atr_stop_multiplier(_ATR_STOP_MULTIPLIER, st)

        if (_ATR_STOP_ENABLED and base in atr_keys
                and epos is not None and epos <= pos):
            atr = (df['ATR14'] if 'ATR14' in df.columns else calc_atr(df))
            stop_price = None
            for j in range(epos, pos + 1):
                stop_price = update_atr_trailing_stop(
                    close.iloc[j], atr.iloc[j], stop_price,
                    multiplier=_atr_mult(j))
            if stop_price is not None and c <= stop_price:
                return 'ATR动态止损'

        # RSI 超买卖出
        rsi_keys = {'boll', 'macd', 'macd1', 'ma1030'}
        if base in rsi_keys:
            try:
                rsi = df['RSI14'] if 'RSI14' in df.columns else calc_rsi(df, 14)
                rsi_thr = get_threshold('MAX_RSI_THRESHOLD')
                rsi_v = float(rsi.iloc[pos])
                if (rsi_thr is not None and pd.notna(rsi_v)
                        and rsi_v > rsi_thr):
                    return 'RSI超买'
            except Exception:
                pass

        if base == 'boma':
            try:
                bb = calc_bollinger(df)
                upper, middle, lower = bb['UPPER'], bb['MIDDLE'], bb['LOWER']
                md = calc_macd(df, fast=12, slow=26, signal=9)
                dif, dea, bar = md['DIF'], md['DEA'], md['MACD']
                death_cross = (pos > 0
                               and dif.iloc[pos - 1] >= dea.iloc[pos - 1]
                               and dif.iloc[pos] < dea.iloc[pos])
                adx = pd.to_numeric(df['ADX14'], errors='coerce')
                pdi = pd.to_numeric(df['PLUS_DI'], errors='coerce')
                mdi = pd.to_numeric(df['MINUS_DI'], errors='coerce')
                di_bearish = (pos > 0
                              and pdi.iloc[pos] < pdi.iloc[pos - 1]
                              and mdi.iloc[pos] > mdi.iloc[pos - 1]
                              and adx.iloc[pos] >= 20)
                st_states = df.attrs.get('stock_trend_states')
                if st_states is None:
                    try:
                        st_states = classify_stock_trend_series(df)
                    except Exception:
                        st_states = None
                st = (st_states.iloc[pos]
                      if st_states is not None and len(st_states) > pos else None)
                mkt_states = df.attrs.get('market_states')
                if mkt_states is not None and len(mkt_states) > 0:
                    try:
                        mkt_states = mkt_states.reindex(df.index, method='ffill')
                    except Exception:
                        mkt_states = None
                mkt_state = (mkt_states.iloc[pos]
                             if mkt_states is not None and len(mkt_states) > pos
                             else None)
                if mkt_state == '跌势' and st == '跌势':
                    return '趋势共振卖出'
                upper_pullback = (pos > 0
                                  and close.iloc[pos - 1] >= upper.iloc[pos - 1]
                                  and c < float(upper.iloc[pos]))
                break_mid = (pos > 0
                             and close.iloc[pos - 1] >= middle.iloc[pos - 1]
                             and c < float(middle.iloc[pos]))
                adx_falling = pos > 0 and adx.iloc[pos] < adx.iloc[pos - 1]
                dif_falling = pos > 0 and dif.iloc[pos] < dif.iloc[pos - 1]
                is_bull = st == '涨势'
                is_nonbull = st in ('震荡', '跌势')
                boll1 = (_sw_enabled(SIGNAL_BOLL1_SELL)
                         and is_bull and break_mid and adx_falling)
                boll2 = (_sw_enabled(SIGNAL_BOLL2_SELL)
                         and is_nonbull and upper_pullback and dif_falling)
                macd1_on = (_sw_enabled(SIGNAL_MACD_SELL)
                            and _sw_enabled(SIGNAL_MACD1_SELL))
                macd2_on = (_sw_enabled(SIGNAL_MACD_SELL)
                            and _sw_enabled(SIGNAL_MACD2_SELL))
                sell2_macd1 = (macd1_on and st in ('涨势', '震荡')
                               and death_cross
                               and c < float(middle.iloc[pos]) and di_bearish)
                sell2_macd2 = (macd2_on and st == '跌势'
                               and death_cross)

                stop_price = None
                if epos is not None and epos <= pos:
                    atr = (df['ATR14'] if 'ATR14' in df.columns
                           else calc_atr(df))
                    for j in range(epos, pos + 1):
                        stop_price = update_atr_trailing_stop(
                            close.iloc[j], atr.iloc[j], stop_price,
                            multiplier=_atr_mult(j))
                sell3 = (_ATR_STOP_ENABLED and stop_price is not None
                         and c < stop_price
                         and c > float(lower.iloc[pos]))

                reasons = []
                if boll1:
                    reasons.append('卖出信号BOLL1')
                if boll2:
                    reasons.append('卖出信号BOLL2')
                if sell2_macd1:
                    reasons.append('卖出信号MACD1')
                if sell2_macd2:
                    reasons.append('卖出信号MACD2')
                if sell3:
                    reasons.append('卖出信号ATR')
                if reasons:
                    return '/'.join(reasons)
            except Exception:
                pass
            return 'BOMA卖出信号'

        if base == 'boll':
            try:
                upper = calc_bollinger(df)['UPPER']
                if (pos > 0 and close.iloc[pos - 1] >= upper.iloc[pos - 1]
                        and c < float(upper.iloc[pos])):
                    return 'BOLL上轨回落'
            except Exception:
                pass
            return 'BOLL卖出信号'
        if base in ('macd', 'macd1'):
            try:
                fast, slow, sig = ((12, 26, 9) if base == 'macd'
                                   else (6, 13, 5))
                md = calc_macd(df, fast=fast, slow=slow, signal=sig)
                dif, dea = md['DIF'], md['DEA']
                if (pos > 0 and dif.iloc[pos - 1] >= dea.iloc[pos - 1]
                        and dif.iloc[pos] < dea.iloc[pos]):
                    return 'MACD死叉'
            except Exception:
                pass
            return 'MACD卖出信号'
        if base == 'ma1030':
            try:
                fast = pd.to_numeric(df['MA10'], errors='coerce')
                slow = pd.to_numeric(df['MA30'], errors='coerce')
                if (pos > 0 and fast.iloc[pos - 1] >= slow.iloc[pos - 1]
                        and fast.iloc[pos] < slow.iloc[pos]):
                    return 'MA10下穿MA30'
            except Exception:
                pass
            return 'MA1030卖出信号'
        if base == 'kdj':
            return 'KDJ死叉'
        if base == 'sar':
            return 'SAR转空'
        if base == 'hold':
            return '持有策略卖出'
        if base == 'quality_value':
            return '质价策略卖出'
        return f'{_strat_label(sk)}卖出信号'
    except Exception:
        return '策略卖出信号'


def _latest_signal_reason_detail(sk, df, signals):
    """返回最新信号的详细原因；BOMA 输出具体买卖信号编号。"""
    if signals is None or len(signals) == 0:
        return ''
    try:
        val = float(signals.iloc[-1]) if hasattr(signals, 'iloc') else float(signals[-1])
    except Exception:
        return ''
    if val == 0:
        return ''
    if _strat_base(sk) == 'boma':
        try:
            strat = _build_strategy_instance('boma', STRATEGY_MAP, 0)
            raw_signals = _generate_effective_signals(
                strat, df, apply_market_regime=False,
                apply_downloss_signal=False, attach_score=False)
            parts = strat.latest_signal_reasons(df, raw_signals)
            if parts:
                return '；'.join(parts)
        except Exception:
            pass
    return _signal_reason(sk, val)


def _onestock_signal_entry_detail(sk, df, d):
    """推断 onestock 买入信号的具体原因。"""
    base = _strat_base(sk)
    if base == 'boma':
        try:
            if d not in df.index:
                return '买入信号'
            pos = int(df.index.get_loc(d))
            if pos < 1:
                return '买入信号'
            close = pd.to_numeric(df['close'], errors='coerce')
            c = float(close.iloc[pos])
            bb = calc_bollinger(df)
            lower = bb['LOWER']
            md = calc_macd(df, fast=12, slow=26, signal=9)
            dif, dea, bar = md['DIF'], md['DEA'], md['MACD']
            rsi6, rsi12 = calc_rsi(df, 6), calc_rsi(df, 12)
            rsi_order = rsi6.iloc[pos] > rsi12.iloc[pos]
            ma5 = pd.to_numeric(df['MA5'], errors='coerce')
            price_above_ma5 = c > float(ma5.iloc[pos])
            rsi14 = calc_rsi(df, 14)
            ma20 = pd.to_numeric(df['MA20'], errors='coerce')
            ma20_val = float(ma20.iloc[pos])
            bias_negative = ((c - ma20_val) / ma20_val < 0) if ma20_val != 0 else False
            market_states = df.attrs.get('market_states')
            if market_states is not None and len(market_states) > 0:
                try:
                    market_states = market_states.reindex(df.index, method='ffill')
                except Exception:
                    market_states = None
            state = (market_states.iloc[pos] if market_states is not None else None)
            mkt_bull = state == '涨势'
            lower_rebound = (close.iloc[pos - 1] <= lower.iloc[pos - 1]
                             and c > float(lower.iloc[pos]))
            green_short_1 = (bar.iloc[pos] < 0 and bar.iloc[pos - 1] < 0
                             and bar.iloc[pos] > bar.iloc[pos - 1])
            bar_ok = bar.iloc[pos] > 0 or green_short_1
            buy1 = lower_rebound and bar_ok and rsi_order and price_above_ma5
            golden = (dif.iloc[pos - 1] <= dea.iloc[pos - 1]
                      and dif.iloc[pos] > dea.iloc[pos])
            adx = pd.to_numeric(df['ADX14'], errors='coerce')
            pdi = pd.to_numeric(df['PLUS_DI'], errors='coerce')
            mdi = pd.to_numeric(df['MINUS_DI'], errors='coerce')
            macd1 = (golden and dif.iloc[pos] >= 0 and mkt_bull
                     and adx.iloc[pos] > 20
                     and pdi.iloc[pos] > mdi.iloc[pos])
            macd2 = (golden and dif.iloc[pos] < 0
                     and rsi14.iloc[pos] < 30 and bias_negative
                     and adx.iloc[pos] > 20)
            adxr = calc_adxr(df)
            cross_up = (pos > 0 and pdi.iloc[pos - 1] <= mdi.iloc[pos - 1]
                        and pdi.iloc[pos] > mdi.iloc[pos])
            buy3 = (cross_up and adx.iloc[pos] > 25
                    and adxr.iloc[pos] > adx.iloc[pos]
                    and price_above_ma5)
            reasons = []
            if buy1:
                reasons.append('买入信号BOLL')
            if macd1:
                reasons.append('买入信号MACD1')
            if macd2:
                reasons.append('买入信号MACD2')
            if buy3:
                reasons.append('买入信号ADX')
            if reasons:
                return '/'.join(reasons)
        except Exception:
            pass
        return 'BOMA买入信号'
    return _signal_reason(sk, 1, 0)


def _run_onestock_backtest(symbols, start_date, end_date, strat_keys, strat_enhance,
                           capital, output, random_mode=False, random_count=100, fix_capital=False,
                           file_part='portfolio', replace_on_buy=0,
                           down_loss_sell=False, verbose=False, num_positions=1,
                           pool_file=None):
    """backtest 持仓轮动回测（最多同时持有 num_positions 只）

    num_positions=1 即原单持仓轮动：空仓时按 -g 策略顺序寻找买入信号，用全部资金买入，
    持仓期间仅当「该股票 + 该策略」出现卖出信号时卖出。

    num_positions=N>1 时允许同时持有至多 N 只股票：每个交易日先处理卖出（各持仓对应策略
    出现卖出信号则卖出），再用剩余空位买入未持仓且发出买入信号的股票，直至补满 N 只；
    当日卖出后空出的仓位可立即再次买入。

    选股顺序：先按「策略顺序」，同一策略内按该股票当日「综合打分」从高到低排序，
    打分相同则按股票池顺序。

    资金规则：
      - fix_capital=True：每只买入金额固定为 初始资金 / N（现金不足也按该额度成交，现金可为负）；
      - fix_capital=False：每只买入金额 = 可用现金 / (N - 当前持股数)。

    replace_on_buy=1（仅 N=1 生效）：已持仓且出现其它股票买入信号、当前持仓 RSI>75 时换股。

    random_mode=True 时，每次完全空仓（含初始）会重新随机抽取股票池。

    大盘趋势为“跌势”时禁止买入。
    """
    commission, slippage = 0.0003, 0.0001
    max_positions = max(1, int(num_positions or 1))
    source_keys = [k for k in strat_keys if _strat_base(k) != 'hold']
    if not source_keys:
        click.echo(click.style('  错误: 至少需要一个非 hold 策略', fg='red'))
        return

    trend_by_code = {
        _HS300_INDEX_CODE: market_trend_states(
            start_date, end_date, _HS300_INDEX_CODE),
        _ZZ1000_INDEX_CODE: market_trend_states(
            start_date, end_date, _ZZ1000_INDEX_CODE),
    }

    def _trend_for_symbol(sym):
        return trend_by_code.get(_symbol_index_code(sym),
                                 trend_by_code[_ZZ1000_INDEX_CODE])

    fetcher = DataFetcher()

    def load_one(sym):
        try:
            df = fetcher.get_stock_data(sym, _warmup_fetch_start(start_date), end_date)
            if df is None or len(df) == 0:
                return None
            src_used = df.attrs.get('source')
            df = add_all_indicators(df)
            df = _truncate_analysis_df(df, start_date)
            if len(df) == 0:
                return None
            df.attrs['source'] = src_used
            df.attrs['symbol'] = sym
            df.attrs['ref_date'] = end_date
            df.attrs['limit_skip'] = (infer_asset_type(sym) != 'stock')
            sym_trend = _trend_for_symbol(sym)
            from core.signal_score import compute_signal_score_series
            sigs = {}
            scores = {}
            for sk in source_keys:
                strat = _build_strategy_instance(sk, STRATEGY_MAP, strat_enhance.get(sk, 0))
                sig = _generate_effective_signals(
                    strat, df, market_states=sym_trend,
                    apply_downloss_signal=False)
                sigs[sk] = sig
                try:
                    scores[sk] = compute_signal_score_series(df, sig)
                except Exception:
                    scores[sk] = pd.Series(0, index=df.index, dtype=int)
            return df, sigs, scores
        except Exception:
            return None

    def load_pool(pool, header=None):
        loaded = {}
        total = len(pool)
        if header:
            click.echo(click.style(f'\n  {header}（{total} 只）', fg='blue'),
                       nl=False)
        for i, sym in enumerate(pool):
            if total and ((i + 1) % 10 == 0 or i + 1 == total):
                pct = int((i + 1) * 100 / total)
                click.echo(click.style(f' ->{pct}%', fg='blue'), nl=False)
            d = load_one(sym)
            if d is not None:
                loaded[sym] = d
        if header:
            click.echo()
        return loaded

    _title = '单持仓轮动回测' if max_positions == 1 else f'{max_positions} 持仓轮动回测'
    _print_header(_title)

    loaded = load_pool(symbols, header='正在加载股票池')
    if not loaded:
        click.echo(click.style('  错误: 无有效股票数据', fg='red'))
        return

    calendar = sorted(set().union(*[set(df.index) for df, _, _ in loaded.values()]))
    if not calendar:
        click.echo(click.style('  错误: 无有效交易日', fg='red'))
        return

    cash = float(capital)
    holds = {}        # symbol -> {'symbol','sk','shares','cost','entry_date','entry_price',...}
    trades = []
    equity = []
    need_refresh = False

    def _price(sym, d):
        df = loaded[sym][0]
        p = df['close'].asof(d)
        if pd.isna(p):
            p = df['close'].iloc[-1]
        return float(p)

    def _sell_hold(h, d, reason, detail=None):
        nonlocal cash
        sym = h['symbol']
        df = loaded[sym][0]
        raw = float(df['close'].loc[d]) if d in df.index else _price(sym, d)
        exec_px = raw * (1 - slippage)
        proceeds = h['shares'] * exec_px * (1 - commission)
        cash += proceeds
        reason_detail = detail if detail else reason
        if reason == 'signal':
            held_states = _trend_for_symbol(sym)
            held_state = (held_states.get(d) if held_states is not None
                          and len(held_states) > 0 else None)
            reason_detail = _onestock_signal_exit_detail(
                h['sk'], df, d, h, market_state=held_state)
        elif detail is None:
            reason_detail = {
                'downloss': '大盘下跌/震荡止损',
                'replace': '换股',
                'end': '期末平仓',
            }.get(reason, reason)
        entry_dir = _future_dir_sign(df, h['entry_date'], 1)
        exit_dir = _future_dir_sign(df, d, 1)
        holding_days = sum(1 for x in calendar if h['entry_date'] < x <= d)
        # 买入分组统一按信号日之后第 5/10 个交易日计算，不考虑期间提前卖出
        entry5_dir = _future_dir_sign(df, h['entry_date'], 5)
        entry10_dir = _future_dir_sign(df, h['entry_date'], 10)
        exit5_dir = _future_dir_sign(df, d, 5)
        exit10_dir = _future_dir_sign(df, d, 10)
        trades.append({
            'entry_date': h['entry_date'],
            'exit_date': d,
            'symbol': sym,
            'strategy': _strat_label(h['sk']),
            'strategy_key': h['sk'],
            'entry_price': h['entry_price'],
            'exit_price': exec_px,
            'position': h['shares'],
            'return': (proceeds / h['cost'] - 1) if h['cost'] else 0.0,
            'profit': (exec_px - h['entry_price']) * h['shares'],
            'holding_days': holding_days,
            'exit_reason': reason,
            'exit_reason_detail': reason_detail,
            'entry_reason_detail': h.get('entry_reason_detail', ''),
            # 方向：买入看之后是否上涨，卖出看之后是否下跌；None=未来交易日不足
            'entry_next_up': None if entry_dir is None else entry_dir > 0,
            'exit_next_down': None if exit_dir is None else exit_dir < 0,
            'entry_day5_up': None if entry5_dir is None else entry5_dir > 0,
            'exit_day5_down': None if exit5_dir is None else exit5_dir < 0,
            'entry_day10_up': None if entry10_dir is None else entry10_dir > 0,
            'exit_day10_down': None if exit10_dir is None else exit10_dir < 0,
        })
        holds.pop(sym, None)

    def _buy(sym, sk, df, d, amount):
        nonlocal cash
        if amount <= 0:
            return
        exec_px = float(df['close'].loc[d]) * (1 + slippage)
        shares = amount / (exec_px * (1 + commission))
        cash -= amount
        holds[sym] = {'symbol': sym, 'sk': sk, 'shares': shares,
                      'cost': amount, 'entry_date': d, 'entry_price': exec_px,
                      'entry_reason_detail': _onestock_signal_entry_detail(sk, df, d)}

    def _buy_amount():
        """本次买入金额：fixcapital=初始资金/N；否则=可用现金/(N-当前持股数)。"""
        if fix_capital:
            return float(capital) / max_positions
        remaining = max_positions - len(holds)
        if remaining <= 0:
            return 0.0
        return cash / remaining

    def _pick_buy(d):
        # 策略顺序优先；同一策略内按综合打分从高到低，打分相同按股票池顺序
        for sk in source_keys:
            best = None
            for sym in symbols:
                if sym in holds or sym not in loaded:
                    continue
                df, sigs, scores = loaded[sym]
                if d not in df.index:
                    continue
                if float(sigs[sk].loc[d]) <= 0:
                    continue
                score = 0.0
                try:
                    v = scores[sk].loc[d]
                    if pd.notna(v):
                        score = float(v)
                except Exception:
                    score = 0.0
                if best is None or score > best[0]:
                    best = (score, sym, df)
            if best is not None:
                return best[1], sk, best[2]
        return None

    def _rsi_above_75(sym, d):
        df = loaded[sym][0]
        if d not in df.index:
            return False
        rsi = df['RSI14'] if 'RSI14' in df.columns else calc_rsi(df, 14)
        val = rsi.loc[d]
        return pd.notna(val) and float(val) > 75.0

    for d in calendar:
        # 1) 卖出：各持仓对应策略出现卖出信号时清仓
        sold = False
        if holds:
            for sym in list(holds.keys()):
                h = holds[sym]
                df, sigs, _ = loaded[sym]
                if d in df.index and float(sigs[h['sk']].loc[d]) < 0:
                    _sell_hold(h, d, 'signal')
                    sold = True

        # 2) 完全空仓且需要时刷新随机股票池
        if random_mode and not holds and need_refresh:
            new_syms = _sample_random_pool(random_count)
            if new_syms:
                symbols = new_syms
                loaded = load_pool(symbols, header='空仓，刷新随机股票池')
            need_refresh = False

        # 3) 买入：当日卖出后空出的仓位可再次买入
        if max_positions == 1 and replace_on_buy == 1 and holds:
            # 单持仓换股：出现其它股票买入信号且当前持仓 RSI>75 时换股
            cur = next(iter(holds.values()))
            pick = _pick_buy(d)
            if (pick is not None and pick[0] != cur['symbol']
                    and _rsi_above_75(cur['symbol'], d)):
                _sell_hold(cur, d, 'replace')
                _buy(*pick, d, _buy_amount())
        else:
            # 用剩余空位按「策略顺序 + 综合打分降序」买入，直至补满 N 只
            while len(holds) < max_positions:
                pick = _pick_buy(d)
                if pick is None:
                    break
                _buy(*pick, d, _buy_amount())

        # 4) 记录当日权益
        eq = cash + sum(h['shares'] * _price(sym, d) for sym, h in holds.items())
        equity.append(eq)
        if sold and not holds:
            need_refresh = random_mode

    # 期末仍持仓：按最后交易日收盘强制平仓
    if holds:
        for sym in list(holds.keys()):
            _sell_hold(holds[sym], calendar[-1], 'end')
        equity[-1] = cash

    total_return = (equity[-1] - capital) / capital if capital else 0.0
    sharpe, max_dd = _calc_sharpe_drawdown(equity)
    hold_sum = sum(t['holding_days'] for t in trades)
    annual_return = (total_return * TRADING_DAYS_PER_YEAR / hold_sum) if hold_sum else 0.0
    rets = [t['return'] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r < 0]
    win_rate = len(wins) / len(rets) if rets else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else (float('inf') if wins else 0.0)

    result = {
        'total_return': total_return,
        'annual_return': annual_return,
        'max_drawdown': max_dd,
        'sharpe_ratio': sharpe,
        'win_rate': win_rate,
        'total_trades': len(trades),
        'profit_factor': profit_factor,
    }

    breakdown_headers, breakdown_rows = _build_onestock_breakdown(trades, source_keys, result)
    _print_section('回测结果')
    click.echo(click.style(f'  策略: {", ".join(_strat_label(k) for k in source_keys)}', fg='cyan'))
    if random_mode:
        pool_label = '随机股票池'
    elif pool_file:
        pool_label = os.path.basename(str(pool_file))
    else:
        pool_label = '自选股票'
    click.echo(click.style(f'  股票池: {pool_label}', fg='white'))
    click.echo(click.style(f'  区间: {start_date} ~ {end_date}'
                           + (' · 随机股票池' if random_mode else ''), fg='white'))
    click.echo(click.style(f'  初始资金: {capital:,.0f}', fg='white'))
    click.echo(click.style(f'  最大同时持仓: {max_positions}', fg='white'))
    _print_table(breakdown_headers, breakdown_rows)

    buy_reason_table = sell_reason_table = None
    if verbose:
        buy_reason_table = _build_reason_return_table(
            trades, 'entry_reason_detail', 'entry_next_up', 'entry_day5_up',
            'entry_day10_up')
        sell_reason_table = _build_reason_return_table(
            trades, 'exit_reason_detail', 'exit_next_down', 'exit_day5_down',
            'exit_day10_down')
        _print_section('买入信号分组收益')
        _print_table(*buy_reason_table, disable_numparse=True)
        _print_section('卖出信号分组收益')
        _print_table(*sell_reason_table, disable_numparse=True)

    # 交易明细
    trade_headers = ['买入日期', '卖出日期', '股票代码', '策略', '买入价', '卖出价',
                     '持股数', '持仓天数', '收益率', '盈亏额', '买入原因', '卖出原因']
    reason_map = {'signal': '信号', 'end': '期末平仓', 'replace': '换股',
                  'downloss': '大盘下跌/震荡止损'}
    trade_rows = []
    for t in trades:
        pnl = (t['exit_price'] - t['entry_price']) * t['position']
        trade_rows.append([
            pd.Timestamp(t['entry_date']).strftime('%Y-%m-%d'),
            pd.Timestamp(t['exit_date']).strftime('%Y-%m-%d'),
            t['symbol'], t['strategy'],
            f"{t['entry_price']:.3f}", f"{t['exit_price']:.3f}",
            f"{t['position']:.0f}", t['holding_days'],
            f"{t['return'] * 100:.2f}%", f"{pnl:,.0f}",
            t.get('entry_reason_detail') or _signal_reason(t.get('strategy', ''), 1),
            t.get('exit_reason_detail') or reason_map.get(
                t['exit_reason'], t['exit_reason']),
        ])
    if trade_rows:
        # 汇总行：统计持仓天数/收益率/盈亏额合计
        _pnl_sum = sum((t['exit_price'] - t['entry_price']) * t['position'] for t in trades)
        _hd_sum = sum(t['holding_days'] for t in trades)
        _ret_sum = sum(t['return'] for t in trades)
        trade_rows.append(['汇总', '', '', '', '', '', '', _hd_sum,
                           f'{_ret_sum * 100:.2f}%', f'{_pnl_sum:,.0f}', '', ''])

    charts_html = ''
    chart_path = None
    if verbose:
        try:
            index_data = {
                _HS300_INDEX_CODE: fetcher.get_index_data(
                    _HS300_INDEX_CODE, start_date, end_date),
                _ZZ1000_INDEX_CODE: fetcher.get_index_data(
                    _ZZ1000_INDEX_CODE, start_date, end_date),
            }
            buy_index = {}
            for t in trades:
                try:
                    buy_index[pd.Timestamp(t['entry_date'])] = _symbol_index_code(
                        t.get('symbol'))
                except Exception:
                    pass

            active_code = _ZZ1000_INDEX_CODE
            chart_dates, chart_rows = [], []
            for d in calendar:
                if d in buy_index:
                    active_code = buy_index[d]
                used_code = active_code
                idx_df = index_data.get(used_code)
                if idx_df is None or d not in idx_df.index:
                    used_code = _ZZ1000_INDEX_CODE
                    idx_df = index_data.get(used_code)
                if idx_df is None or d not in idx_df.index:
                    continue
                chart_dates.append(d)
                chart_rows.append(idx_df.loc[d])

            if chart_rows:
                chart_index_df = pd.DataFrame(
                    chart_rows, index=pd.DatetimeIndex(chart_dates))
                chart_dates_idx = pd.DatetimeIndex(chart_dates)
                chart_trend_map = []
                for _code, _label in (
                        (_ZZ1000_INDEX_CODE, '中证1000'),
                        (_HS300_INDEX_CODE, '沪深300')):
                    _st = trend_by_code.get(_code)
                    if _st is None or len(_st) == 0:
                        continue
                    chart_trend_map.append(
                        (_label, _st.reindex(chart_dates_idx, method='ffill')))
                calendar_idx = pd.DatetimeIndex(calendar[:len(equity)])
                equity_series = pd.Series(equity, index=calendar_idx).reindex(
                    chart_index_df.index, method='ffill')
                safe_part = re.sub(r'[^\w.-]+', '_', str(file_part or 'portfolio'))
                chart_name = (f'equity_{safe_part}_'
                              f'{str(start_date).replace("-", "")}_'
                              f'{str(end_date).replace("-", "")}.png')
                chart_path = os.path.join(output, chart_name)
                ChartGenerator(output_dir=output).plot_index_equity_with_trades(
                    chart_index_df, equity_series, trades,
                    title=f'沪深300/中证1000指数K线与持仓权益曲线（{start_date}~{end_date}）',
                    save_path=chart_path, trend_states=chart_trend_map,
                    index_frames=index_data)
                b64 = _img_to_b64(chart_path)
                if b64:
                    charts_html = (
                        '<div class="chart"><h3>指数K线与权益曲线</h3>'
                        f'<img src="data:image/png;base64,{b64}" '
                        'alt="指数K线与权益曲线" '
                        'style="max-width:100%;border:1px solid #ddd;border-radius:4px;">'
                        '</div>')
                    click.echo(click.style(f'\n  权益图表: {chart_path}', fg='green'))
                else:
                    click.echo(click.style('  权益图表生成失败: 图片编码为空', fg='yellow'))
            else:
                click.echo(click.style('  权益图表生成失败: 指数数据为空', fg='yellow'))
        except Exception as e:
            click.echo(click.style(f'  权益图表生成失败: {e}', fg='yellow'))

    # --verbose 且整个回测仅交易一只股票：追加该股票的K线与买卖信号图
    if verbose and trades:
        sole_symbols = {str(t.get('symbol')) for t in trades if t.get('symbol')}
        if len(sole_symbols) == 1:
            try:
                sym = next(iter(sole_symbols))
                if sym in loaded:
                    stock_df, stock_sigs, _ = loaded[sym]
                    sk = None
                    for t in trades:
                        if str(t.get('symbol')) == sym:
                            sk = t.get('strategy_key') or t.get('strategy')
                            break
                    sig = stock_sigs.get(sk) if sk else None
                    if sig is not None and len(sig) > 0:
                        safe_sym = re.sub(r'[^\w.-]+', '_', sym)
                        stock_chart_name = (
                            f'kline_{safe_sym}_{sk}_'
                            f'{str(start_date).replace("-", "")}_'
                            f'{str(end_date).replace("-", "")}.png')
                        stock_chart_path = os.path.join(output, stock_chart_name)
                        stock_name = _SYMBOL_NAME_MAP.get(sym, '')
                        title = (f'{sym} {stock_name} - K线与买卖信号'
                                 .replace('  ', ' ').strip())
                        ChartGenerator(output_dir=output).plot_signal_composite(
                            stock_df, sig, strategy_key=sk, title=title,
                            save_path=stock_chart_path)
                        b64 = _img_to_b64(stock_chart_path)
                        if b64:
                            charts_html += (
                                '<div class="chart"><h3>个股K线与买卖信号</h3>'
                                f'<img src="data:image/png;base64,{b64}" '
                                'alt="个股K线与买卖信号" '
                                'style="max-width:100%;border:1px solid #ddd;border-radius:4px;">'
                                '</div>')
                            click.echo(click.style(
                                f'\n  个股K线图表: {stock_chart_path}', fg='green'))
            except Exception as e:
                click.echo(click.style(f'  个股K线图表生成失败: {e}', fg='yellow'))

    html_path = None
    try:
        tables = [('回测结果', breakdown_headers, breakdown_rows)]
        if verbose and buy_reason_table is not None:
            tables.append(('买入信号分组收益', *buy_reason_table))
            tables.append(('卖出信号分组收益', *sell_reason_table))
        if trade_rows:
            tables.append(('交易明细', trade_headers, trade_rows))
        _sources = [d[0].attrs.get('source') for d in loaded.values() if d]
        _strat_names = [_strat_label(k) for k in source_keys]
        _report_filename = 'backtest_' + _backtest_report_filename(
            file_part, _strat_names, start_date, end_date)

        html_path = _generate_backtest_html(
            (f'单持仓轮动({len(loaded)}只)' if max_positions == 1
             else f'{max_positions}持仓轮动({len(loaded)}只)'),
            start_date, end_date, capital, tables=tables,
            report_filename=_report_filename,
            source_label=_format_sources(_sources),
            charts_html=charts_html)
        click.echo(click.style(f'\n  HTML报告已保存到: {html_path}', fg='green'))
    except Exception as e:
        click.echo(click.style(f'\n  HTML报告生成失败: {e}', fg='yellow'))

    click.echo()
    click.echo(click.style('  回测完成!', fg='green', bold=True))
    return {
        'result': result,
        'trades': trades,
        'equity': equity,
        'html_path': html_path,
        'chart_path': chart_path,
        'breakdown_headers': breakdown_headers,
        'breakdown_rows': breakdown_rows,
    }


# ============================================================================
# backtest 命令 - 回测指定策略
# ============================================================================


@cli.command('backtest')
@_src_option
@_maxdrawdown_option
@_limit_option
@click.option('--symbol', '-s', default=None, help='股票代码（与 -sf 至少指定一个）')
@click.option('--symbol-file', '-sf', default=None, help='从指定文件读取股票代码进行批量回测（空格/逗号/换行分隔），仅处理有效 6 位代码并忽略名称，与 -s 同时使用时合并')
@click.option('--random', is_flag=True, default=False, help='按 沪市主板:深市主板:创业板:科创板=6:5:5:2 比例随机抽取 100 只股票回测（忽略 -s 和 -sf），完成后将代码写入 report/random_<mmddHHMM>.txt')
@click.option('--strategy', '-g', default='boma',     help='策略选择 [ma1030|macd|macd1|boll|boma|kdj|sar|quality_value|hold|all]，多个用逗号分隔，all表示全部（默认boma）')
@click.option('--start', '-st', default=None, help='开始日期；配合 -d 时结束日期 = 开始日期 + 天数；未指定时按 -d 自结束日期前推，格式: YYYYMMDD')
@click.option('--end', '-e', default=None, help='结束日期（优先于 -d 推算），格式: YYYYMMDD')
@click.option('--duration', '-d', default=180, type=click.IntRange(1, 100000), show_default=True, help='时段天数（默认180）：无 -st 时开始=结束前N天；有 -st 且无 -e 时结束=开始+N天')
@click.option('--capital', '-c', default=100, type=float, help='初始资金（单位：万元，默认100万元）')
@click.option('--output', '-o', default='./output', help='图表输出目录（默认./output）')
@click.option('--chart', is_flag=True, default=False, help='生成K线图等图形（默认仅输出表格报告）')
@click.option('--fixcapital', is_flag=True, default=False, help='固定资金模式：每笔买入金额固定为 -c/N（N 见 -n）；现金不足时仍按该金额买入，剩余现金可为负')
@click.option('--num-positions', '-n', default=1, type=click.IntRange(1, 100), help='同时持有的股票数量 N（默认1）；--fixcapital 时每只买入固定为 初始资金/N，否则为 可用资金/(N-当前持股数)')
@click.option('--verbose', is_flag=True, default=False, help='backtest 时在 HTML 报告中增加指数K线与权益曲线单图')
def backtest_cmd(symbol, symbol_file, random, strategy, start, end, duration, capital, output, chart, src, fixcapital, num_positions, verbose, atr_stop_ratio, limit):
    """回测：持仓轮动流程（默认单持仓，-n 可同时持有 N 只）

    空仓/有空位时先按 -g 策略顺序，再按当日综合打分从高到低选择买入信号；持仓期间按对应策略卖出；
    -n N 允许同时持有至多 N 只：--fixcapital 时每只买入 初始资金/N，否则为 可用资金/(N-当前持股数)。
    -d N 指定时段天数（默认180），未指定 -st 时开始日期 = 结束日期前 N 天。
    指定 -sf 时从文件读取股票池，未指定 -s/-sf 时默认 input/pool.txt。
    指定 --random 时按板块比例随机抽取 100 只股票，空仓时刷新股票池。
    -g 支持多个策略（逗号或竖线分隔）。
    """
    try:
        set_primary_source(src)
        _set_fix_capital(fixcapital)
        _set_maxdrawdown(atr_stop_ratio)
        _set_limit(limit)
        capital = capital * 10000  # -c 单位为万元，内部换算为元
        strat_specs = _split_strategies(strategy)
        if any(s == 'all' for s in strat_specs):
            strat_keys = ALL_SELECTION
            strat_enhance = {k: 0 for k in strat_keys}
        else:
            strat_keys = []
            strat_enhance = {}
            for spec in strat_specs:
                base_key, _ = _parse_strategy_spec(spec)
                if base_key not in STRATEGY_MAP:
                    click.echo(click.style(f'  错误: 未知策略 "{base_key}"，可选: {", ".join(STRATEGY_MAP.keys())}', fg='red'))
                    return
                token = base_key
                if token not in strat_keys:
                    strat_keys.append(token)
                strat_enhance[token] = 0

        _ctx = click.get_current_context()
        duration_explicit = (
            _ctx.get_parameter_source('duration') == ParameterSource.COMMANDLINE)
        start_date, end_date = _parse_dates(
            start, end, default_days=duration, duration_explicit=duration_explicit)
        strat_names = [_strat_label(k) for k in strat_keys]

        # 随机抽样模式：忽略 -s 与 -sf，按板块比例随机抽取 100 只股票
        random_symbols = []
        if random:
            click.echo(click.style('  正在获取股票列表（随机抽样）...', fg='blue'))
            fetcher0 = DataFetcher()
            stock_list = fetcher0.get_stock_list(sort_key='changepercent', max_pages=60)
            if stock_list is None or len(stock_list) == 0:
                click.echo(click.style('  错误: 未能获取股票列表', fg='red'))
                return
            picked = _random_pick_by_board(stock_list, 100)
            recs = picked.to_dict('records') if hasattr(picked, 'to_dict') else picked
            random_symbols = [r.get('symbol', '') or r.get('code', '') for r in recs]
            random_symbols = [s for s in random_symbols if s]
            click.echo(click.style(
                f'  按 沪市主板:深市主板:创业板:科创板=6:5:5:2 随机抽取 {len(random_symbols)} 只股票', fg='green'))
            if not random_symbols:
                click.echo(click.style('  错误: 随机抽样未获得任何股票', fg='red'))
                return

        # 收集股票代码（-s 与 -sf 合并去重）
        symbols = []
        if random:
            symbols = list(random_symbols)
        else:
            if symbol:
                symbols.extend(_normalize_symbol_tokens(re.split(r'[,\s，]+', symbol)))
            if symbol_file:
                file_symbols = _read_symbols_file(symbol_file)
                symbols.extend(file_symbols)
            symbols = list(dict.fromkeys(symbols))

        if not symbols:
            # 未指定 -s/-sf 时，默认采用 -sf input/pool.txt
            pool_path = os.path.join(app_root(), 'input', 'pool.txt')
            file_symbols = _read_symbols_file(pool_path)
            if not file_symbols:
                click.echo(click.style(
                    '  错误: 未指定 -s/-sf，且 input/pool.txt 无有效股票代码', fg='red'))
                return
            symbols = file_symbols
            symbol_file = pool_path

        _ensure_symbol_cap_map(symbols)

        # 报告文件名中的 <file> 部分：--random → random；-sf → 文件名（不含路径/扩展名）；否则为 -s 股票代码
        if random:
            _file_part = 'random'
        elif symbol_file:
            _file_part = os.path.splitext(os.path.basename(symbol_file))[0]
        else:
            _file_part = '-'.join(str(symbol or '').split())

        # backtest 固定采用单持仓轮动流程（原 --onestock），不再提供普通批量/单股回测入口
        _run_onestock_backtest(
            symbols, start_date, end_date, strat_keys, strat_enhance,
            capital, output, random_mode=random, fix_capital=fixcapital,
            file_part=_file_part, replace_on_buy=0,
            verbose=verbose, num_positions=num_positions,
            pool_file=symbol_file)
        return

        # 以下为原批量/单股回测路径，CLI 已固定走单持仓轮动流程，保留代码供内部参考。
        # -sf / --random 批量回测：每只股票每策略一行，各项指标作为列
        if symbol_file or random:
            _print_header('批量回测', f'{len(symbols)} 只股票 - {", ".join(strat_names)}')
            click.echo(click.style(f'  回测区间: {start_date} ~ {end_date}', fg='white'))
            click.echo(click.style(f'  初始资金: {capital:,.0f}', fg='white'))

            fetcher = DataFetcher()
            batch_results = []
            sym_charts = {}  # 每只股票的多策略综合图（K线+信号+权益），供单独报告使用

            def _pct(v):
                return f'{v * 100:.2f}%' if v is not None else 'N/A'

            def _f(v):
                return f'{v:.2f}' if v is not None else 'N/A'

            with click.progressbar(symbols, label='  回测进度') as bar:
                for sym in bar:
                    try:
                        df = fetcher.get_stock_data(sym, _warmup_fetch_start(start_date), end_date)
                        if df is None or len(df) == 0:
                            continue
                        src_used = df.attrs.get('source')
                        df, _, _, _ = _apply_realtime_to_df(df, sym, fetcher, end_date, verbose=False)
                        df = add_all_indicators(df)
                        df = _truncate_analysis_df(df, start_date)
                        df.attrs['symbol'] = sym
                        sig_map = {}
                        _sym_strategies_data = []
                        for sk in strat_keys:
                            result = _run_strategy(sk, df, capital, enhance=strat_enhance.get(sk, 0))
                            batch_results.append({'symbol': sym, 'strategy': _strat_label(sk),
                                                  'risk': result, 'source': src_used})
                            sig_map[sk] = result.get('signals')
                            _sym_strategies_data.append({
                                'key': sk, 'name': _strat_label(sk),
                                'signals': result.get('signals'),
                                'equity_curve': result.get('equity_curve'),
                            })
                        # --verbose：生成该股票综合图表并缓存（K线+各策略信号+权益曲线）
                        if verbose:
                            try:
                                _date_tag = _build_date_tag(start_date, end_date)
                                _cg = ChartGenerator(output_dir=output,
                                                     prefix=f'{sym}_multi', date_tag=_date_tag)
                                _cpath = _cg.plot_multi_strategy_composite(
                                    df, _sym_strategies_data, title=f'{sym} 多策略回测对比')
                                _cb64 = _img_to_b64(_cpath)
                                if _cb64:
                                    sym_charts[sym] = (
                                        f'<img src="data:image/png;base64,{_cb64}" '
                                        f'alt="多策略对比">')
                            except Exception:
                                pass
                    except Exception:
                        continue

            if not batch_results:
                click.echo(click.style('  未获取到任何可回测的股票', fg='yellow'))
                return

            # 汇总统计（按策略 + 全部 / 按股票）
            from collections import defaultdict as _dd
            _by_strat = _dd(list)
            _strt_trades = _dd(int)
            _strt_hold = _dd(list)
            _strt_trade_rets = _dd(list)
            for r in batch_results:
                _nt = r['risk'].get('total_trades')
                if _nt:
                    _strt_trades[r['strategy']] += _nt
                _tdf = r['risk'].get('trades_df')
                if _tdf is not None and len(_tdf):
                    if 'holding_days' in _tdf.columns:
                        _strt_hold[r['strategy']].extend(
                            float(x) for x in _tdf['holding_days'].dropna().tolist())
                    if 'return' in _tdf.columns:
                        _strt_trade_rets[r['strategy']].extend(
                            float(x) for x in
                            pd.to_numeric(_tdf['return'], errors='coerce').dropna().tolist())
                _tr = r['risk'].get('total_return')
                if _tr is not None:
                    _by_strat[r['strategy']].append(_tr)

            def _agg(rets, trade_rets=None):
                """汇总收益率指标。

                trade_wr（交易正收益比）= 正收益交易笔数 / 总交易笔数
                run_wr（组合正收益比）= 总收益为正的组合数 / 总组合数
                """
                n = len(rets)
                if trade_rets:
                    trade_wr = sum(1 for x in trade_rets if x > 0) / len(trade_rets) * 100
                else:
                    trade_wr = 0.0
                run_wr = (sum(1 for x in rets if x > 0) / n * 100) if n else 0.0
                return (n, sum(rets) / n, float(np.median(rets)),
                        trade_wr, run_wr, max(rets), min(rets))

            def _fmt_avg_trades(v):
                """平均交易次数：不带百分号，固定一位小数显示"""
                return f'{v:.1f}'

            def _hold_days(days, divisor):
                """平均持股天数：总持股天数/股票数（策略收益分析表）或总持股天数/策略数（个股收益分析表）；无记录返回 None"""
                if not days or not divisor:
                    return None
                return sum(days) / divisor

            def _hold_annual(avg_ret, hold, avg_tr):
                """持股年化收益率（与 _calc_hold_annual 同口径）"""
                return _calc_hold_annual(avg_ret, hold, avg_tr)

            def _mean(vals):
                vals = [v for v in vals if v is not None]
                return sum(vals) / len(vals) if vals else None

            def _fmt_pct(v):
                return 'N/A' if v is None else f'{v * 100:.2f}%'

            # ---- 构建报告表格 ----
            # 1) 批量回测结果：按代码分组，同代码内按总收益率降序，去除"结束信号"栏
            html_batch_headers = ['代码', '策略', '总收益率', '年化收益率', '最大回撤',
                                  '夏普比率', '胜率', '交易次数', '盈利因子']
            _grp = {}
            _grp_order = []
            for r in batch_results:
                if r['symbol'] not in _grp:
                    _grp[r['symbol']] = []
                    _grp_order.append(r['symbol'])
                _grp[r['symbol']].append(r)

            def _ret_key(x):
                v = x['risk'].get('total_return')
                return v if v is not None else float('-inf')

            html_batch_rows = []
            for _sym in _grp_order:
                for r in sorted(_grp[_sym], key=_ret_key, reverse=True):
                    risk = r['risk']
                    html_batch_rows.append([
                        r['symbol'], r['strategy'],
                        _pct(risk.get('total_return')),
                        _pct(risk.get('annual_return')),
                        _pct(risk.get('max_drawdown')),
                        _f(risk.get('sharpe_ratio')),
                        _pct(risk.get('win_rate')),
                        _fmt_trade_count(risk),
                        _f(risk.get('profit_factor')),
                    ])

            # 2) 策略收益分析：平均交易次数、持股天数（置于股票数后面）
            html_strat_headers = ['策略', '股票数', '平均交易次数', '平均持股天数', '平均总收益率',
                                  '持股年化收益率', '中位数收益率', '交易正收益比',
                                  '个股正收益比', '最高', '最低']
            _strat_all = []    # 全部策略行（含持有）
            _calc_data = []    # 用于"平均"行计算（不含持有）
            for _name in sorted(_by_strat):
                n, avg, med, trade_wr, stock_wr, mx, mn = _agg(
                    _by_strat[_name], _strt_trade_rets.get(_name))
                avg_tr = _strt_trades.get(_name, 0) / n if n else 0
                _hd = _hold_days(_strt_hold.get(_name, []), n)
                _d = {'name': _name, 'n': n, 'avg_tr': avg_tr, 'hd': _hd,
                      'avg': avg, 'ha': _hold_annual(avg, _hd, avg_tr),
                      'med': med, 'trade_wr': trade_wr, 'stock_wr': stock_wr,
                      'mx': mx, 'mn': mn}
                _strat_all.append(_d)
                if _name != '持有':
                    _calc_data.append(_d)

            def _strat_cells(d):
                return [d['name'], f"{d['n']:.0f}", _fmt_avg_trades(d['avg_tr']),
                        'N/A' if d['hd'] is None else f"{d['hd']:.1f}",
                        _fmt_pct(d['avg']), _fmt_pct(d['ha']), _fmt_pct(d['med']),
                        f"{d['trade_wr']:.0f}%", f"{d['stock_wr']:.0f}%",
                        _fmt_pct(d['mx']), _fmt_pct(d['mn'])]

            _strat_rows_data = list(_strat_all)
            # 策略行（含持有）按平均总收益率从大到小排序
            _strat_rows_data.sort(key=lambda d: d['avg'], reverse=True)
            if _calc_data:
                # "平均"行：最高取最高、最低取最低，其它栏取各列平均值（不含"持有"）
                _strat_rows_data.append({
                    'name': '平均',
                    'n': _mean([d['n'] for d in _calc_data]),
                    'avg_tr': _mean([d['avg_tr'] for d in _calc_data]),
                    'hd': _mean([d['hd'] for d in _calc_data]),
                    'avg': _mean([d['avg'] for d in _calc_data]),
                    'ha': _mean([d['ha'] for d in _calc_data]),
                    'med': _mean([d['med'] for d in _calc_data]),
                    'trade_wr': _mean([d['trade_wr'] for d in _calc_data]),
                    'stock_wr': _mean([d['stock_wr'] for d in _calc_data]),
                    'mx': max(d['mx'] for d in _calc_data),
                    'mn': min(d['mn'] for d in _calc_data),
                })
            html_strat_rows = [_strat_cells(d) for d in _strat_rows_data]

            # 3) 个股收益分析：每只股票一行（类似策略收益分析表）
            _by_sym = _dd(list)
            _sym_trades = _dd(int)
            _sym_hold = _dd(list)
            _sym_trade_rets = _dd(list)
            for r in batch_results:
                _nt = r['risk'].get('total_trades')
                if _nt:
                    _sym_trades[r['symbol']] += _nt
                _tdf = r['risk'].get('trades_df')
                if _tdf is not None and len(_tdf):
                    if 'holding_days' in _tdf.columns:
                        _sym_hold[r['symbol']].extend(
                            float(x) for x in _tdf['holding_days'].dropna().tolist())
                    if 'return' in _tdf.columns:
                        _sym_trade_rets[r['symbol']].extend(
                            float(x) for x in
                            pd.to_numeric(_tdf['return'], errors='coerce').dropna().tolist())
                _tr = r['risk'].get('total_return')
                if _tr is not None:
                    _by_sym[r['symbol']].append(_tr)
            stock_headers = ['股票代码', '策略数', '平均交易次数', '平均持股天数', '平均总收益率',
                             '持股年化收益率', '中位数收益率', '交易正收益比',
                             '策略正收益比', '最高', '最低']
            _stock_data = []
            for _sym in sorted(_by_sym):
                n, avg, med, trade_wr, strat_wr, mx, mn = _agg(
                    _by_sym[_sym], _sym_trade_rets.get(_sym))
                avg_tr = _sym_trades.get(_sym, 0) / n if n else 0
                _hd = _hold_days(_sym_hold.get(_sym, []), n)
                _stock_data.append({'sym': _sym, 'n': n, 'avg_tr': avg_tr, 'hd': _hd,
                                    'avg': avg, 'ha': _hold_annual(avg, _hd, avg_tr),
                                    'med': med, 'trade_wr': trade_wr,
                                    'strat_wr': strat_wr, 'mx': mx, 'mn': mn})

            def _stock_cells(d):
                return [d['sym'], f"{d['n']:.0f}", _fmt_avg_trades(d['avg_tr']),
                        'N/A' if d['hd'] is None else f"{d['hd']:.1f}",
                        _fmt_pct(d['avg']), _fmt_pct(d['ha']), _fmt_pct(d['med']),
                        f"{d['trade_wr']:.0f}%", f"{d['strat_wr']:.0f}%",
                        _fmt_pct(d['mx']), _fmt_pct(d['mn'])]

            _stock_rows_data = list(_stock_data)
            if _stock_data:
                # "平均"行：最高取最高、最低取最低，其它栏取各列平均值
                _stock_rows_data.append({
                    'sym': '平均',
                    'n': _mean([d['n'] for d in _stock_data]),
                    'avg_tr': _mean([d['avg_tr'] for d in _stock_data]),
                    'hd': _mean([d['hd'] for d in _stock_data]),
                    'avg': _mean([d['avg'] for d in _stock_data]),
                    'ha': _mean([d['ha'] for d in _stock_data]),
                    'med': _mean([d['med'] for d in _stock_data]),
                    'trade_wr': _mean([d['trade_wr'] for d in _stock_data]),
                    'strat_wr': _mean([d['strat_wr'] for d in _stock_data]),
                    'mx': max(d['mx'] for d in _stock_data),
                    'mn': min(d['mn'] for d in _stock_data),
                })
            # 所有行（含"平均"）按平均总收益率从大到小排序
            _stock_rows_data.sort(key=lambda d: d['avg'], reverse=True)
            stock_rows = [_stock_cells(d) for d in _stock_rows_data]

            # 打印输出：仅策略收益分析（个股收益分析仅保留在 HTML 报告中）
            _print_section('策略收益分析')
            _print_table(html_strat_headers, html_strat_rows)

            # 表格：策略收益分析、个股收益分析、批量回测结果
            html_tables = [('策略收益分析', html_strat_headers, html_strat_rows),
                           ('个股收益分析', stock_headers, stock_rows),
                           ('批量回测结果', html_batch_headers, html_batch_rows)]
            # --verbose：合并报表才输出交易明细（默认不输出，保持报表精简）
            if verbose:
                # 交易明细：汇总所有 股票×策略 的每笔买卖记录（含代码与策略列）
                _reason_map = {'signal': '信号', 'end': '期末平仓'}
                _detail_headers = ['代码', '策略', '买入日期', '卖出日期', '买入价', '卖出价',
                                   '持股数', '持仓天数', '收益率', '盈亏额', '退出原因']
                _detail_rows = []
                _sum_hd = 0
                _sum_ret = 0.0
                _sum_pnl = 0.0
                for r in batch_results:
                    _tdf = r['risk'].get('trades_df')
                    if _tdf is None or len(_tdf) == 0:
                        continue
                    for _, t in _tdf.iterrows():
                        ed = t.get('entry_date')
                        xd = t.get('exit_date')
                        ed = ed.strftime('%Y-%m-%d') if hasattr(ed, 'strftime') else str(ed)
                        xd = xd.strftime('%Y-%m-%d') if hasattr(xd, 'strftime') else str(xd)
                        ep = float(t.get('entry_price', 0) or 0)
                        xp = float(t.get('exit_price', 0) or 0)
                        pos = float(t.get('position', 0) or 0)
                        ret = float(t.get('return', 0) or 0)
                        hd = int(t.get('holding_days', 0) or 0)
                        pnl = (xp - ep) * pos
                        reason = _reason_map.get(t.get('exit_reason'), t.get('exit_reason'))
                        _detail_rows.append([
                            r['symbol'], r['strategy'], ed, xd, f'{ep:.4f}', f'{xp:.4f}',
                            f'{pos:.0f}', hd, f'{ret * 100:.2f}%', f'{pnl:,.2f}', reason,
                        ])
                        _sum_hd += hd
                        _sum_ret += ret
                        _sum_pnl += pnl
                if _detail_rows:
                    # 汇总行：统计持仓天数/收益率/盈亏额合计
                    _detail_rows.append(['汇总', '', '', '', '', '', '', _sum_hd,
                                         f'{_sum_ret * 100:.2f}%', f'{_sum_pnl:,.2f}', ''])
                    html_tables.append(('交易明细', _detail_headers, _detail_rows))
                # 某股票在某策略下回测的总收益率 < 0 时，追加该股票+策略的详细交易信息
                for r in batch_results:
                    tr = r['risk'].get('total_return')
                    if tr is None or tr >= 0:
                        continue
                    _th, _tr = _build_trades_detail_table(r['risk'].get('trades_df'))
                    if _tr:
                        html_tables.append(
                            (f'详细交易信息 - {r["symbol"]} {r["strategy"]}', _th, _tr))

            # 合并报告文件名：backtest_<file>_<start>_<end>_<策略名>_<mmddHHMM>.html
            _combined_filename = 'backtest_' + _backtest_report_filename(
                _file_part, strat_names, start_date, end_date)

            # 生成 HTML 报告
            try:
                html_path = _generate_backtest_html(
                    f'批量回测({len(symbols)}只)', start_date, end_date, capital,
                    tables=html_tables, report_filename=_combined_filename,
                    source_label=_format_sources(r.get('source') for r in batch_results),
                )
                click.echo(click.style(f'  HTML报告已保存到: {html_path}', fg='green'))
            except Exception as e:
                click.echo(click.style(f'  HTML报告生成失败: {e}', fg='yellow'))

            # --verbose：每只股票单独生成 HTML 报告
            # 文件名：<symbol>_<start>_<end>_<策略名连续拼接>_<mmddHHMM>.html
            for _sym in (_grp_order if verbose else []):
                try:
                    _sym_results = _grp[_sym]
                    _sym_strat_headers = ['策略', '交易次数', '持股天数', '总收益率', '年化收益率',
                                          '夏普比率', '最大回撤', '胜率', '盈利因子']
                    _sym_strat_rows = []
                    for r in sorted(_sym_results, key=_ret_key, reverse=True):
                        risk = r['risk']
                        _tdf_s = risk.get('trades_df')
                        if (_tdf_s is not None and len(_tdf_s)
                                and 'holding_days' in _tdf_s.columns):
                            _hold_sum = int(pd.to_numeric(
                                _tdf_s['holding_days'], errors='coerce').fillna(0).sum())
                        else:
                            _hold_sum = 0
                        _sym_strat_rows.append([
                            r['strategy'],
                            _fmt_trade_count(risk),
                            _hold_sum,
                            _pct(risk.get('total_return')),
                            _pct(risk.get('annual_return')),
                            _f(risk.get('sharpe_ratio')),
                            _pct(risk.get('max_drawdown')),
                            _pct(risk.get('win_rate')),
                            _f(risk.get('profit_factor')),
                        ])
                    # 该股票全部交易明细（含策略列）
                    _sym_detail_headers = ['策略', '买入日期', '卖出日期', '买入价', '卖出价',
                                           '持股数', '持仓天数', '收益率', '盈亏额', '退出原因']
                    _sym_detail_rows = []
                    _sym_sum_hd = 0
                    _sym_sum_ret = 0.0
                    _sym_sum_pnl = 0.0
                    for r in _sym_results:
                        _tdf = r['risk'].get('trades_df')
                        if _tdf is None or len(_tdf) == 0:
                            continue
                        for _, t in _tdf.iterrows():
                            ed = t.get('entry_date')
                            xd = t.get('exit_date')
                            ed = ed.strftime('%Y-%m-%d') if hasattr(ed, 'strftime') else str(ed)
                            xd = xd.strftime('%Y-%m-%d') if hasattr(xd, 'strftime') else str(xd)
                            ep = float(t.get('entry_price', 0) or 0)
                            xp = float(t.get('exit_price', 0) or 0)
                            pos = float(t.get('position', 0) or 0)
                            ret = float(t.get('return', 0) or 0)
                            hd = int(t.get('holding_days', 0) or 0)
                            pnl = (xp - ep) * pos
                            reason = _reason_map.get(t.get('exit_reason'), t.get('exit_reason'))
                            _sym_detail_rows.append([
                                r['strategy'], ed, xd, f'{ep:.4f}', f'{xp:.4f}',
                                f'{pos:.0f}', hd, f'{ret * 100:.2f}%', f'{pnl:,.2f}', reason,
                            ])
                            _sym_sum_hd += hd
                            _sym_sum_ret += ret
                            _sym_sum_pnl += pnl
                    _sym_tables = [('回测结果（按策略）', _sym_strat_headers, _sym_strat_rows)]
                    if _sym_detail_rows:
                        # 汇总行：统计持仓天数/收益率/盈亏额合计
                        _sym_detail_rows.append(['汇总', '', '', '', '', '', _sym_sum_hd,
                                                 f'{_sym_sum_ret * 100:.2f}%',
                                                 f'{_sym_sum_pnl:,.2f}', ''])
                        _sym_tables.append(('交易明细', _sym_detail_headers, _sym_detail_rows))
                    _sym_path = _generate_backtest_html(
                        _sym, start_date, end_date, capital, tables=_sym_tables,
                        charts_html=sym_charts.get(_sym, ''),
                        report_filename=_backtest_report_filename(
                            _sym, strat_names, start_date, end_date),
                        source_label=_format_sources(
                            r.get('source') for r in _sym_results),
                    )
                    click.echo(click.style(f'  HTML报告已保存到: {_sym_path}', fg='green'))
                except Exception as e:
                    click.echo(click.style(f'  {_sym} HTML报告生成失败: {e}', fg='yellow'))

            # 随机抽样模式：将本次抽取的股票代码写入 report/random_<mmddHHMM>.txt
            if random:
                try:
                    os.makedirs('report', exist_ok=True)
                    random_path = os.path.join(
                        'report', f'random_{datetime.now().strftime("%m%d%H%M")}.txt')
                    with open(random_path, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(symbols) + '\n')
                    click.echo(click.style(
                        f'  随机抽样股票代码已写入: {random_path} ({len(symbols)} 只)', fg='green'))
                except Exception as e:
                    click.echo(click.style(f'  写入随机抽样文件失败: {e}', fg='yellow'))

            click.echo()
            click.echo(click.style('  回测完成!', fg='green', bold=True))
            click.echo()
            return

        # 单只股票详细回测
        _print_header('策略回测', f'{symbol} - {", ".join(strat_names)}')

        click.echo(click.style(f'  回测区间: {start_date} ~ {end_date}', fg='white'))
        click.echo(click.style(f'  初始资金: {capital:,.0f}', fg='white'))

        # 获取数据
        click.echo(click.style('\n  正在获取股票数据...', fg='blue'))
        fetcher = DataFetcher()
        df = fetcher.get_stock_data(symbol, _warmup_fetch_start(start_date), end_date)

        if df is None or len(df) == 0:
            click.echo(click.style(f'  错误: 未能获取股票 {symbol} 的数据', fg='red'))
            return

        src_used = df.attrs.get('source')

        click.echo(click.style(f'  获取到 {len(df)} 条数据记录（含前推 {_ANALYSIS_WARMUP_DAYS} 天预热）', fg='green'))

        # 合并当日实时行情
        df, _, _, _ = _apply_realtime_to_df(df, symbol, fetcher, end_date)

        # 计算指标
        click.echo(click.style('  正在计算技术指标...', fg='blue'))
        df = add_all_indicators(df)
        click.echo(click.style(f'  已计算 {len(df.columns)} 项指标', fg='green'))

        # 信号分析/回测从 start 日期开始：忽略预热期数据
        df = _truncate_analysis_df(df, start_date)

        # 运行策略（支持多策略）
        if len(strat_keys) > 1:
            # 多策略：输出对比表（每策略一行）
            df.attrs['symbol'] = symbol
            all_results = {}
            for sk in strat_keys:
                click.echo(click.style(f'  正在运行策略: {_strat_label(sk)}...', fg='blue'))
                all_results[sk] = _run_strategy(sk, df, capital, enhance=strat_enhance.get(sk, 0))

            _print_section('多策略回测结果')

            def _pct(v):
                return f'{v * 100:.2f}%' if v is not None else 'N/A'

            def _f(v):
                return f'{v:.2f}' if v is not None else 'N/A'

            headers = ['策略', '总收益率', '年化收益率', '最大回撤', '夏普比率', '胜率', '交易次数', '盈利因子', '结束信号']
            table_rows = []
            for sk in strat_keys:
                risk = all_results[sk]
                sig_series = risk.get('signals')
                sig_value = None
                if sig_series is not None and len(sig_series) > 0:
                    sig_value = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
                sig_text = {1.0: '买入', 0.0: '观望', -1.0: '卖出'}.get(sig_value, 'N/A')
                table_rows.append([
                    _strat_label(sk),
                    _pct(risk.get('total_return')),
                    _pct(risk.get('annual_return')),
                    _pct(risk.get('max_drawdown')),
                    _f(risk.get('sharpe_ratio')),
                    _pct(risk.get('win_rate')),
                    _fmt_trade_count(risk),
                    _f(risk.get('profit_factor')),
                    sig_text,
                ])
            _print_table(headers, table_rows)

            # 生成多策略合并对比图并嵌入 HTML 报告
            charts_html = ''
            try:
                date_tag = _build_date_tag(start_date, end_date)
                multi_chart_gen = ChartGenerator(output_dir=output, prefix=f'{symbol}_multi', date_tag=date_tag)
                strategies_data = []
                for sk in strat_keys:
                    strategies_data.append({
                        'key': sk,
                        'name': _strat_label(sk),
                        'signals': all_results[sk].get('signals'),
                        'equity_curve': all_results[sk].get('equity_curve'),
                    })
                multi_path = multi_chart_gen.plot_multi_strategy_composite(df, strategies_data,
                                                                           title=f'{symbol} 多策略回测对比')
                b64 = _img_to_b64(multi_path)
                if b64:
                    charts_html = f'<img src="data:image/png;base64,{b64}" alt="多策略对比">'
            except Exception:
                pass

            try:
                tables = [('多策略回测结果', headers, table_rows)]
                for sk in strat_keys:
                    th, tr = _build_trades_detail_table(all_results[sk].get('trades_df'))
                    if tr:
                        tables.append((f'交易明细 - {_strat_label(sk)}', th, tr))
                html_path = _generate_backtest_html(
                    symbol, start_date, end_date, capital,
                    tables=tables, charts_html=charts_html,
                    report_filename=_backtest_report_filename(
                        symbol, strat_names, start_date, end_date),
                    source_label=src_used,
                )
                click.echo(click.style(f'  HTML报告已保存到: {html_path}', fg='green'))
            except Exception as e:
                click.echo(click.style(f'  HTML报告生成失败: {e}', fg='yellow'))

            click.echo()
            click.echo(click.style('  回测完成!', fg='green', bold=True))
            click.echo()
            return

        # 单策略详细回测
        strategy_key = strat_keys[0]
        strategy_name = STRATEGY_MAP[strategy_key][0]
        click.echo(click.style(f'  正在运行策略: {strategy_name}...', fg='blue'))
        date_tag = _build_date_tag(start_date, end_date)
        chart_gen = ChartGenerator(output_dir=output, date_tag=date_tag)
        df.attrs['symbol'] = symbol
        result, chart_paths, sname, sig_series = _run_single_analysis(df, strategy_key, capital, chart_gen,
                                                                      enhance=strat_enhance.get(strategy_key, 0))
        # 为 backtest 命令单独生成信号图表（无其他策略背景）
        if chart:
            try:
                bt_chart_gen = ChartGenerator(output_dir=output, prefix=f'{symbol}_{strategy_key}', date_tag=date_tag)
                chart_paths['signals'] = bt_chart_gen.plot_signal_on_price(
                    df, sig_series, title=f'{symbol} {strategy_name} - 买卖信号'
                )
            except Exception:
                chart_paths['signals'] = ''
        risk = result  # 结果已包含风险指标

        # 输出详细回测结果
        _print_section('回测结果')

        headers = ['指标', '数值']
        rows = _format_risk_report_rows(risk)

        # 补充更多指标
        rows.append(['年化波动率', f'{risk.get("annual_volatility", 0) * 100:.2f}%' if risk.get('annual_volatility') is not None else 'N/A'])
        rows.append(['卡玛比率', f'{risk.get("calmar_ratio", 0):.2f}' if risk.get('calmar_ratio') is not None else 'N/A'])
        rows.append(['平均持仓天数', f'{risk.get("avg_holding_days", 0):.1f}' if risk.get('avg_holding_days') is not None else 'N/A'])

        _print_table(headers, rows)

        # 交易明细：每笔买卖的时间与该时段盈亏
        trade_headers, trade_rows = _build_trades_detail_table(risk.get('trades_df'))
        if trade_rows:
            _print_section('交易明细（买卖时间与盈亏）')
            _print_table(trade_headers, trade_rows)

        # 图表路径
        if chart_paths:
            click.echo(click.style(f'\n    生成的图表:', fg='white'))
            for chart_name, chart_path in chart_paths.items():
                click.echo(click.style(f'      - {chart_name}: {chart_path}', fg='green'))

        # 生成 HTML 报告（含信号图表）
        charts_html = ''
        try:
            bt_chart_gen = ChartGenerator(output_dir=output, prefix=f'{symbol}_{strategy_key}', date_tag=date_tag)
            eq_curve = risk.get('equity_curve')
            sig_path = bt_chart_gen.plot_signal_composite(
                df, sig_series, strategy_key=strategy_key,
                title=f'{symbol} {strategy_name} - 回测效果', equity_curve=eq_curve,
            )
            b64 = _img_to_b64(sig_path)
            if b64:
                charts_html = f'<img src="data:image/png;base64,{b64}" alt="信号图">'
        except Exception:
            pass

        try:
            tables = [('回测结果', headers, rows)]
            if trade_rows:
                tables.append(('交易明细（买卖时间与盈亏）', trade_headers, trade_rows))
            html_path = _generate_backtest_html(
                f'{symbol} {sname}'.strip(), start_date, end_date, capital,
                tables=tables, charts_html=charts_html,
                report_filename=_backtest_report_filename(
                    symbol, [sname], start_date, end_date),
                source_label=src_used,
            )
            click.echo(click.style(f'\n  HTML报告已保存到: {html_path}', fg='green'))
        except Exception as e:
            click.echo(click.style(f'  HTML报告生成失败: {e}', fg='yellow'))

        click.echo()
        click.echo(click.style('  回测完成!', fg='green', bold=True))
        click.echo()

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


# ============================================================================
# compare 命令 - 策略对比
# ============================================================================


@cli.command('compare')
@_src_option
@_maxdrawdown_option
@click.option('--symbol', '-s', required=True, help='股票代码（必需）')
@click.option('--start', '-st', default=None, help='开始日期（默认180天前），格式: YYYYMMDD')
@click.option('--end', '-e', default=None, help='结束日期，格式: YYYYMMDD')
@click.option('--capital', '-c', default=100, type=float, help='初始资金（单位：万元，默认100万元）')
@click.option('--chart', is_flag=True, default=False, help='生成策略对比图（默认仅输出表格报告）')
def compare_cmd(symbol, start, end, capital, chart, src, atr_stop_ratio):
    """策略对比：重点输出策略的对比结果

    流程：同时运行所有策略 -> 对比回测结果 -> 生成对比图表 -> 输出排名
    """
    try:
        set_primary_source(src)
        _set_maxdrawdown(atr_stop_ratio)
        capital = capital * 10000  # -c 单位为万元，内部换算为元
        start_date, end_date = _parse_dates(start, end)
        _ensure_symbol_cap_map([symbol])
        _preload_trend_states(start_date, end_date)

        _print_header('策略对比分析', symbol)

        click.echo(click.style(f'  分析区间: {start_date} ~ {end_date}', fg='white'))
        click.echo(click.style(f'  初始资金: {capital:,.0f}', fg='white'))

        # 获取数据
        click.echo(click.style('\n  正在获取股票数据...', fg='blue'))
        fetcher = DataFetcher()
        df = fetcher.get_stock_data(symbol, _warmup_fetch_start(start_date), end_date)

        if df is None or len(df) == 0:
            click.echo(click.style(f'  错误: 未能获取股票 {symbol} 的数据', fg='red'))
            return

        click.echo(click.style(f'  获取到 {len(df)} 条数据记录（含前推 {_ANALYSIS_WARMUP_DAYS} 天预热）', fg='green'))

        # 合并当日实时行情
        df, _, _, _ = _apply_realtime_to_df(df, symbol, fetcher, end_date)

        # 计算指标
        click.echo(click.style('  正在计算技术指标...', fg='blue'))
        df = add_all_indicators(df)
        click.echo(click.style(f'  已计算 {len(df.columns)} 项指标', fg='green'))

        # 信号分析/对比从 start 日期开始：忽略预热期数据
        df = _truncate_analysis_df(df, start_date)
        df.attrs['symbol'] = symbol

        # 运行所有策略
        all_results = {}
        all_risks = {}

        for sk in ALL_STRATEGIES:
            click.echo(click.style(f'  正在运行策略: {_strat_label(sk)}...', fg='blue'))
            result = _run_strategy(sk, df, capital)
            risk = result  # 结果已包含风险指标
            all_results[sk] = result
            all_risks[sk] = risk

        # 输出对比表格
        _print_section('策略对比结果')

        headers = ['策略', '结束信号', '总收益率', '年化收益率', '持股年化收益率',
                   '最大回撤', '夏普比率', '胜率', '交易次数', '盈利因子']
        table_rows = []
        for sk in ALL_STRATEGIES:
            risk = all_risks[sk]
            name = _strat_label(sk)

            # 结束日期信号值（1=买, 0=观望, -1=卖）
            sig_series = all_results[sk].get('signals')
            sig_value = None
            if sig_series is not None and len(sig_series) > 0:
                sig_value = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
            sig_text = {1.0: '买入', 0.0: '观望', -1.0: '卖出'}.get(sig_value, 'N/A')

            def _pct(v):
                return f'{v * 100:.2f}%' if v is not None else 'N/A'

            def _f(v):
                return f'{v:.2f}' if v is not None else 'N/A'

            # 持股年化收益率：与 backtest 同口径
            _tdf = risk.get('trades_df')
            if _tdf is not None and len(_tdf) and 'holding_days' in _tdf.columns:
                _total_hold = float(pd.to_numeric(
                    _tdf['holding_days'], errors='coerce').fillna(0).sum())
            else:
                _total_hold = None
            _hold_annual = _calc_hold_annual(
                risk.get('total_return'), _total_hold, risk.get('total_trades') or 0)

            table_rows.append([
                name,
                sig_text,
                _pct(risk.get('total_return')),
                _pct(risk.get('annual_return')),
                _pct(_hold_annual),
                _pct(risk.get('max_drawdown')),
                _f(risk.get('sharpe_ratio')),
                _pct(risk.get('win_rate')),
                _fmt_trade_count(risk),
                _f(risk.get('profit_factor')),
            ])

        _print_table(headers, table_rows)

        # 排名
        _print_section('综合排名（按夏普比率）')

        def _end_signal(sk):
            sig_series = all_results[sk].get('signals')
            if sig_series is not None and len(sig_series) > 0:
                v = float(sig_series.iloc[-1]) if hasattr(sig_series, 'iloc') else float(sig_series[-1])
                return v
            return None

        rankings = [(_strat_label(sk), all_risks[sk].get('sharpe_ratio'),
                     all_risks[sk].get('total_return'),
                     all_risks[sk].get('max_drawdown'),
                     all_risks[sk].get('total_trades'),
                     _end_signal(sk)) for sk in ALL_STRATEGIES]
        rankings.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True)

        headers = ['排名', '策略', '夏普比率', '总收益率', '最大回撤', '交易次数', '结束信号']
        rank_rows = []
        for i, (name, sharpe, total_ret, drawdown, nt, sig) in enumerate(rankings, 1):
            shp_str = f'{sharpe:.2f}' if sharpe is not None else 'N/A'
            ret_str = f'{total_ret * 100:.2f}%' if total_ret is not None else 'N/A'
            dd_str = f'{drawdown * 100:.2f}%' if drawdown is not None else 'N/A'
            tr_str = str(nt) if nt is not None else 'N/A'
            sig_str = {1.0: '买入', 0.0: '观望', -1.0: '卖出'}.get(sig, 'N/A')
            rank_rows.append([i, name, shp_str, ret_str, dd_str, tr_str, sig_str])
        _print_table(headers, rank_rows)

        # 生成对比图表
        if chart:
            click.echo(click.style('\n  正在生成策略对比图表...', fg='blue'))
            chart_gen = ChartGenerator(output_dir='./output', date_tag=_build_date_tag(start_date, end_date))
            try:
                compare_data = {_strat_label(sk): all_results[sk] for sk in ALL_STRATEGIES}
                compare_path = chart_gen.plot_compare_strategies(compare_data)
                click.echo(click.style(f'    策略对比图: {compare_path}', fg='green'))
            except Exception as e:
                click.echo(click.style(f'    对比图生成失败: {e}', fg='yellow'))

        click.echo()
        click.echo(click.style('  对比分析完成!', fg='green', bold=True))
        click.echo()

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


# ============================================================================
# indicators 命令 - 计算技术指标
# ============================================================================


@cli.command('indicators')
@_src_option
@click.option('--symbol', '-s', required=True, help='股票代码（必需）')
@click.option('--start', '-st', default=None, help='开始日期（默认180天前），格式: YYYYMMDD')
@click.option('--end', '-e', default=None, help='结束日期，格式: YYYYMMDD')
@click.option('--output', '-o', default=None, help='输出CSV路径（可选，不指定则打印到屏幕）')
@click.option('--chart', is_flag=True, default=False, help='生成包含所有因子曲线子图的K线图并输出为HTML文件（保存到 report/）')
def indicators_cmd(symbol, start, end, output, chart, src):
    """计算各个因子：重点输出各因子、指标的值

    流程：获取数据 -> 计算所有指标 -> 保存到CSV或打印
    --chart 时额外生成全部因子子图K线图，输出为 HTML 文件。
    """
    try:
        set_primary_source(src)
        start_date, end_date = _parse_dates(start, end)

        _print_header('技术指标计算', symbol)
        click.echo(click.style(f'  计算区间: {start_date} ~ {end_date}', fg='white'))

        # 获取数据
        click.echo(click.style('\n  正在获取股票数据...', fg='blue'))
        fetcher = DataFetcher()
        df = fetcher.get_stock_data(symbol, _warmup_fetch_start(start_date), end_date)

        if df is None or len(df) == 0:
            click.echo(click.style(f'  错误: 未能获取股票 {symbol} 的数据', fg='red'))
            return

        click.echo(click.style(f'  获取到 {len(df)} 条数据记录（含前推 {_ANALYSIS_WARMUP_DAYS} 天预热）', fg='green'))

        # 计算指标
        click.echo(click.style('  正在计算所有技术指标...', fg='blue'))
        df = add_all_indicators(df)

        if df is None or len(df) == 0:
            click.echo(click.style('  错误: 指标计算失败', fg='red'))
            return

        click.echo(click.style(f'  已计算 {len(df.columns)} 项指标', fg='green'))

        # 指标展示从 start 日期开始：忽略预热期数据
        df = _truncate_analysis_df(df, start_date)

        indicator_cols = [c for c in df.columns if c not in ['Open', 'High', 'Low', 'Close', 'Volume', 'date']]

        # --chart：生成全部因子子图K线图并输出为 HTML
        html_path = None
        if chart:
            click.echo(click.style('\n  正在生成全部因子子图K线图...', fg='blue'))
            try:
                date_tag = _build_date_tag(start_date, end_date)
                cg = ChartGenerator(output_dir='./output', prefix=f'{symbol}_indicators', date_tag=date_tag)
                png_path = cg.plot_all_indicators(df, title=f'{symbol} 全部指标子图 ({start_date} ~ {end_date})')
                click.echo(click.style(f'  因子K线图: {png_path}', fg='green'))

                from datetime import datetime as dt
                b64 = _img_to_b64(png_path)
                html_path = os.path.join('report', f'indicators_{symbol}_{dt.now().strftime("%Y%m%d_%H%M%S")}.html')
                os.makedirs('report', exist_ok=True)
                img_html = f'<img src="data:image/png;base64,{b64}" alt="因子子图K线" style="width:100%;">' if b64 else '图表生成失败'
                html_doc = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>指标因子图表 - {symbol}</title>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; margin: 20px; color: #333; }}
h1 {{ color: #1a5276; }}
.info {{ color: #666; margin-bottom: 10px; }}
</style>
</head>
<body>
<h1>{symbol} 全部因子子图K线</h1>
<div class="info">
    <span><strong>区间:</strong> {start_date} ~ {end_date}</span>
    <span style="margin-left:20px;"><strong>指标数:</strong> {len(indicator_cols)}</span>
    <span style="margin-left:20px;"><strong>生成时间:</strong> {dt.now().strftime("%Y-%m-%d %H:%M:%S")}</span>
    <span style="margin-left:20px;"><strong>命令选项:</strong> {_command_options_str()}</span>
    <span style="margin-left:20px;"><strong>软件版本:</strong> {__version__}</span>
</div>
{img_html}
</body>
</html>'''
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html_doc)
                click.echo(click.style(f'  HTML图表已保存到: {html_path}', fg='green'))
            except Exception as e:
                click.echo(click.style(f'  因子图表生成失败: {e}', fg='yellow'))

        # 列出所有指标
        _print_section('指标列表')
        click.echo(click.style(f'  共 {len(indicator_cols)} 项指标:', fg='white'))
        for i, col in enumerate(indicator_cols):
            click.echo(f'    {i+1:>3d}. {col}')

        # 输出数据
        if output:
            click.echo(click.style(f'\n  正在保存到 {output}...', fg='blue'))
            # 确保输出目录存在
            output_dir = os.path.dirname(output)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            df.to_csv(output, index=True, encoding='utf-8-sig')
            click.echo(click.style(f'  已保存到: {output}', fg='green'))
            click.echo(click.style(f'  文件大小: {os.path.getsize(output):,} 字节', fg='white'))
        else:
            # 打印尾部数据预览
            _print_section('数据预览（最近10条）')
            click.echo(click.style(df.tail(10).to_string(), fg='white'))

        click.echo()
        click.echo(click.style('  指标计算完成!', fg='green', bold=True))
        click.echo()

    except Exception as e:
        click.echo(click.style(f'\n  错误: {e}', fg='red', bold=True))
        import traceback
        click.echo(click.style(traceback.format_exc(), fg='red'))


# ============================================================================
# 主入口
# ============================================================================


if __name__ == '__main__':
    cli()