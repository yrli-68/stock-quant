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
from core.indicators import calc_ema, calc_rsi
from core.thresholds import UNSET, get_threshold


class EMACrossStrategy(Strategy):
    """
    双指数均线交叉策略

    使用两条不同周期的指数移动平均线（EMA），当快线从下方上穿慢线时
    产生买入信号，当快线从上方下穿慢线时产生卖出信号。

    Attributes:
        fast_period (int): 快线周期，默认 10
        slow_period (int): 慢线周期，默认 30
    """

    def __init__(self, fast_period=10, slow_period=30, rsi_buy_max=UNSET, rsi_sell_min=UNSET,
                 name='EMACross'):
        """
        初始化双指数均线交叉策略

        Args:
            fast_period (int): 快速均线周期，默认 10
            slow_period (int): 慢速均线周期，默认 30
            rsi_buy_max (float): 买入附加条件 RSI < rsi_buy_max（None 表示不启用；
                未传参时读取 pyproject.toml 的 MIN_RSI_THRESHOLD）
            rsi_sell_min (float): 卖出附加条件 RSI > rsi_sell_min（None 表示不启用；
                未传参时读取 pyproject.toml 的 MAX_RSI_THRESHOLD）
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.rsi_buy_max = (get_threshold('MIN_RSI_THRESHOLD')
                            if rsi_buy_max is UNSET else rsi_buy_max)
        self.rsi_sell_min = (get_threshold('MAX_RSI_THRESHOLD')
                             if rsi_sell_min is UNSET else rsi_sell_min)

    def generate_signals(self, df):
        """
        生成双指数均线交叉交易信号

        计算快线和慢线的指数移动平均，然后检测交叉点：
            - 金叉：快线上穿慢线（上一期快线 <= 上一期慢线 且 当期快线 > 当期慢线）→ 买入(1)
            - 死叉：快线下穿慢线（上一期快线 >= 上一期慢线 且 当期快线 < 当期慢线）→ 卖出(-1)
            - 其余情况 → 持有(0)

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 计算快线和慢线的指数移动平均
        fast_ema = calc_ema(df, self.fast_period)
        slow_ema = calc_ema(df, self.slow_period)

        # 初始化信号序列，全部为 0（持有）
        signals = pd.Series(0.0, index=df.index, dtype=float)

        # RSI 附加过滤：仅当配置了阈值时才计算
        rsi = None
        if self.rsi_buy_max is not None or self.rsi_sell_min is not None:
            rsi = df['RSI14'] if 'RSI14' in df.columns else calc_rsi(df, 14)

        # 检测金叉（快线上穿慢线）：买入信号
        # 条件：上一期快线 <= 上一期慢线，且当期快线 > 当期慢线，且 RSI < rsi_buy_max
        golden_cross = (fast_ema.shift(1) <= slow_ema.shift(1)) & (fast_ema > slow_ema)
        if self.rsi_buy_max is not None:
            golden_cross = golden_cross & (rsi < self.rsi_buy_max)

        # 检测死叉（快线下穿慢线）：卖出信号；或 RSI > rsi_sell_min
        # 条件：上一期快线 >= 上一期慢线，且当期快线 < 当期慢线
        death_cross = (fast_ema.shift(1) >= slow_ema.shift(1)) & (fast_ema < slow_ema)
        if self.rsi_sell_min is not None:
            death_cross = death_cross | (rsi > self.rsi_sell_min)

        # 基础版条件 → 弱信号
        signals[golden_cross] = self.signal_value(1)
        signals[death_cross] = self.signal_value(-1)

        return signals
