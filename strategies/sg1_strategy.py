#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SG1 策略（策略1）

基于 3 日均线与 10 日均线（默认附加 RSI 过滤）：
    - 买(+1)：MA3 上穿 MA10（金叉）且当日收盘价 > 前一日收盘价 × 99%（涨）且 RSI < MIN_RSI_THRESHOLD
    - 卖(-1)：MA3 下穿 MA10（死叉）或 RSI > MAX_RSI_THRESHOLD 或买入后自最高收盘价回撤 ≥ MAX_DRAWDWON_THRESHOLD

阈值读取 pyproject.toml 的 thresholds 配置（默认 MIN_RSI=25 / MAX_RSI=85 / 回撤15%）。

本策略无强买/强卖条件（不随增强级别变化）。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_ma, calc_ema, calc_rsi
from core.thresholds import UNSET, get_threshold, get_max_drawdown_threshold


class SG1Strategy(Strategy):
    """SG1 策略：MA3/MA10 交叉（严格买入 + 最大回撤止损保护，无强信号）

    Attributes:
        fast_period (int): 快线周期，默认 3
        slow_period (int): 慢线周期，默认 10
        max_drawdown (float): 最大回撤止损阈值（相对买入后最高收盘价），默认 0.15
        use_ema (bool): True 时用 EMA（指数移动平均），False 用 SMA，默认 False
    """

    # 由 generate_signals 内部同步 -sn 与涨幅过滤（in_pos 仅对实际入场生效）
    integrates_buy_filter = True

    def __init__(self, fast_period=3, slow_period=10, max_drawdown=UNSET, use_ema=False,
                 rsi_buy_max=UNSET, rsi_sell_min=UNSET, name='SG1'):
        """
        初始化 SG1 策略

        Args:
            fast_period (int): 快速均线周期，默认 3
            slow_period (int): 慢速均线周期，默认 10
            max_drawdown (float): 最大回撤止损阈值（0.15 表示 15%；None 表示不启用；
                未传参时读取 pyproject.toml 的 MAX_DRAWDWON_THRESHOLD）
            use_ema (bool): True 用 EMA，False 用 SMA
            rsi_buy_max (float): 买入附加条件 RSI < rsi_buy_max（None 表示不启用；
                未传参时读取 pyproject.toml 的 MIN_RSI_THRESHOLD）
            rsi_sell_min (float): 卖出附加条件 RSI > rsi_sell_min（None 表示不启用；
                未传参时读取 pyproject.toml 的 MAX_RSI_THRESHOLD）
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.max_drawdown = (get_max_drawdown_threshold()
                             if max_drawdown is UNSET else max_drawdown)
        self.use_ema = use_ema
        self.rsi_buy_max = (get_threshold('MIN_RSI_THRESHOLD')
                            if rsi_buy_max is UNSET else rsi_buy_max)
        self.rsi_sell_min = (get_threshold('MAX_RSI_THRESHOLD')
                             if rsi_sell_min is UNSET else rsi_sell_min)

    def generate_signals(self, df, seqnum=0, max_buy_gain=None, sar_ok=None,
                         index_ok=None):
        """
        生成 SG1 交易信号

        - 买(+1)：快线上穿慢线（金叉）且 `close > close.shift(1) * 0.99`（当日跌幅不超过1%）
        - 卖(-1)：快线下穿慢线（死叉）或 RSI > MAX_RSI_THRESHOLD 或买入后自最高收盘价回撤 ≥ MAX_DRAWDWON_THRESHOLD
        - 其余 → 持有(0)

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列
            seqnum (int): 买入信号序号阈值（0=不过滤）；内部据此同步 in_pos，
                使被过滤的买入不会被视为已入场
            max_buy_gain (float): 当日涨幅达到该值时取消买入（None=不限制）
            sar_ok (pd.Series): 买入所需的 SAR 条件（True=允许买入；None=不检查）
            index_ok (pd.Series): 买入所需的上证指数条件（True=允许买入；None=不检查）

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        calc = calc_ema if self.use_ema else calc_ma
        fast_ma = calc(df, self.fast_period)
        slow_ma = calc(df, self.slow_period)
        close = df['close']
        prev_close = close.shift(1)

        # RSI 附加过滤：仅当配置了阈值时才计算
        rsi = None
        if self.rsi_buy_max is not None or self.rsi_sell_min is not None:
            rsi = df['RSI14'] if 'RSI14' in df.columns else calc_rsi(df, 14)

        # 金叉（快线上穿慢线）+ 当日跌幅不超过 1%（close > 前收×99%）
        golden_cross = (
            (fast_ma.shift(1) <= slow_ma.shift(1)) & (fast_ma > slow_ma)
            & (close > prev_close * 0.99)
        )
        if self.rsi_buy_max is not None:
            golden_cross = golden_cross & (rsi < self.rsi_buy_max)
        # 死叉：快线下穿慢线
        death_cross = (fast_ma.shift(1) >= slow_ma.shift(1)) & (fast_ma < slow_ma)

        signals = pd.Series(0.0, index=df.index, dtype=float)

        # 逐日跟踪持仓与买入后最高收盘价，实现最大回撤止损。
        # in_pos 只在买入信号实际生效（通过序号与涨幅过滤）后置为 True，
        # 避免被过滤的买入导致后续买入被抑制或产生无持仓的孤立卖出。
        gains = close.pct_change() if max_buy_gain is not None else None
        seq_target = seqnum if seqnum and seqnum > 0 else 1
        buy_count = 0
        in_pos = False
        peak = 0.0
        for i in range(len(df)):
            c = float(close.iloc[i])
            rsi_sell = (self.rsi_sell_min is not None
                        and rsi is not None
                        and bool(rsi.iloc[i] > self.rsi_sell_min))
            sell_signal = bool(death_cross.iloc[i]) or rsi_sell
            if not in_pos:
                # 空仓期间的卖出信号同样重置买入计数
                if sell_signal:
                    buy_count = 0
                if bool(golden_cross.iloc[i]):
                    gain_ok = True
                    if gains is not None:
                        g = float(gains.iloc[i])
                        gain_ok = (not np.isnan(g)) and g < max_buy_gain
                    if seqnum and seqnum > 0:
                        # 序号按原始买入信号计数（包含被涨幅过滤的买入）
                        buy_count += 1
                        allow = (buy_count == seq_target) and gain_ok
                    else:
                        allow = gain_ok
                    allow = (allow
                             and (sar_ok is None or bool(sar_ok.iloc[i]))
                             and (index_ok is None or bool(index_ok.iloc[i])))
                    if allow:
                        signals.iloc[i] = self.signal_value(1)      # 弱买 +0.5
                        in_pos = True
                        peak = c
                        buy_count = 0
            else:
                if c > peak:
                    peak = c
                dd = (peak - c) / peak if peak > 0 else 0.0
                if self.max_drawdown is not None and dd >= self.max_drawdown:
                    signals.iloc[i] = self.signal_value(-1)     # 回撤止损（弱卖 -0.5）
                    in_pos = False
                    buy_count = 0
                elif sell_signal:
                    signals.iloc[i] = self.signal_value(-1)     # 死叉或 RSI 超买（弱卖 -0.5）
                    in_pos = False
                    buy_count = 0

        return signals
