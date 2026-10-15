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

        增强级别 1（enhance>=1，`-g1 macd`）：金叉分级 + 金叉/死叉次日延续确认
            - 金叉日（满足 MA60/放量/次日确认等任一条件）→ 买入(1)
            - 死叉日（满足 MA60/放量/次日确认等任一条件）→ 卖出(-1)
        （弱买/强买已合并为「买」1，弱卖/强卖已合并为「卖」-1）

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列（增强级还需 'volume' 列）

        Returns:
            pd.Series: 交易信号序列（1/0/-1），索引与 df 对齐
        """
        # 计算 MACD 指标
        macd_df = calc_macd(df, fast=self.fast, slow=self.slow, signal=self.signal)
        dif = macd_df['DIF']
        dea = macd_df['DEA']

        # 金叉买入：DIF 上穿 DEA
        # 条件：上一期 DIF <= 上一期 DEA，当期 DIF > 当期 DEA
        # 注：金叉时 DIF > DEA 恒成立，故 MACD 柱 > 0 为冗余条件，已省略
        golden_cross = (
            (dif.shift(1) <= dea.shift(1)) &
            (dif > dea)
        )
        # 死叉卖出：DIF 下穿 DEA
        # 条件：上一期 DIF >= 上一期 DEA，当期 DIF < 当期 DEA
        death_cross = (dif.shift(1) >= dea.shift(1)) & (dif < dea)

        # 基础版（enhance==0）→ 简单金叉/死叉信号
        if self.enhance < 1:
            signals = pd.Series(0.0, index=df.index, dtype=float)
            signals[golden_cross] = self.signal_value(1)     # +1
            signals[death_cross] = self.signal_value(-1)     # -1
            return signals

        # ===== 增强级别 1：金叉分级 + 逐日延续确认（逐日顺序计算）=====
        close = df['close']
        ma_slow = calc_ma(df, self.ma_slow)
        if 'volume' in df.columns:
            vol_ma20 = df['volume'].rolling(20).mean()
            vol_ok = df['volume'] > vol_ma20
        else:
            vol_ok = pd.Series(False, index=df.index)
        ret = close.pct_change()  # (close-close.shift)/close.shift

        up_ma = close > ma_slow          # 买入确认：站上 MA60
        down_ma = close < ma_slow        # 卖出确认：跌破 MA60

        # 前一天是否为金叉/死叉（次日延续确认的触发条件）
        golden_yest = golden_cross.shift(1).fillna(False)
        death_yest = death_cross.shift(1).fillna(False)

        n = len(df)
        signals = pd.Series(0.0, index=df.index, dtype=float)
        for i in range(n):
            if golden_cross.iloc[i]:
                signals.iloc[i] = 1.0                       # 买入
            elif death_cross.iloc[i]:
                signals.iloc[i] = -1.0                      # 卖出
            elif golden_yest.iloc[i]:
                # 金叉次日延续确认 → 买入
                prev = signals.iloc[i - 1] if i > 0 else 0.0
                small_chg = ret.iloc[i] < 0.03
                if small_chg and ((up_ma.iloc[i] and bool(vol_ok.iloc[i])) or prev >= 1.0):
                    signals.iloc[i] = 1.0
            elif death_yest.iloc[i]:
                # 死叉次日延续确认 → 卖出
                prev = signals.iloc[i - 1] if i > 0 else 0.0
                small_chg = ret.iloc[i] < 0.03
                if small_chg and ((down_ma.iloc[i] and bool(vol_ok.iloc[i])) or prev <= -1.0):
                    signals.iloc[i] = -1.0

        return signals