#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
布林带策略 (Bollinger Bands Strategy)

基于布林带指标的价格触及上下轨来生成交易信号：
    - 价格触及下轨后反弹 → 买入信号（股价在低位获得支撑）
    - 价格触及上轨后回落 → 卖出信号（股价在高位遇到阻力）

布林带由中轨（均线）和上下轨（均线 ± K倍标准差）组成，
用于衡量价格的相对高低和波动性。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_bollinger


class BollingerStrategy(Strategy):
    """
    布林带突破策略

    利用布林带的上下轨作为支撑和阻力位来生成反转交易信号。
    当价格触及下轨（超卖）后反弹时买入，当价格触及上轨（超买）
    后回落时卖出。

    Attributes:
        period (int): 布林带中轨周期，默认 20
        std (float): 标准差倍数，默认 2
    """

    def __init__(self, period=20, std=2, name='Bollinger'):
        """
        初始化布林带策略

        Args:
            period (int): 布林带中轨（均线）周期，默认 20
            std (float): 上下轨的标准差倍数，默认 2
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.period = period
        self.std = std

    def generate_signals(self, df):
        """
        生成布林带交易信号

        计算布林带指标，然后检测价格与上下轨的交互：
            - 价格触及下轨后反弹 → 买入(1)
              （上一期收盘价 <= 下轨，当期收盘价 > 下轨）
            - 价格触及上轨后回落 → 卖出(-1)
              （上一期收盘价 >= 上轨，当期收盘价 < 上轨）
            - 其余情况 → 持有(0)

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 计算布林带指标
        bb = calc_bollinger(df, period=self.period, std=self.std)
        upper = bb['UPPER']
        lower = bb['LOWER']
        close = df['close']

        # 初始化信号序列
        signals = pd.Series(0, index=df.index, dtype=int)

        # 下轨反弹买入：上一期收盘价 <= 下轨，当期收盘价 > 下轨
        # 表示价格触及下轨支撑后开始反弹
        buy_signal = (close.shift(1) <= lower.shift(1)) & (close > lower)
        signals[buy_signal] = 1

        # 上轨回落卖出：上一期收盘价 >= 上轨，当期收盘价 < 上轨
        # 表示价格触及上轨阻力后开始回落
        sell_signal = (close.shift(1) >= upper.shift(1)) & (close < upper)
        signals[sell_signal] = -1

        return signals