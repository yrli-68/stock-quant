#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
买入并持有策略 (Hold Strategy)

基准对照策略：从分析期 start 起全仓买入，一直持有到分析期结束，
不产生任何卖出信号。用于与其它策略做效果对比。
"""

import pandas as pd

from core.strategy import Strategy


class HoldStrategy(Strategy):
    """
    买入并持有策略（基准对照）

    Attributes:
        warmup (int): 预热天数（根），前 warmup 根信号为观望，默认 0（start 起即买入）
    """

    def __init__(self, warmup=0, name='Hold'):
        """
        初始化买入并持有策略

        Args:
            warmup (int): 前 warmup 根 K 线不买入（观望），默认 0（分析期 start 起买入）
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.warmup = warmup

    def generate_signals(self, df):
        """
        生成买入并持有信号：前 warmup 根观望，之后全部强买(1) 并持有到结束

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        signals = pd.Series(0.0, index=df.index, dtype=float)
        start = min(self.warmup, len(df))
        signals.iloc[start:] = self.signal_value(1, strong=True)
        return signals
