#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SG2 策略（策略2）

信号逻辑同 SG1，均线周期不同（MA5 × MA20），并附加 RSI 过滤：
    - 弱买(+0.5)：MA5 斜率 > 0 且 MA5 上穿 MA20（金叉）且当日收盘价 > 前一日收盘价 × 99%（涨）且 RSI < MIN_RSI_THRESHOLD
    - 弱卖(-0.5)：MA5 下穿 MA20（死叉）或 RSI > MAX_RSI_THRESHOLD 或买入后自最高收盘价回撤 ≥ MAX_DRAWDWON_THRESHOLD

仅输出弱信号（±0.5），无强买/强卖条件。
"""

from strategies.sg1_strategy import SG1Strategy
from core.thresholds import UNSET


class SG2Strategy(SG1Strategy):
    """SG2 策略：MA5/MA20 交叉（逻辑同 SG1，周期不同）

    Attributes:
        fast_period (int): 快线周期，默认 5
        slow_period (int): 慢线周期，默认 20
        max_drawdown (float): 最大回撤止损阈值（相对买入后最高收盘价），默认 0.15
    """

    def __init__(self, fast_period=5, slow_period=20, max_drawdown=UNSET, name='SG2'):
        """
        初始化 SG2 策略

        Args:
            fast_period (int): 快速均线周期，默认 5
            slow_period (int): 慢速均线周期，默认 20
            max_drawdown (float): 最大回撤止损阈值（0.15 表示 15%；未传参时
                读取 pyproject.toml 的 MAX_DRAWDWON_THRESHOLD）
            name (str): 策略名称
        """
        super().__init__(fast_period=fast_period, slow_period=slow_period,
                         max_drawdown=max_drawdown, name=name)
