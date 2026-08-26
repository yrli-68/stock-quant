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

    def __init__(self, name='BaseStrategy', enhance=0):
        """
        初始化策略基类

        Args:
            name (str): 策略名称，默认为 'BaseStrategy'
            enhance (int): 信号增强判定级别，0=基本判定，>0 时启用对应增强条件
        """
        self.name = name
        self.enhance = enhance

    def signal_value(self, direction, strong=False):
        """
        返回带方向的信号值：

            - 弱信号 (strong=False)：±0.5（符合基础版条件）
            - 强信号 (strong=True)：±1.0（符合增强版条件）

        Args:
            direction (int): +1 表示买入方向，-1 表示卖出方向
            strong (bool): True 返回强信号 ±1.0，False 返回弱信号 ±0.5

        Returns:
            float: 信号值（±0.5 或 ±1.0）
        """
        magnitude = 1.0 if strong else 0.5
        return magnitude * direction

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
                        1   = 强买入（做多）
                        0.5 = 弱买入
                        0   = 持有（不操作）
                       -0.5 = 弱卖出
                       -1   = 强卖出（平仓/做空）
        """
        pass

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