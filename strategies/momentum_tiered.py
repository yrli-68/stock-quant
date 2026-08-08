#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
动量分层策略 (Momentum Tiered Strategy) — 指数专属

指数天然适合动量策略——强者恒强。该策略通过动量收益与趋势共振
来生成交易信号，适用于大盘指数（如上证指数、沪深300等）。

信号逻辑：
    - 买入：过去N日动量收益为正，且 MA20 > MA60（趋势+动量共振）
    - 卖出：动量收益转负（趋势衰竭）
    - 观望：其余情况
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_ma, calc_momentum_return


class MomentumTieredStrategy(Strategy):
    """
    动量分层策略

    结合动量收益和趋势确认两个维度来判断市场方向：
        - 动量维度：过去60日的累计收益率，正值表示向上动量
        - 趋势维度：MA20与MA60的排列关系，确认中期趋势方向

    指数不同于个股，动量效应更显著，且趋势一旦形成会持续较长时间。
    本策略只在动量+趋势双重共振时才触发信号，避免单一维度的噪声。

    Attributes:
        momentum_period (int): 动量回看周期，默认60个交易日
        ma_fast (int): 快速均线周期，默认20
        ma_slow (int): 慢速均线周期，默认60
    """

    def __init__(self, momentum_period=60, ma_fast=20, ma_slow=60,
                 name='MomentumTiered'):
        """
        初始化动量分层策略

        Args:
            momentum_period (int): 动量计算周期，默认60日
            ma_fast (int): 快速均线（趋势确认），默认20日
            ma_slow (int): 慢速均线（趋势确认），默认60日
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.momentum_period = momentum_period
        self.ma_fast = ma_fast
        self.ma_slow = ma_slow

    def generate_signals(self, df):
        """
        生成动量分层交易信号

        信号逻辑：
            1. 计算过去 N 日的动量收益率
            2. 计算 MA20 和 MA60 用于趋势确认
            3. 动量 > 0 且 MA20 > MA60（中期多头排列）→ 买入(1)
            4. 动量 < 0 → 卖出(-1)
            5. 其余情况 → 持有(0)

        设计原理：
            - 动量 > 0 且 MA20 > MA60：价格在上升趋势中且持续走强，
              动量与趋势共振，是最可靠的做多信号
            - 动量 < 0：价格动能转弱，无论趋势如何都应离场观望，
              这是止损/止盈条件的核心
            - 动量 > 0 但 MA20 < MA60：有反弹动能但趋势未转多，
              不做多（避免抄底陷阱）

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，1=买入, -1=卖出, 0=持有
        """
        # 计算动量收益率
        momentum = calc_momentum_return(df, period=self.momentum_period)

        # 计算趋势均线
        ma_fast = calc_ma(df, self.ma_fast)
        ma_slow = calc_ma(df, self.ma_slow)

        # 初始化信号
        signals = pd.Series(0, index=df.index, dtype=int)

        # 买入信号：动量 > 0 且 MA20 > MA60（趋势+动量共振）
        buy_condition = (momentum > 0) & (ma_fast > ma_slow)
        signals[buy_condition] = 1

        # 卖出信号：动量转负（趋势衰竭，无论均线排列如何）
        sell_condition = momentum < 0
        signals[sell_condition] = -1

        return signals