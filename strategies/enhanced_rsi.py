#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enhanced-RSI 策略 (Enhanced RSI Strategy)

在 RSI 超买超卖策略基础上增强，引入趋势确认：
    - 上升趋势 (close > MA20 且 MA20 > MA60) 中，RSI 上穿 35 即买入（更敏感）
    - 下降趋势 (close < MA20 且 MA20 < MA60) 中，RSI 下穿 65 即卖出（更敏感）
    - 无趋势时回退到基础阈值：RSI 上穿 30 买入、下穿 70 卖出

RSI 是一种动量振荡指标，用于衡量价格变动的速度和幅度，
判断市场是否处于超买或超卖状态。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_rsi, calc_ma


class EnhancedRSIStrategy(Strategy):
    """
    Enhanced-RSI 超买超卖策略

    利用 RSI 指标的超买超卖区域并结合趋势确认来生成反转交易信号。
    趋势向上时使用更敏感的买入阈值（35），趋势向下时使用更敏感的卖出阈值（65）；
    无趋势时回退到基础阈值（30/70）。

    Attributes:
        period (int): RSI 计算周期，默认 14
        oversold (float): 基础超卖阈值，默认 30
        overbought (float): 基础超买阈值，默认 70
        trend_oversold (float): 上升趋势中的买入阈值，默认 35
        trend_overbought (float): 下降趋势中的卖出阈值，默认 65
        ma_fast (int): 趋势快速均线周期，默认 20
        ma_slow (int): 趋势慢速均线周期，默认 60
    """

    def __init__(self, period=14, oversold=30, overbought=70,
                 trend_oversold=35, trend_overbought=65,
                 ma_fast=20, ma_slow=60, name='EnhancedRSI'):
        """
        初始化 Enhanced-RSI 策略

        Args:
            period (int): RSI 计算周期，默认 14
            oversold (float): 基础超卖阈值，默认 30
            overbought (float): 基础超买阈值，默认 70
            trend_oversold (float): 上升趋势中的买入阈值，默认 35
            trend_overbought (float): 下降趋势中的卖出阈值，默认 65
            ma_fast (int): 趋势快速均线周期，默认 20
            ma_slow (int): 趋势慢速均线周期，默认 60
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.trend_oversold = trend_oversold
        self.trend_overbought = trend_overbought
        self.ma_fast = ma_fast
        self.ma_slow = ma_slow

    def generate_signals(self, df):
        """
        生成 Enhanced-RSI 交易信号

        信号逻辑：
            - uptrend = (close > MA20) 且 (MA20 > MA60)
            - downtrend = (close < MA20) 且 (MA20 < MA60)
            - 买入 = (uptrend 且 RSI 上穿 35) 或 (RSI 上穿 30)
            - 卖出 = (downtrend 且 RSI 下穿 65) 或 (RSI 下穿 70)
            - 其余情况 → 持有(0)

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 计算 RSI 指标
        rsi = calc_rsi(df, period=self.period)

        # 计算趋势均线
        close = df['close']
        ma_fast = calc_ma(df, self.ma_fast)
        ma_slow = calc_ma(df, self.ma_slow)

        # 趋势判定
        uptrend = (close > ma_fast) & (ma_fast > ma_slow)
        downtrend = (close < ma_fast) & (ma_fast < ma_slow)

        # 初始化信号序列
        signals = pd.Series(0, index=df.index, dtype=int)

        # 买入：上升趋势中 RSI 上穿 35（更敏感），或基础超卖反弹 RSI 上穿 30
        trend_buy = uptrend & (rsi.shift(1) < self.trend_oversold) & (rsi > self.trend_oversold)
        base_buy = (rsi.shift(1) < self.oversold) & (rsi > self.oversold)
        signals[trend_buy | base_buy] = 1

        # 卖出：下降趋势中 RSI 下穿 65（更敏感），或基础超买回落 RSI 下穿 70
        trend_sell = downtrend & (rsi.shift(1) > self.trend_overbought) & (rsi < self.trend_overbought)
        base_sell = (rsi.shift(1) > self.overbought) & (rsi < self.overbought)
        signals[trend_sell | base_sell] = -1

        return signals
