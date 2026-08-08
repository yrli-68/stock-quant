#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
波动率择时策略 (Volatility Timing Strategy) — 指数专属

指数波动率有均值回归特性：低波动环境下的突破胜率更高，
高波动环境下的下跌风险更大。该策略基于波动率状态和价格突破
来生成交易信号。

信号逻辑：
    - 买入：低波动（< 阈值）且价格突破布林带上轨（窄幅整理后向上突破）
    - 卖出：高波动（> 阈值）且价格跌破 MA20（恐慌抛售信号）
    - 观望：其余情况
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_historical_volatility, calc_ma, calc_bollinger


class VolatilityTimingStrategy(Strategy):
    """
    波动率择时策略

    利用波动率的均值回归特性进行择时：
        - 低波动阶段：市场处于窄幅整理，酝酿方向突破。此时如果
          价格向上突破布林带上轨，说明多头力量积聚后爆发，买入信号
        - 高波动阶段：市场处于恐慌或亢奋状态，风险大幅上升。
          此时如果价格跌破 MA20，说明多头防线失守，卖出信号

    Attributes:
        vol_period (int): 波动率计算周期，默认20日
        vol_low (float): 低波动阈值（年化），默认 0.15（15%）
        vol_high (float): 高波动阈值（年化），默认 0.30（30%）
        bb_period (int): 布林带周期，默认20
        bb_std (float): 布林带标准差倍数，默认1.5（指数用窄带）
        ma_period (int): 卖出参考均线周期，默认20
    """

    def __init__(self, vol_period=20, vol_low=0.15, vol_high=0.30,
                 bb_period=20, bb_std=1.5, ma_period=20,
                 name='VolatilityTiming'):
        """
        初始化波动率择时策略

        Args:
            vol_period (int): 波动率计算周期，默认20日
            vol_low (float): 低波动阈值，年化15%以下为低波动
            vol_high (float): 高波动阈值，年化30%以上为高波动
            bb_period (int): 布林带周期，默认20
            bb_std (float): 布林带标准差倍数，指数用1.5（窄于个股的2.0）
            ma_period (int): 卖出参考均线，默认20日
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.vol_period = vol_period
        self.vol_low = vol_low
        self.vol_high = vol_high
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.ma_period = ma_period

    def generate_signals(self, df):
        """
        生成波动率择时交易信号

        信号逻辑：
            1. 计算年化历史波动率（20日）
            2. 计算布林带（1.5倍标准差，适配指数）
            3. 计算 MA20 作为卖出防线
            4. 波动率 < 15% 且价格突破布林上轨 → 买入(1)
            5. 波动率 > 30% 且价格跌破 MA20 → 卖出(-1)
            6. 其余情况 → 持有(0)

        设计原理：
            - 低波动+突破：市场在窄幅整理后选择方向，向上突破
              的可靠性远高于高波动环境下的突破，因为低波动说明
              多空力量均衡，一旦突破意味着一方胜出
            - 高波动+跌破均线：高波动本身是风险信号，价格跌破
              MA20 确认了空头占优，此时应果断离场

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，1=买入, -1=卖出, 0=持有
        """
        close = df['close']

        # 计算波动率
        volatility = calc_historical_volatility(df, period=self.vol_period)

        # 计算布林带（窄带，适配指数）
        bb = calc_bollinger(df, period=self.bb_period, std=self.bb_std)
        bb_upper = bb['UPPER']

        # 计算卖出参考均线
        ma = calc_ma(df, self.ma_period)

        # 初始化信号
        signals = pd.Series(0, index=df.index, dtype=int)

        # 买入信号：低波动且价格突破布林上轨
        # 条件：上一期波动率 < 低阈值，当期波动率也 < 低阈值，
        #       且当期收盘价 > 当期布林上轨
        # 使用上一期确认低波动是持续的，不是刚刚进入
        low_vol_breakout = (
            (volatility.shift(1) < self.vol_low) &
            (volatility < self.vol_low) &
            (close > bb_upper)
        )
        signals[low_vol_breakout] = 1

        # 卖出信号：高波动且价格跌破 MA20
        # 条件：波动率 > 高阈值，且收盘价 < MA20
        high_vol_breakdown = (
            (volatility > self.vol_high) &
            (close < ma)
        )
        signals[high_vol_breakdown] = -1

        return signals