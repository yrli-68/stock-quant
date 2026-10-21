#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAR 策略 (Parabolic SAR Strategy)

基于抛物线转向指标（Parabolic SAR）与收盘价的位置关系生成交易信号：
    - SAR 由收盘价上方向下突破至收盘价下方 → 买入信号
    - SAR 由收盘价下方向上突破至收盘价上方 → 卖出信号

SAR 由加速因子（AF）与极点价（EP）逐日递推，趋势反转时 SAR 跳到上一轮
极点价另一侧，因此可用 SAR 与收盘价的穿越方向判断趋势转向。
"""

import pandas as pd

from core.strategy import Strategy
from core.indicators import calc_sar


class SARStrategy(Strategy):
    """抛物线转向策略

    SAR_CHECK 宏不作用于 SAR 策略本身（其买入条件已隐含 SAR < close）。

    Attributes:
        af_step (float): 加速因子步长，默认 0.02
        af_max (float): 加速因子上限，默认 0.2
    """

    exclude_sar_check = True

    def __init__(self, af_step=0.02, af_max=0.2, name='SAR'):
        """
        初始化 SAR 策略

        Args:
            af_step (float): 加速因子步长，默认 0.02
            af_max (float): 加速因子上限，默认 0.2
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.af_step = af_step
        self.af_max = af_max

    def generate_signals(self, df):
        """
        生成 SAR 交易信号

            - 买入：上一日 SAR > 上一日收盘价，当日 SAR < 当日收盘价
            - 卖出：上一日 SAR < 上一日收盘价，当日 SAR > 当日收盘价
            - 其余情况 → 持有(0)

        本策略不区分增强级别（买卖判定固定）。

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'high'、'low'、'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        sar = calc_sar(df, af_step=self.af_step, af_max=self.af_max)
        close = df['close']

        prev_sar = sar.shift(1)
        prev_close = close.shift(1)

        buy_signal = (prev_sar > prev_close) & (sar < close)
        sell_signal = (prev_sar < prev_close) & (sar > close)

        signals = pd.Series(0.0, index=df.index, dtype=float)
        signals[buy_signal] = self.signal_value(1)
        signals[sell_signal] = self.signal_value(-1)
        return signals
