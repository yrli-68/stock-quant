#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
复合策略 (Composite Strategy)

通过组合多个子策略，使用加权投票机制来生成综合交易信号。
核心思想：
    - 每个子策略独立生成信号（1=买入, -1=卖出, 0=持有）
    - 按权重对所有子策略的信号进行加权求和
    - 加权信号 > 正阈值时买入，< 负阈值时卖出

这种策略组合方法可以降低单一策略的噪声和误判风险，
提高整体策略的稳定性和可靠性。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy


class CompositeStrategy(Strategy):
    """
    复合策略

    将多个策略组合在一起，通过加权投票机制融合各策略的信号，
    生成最终的交易信号。

    适用场景：
        - 希望结合多种技术指标的优势
        - 降低单一策略的噪声和误判
        - 寻找多策略共振的确认信号

    Attributes:
        strategies (list): 子策略实例列表
        weights (list): 各子策略的权重列表
        threshold (float): 信号阈值，加权信号超过此值才触发交易
    """

    def __init__(self, strategies, weights=None, threshold=0.3, name='Composite'):
        """
        初始化复合策略

        Args:
            strategies (list): 子策略实例列表，每个实例必须继承自 Strategy
            weights (list, optional): 各子策略的权重列表，默认为等权重
            threshold (float): 信号阈值，加权信号 > threshold 时买入，
                              < -threshold 时卖出，默认 0.3
            name (str): 策略名称

        Raises:
            ValueError: 当策略列表为空或权重长度与策略数量不匹配时抛出
        """
        super().__init__(name=name)

        if not strategies:
            raise ValueError("策略列表不能为空")

        self.strategies = strategies
        self.threshold = threshold

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

        遍历所有子策略，让每个子策略独立生成信号，然后按权重
        进行加权投票，最终根据阈值决定买卖信号。

        信号生成逻辑：
            1. 收集所有子策略的信号
            2. 加权求和得到综合信号
            3. 综合信号 > threshold → 买入(1)
            4. 综合信号 < -threshold → 卖出(-1)
            5. 其余情况 → 持有(0)

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 收集所有子策略的信号
        all_signals = []
        for strategy in self.strategies:
            signal = strategy.generate_signals(df)
            all_signals.append(signal)

        # 加权求和：将每个子策略的信号乘以其权重后求和
        weighted_sum = np.zeros(len(df))
        for signal, weight in zip(all_signals, self.weights):
            weighted_sum += signal.values * weight

        # 根据阈值生成最终信号
        final_signals = pd.Series(0, index=df.index, dtype=int)

        # 加权信号 > 正阈值 → 买入
        final_signals[weighted_sum > self.threshold] = 1

        # 加权信号 < 负阈值 → 卖出
        final_signals[weighted_sum < -self.threshold] = -1

        # 附加加权分数曲线与阈值，供图表展示
        final_signals.attrs['weighted_sum'] = pd.Series(weighted_sum, index=df.index)
        final_signals.attrs['threshold'] = self.threshold

        return final_signals