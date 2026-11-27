#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""--verbose 下的每日信号综合打分（10 个指标，-1/0/+1）。"""

import numpy as np
import pandas as pd

from core.indicators import update_atr_trailing_stop, calc_rsi, calc_wr


def combine_signals(sig_map):
    """将多策略信号合成：任一卖出优先，否则任一买入。"""
    series_list = [s for s in (sig_map or {}).values()
                   if s is not None and len(s) > 0]
    if not series_list:
        return None
    index = series_list[0].index
    buy = pd.Series(False, index=index)
    sell = pd.Series(False, index=index)
    for s in series_list:
        v = pd.to_numeric(s.reindex(index), errors='coerce').fillna(0)
        buy |= (v > 0)
        sell |= (v < 0)
    out = pd.Series(0.0, index=index)
    out[buy] = 1.0
    out[sell] = -1.0
    return out


def _last(series):
    try:
        v = series.iloc[-1]
        return float(v) if pd.notna(v) else None
    except Exception:
        return None


def _atr_position_score(df, signals):
    if signals is None or len(signals) == 0:
        return 0
    try:
        close = pd.to_numeric(df['close'], errors='coerce')
        atr = (pd.to_numeric(df['ATR14'], errors='coerce')
               if 'ATR14' in df.columns else None)
        if atr is None:
            return 0
        sig = pd.to_numeric(signals.reindex(df.index), errors='coerce').fillna(0)
        in_pos = False
        entry = None
        stop_price = None
        for i in range(len(df)):
            c = close.iloc[i]
            a = atr.iloc[i]
            if pd.isna(c) or pd.isna(a):
                continue
            c, a = float(c), float(a)
            val = float(sig.iloc[i])
            if not in_pos:
                if val > 0:
                    in_pos = True
                    entry = c
                    stop_price = update_atr_trailing_stop(c, a)
            else:
                stop_price = update_atr_trailing_stop(
                    c, a, stop_price)
                if val < 0:
                    in_pos = False
                    entry = None
                    stop_price = None
        if not in_pos or entry is None or stop_price is None:
            return 0
        pnl = c / entry - 1.0
        distance = c - stop_price
        if pnl > 0 and distance > a:
            return 1
        if distance <= 0.5 * a or pnl <= -0.05:
            return -1
        return 0
    except Exception:
        return 0


def compute_signal_score(df, signals=None):
    """计算最新交易日的 10 指标综合打分。"""
    parts = [0] * 10
    if df is None or len(df) == 0:
        return {'total': 0, 'parts': parts, 'text': '+0 (0000000000)'}

    close = pd.to_numeric(df.get('close'), errors='coerce')
    ma20 = pd.to_numeric(df.get('MA20'), errors='coerce')
    adx = pd.to_numeric(df.get('ADX14'), errors='coerce')
    pdi = pd.to_numeric(df.get('PLUS_DI'), errors='coerce')
    mdi = pd.to_numeric(df.get('MINUS_DI'), errors='coerce')
    bar = pd.to_numeric(df.get('MACD_BAR'), errors='coerce')
    rsi = pd.to_numeric(df.get('RSI14'), errors='coerce')
    try:
        rsi6 = (pd.to_numeric(df['RSI6'], errors='coerce')
                if 'RSI6' in df.columns else calc_rsi(df, 6))
        rsi12 = (pd.to_numeric(df['RSI12'], errors='coerce')
                 if 'RSI12' in df.columns else calc_rsi(df, 12))
    except Exception:
        rsi6 = None
        rsi12 = None
    upper = pd.to_numeric(df.get('BOLL_UPPER'), errors='coerce')
    middle = pd.to_numeric(df.get('BOLL_MIDDLE'), errors='coerce')
    lower = pd.to_numeric(df.get('BOLL_LOWER'), errors='coerce')
    obv = pd.to_numeric(df.get('OBV'), errors='coerce')

    # 1 DMI
    if _last(adx) is not None and _last(pdi) is not None and _last(mdi) is not None:
        a, p, m = _last(adx), _last(pdi), _last(mdi)
        if a >= 20:
            parts[0] = 1 if p > m else (-1 if p < m else 0)

    # 2 MACD
    if _last(bar) is not None:
        b = _last(bar)
        prev_bar = bar.iloc[-2] if len(bar) > 1 and pd.notna(bar.iloc[-2]) else None
        prev2_bar = bar.iloc[-3] if len(bar) > 2 and pd.notna(bar.iloc[-3]) else None
        if prev_bar is not None and prev2_bar is not None:
            if b > prev_bar > prev2_bar:
                parts[1] = 1
            elif b < prev_bar < prev2_bar:
                parts[1] = -1

    # 3 OBV
    try:
        obv_ma5 = obv.rolling(5).mean()
        obv_ma10 = obv.rolling(10).mean()
        price_ma5 = close.rolling(5).mean()
        price_ma10 = close.rolling(10).mean()
        obv_last = _last(obv)
        obv5 = _last(obv_ma5)
        obv10 = _last(obv_ma10)
        price_last = _last(close)
        price5 = _last(price_ma5)
        price10 = _last(price_ma10)
        if None not in (obv_last, obv5, obv10, price_last, price5, price10):
            if obv_last > obv5 > obv10 and price_last > price5 > price10:
                parts[2] = 1
            elif obv_last < obv5 < obv10 and price_last < price5 < price10:
                parts[2] = -1
    except Exception:
        pass

    # 4 BIAS20
    bias = None
    if _last(ma20) not in (None, 0):
        bias = (float(close.iloc[-1]) - _last(ma20)) / _last(ma20)
    if bias is not None:
        if -0.03 <= bias <= 0.06:
            parts[3] = 1
        elif bias > 0.08:
            parts[3] = -1
        else:
            parts[3] = 0

    # 5 RSI
    r = _last(rsi)
    if r is not None:
        golden_cross = False
        death_cross = False
        try:
            cur6 = float(rsi6.iloc[-1])
            cur12 = float(rsi12.iloc[-1])
            prev6 = float(rsi6.iloc[-2])
            prev12 = float(rsi12.iloc[-2])
            if not any(pd.isna(v) for v in (cur6, cur12, prev6, prev12)):
                golden_cross = prev6 <= prev12 and cur6 > cur12
                death_cross = prev6 >= prev12 and cur6 < cur12
        except Exception:
            pass
        if (r < 40 and golden_cross) or r < 30:
            parts[4] = 1
        elif (r > 60 and death_cross) or r > 70:
            parts[4] = -1

    # 6 BOLL
    try:
        c = float(close.iloc[-1])
        mid = _last(middle)
        up = _last(upper)
        lo = _last(lower)
        if mid is not None and up is not None and lo is not None and up > mid > lo:
            prev_c = float(close.iloc[-2]) if len(close) > 1 and pd.notna(close.iloc[-2]) else None
            recent = close.iloc[-5:]
            recent_high = float(recent.max()) if pd.notna(recent.max()) else None
            recent_low = float(recent.min()) if pd.notna(recent.min()) else None
            buy = ((prev_c is not None and prev_c <= lo)
                   or (recent_high is not None and recent_high < up and up > c > mid))
            sell = ((prev_c is not None and prev_c >= up)
                    or (recent_low is not None and recent_low > lo and mid > c > lo))
            if buy:
                parts[5] = 1
            elif sell:
                parts[5] = -1
    except Exception:
        pass

    # 7 WR
    try:
        if 'WR14' in df.columns:
            wr = pd.to_numeric(df['WR14'], errors='coerce')
        else:
            wr = calc_wr(df, 14)
        w = _last(wr)
        if w is not None:
            positive_wr = -w
            if positive_wr > 80:
                parts[6] = 1
            elif positive_wr < 20:
                parts[6] = -1
    except Exception:
        pass

    # 8 ATR position score
    parts[7] = _atr_position_score(df, signals)

    # 9 KDJ
    kdj_k = pd.to_numeric(df.get('KDJ_K'), errors='coerce')
    kdj_d = pd.to_numeric(df.get('KDJ_D'), errors='coerce')
    kdj_j = pd.to_numeric(df.get('KDJ_J'), errors='coerce')
    k = _last(kdj_k)
    d = _last(kdj_d)
    j = _last(kdj_j)
    if k is not None or d is not None or j is not None:
        if (k is not None and k < 20) or (j is not None and j < 0):
            parts[8] = 1
        elif (d is not None and d > 80) or (j is not None and j > 100):
            parts[8] = -1

    # 10 SAR
    sar = pd.to_numeric(df.get('SAR'), errors='coerce')
    ma5 = pd.to_numeric(df.get('MA5'), errors='coerce')
    ma10 = pd.to_numeric(df.get('MA10'), errors='coerce')
    c = _last(close)
    s = _last(sar)
    m5 = _last(ma5)
    m10 = _last(ma10)
    if None not in (c, s, m5, m10):
        if c > s and m5 > m10:
            parts[9] = 1
        elif c < s and m5 < m10:
            parts[9] = -1

    total = int(sum(parts))
    chars = ''.join('+' if p > 0 else ('-' if p < 0 else '0') for p in parts)
    return {'total': total, 'parts': parts, 'text': f'{total:+d} ({chars})'}


def _atr_position_score_series(df, signals):
    """逐日 ATR 持仓打分（与 _atr_position_score 的最新日逻辑一致）。"""
    if signals is None or len(signals) == 0 or 'close' not in df.columns:
        return pd.Series(0, index=df.index, dtype=int)
    if 'ATR14' not in df.columns:
        return pd.Series(0, index=df.index, dtype=int)
    close = pd.to_numeric(df['close'], errors='coerce')
    atr = pd.to_numeric(df['ATR14'], errors='coerce')
    sig = pd.to_numeric(signals.reindex(df.index), errors='coerce').fillna(0)
    out = pd.Series(0, index=df.index, dtype=int)
    in_pos = False
    entry = None
    stop_price = None
    for i in range(len(df)):
        c = close.iloc[i]
        a = atr.iloc[i]
        if pd.isna(c) or pd.isna(a):
            continue
        c, a = float(c), float(a)
        val = float(sig.iloc[i])
        if not in_pos:
            if val > 0:
                in_pos = True
                entry = c
                stop_price = update_atr_trailing_stop(c, a)
        else:
            stop_price = update_atr_trailing_stop(c, a, stop_price)
            if val < 0:
                in_pos = False
                entry = None
                stop_price = None
                continue
        if in_pos and entry is not None and stop_price is not None:
            pnl = c / entry - 1.0
            distance = c - stop_price
            if pnl > 0 and distance > a:
                out.iloc[i] = 1
            elif distance <= 0.5 * a or pnl <= -0.05:
                out.iloc[i] = -1
    return out


def compute_signal_score_series(df, signals=None):
    """逐日计算 10 指标综合打分，返回与 df 索引对齐的整数 Series。"""
    if df is None or len(df) == 0:
        return pd.Series(dtype=int)
    index = df.index
    zeros = lambda: pd.Series(0, index=index, dtype=int)  # noqa: E731

    close = pd.to_numeric(df.get('close'), errors='coerce')
    ma20 = pd.to_numeric(df.get('MA20'), errors='coerce')
    adx = pd.to_numeric(df.get('ADX14'), errors='coerce')
    pdi = pd.to_numeric(df.get('PLUS_DI'), errors='coerce')
    mdi = pd.to_numeric(df.get('MINUS_DI'), errors='coerce')
    bar = pd.to_numeric(df.get('MACD_BAR'), errors='coerce')
    rsi = pd.to_numeric(df.get('RSI14'), errors='coerce')
    try:
        rsi6 = (pd.to_numeric(df['RSI6'], errors='coerce')
                if 'RSI6' in df.columns else calc_rsi(df, 6))
        rsi12 = (pd.to_numeric(df['RSI12'], errors='coerce')
                 if 'RSI12' in df.columns else calc_rsi(df, 12))
    except Exception:
        rsi6 = rsi12 = None
    upper = pd.to_numeric(df.get('BOLL_UPPER'), errors='coerce')
    middle = pd.to_numeric(df.get('BOLL_MIDDLE'), errors='coerce')
    lower = pd.to_numeric(df.get('BOLL_LOWER'), errors='coerce')
    obv = pd.to_numeric(df.get('OBV'), errors='coerce')

    parts = []

    # 1 DMI
    p = zeros()
    if adx is not None and pdi is not None and mdi is not None:
        valid = adx.notna() & pdi.notna() & mdi.notna() & (adx >= 20)
        p[valid & (pdi > mdi)] = 1
        p[valid & (pdi < mdi)] = -1
    parts.append(p)

    # 2 MACD
    p = zeros()
    if bar is not None:
        prev = bar.shift(1)
        prev2 = bar.shift(2)
        up = (bar > prev) & (prev > prev2)
        down = (bar < prev) & (prev < prev2)
        p[up] = 1
        p[down & ~up] = -1
    parts.append(p)

    # 3 OBV
    p = zeros()
    if obv is not None and close is not None:
        obv5 = obv.rolling(5).mean()
        obv10 = obv.rolling(10).mean()
        price5 = close.rolling(5).mean()
        price10 = close.rolling(10).mean()
        up = ((obv > obv5) & (obv5 > obv10)
              & (close > price5) & (price5 > price10))
        down = ((obv < obv5) & (obv5 < obv10)
                & (close < price5) & (price5 < price10))
        p[up] = 1
        p[down & ~up] = -1
    parts.append(p)

    # 4 BIAS20
    p = zeros()
    if ma20 is not None and close is not None:
        bias = (close - ma20) / ma20.where(ma20 != 0)
        inrange = (bias >= -0.03) & (bias <= 0.06)
        high = bias > 0.08
        p[inrange] = 1
        p[high & ~inrange] = -1
    parts.append(p)

    # 5 RSI
    p = zeros()
    if rsi is not None:
        if rsi6 is not None and rsi12 is not None:
            prev6 = rsi6.shift(1)
            prev12 = rsi12.shift(1)
            golden = (prev6 <= prev12) & (rsi6 > rsi12)
            death = (prev6 >= prev12) & (rsi6 < rsi12)
        else:
            golden = death = pd.Series(False, index=index)
        buy = ((rsi < 40) & golden) | (rsi < 30)
        sell = ((rsi > 60) & death) | (rsi > 70)
        p[buy] = 1
        p[sell & ~buy] = -1
    parts.append(p)

    # 6 BOLL
    p = zeros()
    if (close is not None and upper is not None
            and middle is not None and lower is not None):
        prev_c = close.shift(1)
        recent_high = close.rolling(5).max()
        recent_low = close.rolling(5).min()
        buy = ((prev_c <= lower)
               | ((recent_high < upper) & (upper > close) & (close > middle)))
        sell = ((prev_c >= upper)
                | ((recent_low > lower) & (middle > close) & (close > lower)))
        p[buy] = 1
        p[sell & ~buy] = -1
    parts.append(p)

    # 7 WR
    p = zeros()
    try:
        if 'WR14' in df.columns:
            wr = pd.to_numeric(df['WR14'], errors='coerce')
        else:
            wr = calc_wr(df, 14)
        positive_wr = -wr
        p[positive_wr > 80] = 1
        p[(positive_wr < 20) & ~(positive_wr > 80)] = -1
    except Exception:
        pass
    parts.append(p)

    # 8 ATR position score
    parts.append(_atr_position_score_series(df, signals))

    # 9 KDJ
    p = zeros()
    kdj_k = pd.to_numeric(df.get('KDJ_K'), errors='coerce')
    kdj_d = pd.to_numeric(df.get('KDJ_D'), errors='coerce')
    kdj_j = pd.to_numeric(df.get('KDJ_J'), errors='coerce')
    if any(v is not None for v in (kdj_k, kdj_d, kdj_j)):
        def _s(v):
            return v if v is not None else pd.Series(np.nan, index=index)
        k, d, j = _s(kdj_k), _s(kdj_d), _s(kdj_j)
        buy = (k < 20) | (j < 0)
        sell = (d > 80) | (j > 100)
        p[buy] = 1
        p[sell & ~buy] = -1
    parts.append(p)

    # 10 SAR
    p = zeros()
    sar = pd.to_numeric(df.get('SAR'), errors='coerce')
    ma5 = pd.to_numeric(df.get('MA5'), errors='coerce')
    ma10 = pd.to_numeric(df.get('MA10'), errors='coerce')
    if all(v is not None for v in (close, sar, ma5, ma10)):
        buy = (close > sar) & (ma5 > ma10)
        sell = (close < sar) & (ma5 < ma10)
        p[buy] = 1
        p[sell & ~buy] = -1
    parts.append(p)

    total = sum(parts)
    return total.astype(int)
