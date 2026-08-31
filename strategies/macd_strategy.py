#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD 策略 (MACD Strategy)

基于 MACD 指标的金叉死叉来生成交易信号：
    - DIF 上穿 DEA 且 MACD 柱 > 0（金叉）→ 买入信号
    - DIF 下穿 DEA（死叉）→ 卖出信号

MACD 是一种趋势跟踪动量指标，通过快慢 EMA 的差值来反映
价格趋势的强度和方向变化。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_macd, calc_ma


class MACDStrategy(Strategy):
    """
    MACD 交易策略

    利用 MACD 指标的 DIF 线与 DEA 线的交叉来生成买卖信号。
    当 DIF 从下方上穿 DEA 且 MACD 柱状线为正时买入；
    当 DIF 从上方下穿 DEA 时卖出。

    Attributes:
        fast (int): 快速 EMA 周期，默认 12
        slow (int): 慢速 EMA 周期，默认 26
        signal (int): 信号线 DEA 周期，默认 9
        ma_period (int): 增强过滤用趋势快速均线周期，默认 20
        ma_slow (int): 增强过滤用趋势慢速均线周期，默认 60
    """

    def __init__(self, fast=12, slow=26, signal=9, ma_period=20, ma_slow=60, name='MACD'):
        """
        初始化 MACD 策略

        Args:
            fast (int): 快速 EMA 周期，默认 12
            slow (int): 慢速 EMA 周期，默认 26
            signal (int): 信号线 DEA 的 EMA 周期，默认 9
            ma_period (int): 增强过滤用趋势快速均线周期，默认 20
            ma_slow (int): 增强过滤用趋势慢速均线周期，默认 60
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.ma_period = ma_period
        self.ma_slow = ma_slow

    def generate_signals(self, df):
        """
        生成 MACD 交易信号

        计算 MACD 指标（DIF、DEA、MACD 柱），然后检测交叉信号：
            - DIF 上穿 DEA 且 MACD 柱 > 0 → 买入(1)
            - DIF 下穿 DEA → 卖出(-1)
            - 其余情况 → 持有(0)

        增强级别 1（enhance>=1）时：
            - 买入额外要求 close > MA60 → 强买(1)
            - 卖出条件同基础（死叉）→ 按增强信号处理，死叉即强卖(-1)

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 计算 MACD 指标
        macd_df = calc_macd(df, fast=self.fast, slow=self.slow, signal=self.signal)
        dif = macd_df['DIF']
        dea = macd_df['DEA']

        # 初始化信号序列
        signals = pd.Series(0.0, index=df.index, dtype=float)

        # 金叉买入：DIF 上穿 DEA
        # 条件：上一期 DIF <= 上一期 DEA，当期 DIF > 当期 DEA
        # 注：金叉时 DIF > DEA 恒成立，故 MACD 柱 > 0 为冗余条件，已省略
        golden_cross = (
            (dif.shift(1) <= dea.shift(1)) &
            (dif > dea)
        )
        # 死叉卖出：DIF 下穿 DEA（基本与增强一致）
        # 条件：上一期 DIF >= 上一期 DEA，当期 DIF < 当期 DEA
        death_cross = (dif.shift(1) >= dea.shift(1)) & (dif < dea)

        # 基础版条件 → 弱信号
        signals[golden_cross] = self.signal_value(1)
        signals[death_cross] = self.signal_value(-1)

        # 增强级别 1：买入额外要求 close > MA60 → 强信号；卖出条件与基础一致（死叉）→ 按增强信号处理
        if self.enhance >= 1:
            ma_slow = calc_ma(df, self.ma_slow)
            close = df['close']
            signals[golden_cross & (close > ma_slow)] = self.signal_value(1, strong=True)
            signals[death_cross] = self.signal_value(-1, strong=True)

        return signals