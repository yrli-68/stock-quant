#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双均线交叉策略 (MA Cross Strategy)

通过快线和慢线的交叉来生成交易信号：
    - 快线上穿慢线（金叉）→ 买入信号
    - 快线下穿慢线（死叉）→ 卖出信号

这是一种经典的趋势跟踪策略，适用于有明显趋势的市场。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_ma


class MACrossStrategy(Strategy):
    """
    双均线交叉策略

    使用两条不同周期的简单移动平均线（SMA），当快线从下方上穿慢线时
    产生买入信号，当快线从上方下穿慢线时产生卖出信号。

    Attributes:
        fast_period (int): 快线周期，默认 10
        slow_period (int): 慢线周期，默认 30
    """

    def __init__(self, fast_period=10, slow_period=30, name='MACross'):
        """
        初始化双均线交叉策略

        Args:
            fast_period (int): 快速均线周期，默认 10
            slow_period (int): 慢速均线周期，默认 30
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.fast_period = fast_period
        self.slow_period = slow_period

    def generate_signals(self, df):
        """
        生成双均线交叉交易信号

        计算快线和慢线的移动平均，然后检测交叉点：
            - 金叉：快线上穿慢线（上一期快线 <= 上一期慢线 且 当期快线 > 当期慢线）→ 买入(1)
            - 死叉：快线下穿慢线（上一期快线 >= 上一期慢线 且 当期快线 < 当期慢线）→ 卖出(-1)
            - 其余情况 → 持有(0)

        增强级别 1（enhance>=1）时，额外要求短期均线（快线）方向确认：
            - 金叉还需 slope(快线) > 0（短期均线向上）
            - 死叉还需 slope(快线) < 0（短期均线向下）

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 计算快线和慢线的移动平均
        fast_ma = calc_ma(df, self.fast_period)
        slow_ma = calc_ma(df, self.slow_period)

        # 初始化信号序列，全部为 0（持有）
        signals = pd.Series(0.0, index=df.index, dtype=float)

        # 检测金叉（快线上穿慢线）：买入信号
        # 条件：上一期快线 <= 上一期慢线，且当期快线 > 当期慢线
        golden_cross = (fast_ma.shift(1) <= slow_ma.shift(1)) & (fast_ma > slow_ma)

        # 检测死叉（快线下穿慢线）：卖出信号
        # 条件：上一期快线 >= 上一期慢线，且当期快线 < 当期慢线
        death_cross = (fast_ma.shift(1) >= slow_ma.shift(1)) & (fast_ma < slow_ma)

        # 基础版条件 → 弱信号
        signals[golden_cross] = self.signal_value(1)
        signals[death_cross] = self.signal_value(-1)

        # 增强级别 1：额外要求短期均线（快线）方向确认 → 强信号
        if self.enhance >= 1:
            fast_slope = fast_ma.diff()  # 当前快线值 - 上一期值
            signals[golden_cross & (fast_slope > 0)] = self.signal_value(1, strong=True)
            signals[death_cross & (fast_slope < 0)] = self.signal_value(-1, strong=True)

        return signals