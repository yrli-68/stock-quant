#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""沪深300指数大盘趋势判断。"""

import logging
from datetime import datetime, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd

from core.data_fetcher import DataFetcher
from core.indicators import add_all_indicators, calc_rsi_divergence

logger = logging.getLogger(__name__)

DEFAULT_INDEX_CODE = '000852.SH'
ADR_WINDOW = 10
ADR_BULL_THRESHOLD = 1.2
ADR_BEAR_THRESHOLD = 0.7
ADX_TREND_THRESHOLD = 25
ADR_OVERHEAT_THRESHOLD = 1.5
ADR_OVERCOLD_THRESHOLD = 0.75
BIAS20_OVERHEAT_THRESHOLD = 0.07
BIAS20_OVERCOLD_THRESHOLD = -0.07
RSI_OVERHEAT_THRESHOLD = 70.0
RSI_OVERCOLD_THRESHOLD = 30.0

INDEX_NAMES = {
    '000300': '沪深300',
    '000852': '中证1000',
    '000905': '中证500',
    '000016': '上证50',
    '000688': '科创50',
    '000001': '上证指数',
    '399001': '深证成指',
    '399005': '中小100',
    '399006': '创业板指',
    '000903': '中证100',
    '000922': '中证红利',
}


def get_market_trend_index():
    """从 squant.toml 读取大盘趋势指数代码，默认 000852（中证1000）。"""
    try:
        from core.config import load_config
        code = str(load_config().get('market_trend_index', DEFAULT_INDEX_CODE)).strip()
        return code or DEFAULT_INDEX_CODE
    except Exception:
        return DEFAULT_INDEX_CODE


def get_index_name(code):
    """指数中文名；兼容 000300 / 000300.SH / sh000300 等写法。"""
    c = str(code or '').strip().upper()
    if '.' in c:
        left, right = c.split('.', 1)
        if right in ('SH', 'SZ', 'BJ') and left:
            c = left
        elif left in ('SH', 'SZ', 'BJ') and right:
            c = right
    if len(c) > 2 and c[:2] in ('SH', 'SZ', 'BJ') and c[2:].isdigit():
        c = c[2:]
    return INDEX_NAMES.get(c, str(code))


def _value(row, name):
    value = row.get(name)
    if value is None or pd.isna(value):
        return None
    return float(value)


def _fetch_adr_series(start_date, end_date):
    """按 ADR(N)=N日上涨家数之和/N日下跌家数之和 计算市场 ADR。"""
    try:
        from core.db import fetch_market_breadth_counts
        fetch_start = (pd.Timestamp(start_date) - timedelta(days=30)).strftime('%Y-%m-%d')
        rows = fetch_market_breadth_counts(fetch_start, end_date)
        if not rows:
            return pd.Series(dtype=float)
        bdf = pd.DataFrame(rows, columns=['trade_date', 'advance_count', 'decline_count'])
        bdf['trade_date'] = pd.to_datetime(bdf['trade_date'])
        bdf = bdf.drop_duplicates('trade_date').set_index('trade_date').sort_index()
        adv = pd.to_numeric(bdf['advance_count'], errors='coerce')
        dec = pd.to_numeric(bdf['decline_count'], errors='coerce')
        up_sum = adv.rolling(ADR_WINDOW, min_periods=ADR_WINDOW).sum()
        down_sum = dec.rolling(ADR_WINDOW, min_periods=ADR_WINDOW).sum()
        adr = up_sum / down_sum.where(down_sum > 0)
        return adr.dropna()
    except Exception as exc:
        logger.warning("获取市场ADR失败: %s", exc)
        return pd.Series(dtype=float)


def _align_adr(adr, index):
    if adr is None or len(adr) == 0:
        return pd.Series(np.nan, index=index, dtype=float)
    series = pd.to_numeric(adr, errors='coerce').sort_index()
    return series.reindex(index, method='ffill')


def classify_market_trend_series(df, adr=None):
    """逐日判断趋势，返回与 df 索引对齐的状态 Series。"""
    if df is None or len(df) == 0:
        return pd.Series(dtype=object)

    if adr is None:
        adr = _fetch_adr_series(df.index[0], df.index[-1])
    adr_series = _align_adr(adr, df.index)

    ma5 = pd.to_numeric(df.get('MA5'), errors='coerce') if 'MA5' in df.columns else None
    ma20 = pd.to_numeric(df.get('MA20'), errors='coerce') if 'MA20' in df.columns else None
    adx = pd.to_numeric(df.get('ADX14'), errors='coerce') if 'ADX14' in df.columns else None
    plus_di = pd.to_numeric(df.get('PLUS_DI'), errors='coerce') if 'PLUS_DI' in df.columns else None
    minus_di = pd.to_numeric(df.get('MINUS_DI'), errors='coerce') if 'MINUS_DI' in df.columns else None
    dif = pd.to_numeric(df.get('MACD_DIF'), errors='coerce') if 'MACD_DIF' in df.columns else None
    close = pd.to_numeric(df.get('close'), errors='coerce') if 'close' in df.columns else None
    rsi = pd.to_numeric(df.get('RSI14'), errors='coerce') if 'RSI14' in df.columns else None
    if any(v is None for v in (ma5, ma20, adx, plus_di, minus_di,
                               dif, close, rsi)):
        return pd.Series('未知', index=df.index, dtype=object)

    slope5 = close.rolling(5).apply(
        lambda y: float(np.polyfit(np.arange(len(y)), y, 1)[0]), raw=True)
    bias20 = (close - ma20) / ma20.where(ma20 != 0)

    bull = (((ma5 > ma20) & (slope5 > 0))
            | ((adx >= ADX_TREND_THRESHOLD) & (plus_di > minus_di))
            | (adr_series > ADR_BULL_THRESHOLD))
    bear = (((ma5 < ma20) & (slope5 < 0))
            | ((adx >= ADX_TREND_THRESHOLD) & (plus_di < minus_di))
            | (adr_series < ADR_BEAR_THRESHOLD))

    div = calc_rsi_divergence(df)
    rsi_top_div = div['bearish_divergence'].astype(bool)
    rsi_bottom_div = div['bullish_divergence'].astype(bool)

    overheat = (bull
                & ((rsi > RSI_OVERHEAT_THRESHOLD) | rsi_top_div)
                & (adr_series > ADR_OVERHEAT_THRESHOLD)
                & (bias20 > BIAS20_OVERHEAT_THRESHOLD))
    overcold = (bear
                & ((rsi < RSI_OVERCOLD_THRESHOLD) | rsi_bottom_div)
                & (adr_series < ADR_OVERCOLD_THRESHOLD)
                & (bias20 < BIAS20_OVERCOLD_THRESHOLD))

    states = pd.Series('震荡', index=df.index, dtype=object)
    states[bull] = '上涨'
    states[bear] = '下跌'
    states[overheat] = '过热'
    states[overcold] = '过冷'

    valid = (ma5.notna() & ma20.notna() & adx.notna()
             & plus_di.notna() & minus_di.notna() & dif.notna()
             & close.notna() & rsi.notna() & slope5.notna()
             & bias20.notna())
    states[~valid] = '未知'
    return states


def classify_market_trend(df, adr=None):
    """根据沪深300/中证1000日线指标判断最新趋势。"""
    if df is None or len(df) < 60:
        return '未知'
    if adr is None:
        adr = _fetch_adr_series(df.index[0], df.index[-1])
    states = classify_market_trend_series(df, adr=adr)
    if states.empty:
        return '未知'
    return str(states.iloc[-1])


@lru_cache(maxsize=32)
def market_trend_states(start_date, end_date, index_code=None):
    """返回指定区间内每日大盘趋势状态 Series。"""
    code = str(index_code or get_market_trend_index()).strip() or DEFAULT_INDEX_CODE
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date)
    fetch_start = (start_dt - timedelta(days=365)).strftime('%Y-%m-%d')
    try:
        fetcher = DataFetcher()
        df = fetcher.get_index_data(code, fetch_start, end_date)
    except Exception as exc:
        logger.warning("获取%s指数数据失败: %s", get_index_name(code), exc)
        return pd.Series(dtype=object)

    if df is None or df.empty:
        return pd.Series(dtype=object)

    df = add_all_indicators(df)
    adr = _fetch_adr_series(df.index[0], df.index[-1])
    states = classify_market_trend_series(df, adr=adr)
    return states[(states.index >= start_dt) & (states.index <= end_dt)]


def format_market_trend_reason(trend):
    """根据趋势结果字典生成具体触发原因。"""
    if not trend:
        return '数据不足'
    state = str(trend.get('state') or '未知')
    if state == '未知':
        return trend.get('error') or '有效指标不足'
    ma5, ma20 = trend.get('ma5'), trend.get('ma20')
    slope5 = trend.get('slope5')
    adx, pdi, mdi = trend.get('adx'), trend.get('plus_di'), trend.get('minus_di')
    adr, rsi, bias = trend.get('adr'), trend.get('rsi'), trend.get('bias20')
    ma_up = ma5 is not None and ma20 is not None and ma5 > ma20
    ma_down = ma5 is not None and ma20 is not None and ma5 < ma20
    dmi_up = adx is not None and pdi is not None and mdi is not None and adx >= 25 and pdi > mdi
    dmi_down = adx is not None and pdi is not None and mdi is not None and adx >= 25 and pdi < mdi
    adr_up = adr is not None and adr > ADR_BULL_THRESHOLD
    adr_down = adr is not None and adr < ADR_BEAR_THRESHOLD
    up_parts, down_parts = [], []
    if ma_up:
        up_parts.append('MA5>MA20')
    if slope5 is not None and slope5 > 0:
        up_parts.append('slope5>0')
    if dmi_up:
        up_parts.append('ADX>=25且+DI>-DI')
    if adr_up:
        up_parts.append('ADR10>1.2')
    if ma_down:
        down_parts.append('MA5<MA20')
    if slope5 is not None and slope5 < 0:
        down_parts.append('slope5<0')
    if dmi_down:
        down_parts.append('ADX>=25且+DI<-DI')
    if adr_down:
        down_parts.append('ADR10<0.7')

    if state == '上涨':
        return '；'.join(up_parts) or '指数上涨条件成立'
    if state == '下跌':
        return '；'.join(down_parts) or '指数下跌条件成立'
    if state == '过热':
        extra = up_parts[:]
        if rsi is not None and rsi > RSI_OVERHEAT_THRESHOLD:
            extra.append(f'RSI14={rsi:.1f}>70')
        elif trend.get('rsi_top_divergence'):
            extra.append('RSI顶背离')
        if adr is not None and adr > ADR_OVERHEAT_THRESHOLD:
            extra.append('ADR10>1.5')
        if bias is not None and bias > BIAS20_OVERHEAT_THRESHOLD:
            extra.append(f'BIAS20={bias*100:.2f}%>7%')
        return '；'.join(extra) or '过热条件成立'
    if state == '过冷':
        extra = down_parts[:]
        if rsi is not None and rsi < RSI_OVERCOLD_THRESHOLD:
            extra.append(f'RSI14={rsi:.1f}<30')
        elif trend.get('rsi_bottom_divergence'):
            extra.append('RSI底背离')
        if adr is not None and adr < ADR_OVERCOLD_THRESHOLD:
            extra.append('ADR10<0.75')
        if bias is not None and bias < BIAS20_OVERCOLD_THRESHOLD:
            extra.append(f'BIAS20={bias*100:.2f}%<-7%')
        return '；'.join(extra) or '过冷条件成立'
    if state == '震荡':
        pieces = []
        if adx is not None:
            pieces.append(f'ADX14={adx:.1f}')
        return '未满足上涨/下跌条件；' + '；'.join(pieces)
    return '状态条件成立'


@lru_cache(maxsize=16)
def analyze_market_trend(end_date=None, index_code=None):
    """获取指定指数近期日线并分析趋势，返回结果字典。"""
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')
    code = str(index_code or get_market_trend_index()).strip() or DEFAULT_INDEX_CODE
    index_name = get_index_name(code)
    end_dt = pd.Timestamp(end_date)
    start_date = (end_dt - timedelta(days=365)).strftime('%Y-%m-%d')

    try:
        fetcher = DataFetcher()
        df = fetcher.get_index_data(code, start_date, end_date)
    except Exception as exc:
        logger.warning("获取%s指数数据失败: %s", index_name, exc)
        return {'state': '未知', 'date': end_date, 'index_code': code,
                'index_name': index_name, 'error': str(exc)}

    if df is None or df.empty:
        return {'state': '未知', 'date': end_date, 'index_code': code,
                'index_name': index_name, 'error': f'{index_name}指数数据为空'}

    try:
        df = add_all_indicators(df)
        adr_series = _fetch_adr_series(df.index[0], df.index[-1])
        adr_aligned = _align_adr(adr_series, df.index)
        div = calc_rsi_divergence(df)
        state = classify_market_trend(df, adr=adr_series)
        row = df.iloc[-1]
        adr_val = adr_aligned.iloc[-1]
        close_val = _value(row, 'close')
        ma20_val = _value(row, 'MA20')
        bias20_val = ((close_val - ma20_val) / ma20_val
                      if close_val is not None and ma20_val not in (None, 0) else None)
        slope5_val = None
        try:
            cs = pd.to_numeric(df['close'], errors='coerce')
            slope5_val = float(cs.rolling(5).apply(
                lambda y: float(np.polyfit(np.arange(len(y)), y, 1)[0]),
                raw=True).iloc[-1])
        except Exception:
            pass
        result = {
            'state': state,
            'date': df.index[-1].strftime('%Y-%m-%d'),
            'index_code': code,
            'index_name': index_name,
            'close': _value(row, 'close'),
            'ma5': _value(row, 'MA5'),
            'ma10': _value(row, 'MA10'),
            'ma20': _value(row, 'MA20'),
            'ma30': _value(row, 'MA30'),
            'ma120': _value(row, 'MA120'),
            'adx': _value(row, 'ADX14'),
            'plus_di': _value(row, 'PLUS_DI'),
            'minus_di': _value(row, 'MINUS_DI'),
            'dif': _value(row, 'MACD_DIF'),
            'rsi': _value(row, 'RSI14'),
            'rsi_top_divergence': bool(div['bearish_divergence'].iloc[-1]),
            'rsi_bottom_divergence': bool(div['bullish_divergence'].iloc[-1]),
            'adr': None if pd.isna(adr_val) else float(adr_val),
            'adr_window': ADR_WINDOW,
            'bias20': bias20_val,
            'slope5': slope5_val,
            'error': None,
        }
        result['reason'] = format_market_trend_reason(result)
        return result
    except Exception as exc:
        logger.warning("%s趋势计算失败: %s", index_name, exc)
        return {'state': '未知', 'date': end_date, 'index_code': code,
                'index_name': index_name, 'error': str(exc)}
