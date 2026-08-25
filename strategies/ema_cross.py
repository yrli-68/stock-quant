#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双指数均线交叉策略 (EMA Cross Strategy)

与 ma_cross 类似，仅将快慢均线由 SMA 换成 EMA（指数移动平均）：
    - 快线上穿慢线（金叉）→ 买入信号
    - 快线下穿慢线（死叉）→ 卖出信号

EMA 对近期价格赋予更高权重，反应更快，适用于有明显趋势的市场。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_ema


class EMACrossStrategy(Strategy):
    """
    双指数均线交叉策略

    使用两条不同周期的指数移动平均线（EMA），当快线从下方上穿慢线时
    产生买入信号，当快线从上方下穿慢线时产生卖出信号。

    Attributes:
        fast_period (int): 快线周期，默认 10
        slow_period (int): 慢线周期，默认 30
    """

    def __init__(self, fast_period=10, slow_period=30, name='EMACross'):
        """
        初始化双指数均线交叉策略

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
        生成双指数均线交叉交易信号

        计算快线和慢线的指数移动平均，然后检测交叉点：
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
        # 计算快线和慢线的指数移动平均
        fast_ema = calc_ema(df, self.fast_period)
        slow_ema = calc_ema(df, self.slow_period)

        # 初始化信号序列，全部为 0（持有）
        signals = pd.Series(0, index=df.index, dtype=int)

        # 检测金叉（快线上穿慢线）：买入信号
        # 条件：上一期快线 <= 上一期慢线，且当期快线 > 当期慢线
        golden_cross = (fast_ema.shift(1) <= slow_ema.shift(1)) & (fast_ema > slow_ema)

        # 检测死叉（快线下穿慢线）：卖出信号
        # 条件：上一期快线 >= 上一期慢线，且当期快线 < 当期慢线
        death_cross = (fast_ema.shift(1) >= slow_ema.shift(1)) & (fast_ema < slow_ema)

        # 增强级别 1：额外要求短期均线（快线）方向确认
        if self.enhance >= 1:
            fast_slope = fast_ema.diff()  # 当前快线值 - 上一期值
            golden_cross = golden_cross & (fast_slope > 0)
            death_cross = death_cross & (fast_slope < 0)

        signals[golden_cross] = 1
        signals[death_cross] = -1

        return signals
