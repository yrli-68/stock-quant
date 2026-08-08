#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
涨跌比确认策略 (Breadth Confirmation Strategy) — 指数专属

指数由成分股构成，涨跌比（上涨家数/下跌家数）能提前反映市场
内部情绪和宽度。指数的涨跌比是市场健康的"体检报告"，
比指数本身更早发出转向信号。

信号逻辑：
    - 买入：涨跌比 > 2.0 且价格站上 MA5（市场宽度强势+价格确认）
    - 卖出：涨跌比 < 0.5 且价格跌破 MA5（市场宽度衰竭+价格确认）
    - 观望：其余情况

注意：由于无法直接获取实时涨跌家数数据，本策略通过指数成分股
的收盘价变化来近似计算涨跌比（当日上涨成分股数/下跌成分股数）。
当无法获取成分股数据时，降级使用成交量比作为替代指标。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_ma


class BreadthConfirmationStrategy(Strategy):
    """
    涨跌比确认策略

    利用市场宽度（涨跌比）与价格趋势的共振来确认买卖信号。
    涨跌比 > 2.0 意味着多头碾压空头，此时价格上涨是可靠的；
    涨跌比 < 0.5 意味着空头全面占优，此时价格下跌是趋势性的。

    当无法获取成分股涨跌数据时，降级为成交量比模式：
        - 用当日成交量/5日均量 作为市场参与度的代理变量
        - 放量（>1.5倍）上涨 → 买入，缩量下跌 → 卖出

    Attributes:
        breadth_high (float): 涨跌比买入阈值，默认 2.0
        breadth_low (float): 涨跌比卖出阈值，默认 0.5
        ma_period (int): 价格确认均线周期，默认5
        vol_ratio_threshold (float): 成交量比阈值（降级模式），默认 1.5
    """

    def __init__(self, breadth_high=2.0, breadth_low=0.5,
                 ma_period=5, vol_ratio_threshold=1.5,
                 name='BreadthConfirmation'):
        """
        初始化涨跌比确认策略

        Args:
            breadth_high (float): 涨跌比买入阈值，默认 2.0
            breadth_low (float): 涨跌比卖出阈值，默认 0.5
            ma_period (int): 价格确认均线周期，默认5
            vol_ratio_threshold (float): 成交量比阈值（降级模式），默认 1.5
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.breadth_high = breadth_high
        self.breadth_low = breadth_low
        self.ma_period = ma_period
        self.vol_ratio_threshold = vol_ratio_threshold

    def generate_signals(self, df, breadth_data=None):
        """
        生成涨跌比确认交易信号

        信号逻辑（主模式 — 有涨跌比数据）：
            1. 计算 MA5 用于价格确认
            2. 涨跌比 > 2.0 且收盘价 > MA5 → 买入(1)
            3. 涨跌比 < 0.5 且收盘价 < MA5 → 卖出(-1)
            4. 其余情况 → 持有(0)

        降级模式（无涨跌比数据，仅使用价格和成交量）：
            1. 计算成交量比 = 当日量 / 5日均量
            2. 成交量比 > 1.5 且收盘价 > MA5 → 买入(1)
            3. 成交量比 < 0.6 且收盘价 < MA5 → 卖出(-1)
            4. 其余情况 → 持有(0)

        设计原理：
            - 涨跌比 > 2.0：上涨家数是下跌家数的2倍以上，说明
              市场宽度极强，多头力量全面扩散，此时做多安全性极高
            - 涨跌比 < 0.5：下跌家数是上涨家数的2倍以上，说明
              市场宽度极弱，空头全面压制，此时应果断离场
            - 降级模式：成交量比是市场参与度的代理，放量上涨说明
              资金认可，缩量下跌说明无人接盘

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列
            breadth_data (pd.Series, optional): 每日涨跌比数据，
                索引与 df 对齐。若为 None 则使用降级模式

        Returns:
            pd.Series: 交易信号序列，1=买入, -1=卖出, 0=持有
        """
        close = df['close']

        # 计算价格确认均线
        ma = calc_ma(df, self.ma_period)

        # 初始化信号
        signals = pd.Series(0, index=df.index, dtype=int)

        if breadth_data is not None and len(breadth_data) > 0:
            # ---- 主模式：使用涨跌比数据 ----
            # 对齐索引
            breadth = breadth_data.reindex(df.index)

            # 买入：涨跌比 > 高阈值 且 价格站上 MA5
            buy_condition = (
                (breadth > self.breadth_high) &
                (close > ma)
            )
            signals[buy_condition] = 1

            # 卖出：涨跌比 < 低阈值 且 价格跌破 MA5
            sell_condition = (
                (breadth < self.breadth_low) &
                (close < ma)
            )
            signals[sell_condition] = -1

        else:
            # ---- 降级模式：使用成交量比作为替代 ----
            if 'volume' in df.columns:
                vol_ma = df['volume'].rolling(window=5).mean()
                vol_ratio = df['volume'] / vol_ma

                # 买入：放量上涨（量比 > 1.5 且 价格 > MA5）
                buy_condition = (
                    (vol_ratio > self.vol_ratio_threshold) &
                    (close > ma)
                )
                signals[buy_condition] = 1

                # 卖出：缩量下跌（量比 < 0.6 且 价格 < MA5）
                sell_condition = (
                    (vol_ratio < 0.6) &
                    (close < ma)
                )
                signals[sell_condition] = -1

        return signals