#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD1 策略 (MACD1 Strategy)

基于 MACD 指标的金叉死叉来生成交易信号：
    - DIF 上穿 DEA（金叉）且 RSI < MIN_RSI_THRESHOLD → 买入信号
    - DIF 下穿 DEA（死叉）或 RSI > MAX_RSI_THRESHOLD，或触发 ATR 动态止损（止损价=max(前一日止损价, close-N*ATR14)） → 卖出信号

MACD 是一种趋势跟踪动量指标，通过快慢 EMA 的差值来反映
价格趋势的强度和方向变化。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import (calc_atr, calc_macd, calc_rsi,
                             update_atr_trailing_stop,
                             adjust_atr_stop_multiplier)
from core.thresholds import UNSET, get_threshold


class MACD1Strategy(Strategy):
    """
    MACD1 交易策略

    利用 MACD 指标的 DIF 线与 DEA 线的交叉来生成买卖信号。
    当 DIF 从下方上穿 DEA 且 MACD 柱状线为正时买入；
    当 DIF 从上方下穿 DEA 时卖出。

    Attributes:
        fast (int): 快速 EMA 周期，默认 6
        slow (int): 慢速 EMA 周期，默认 13
        signal (int): 信号线 DEA 周期，默认 5
        use_atr_stop (bool): 是否启用 ATR 动态止损
    """

    integrates_buy_filter = True

    def __init__(self, fast=6, slow=13, signal=5,
                 rsi_buy_max=UNSET, rsi_sell_min=UNSET, use_atr_stop=False,
                 name='MACD1'):
        """
        初始化 MACD 策略

        Args:
            fast (int): 快速 EMA 周期，默认 6
            slow (int): 慢速 EMA 周期，默认 13
            signal (int): 信号线 DEA 的 EMA 周期，默认 5
            rsi_buy_max (float): 买入附加条件 RSI < rsi_buy_max（None 表示不启用；
                未传参时读取 squant.toml 的 MIN_RSI_THRESHOLD）
            rsi_sell_min (float): 卖出附加条件 RSI > rsi_sell_min（None 表示不启用；
                未传参时读取 squant.toml 的 MAX_RSI_THRESHOLD）
            use_atr_stop (bool): 是否启用 ATR 动态止损（止损价=max(前一日止损价, close-N*ATR14)）
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.rsi_buy_max = (get_threshold('MIN_RSI_THRESHOLD')
                            if rsi_buy_max is UNSET else rsi_buy_max)
        self.rsi_sell_min = (get_threshold('MAX_RSI_THRESHOLD')
                             if rsi_sell_min is UNSET else rsi_sell_min)
        self.use_atr_stop = bool(use_atr_stop)
        self.atr_stop_multiplier = 2.0

    def generate_signals(self, df):
        """
        生成 MACD 交易信号

        计算 MACD 指标（DIF、DEA、MACD 柱），然后检测交叉信号：
            - DIF 上穿 DEA 且 RSI < MIN_RSI_THRESHOLD → 买入(1)
            - DIF 下穿 DEA 或 RSI > MAX_RSI_THRESHOLD，或买入后自最高收盘价
              触发 ATR 动态止损 → 卖出(-1)
            - 其余情况 → 持有(0)

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列

        Returns:
            pd.Series: 交易信号序列（1/0/-1），索引与 df 对齐
        """
        # 计算 MACD 指标
        macd_df = calc_macd(df, fast=self.fast, slow=self.slow, signal=self.signal)
        dif = macd_df['DIF']
        dea = macd_df['DEA']

        # RSI 过滤：优先使用 df 中已计算的 RSI14，否则现算
        rsi = df['RSI14'] if 'RSI14' in df.columns else calc_rsi(df, 14)

        # 金叉买入：DIF 上穿 DEA 且 RSI < rsi_buy_max（MIN_RSI_THRESHOLD）
        # 条件：上一期 DIF <= 上一期 DEA，当期 DIF > 当期 DEA，且当期 RSI 低于阈值
        golden_cross = (
            (dif.shift(1) <= dea.shift(1)) &
            (dif > dea) &
            (rsi < self.rsi_buy_max)
        )
        # 死叉卖出：DIF 下穿 DEA，或 RSI > rsi_sell_min（MAX_RSI_THRESHOLD）
        death_cross = ((dif.shift(1) >= dea.shift(1)) & (dif < dea)) | (rsi > self.rsi_sell_min)

        signals = pd.Series(0.0, index=df.index, dtype=float)
        signals[golden_cross] = self.signal_value(1)
        signals[death_cross] = self.signal_value(-1)
        return self._apply_position_risk(signals, df)

    def _apply_position_risk(self, signals, df):
        """跟踪持仓，追加 ATR 动态止损"""
        close = df['close']
        atr = df['ATR14'] if 'ATR14' in df.columns else calc_atr(df)
        market_states = df.attrs.get('market_states')
        if market_states is not None and len(market_states) > 0:
            try:
                market_states = market_states.reindex(df.index, method='ffill')
            except Exception:
                market_states = None
        out = signals.copy()
        in_pos = False
        stop_price = None
        for i in range(len(out)):
            c = float(close.iloc[i])
            state = market_states.iloc[i] if market_states is not None else None
            atr_mult = adjust_atr_stop_multiplier(self.atr_stop_multiplier, state)
            sig = float(out.iloc[i])
            if not in_pos:
                if sig > 0:
                    in_pos = True
                    stop_price = update_atr_trailing_stop(
                        c, atr.iloc[i], multiplier=atr_mult)
            else:
                if self.use_atr_stop:
                    stop_price = update_atr_trailing_stop(
                        c, atr.iloc[i], stop_price, multiplier=atr_mult)
                    if stop_price is not None and c <= stop_price:
                        out.iloc[i] = self.signal_value(-1)
                        in_pos = False
                        stop_price = None
                        continue
                if sig < 0:
                    in_pos = False
                    stop_price = None
        return out