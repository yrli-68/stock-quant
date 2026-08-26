#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KDJ 策略 (KDJ Strategy)

基于随机指标 (KDJ) 的金叉死叉来生成交易信号：
    - K 线在超卖区（< oversold）上穿 D 线（低位金叉）→ 买入信号
    - K 线在超买区（> overbought）下穿 D 线（高位死叉）→ 卖出信号

KDJ 是一种随机振荡指标，通过比较收盘价在近期高低价区间中的位置，
反映价格动量的强弱与超买超卖状态。
"""

import pandas as pd

from core.strategy import Strategy
from core.indicators import calc_kdj, calc_ma


class KDJStrategy(Strategy):
    """
    KDJ 随机指标策略

    利用 KDJ 指标的 K 线与 D 线的交叉来生成买卖信号：
    当 K 线在超卖区域向上穿越 D 线（金叉）时买入；
    当 K 线在超买区域向下穿越 D 线（死叉）时卖出。

    Attributes:
        n (int): RSV 计算周期，默认 9
        m1 (int): K 线平滑周期，默认 3
        m2 (int): D 线平滑周期，默认 3
        oversold (float): 超卖阈值，默认 20
        overbought (float): 超买阈值，默认 80
    """

    def __init__(self, n=9, m1=3, m2=3, oversold=20, overbought=80, ma_slow=60, name='KDJ'):
        """
        初始化 KDJ 策略

        Args:
            n (int): RSV 计算周期，默认 9
            m1 (int): K 线平滑周期，默认 3
            m2 (int): D 线平滑周期，默认 3
            oversold (float): 超卖阈值，K 低于此值视为超卖，默认 20
            overbought (float): 超买阈值，K 高于此值视为超买，默认 80
            ma_slow (int): 增强过滤用慢速均线周期，默认 60
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.n = n
        self.m1 = m1
        self.m2 = m2
        self.oversold = oversold
        self.overbought = overbought
        self.ma_slow = ma_slow

    def generate_signals(self, df):
        """
        生成 KDJ 交易信号

        计算 KDJ 指标（K、D、J），然后检测交叉信号：
            - 金叉买入：上一期 K <= D，当期 K > D
            - 死叉卖出：上一期 K >= D，当期 K < D
            - 其余情况 → 持有(0)

        增强级别 1（enhance>=1）时：
            - 买入额外要求 close > MA60
            - 卖出条件同基础（死叉）

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'high'、'low'、'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 计算 KDJ 指标
        kdj = calc_kdj(df, n=self.n, m1=self.m1, m2=self.m2)
        k = kdj['K']
        d = kdj['D']

        # 初始化信号序列
        signals = pd.Series(0.0, index=df.index, dtype=float)

        # 金叉买入：K 上穿 D
        golden_cross = (k.shift(1) <= d.shift(1)) & (k > d)

        # 死叉卖出：K 下穿 D
        death_cross = (k.shift(1) >= d.shift(1)) & (k < d)

        # 基础版条件 → 弱信号
        signals[golden_cross] = self.signal_value(1)
        signals[death_cross] = self.signal_value(-1)

        # 增强级别 1：买入额外要求 close > MA60 → 强信号
        if self.enhance >= 1:
            ma_slow = calc_ma(df, self.ma_slow)
            close = df['close']
            signals[golden_cross & (close > ma_slow)] = self.signal_value(1, strong=True)

        return signals
