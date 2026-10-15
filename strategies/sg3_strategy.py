#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SG3 策略（策略3）

与 SG2 完全相同的信号逻辑，仅均线改用指数移动平均（EMA5 × EMA20）：
    - 弱买(+0.5)：EMA5 斜率 > 0 且 EMA5 上穿 EMA20（金叉）且当日收盘价 > 前一日收盘价 × 99%（涨）且 < 前收 × 109%（涨幅<9%）
    - 弱卖(-0.5)：EMA5 下穿 EMA20（死叉）或持仓期自最高收盘价回撤 ≥ max_drawdown(15%)

仅输出弱信号（±0.5），无强买/强卖条件。
"""

from strategies.sg1_strategy import SG1Strategy


class SG3Strategy(SG1Strategy):
    """SG3 策略：EMA5/EMA20 交叉（逻辑同 SG2，均线为 EMA）

    Attributes:
        fast_period (int): 快线周期，默认 5
        slow_period (int): 慢线周期，默认 20
        max_drawdown (float): 最大回撤止损阈值（相对持仓期最高收盘价），默认 0.15
    """

    def __init__(self, fast_period=5, slow_period=20, max_drawdown=0.15, name='SG3'):
        """
        初始化 SG3 策略

        Args:
            fast_period (int): 快速 EMA 周期，默认 5
            slow_period (int): 慢速 EMA 周期，默认 20
            max_drawdown (float): 最大回撤止损阈值（0.15 表示 15%）
            name (str): 策略名称
        """
        super().__init__(fast_period=fast_period, slow_period=slow_period,
                         max_drawdown=max_drawdown, use_ema=True, name=name)
