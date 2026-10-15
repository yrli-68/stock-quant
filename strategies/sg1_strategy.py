#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SG1 策略（策略1）

基于 3 日均线与 10 日均线：
    - 弱买(+0.5)：MA3 斜率 > 0 且 MA3 上穿 MA10（金叉）且当日收盘价 > 前一日收盘价 × 99%（涨）且 < 前收 × 109%（涨幅<9%）
    - 弱卖(-0.5)：MA3 下穿 MA10（死叉）或持仓期自最高收盘价回撤 ≥ max_drawdown(15%)

本策略仅输出弱信号（±0.5），无强买/强卖条件（不随增强级别变化）。
"""

import pandas as pd

from core.strategy import Strategy
from core.indicators import calc_ma, calc_ema


class SG1Strategy(Strategy):
    """SG1 策略：MA3/MA10 交叉（严格买入 + 最大回撤止损保护，无强信号）

    Attributes:
        fast_period (int): 快线周期，默认 3
        slow_period (int): 慢线周期，默认 10
        max_drawdown (float): 最大回撤止损阈值（相对持仓期最高收盘价），默认 0.15
        use_ema (bool): True 时用 EMA（指数移动平均），False 用 SMA，默认 False
    """

    def __init__(self, fast_period=3, slow_period=10, max_drawdown=0.15, use_ema=False, name='SG1'):
        """
        初始化 SG1 策略

        Args:
            fast_period (int): 快速均线周期，默认 3
            slow_period (int): 慢速均线周期，默认 10
            max_drawdown (float): 最大回撤止损阈值（0.15 表示 15%）
            use_ema (bool): True 用 EMA，False 用 SMA
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.max_drawdown = max_drawdown
        self.use_ema = use_ema

    def generate_signals(self, df):
        """
        生成 SG1 交易信号

        - 弱买(+0.5)：快线斜率>0 且快线上穿慢线（金叉）且 `close > close.shift(1) * 0.99` 且 `close < close.shift(1) * 1.09`（当日涨幅<9%）
        - 弱卖(-0.5)：快线下穿慢线（死叉）或持仓期自最高收盘价回撤 ≥ `max_drawdown`(15%)
        - 其余 → 持有(0)

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        calc = calc_ema if self.use_ema else calc_ma
        fast_ma = calc(df, self.fast_period)
        slow_ma = calc(df, self.slow_period)
        close = df['close']
        prev_close = close.shift(1)
        fast_slope = fast_ma.diff()

        # 金叉（快线上穿慢线）+ 快线斜率>0 + 当日涨幅在 (-1%, +9%) 之间
        # 即 close > 前收×99%（上涨）且 close < 前收×109%（涨幅<9%）
        golden_cross = (
            (fast_ma.shift(1) <= slow_ma.shift(1)) & (fast_ma > slow_ma)
            & (fast_slope > 0) & (close > prev_close * 0.99) & (close < prev_close * 1.09)
        )
        # 死叉：快线下穿慢线
        death_cross = (fast_ma.shift(1) >= slow_ma.shift(1)) & (fast_ma < slow_ma)

        signals = pd.Series(0.0, index=df.index, dtype=float)

        # 逐日跟踪持仓与持仓期最高收盘价，实现最大回撤止损
        in_pos = False
        peak = 0.0
        for i in range(len(df)):
            c = float(close.iloc[i])
            if not in_pos:
                if bool(golden_cross.iloc[i]):
                    signals.iloc[i] = self.signal_value(1)      # 弱买 +0.5
                    in_pos = True
                    peak = c
            else:
                if c > peak:
                    peak = c
                dd = (peak - c) / peak if peak > 0 else 0.0
                if dd >= self.max_drawdown:
                    signals.iloc[i] = self.signal_value(-1)     # 回撤止损（弱卖 -0.5）
                    in_pos = False
                elif bool(death_cross.iloc[i]):
                    signals.iloc[i] = self.signal_value(-1)     # 死叉（弱卖 -0.5）
                    in_pos = False

        return signals
