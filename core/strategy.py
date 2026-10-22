#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易策略基类模块
定义所有策略必须实现的抽象基类
"""

from abc import ABC, abstractmethod
import pandas as pd
import numpy as np


class Strategy(ABC):
    """
    交易策略基类

    所有自定义策略必须继承此类并实现 generate_signals 方法。
    基类提供了信号生成和持仓计算的通用接口。

    Attributes:
        name (str): 策略名称，用于标识和日志输出
    """

    def __init__(self, name='BaseStrategy'):
        """
        初始化策略基类

        Args:
            name (str): 策略名称，默认为 'BaseStrategy'
        """
        self.name = name

    def signal_value(self, direction, strong=False):
        """
        返回带方向的信号值：统一为 ±1.0（买入 +1 / 卖出 -1）

        弱买/强买已合并为「买」(1)，弱卖/强卖已合并为「卖」(-1)；
        `strong` 参数仅为兼容保留，不再影响取值。

        Args:
            direction (int): +1 表示买入方向，-1 表示卖出方向
            strong (bool): 已废弃，忽略

        Returns:
            float: 信号值（+1.0 或 -1.0）
        """
        return 1.0 * direction

    @abstractmethod
    def generate_signals(self, df):
        """
        生成交易信号（抽象方法，子类必须实现）

        根据输入的行情数据 DataFrame 计算并返回交易信号序列。

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列，
                               还可以包含 'open', 'high', 'low', 'volume' 等列

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
                        1  = 买入
                        0  = 持有（不操作）
                       -1  = 卖出
        """
        pass

    def signal_residual(self, signals):
        """
        计算信号残值序列。

        规则：
            - 初始残值 = 0
            - 出现买卖信号时，残值 = 信号值（强买 1 / 弱买 0.5 / 弱卖 -0.5 / 强卖 -1）
            - 无买卖信号（信号为 0）时，残值 = 上一期残值 / 2（逐日衰减）

        Args:
            signals (pd.Series): 交易信号序列

        Returns:
            pd.Series: 信号残值序列，索引与 signals 对齐
        """
        residual = 0.0
        values = []
        for s in signals:
            if s != 0:
                residual = float(s)
            else:
                residual = residual / 2.0
            values.append(residual)
        return pd.Series(values, index=signals.index, name='signal_residual')

    def calculate_position(self, df, signals):
        """
        根据信号计算持仓状态

        将交易信号转换为持仓状态序列。默认实现为：买入信号(>0)转为持仓1，
        卖出信号(<0)转为持仓0，其余(0)保持前一日持仓状态。

        Args:
            df (pd.DataFrame): 行情数据
            signals (pd.Series): 交易信号序列

        Returns:
            pd.Series: 持仓状态序列
                       1 = 持有仓位
                       0 = 空仓
        """
        # 将信号转换为持仓：买入信号=1，卖出信号=0，其他信号保持前值
        position = signals.copy()
        position[position > 0] = 1
        position[position < 0] = 0
        position = position.replace(0, np.nan)
        position = position.fillna(method='ffill').fillna(0).astype(int)
        return position