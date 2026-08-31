#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI 策略 (RSI Strategy)

基于相对强弱指标 (RSI) 的超买超卖信号来生成交易信号：
    - RSI 从超卖区（< 30）上穿 30 → 买入信号（超卖反弹）
    - RSI 从超买区（> 70）下穿 70 → 卖出信号（超买回落）

RSI 是一种动量振荡指标，用于衡量价格变动的速度和幅度，
判断市场是否处于超买或超卖状态。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_rsi


class RSIStrategy(Strategy):
    """
    RSI 超买超卖策略

    利用 RSI 指标的超买超卖区域来生成反转交易信号。
    当 RSI 从超卖区域回升时买入，从超买区域回落时卖出。

    Attributes:
        period (int): RSI 计算周期，默认 14
        oversold (float): 超卖阈值，默认 30
        overbought (float): 超买阈值，默认 70
        enh_oversold (float): 增强级超卖阈值（更深超卖区），默认 25
        enh_overbought (float): 增强级超买阈值（更深超买区），默认 75
    """

    def __init__(self, period=14, oversold=30, overbought=70,
                 enh_oversold=25, enh_overbought=75, name='RSI'):
        """
        初始化 RSI 策略

        Args:
            period (int): RSI 计算周期，默认 14
            oversold (float): 超卖阈值，RSI 低于此值视为超卖，默认 30
            overbought (float): 超买阈值，RSI 高于此值视为超买，默认 70
            enh_oversold (float): 增强级超卖阈值，默认 25
            enh_overbought (float): 增强级超买阈值，默认 75
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.period = period
        self.oversold = oversold
        self.overbought = overbought
        self.enh_oversold = enh_oversold
        self.enh_overbought = enh_overbought

    def generate_signals(self, df):
        """
        生成 RSI 交易信号

        计算 RSI 指标，然后检测超买超卖区域的突破信号：
            - RSI 从超卖区（< oversold）上穿 oversold 阈值 → 买入(1)
            - RSI 从超买区（> overbought）下穿 overbought 阈值 → 卖出(-1)
            - 其余情况 → 持有(0)

        增强级别 1（enhance>=1）时，改用更深超卖/超买阈值判定（超卖 25、超买 75）：
            - 买入：RSI 从更超卖区（< 25）上穿 25 → 强买(1)
            - 卖出：RSI 从更超买区（> 75）下穿 75 → 强卖(-1)
            增强条件与基础条件不同，因此仅满足基础条件（30/70）时仍出弱信号（±0.5）。

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 计算 RSI 指标
        rsi = calc_rsi(df, period=self.period)

        # 初始化信号序列
        signals = pd.Series(0.0, index=df.index, dtype=float)

        # 超卖反弹买入：上一期 RSI < 超卖阈值，当期 RSI > 超卖阈值
        # 即 RSI 从超卖区域向上突破超卖线
        buy_signal = (rsi.shift(1) < self.oversold) & (rsi > self.oversold)
        signals[buy_signal] = self.signal_value(1)

        # 超买回落卖出：上一期 RSI > 超买阈值，当期 RSI < 超买阈值
        # 即 RSI 从超买区域向下突破超买线
        sell_signal = (rsi.shift(1) > self.overbought) & (rsi < self.overbought)
        signals[sell_signal] = self.signal_value(-1)

        # 增强级别 1：改用更深超卖/超买阈值（25/75）→ 强信号
        if self.enhance >= 1:
            buy_enhanced = (rsi.shift(1) < self.enh_oversold) & (rsi > self.enh_oversold)
            sell_enhanced = (rsi.shift(1) > self.enh_overbought) & (rsi < self.enh_overbought)
            signals[buy_enhanced] = self.signal_value(1, strong=True)
            signals[sell_enhanced] = self.signal_value(-1, strong=True)

        return signals