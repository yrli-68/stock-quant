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
        bbw_lookback (int): 增强过滤中布林带宽度的统计回看窗口（默认 100）
    """

    def __init__(self, period=20, std=2, name='Bollinger', bbw_lookback=100):
        """
        初始化布林带策略

        Args:
            period (int): 布林带中轨（均线）周期，默认 20
            std (float): 上下轨的标准差倍数，默认 2
            name (str): 策略名称
            bbw_lookback (int): 布林带宽度过滤的回看窗口，默认 100
        """
        super().__init__(name=name)
        self.period = period
        self.std = std
        self.bbw_lookback = bbw_lookback

    def generate_signals(self, df):
        """
        生成布林带交易信号

        计算布林带指标，然后检测价格与上下轨的交互：
            - 价格触及下轨后反弹 → 买入(1)
              （上一期收盘价 <= 下轨，当期收盘价 > 下轨）
            - 价格触及上轨后回落 → 卖出(-1)
              （上一期收盘价 >= 上轨，当期收盘价 < 上轨）
            - 其余情况 → 持有(0)

        增强级别 1（enhance>=1）时，额外增加布林带宽度（bbw）过滤：
            - bbw = (UPPER - LOWER) / MIDDLE
            - bbw_pctl = bbw 在回看窗口内的 50% 分位数（中位数）
            - is_squeeze   = bbw <= bbw_pctl * 0.2   （极度窄幅挤压，横盘蓄力）
            - is_extraWide = bbw >= bbw_pctl * 1.2   （带宽极端宽大，暴涨暴跌尾声）
            - bb_filter = not is_squeeze and not is_extraWide
            - buy  = base_buy  and bb_filter
            - sell = base_sell and not is_squeeze

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 计算布林带指标
        bb = calc_bollinger(df, period=self.period, std=self.std)
        upper = bb['UPPER']
        lower = bb['LOWER']
        middle = bb['MIDDLE']
        close = df['close']

        # 初始化信号序列
        signals = pd.Series(0, index=df.index, dtype=int)

        # 下轨反弹买入：上一期收盘价 <= 下轨，当期收盘价 > 下轨
        # 表示价格触及下轨支撑后开始反弹
        buy_signal = (close.shift(1) <= lower.shift(1)) & (close > lower)

        # 上轨回落卖出：上一期收盘价 >= 上轨，当期收盘价 < 上轨
        # 表示价格触及上轨阻力后开始回落
        sell_signal = (close.shift(1) >= upper.shift(1)) & (close < upper)

        # 增强级别 1：布林带宽度过滤
        if self.enhance >= 1:
            bbw = (upper - lower) / middle
            bbw_pctl = bbw.rolling(window=self.bbw_lookback).quantile(0.5)
            is_squeeze = bbw <= bbw_pctl * 0.2
            is_extraWide = bbw >= bbw_pctl * 1.2
            bb_filter = ~(is_squeeze | is_extraWide)
            buy_signal = buy_signal & bb_filter
            sell_signal = sell_signal & ~is_squeeze

        signals[buy_signal] = 1
        signals[sell_signal] = -1

        return signals