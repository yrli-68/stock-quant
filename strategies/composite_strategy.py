#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复合策略 (Composite Strategy)

组合多个子策略，通过加权投票机制生成综合交易信号。

核心思想：
    - 每个子策略独立生成信号（1/0.5=买入, -1/-0.5=卖出, 0=持有）
    - 每个子策略的参与加权计算的原始值为：信号值 × signal_weight + 仓位水平 × position_weight
      （signal_weight 默认 0.7，position_weight 默认 0.3；仓位水平由信号按 fraction 模式累加得到）
    - 按权重对所有子策略的原始值加权求和，得到原始加权信号 S_raw
    - 对 S_raw 先做轻度平滑（rolling(3).mean()）得到 S_raw_smooth，再对 S_raw_smooth 做
      滚动 Z-score 标准化（过去 window 期均值/标准差）解决多策略加权导致信号被稀释、
      振幅变小的问题：
          z_t = (S_raw_smooth,t - μ_window) / σ_window
    - z > z_buy(1.0) 时买入，z < z_sell(-1.0) 时卖出，其余观望

参与加权的策略及其权重由 `input/stock-quant.json` 的 `composite` 配置决定。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy


def compute_position_level(signals):
    """根据信号序列计算 0~1 的仓位水平序列（与回测 fraction 模式累加逻辑一致）

    规则：
        - 弱买(+0.5)/强买(+1) → 累加 abs(signal)，封顶 1
        - 弱卖(-0.5)/强卖(-1) → 递减 abs(signal)，下限 0

    Args:
        signals (pd.Series): 信号序列

    Returns:
        pd.Series: 仓位水平序列，索引与 signals 对齐
    """
    pos = 0.0
    levels = []
    for s in signals:
        if s > 0:
            pos = min(1.0, pos + abs(s))
        elif s < 0:
            pos = max(0.0, pos - abs(s))
        levels.append(pos)
    return pd.Series(levels, index=signals.index)


class CompositeStrategy(Strategy):
    """
    复合策略

    Attributes:
        strategies (list): 子策略实例列表
        weights (list): 各子策略的归一化权重列表
        smooth_window (int): 原始加权信号的轻度平滑窗口（默认 3）
        window (int): 滚动 Z-score 标准化窗口（默认 20）
        z_buy (float): Z 值买入阈值（默认 1.0）
        z_sell (float): Z 值卖出阈值（默认 -1.0）
        signal_weight (float): 信号值的加权占比（默认 0.7）
        position_weight (float): 仓位水平的加权占比（默认 0.3）
    """

    def __init__(self, strategies, weights=None, name='Composite',
                 signal_weight=0.7, position_weight=0.3, smooth_window=3, window=20, z_buy=1.0, z_sell=-1.0):
        """
        初始化复合策略

        Args:
            strategies (list): 子策略实例列表，每个实例必须继承自 Strategy
            weights (list, optional): 各子策略的权重列表，默认等权重
            name (str): 策略名称
            signal_weight (float): 信号值加权占比，默认 0.7
            position_weight (float): 仓位水平加权占比，默认 0.3
            smooth_window (int): 原始加权信号的轻度平滑窗口，默认 3
            window (int): 滚动 Z-score 标准化窗口，默认 20
            z_buy (float): Z 值买入阈值，默认 1.0
            z_sell (float): Z 值卖出阈值，默认 -1.0

        Raises:
            ValueError: 当策略列表为空或权重长度与策略数量不匹配时抛出
        """
        super().__init__(name=name)

        if not strategies:
            raise ValueError("策略列表不能为空")

        self.strategies = strategies
        self.smooth_window = smooth_window
        self.window = window
        self.z_buy = z_buy
        self.z_sell = z_sell
        self.signal_weight = signal_weight
        self.position_weight = position_weight

        # 设置权重：如果未提供则使用等权重
        if weights is None:
            self.weights = [1.0 / len(strategies)] * len(strategies)
        else:
            if len(weights) != len(strategies):
                raise ValueError(
                    f"权重数量 ({len(weights)}) 与策略数量 ({len(strategies)}) 不匹配"
                )
            # 归一化权重，使权重之和为 1
            total_weight = sum(weights)
            self.weights = [w / total_weight for w in weights]

    def generate_signals(self, df):
        """
        生成复合交易信号

        1. 每个子策略生成信号后计算原始值（信号值 × signal_weight + 仓位水平 × position_weight）；
        2. 按权重加权求和得到原始加权信号 S_raw；
        3. 对 S_raw 做轻度平滑（rolling(smooth_window).mean()，默认 3 期）得到 S_raw_smooth；
        4. 对 S_raw_smooth 做滚动 Z-score 标准化：
              z_t = (S_raw_smooth,t - μ_window) / σ_window
           其中 μ_window / σ_window 为过去 window 期平滑后信号的均值/标准差；
        5. 根据 Z 值阈值决定买卖信号：
              z > z_buy(1.0)   → 买入
              z < z_sell(-1.0) → 卖出
              其余             → 观望

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 收集所有子策略的原始值（信号*signal_weight + 仓位水平*position_weight）
        raw_sums = []
        for strategy, weight in zip(self.strategies, self.weights):
            signal = strategy.generate_signals(df)
            in_position = compute_position_level(signal)
            raw = signal * self.signal_weight + in_position * self.position_weight
            raw_sums.append(raw.values * weight)

        weighted_sum = np.zeros(len(df))
        for rv in raw_sums:
            weighted_sum += rv
        s_raw = pd.Series(weighted_sum, index=df.index)

        # 轻度平滑：S_raw_smooth = S_raw.rolling(smooth_window).mean()
        s_raw_smooth = s_raw.rolling(self.smooth_window, min_periods=1).mean()

        # 对平滑后的信号做滚动 Z-score 标准化（标准差为 0 时取 0）
        mu = s_raw_smooth.rolling(window=self.window, min_periods=1).mean()
        sigma = s_raw_smooth.rolling(window=self.window, min_periods=1).std()
        z = (s_raw_smooth - mu) / sigma.where(sigma > 1e-9, np.nan)
        z = z.fillna(0.0)
        # Z 值一般分布在 [-3, 3]，做安全裁剪
        z = z.clip(-3.0, 3.0)

        # 根据 Z 值阈值生成最终信号
        final_signals = pd.Series(0.0, index=df.index, dtype=float)

        final_signals[z > self.z_buy] = self.signal_value(1)
        final_signals[z < self.z_sell] = self.signal_value(-1)

        # 附加原始加权信号、平滑信号、Z 分数曲线与阈值，供图表展示
        final_signals.attrs['weighted_sum'] = s_raw
        final_signals.attrs['raw_smooth'] = s_raw_smooth
        final_signals.attrs['z_score'] = z
        final_signals.attrs['z_buy'] = self.z_buy
        final_signals.attrs['z_sell'] = self.z_sell

        return final_signals
