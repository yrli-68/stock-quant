#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技术指标计算模块

提供常用的技术指标计算函数，所有函数均返回 pandas Series 或 DataFrame，
索引与输入的行情数据对齐。

包含的指标：
    - calc_ma: 移动平均线 (SMA)
    - calc_ema: 指数移动平均线 (EMA)
    - calc_macd: MACD 指标 (DIF, DEA, MACD柱)
    - calc_rsi: 相对强弱指标 (RSI)
    - calc_bollinger: 布林带 (上轨, 中轨, 下轨)
"""

import pandas as pd
import numpy as np


def calc_ma(df, period, price_col='close'):
    """
    计算简单移动平均线 (SMA)

    Args:
        df (pd.DataFrame): 行情数据
        period (int): 移动平均周期
        price_col (str): 价格列名，默认为 'close'

    Returns:
        pd.Series: 移动平均线序列
    """
    return df[price_col].rolling(window=period).mean()


def calc_ema(df, period, price_col='close'):
    """
    计算指数移动平均线 (EMA)

    Args:
        df (pd.DataFrame): 行情数据
        period (int): 指数移动平均周期
        price_col (str): 价格列名，默认为 'close'

    Returns:
        pd.Series: 指数移动平均线序列
    """
    return df[price_col].ewm(span=period, adjust=False).mean()


def calc_macd(df, fast=12, slow=26, signal=9, price_col='close'):
    """
    计算 MACD 指标

    MACD 由三部分组成：
        - DIF (快线): 快速EMA - 慢速EMA
        - DEA (慢线/信号线): DIF的EMA
        - MACD柱 (柱状线): 2 * (DIF - DEA)

    Args:
        df (pd.DataFrame): 行情数据
        fast (int): 快速EMA周期，默认12
        slow (int): 慢速EMA周期，默认26
        signal (int): 信号线EMA周期，默认9
        price_col (str): 价格列名，默认为 'close'

    Returns:
        pd.DataFrame: 包含 'DIF', 'DEA', 'MACD' 三列的 DataFrame
    """
    price = df[price_col]
    ema_fast = price.ewm(span=fast, adjust=False).mean()
    ema_slow = price.ewm(span=slow, adjust=False).mean()

    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_bar = 2 * (dif - dea)

    result = pd.DataFrame({
        'DIF': dif,
        'DEA': dea,
        'MACD': macd_bar
    }, index=df.index)

    return result


def calc_rsi(df, period=14, price_col='close'):
    """
    计算相对强弱指标 (RSI)

    使用 Wilder 平滑方法计算 RSI。
    RSI = 100 - 100 / (1 + RS)，其中 RS = 平均涨幅 / 平均跌幅

    Args:
        df (pd.DataFrame): 行情数据
        period (int): RSI 计算周期，默认14
        price_col (str): 价格列名，默认为 'close'

    Returns:
        pd.Series: RSI 值序列，范围 [0, 100]
    """
    price = df[price_col]
    delta = price.diff()

    # 分离涨幅和跌幅
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    # 使用 Wilder 平滑（指数移动平均的 alpha=1/period）
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))

    return rsi


def calc_bollinger(df, period=20, std=2, price_col='close'):
    """
    计算布林带 (Bollinger Bands)

    布林带由三条线组成：
        - 中轨 (MIDDLE): N日均线
        - 上轨 (UPPER): 中轨 + K * N日标准差
        - 下轨 (LOWER): 中轨 - K * N日标准差

    Args:
        df (pd.DataFrame): 行情数据
        period (int): 移动平均周期，默认20
        std (float): 标准差倍数，默认2
        price_col (str): 价格列名，默认为 'close'

    Returns:
        pd.DataFrame: 包含 'UPPER', 'MIDDLE', 'LOWER' 三列的 DataFrame
    """
    price = df[price_col]
    middle = price.rolling(window=period).mean()
    rolling_std = price.rolling(window=period).std()

    upper = middle + std * rolling_std
    lower = middle - std * rolling_std

    result = pd.DataFrame({
        'UPPER': upper,
        'MIDDLE': middle,
        'LOWER': lower
    }, index=df.index)

    return result


def calc_kdj(df, n=9, m1=3, m2=3, price_col='close'):
    """KDJ随机指标"""
    price = df[price_col]
    low_n = price.rolling(window=n).min()
    high_n = price.rolling(window=n).max()
    rsv = (price - low_n) / (high_n - low_n) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(com=m1-1, adjust=False).mean()
    d = k.ewm(com=m2-1, adjust=False).mean()
    j = 3 * k - 2 * d
    return pd.DataFrame({'K': k, 'D': d, 'J': j}, index=df.index)


def calc_atr(df, period=14):
    """平均真实波幅"""
    high, low, close = df['high'], df['low'], df['close']
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calc_obv(df):
    """能量潮指标"""
    close_diff = df['close'].diff()
    obv = pd.Series(0, index=df.index, dtype=float)
    for i in range(1, len(df)):
        if close_diff.iloc[i] > 0:
            obv.iloc[i] = obv.iloc[i-1] + df['volume'].iloc[i]
        elif close_diff.iloc[i] < 0:
            obv.iloc[i] = obv.iloc[i-1] - df['volume'].iloc[i]
        else:
            obv.iloc[i] = obv.iloc[i-1]
    return obv


def calc_cci(df, period=20):
    """顺势指标"""
    tp = (df['high'] + df['low'] + df['close']) / 3
    ma = tp.rolling(window=period).mean()
    md = tp.rolling(window=period).apply(lambda x: np.abs(x - x.mean()).mean())
    return (tp - ma) / (0.015 * md)


def calc_wr(df, period=14):
    """威廉指标"""
    high_n = df['high'].rolling(window=period).max()
    low_n = df['low'].rolling(window=period).min()
    return (high_n - df['close']) / (high_n - low_n) * -100


def calc_volume_ma(df, period=5):
    """成交量均线"""
    return df['volume'].rolling(window=period).mean()


def calc_vwap(df):
    """成交量加权平均价"""
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    vwap = (typical_price * df['volume']).cumsum() / df['volume'].cumsum()
    return vwap


def calc_historical_volatility(df, period=20, price_col='close', annualize=True):
    """
    计算历史波动率

    基于对数收益率的标准差计算，用于衡量价格的波动程度。
    指数的波动率有均值回归特性，低波动后往往酝酿方向突破。

    Args:
        df (pd.DataFrame): 行情数据
        period (int): 计算周期，默认20个交易日
        price_col (str): 价格列名
        annualize (bool): 是否年化，默认 True

    Returns:
        pd.Series: 历史波动率序列（年化则为百分比小数）
    """
    price = df[price_col]
    log_returns = np.log(price / price.shift(1))
    rolling_vol = log_returns.rolling(window=period).std()
    if annualize:
        rolling_vol = rolling_vol * np.sqrt(252)
    return rolling_vol


def calc_momentum_return(df, period=60, price_col='close'):
    """
    计算动量收益率

    计算过去 N 日的累计收益率，用于衡量价格的趋势强度。
    指数天然适合动量策略——强者恒强。

    Args:
        df (pd.DataFrame): 行情数据
        period (int): 回看周期，默认60个交易日
        price_col (str): 价格列名

    Returns:
        pd.Series: 动量收益率序列（小数形式，如 0.05 表示 5%）
    """
    price = df[price_col]
    return price / price.shift(period) - 1


def calc_macd_divergence(df, fast=12, slow=26, signal=9, lookback=20, price_col='close'):
    """
    检测 MACD 顶背离和底背离

    背离是指数反转最可靠的先行指标：
        - 底背离：价格创新低，但 MACD 的 DIF 未创新低 → 看涨反转信号
        - 顶背离：价格创新高，但 MACD 的 DIF 未创新高 → 看跌反转信号

    Args:
        df (pd.DataFrame): 行情数据
        fast (int): 快速 EMA 周期
        slow (int): 慢速 EMA 周期
        signal (int): 信号线周期
        lookback (int): 回看窗口，用于寻找局部极值
        price_col (str): 价格列名

    Returns:
        pd.DataFrame: 包含 'bullish_divergence'（底背离）和
                      'bearish_divergence'（顶背离）的 DataFrame，
                      值为 1 表示当日出现背离信号
    """
    price = df[price_col]
    macd_df = calc_macd(df, fast=fast, slow=slow, signal=signal)
    dif = macd_df['DIF']

    bullish = pd.Series(0, index=df.index, dtype=int)
    bearish = pd.Series(0, index=df.index, dtype=int)

    for i in range(lookback, len(df)):
        # 回看窗口内的价格和 DIF
        price_window = price.iloc[i - lookback:i + 1]
        dif_window = dif.iloc[i - lookback:i + 1]

        # 当前价格
        current_price = price.iloc[i]
        current_dif = dif.iloc[i]

        # 找窗口内价格最低点
        price_min_idx = price_window.idxmin()
        price_min = price_window.min()

        # 底背离：价格创新低（或接近前低），但 DIF 高于前低点对应的 DIF
        if current_price <= price_min * 1.02 and price_min_idx is not None:
            dif_at_price_min = dif.loc[price_min_idx]
            if current_dif > dif_at_price_min and current_price < price.iloc[i - 1]:
                # 确认价格在下行但 DIF 在上行
                bullish.iloc[i] = 1

        # 找窗口内价格最高点
        price_max_idx = price_window.idxmax()
        price_max = price_window.max()

        # 顶背离：价格创新高（或接近前高），但 DIF 低于前高点对应的 DIF
        if current_price >= price_max * 0.98 and price_max_idx is not None:
            dif_at_price_max = dif.loc[price_max_idx]
            if current_dif < dif_at_price_max and current_price > price.iloc[i - 1]:
                bearish.iloc[i] = 1

    return pd.DataFrame({
        'bullish_divergence': bullish,
        'bearish_divergence': bearish
    }, index=df.index)


def calc_adx(df, period=14):
    """计算 ADX / DMI 指标（平均趋向指数）

    Returns:
        pd.DataFrame: 包含 'ADX', 'PLUS_DI', 'MINUS_DI' 三列
    """
    high = df['high']
    low = df['low']
    close = df['close']

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr)

    denom = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / denom.where(denom != 0)
    dx = dx.fillna(0.0)
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()

    return pd.DataFrame({'ADX': adx, 'PLUS_DI': plus_di, 'MINUS_DI': minus_di}, index=df.index)


def classify_trend(df, date=None):
    """判断个股当前趋势

    趋势分为三类：强势（多头）、弱势（空头）、横盘震荡（无趋势）。

    Args:
        df (pd.DataFrame): 已包含技术指标的行情数据（需 add_all_indicators 处理过）
        date: 判断时点，默认取最新交易日；传入日期则只用 <= 该日期的数据

    Returns:
        str: '强势' / '弱势' / '横盘震荡'
    """
    if df is None or len(df) == 0:
        return '横盘震荡'

    if date is not None:
        sub = df.loc[df.index <= date]
    else:
        sub = df
    if len(sub) == 0:
        return '横盘震荡'

    row = sub.iloc[-1]

    def _v(name):
        v = row.get(name)
        if v is None or pd.isna(v):
            return None
        return float(v)

    ma20 = _v('MA20')
    ma60 = _v('MA60')
    ma120 = _v('MA120')
    adx = _v('ADX14')
    plus_di = _v('PLUS_DI')
    minus_di = _v('MINUS_DI')
    dif = _v('MACD_DIF')

    closes = sub['close'].astype(float).values
    slope = 0.0
    slope_norm = 0.0
    if len(closes) >= 2:
        y = closes[-60:]
        x = np.arange(len(y), dtype=float)
        slope = float(np.polyfit(x, y, 1)[0])
        mean_y = y.mean()
        if mean_y > 0:
            slope_norm = slope / mean_y

    high60 = sub['high'].tail(60).max()
    low60 = sub['low'].tail(60).min()
    amplitude = None
    if not (pd.isna(high60) or pd.isna(low60)) and low60 > 0:
        amplitude = float(high60 / low60)

    # 强势（全部满足）
    if (ma20 is not None and ma60 is not None and ma120 is not None
            and ma20 > ma60 > ma120
            and adx is not None and adx >= 25
            and plus_di is not None and minus_di is not None and plus_di > minus_di
            and slope > 0
            and dif is not None and dif > 0):
        return '强势'

    # 弱势（全部满足）
    if (ma20 is not None and ma60 is not None and ma120 is not None
            and ma20 < ma60 < ma120
            and adx is not None and adx >= 25
            and plus_di is not None and minus_di is not None and minus_di > plus_di
            and slope < 0
            and dif is not None and dif < 0):
        return '弱势'

    # 横盘震荡（满足任意两条）
    cond_count = 0
    if adx is not None and adx < 25:
        cond_count += 1
    if ma20 is not None and ma60 is not None and ma60 != 0 and abs(ma20 - ma60) / ma60 < 0.03:
        cond_count += 1
    if abs(slope_norm) < 0.001:
        cond_count += 1
    if amplitude is not None and amplitude < 1.3:
        cond_count += 1

    if cond_count >= 2:
        return '横盘震荡'

    return '横盘震荡'


def add_all_indicators(df):
    """一次性添加所有技术指标到DataFrame"""
    df = df.copy()
    df['MA5'] = calc_ma(df, 5)
    df['MA10'] = calc_ma(df, 10)
    df['MA20'] = calc_ma(df, 20)
    df['MA30'] = calc_ma(df, 30)
    df['MA60'] = calc_ma(df, 60)
    df['MA120'] = calc_ma(df, 120)
    df['EMA10'] = calc_ema(df, 10)
    df['EMA12'] = calc_ema(df, 12)
    df['EMA26'] = calc_ema(df, 26)
    df['EMA30'] = calc_ema(df, 30)
    macd = calc_macd(df)
    df['MACD_DIF'] = macd['DIF']
    df['MACD_DEA'] = macd['DEA']
    df['MACD_BAR'] = macd['MACD']
    df['RSI14'] = calc_rsi(df, 14)
    boll = calc_bollinger(df)
    df['BOLL_UPPER'] = boll['UPPER']
    df['BOLL_MIDDLE'] = boll['MIDDLE']
    df['BOLL_LOWER'] = boll['LOWER']
    kdj = calc_kdj(df)
    df['KDJ_K'] = kdj['K']
    df['KDJ_D'] = kdj['D']
    df['KDJ_J'] = kdj['J']
    df['ATR14'] = calc_atr(df, 14)
    adx = calc_adx(df, 14)
    df['ADX14'] = adx['ADX']
    df['PLUS_DI'] = adx['PLUS_DI']
    df['MINUS_DI'] = adx['MINUS_DI']
    df['OBV'] = calc_obv(df)
    df['CCI20'] = calc_cci(df, 20)
    df['WR14'] = calc_wr(df, 14)
    df['VOL_MA5'] = calc_volume_ma(df, 5)
    df['VWAP'] = calc_vwap(df)
    df['HV20'] = calc_historical_volatility(df, 20)
    df['MOM60'] = calc_momentum_return(df, 60)
    return df