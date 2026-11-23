#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SG2 策略（MA520）

基于 5 日均线与 20 日均线（可切换 EMA，默认 SMA），附加 RSI 过滤：
    - 买(+1)：快线上穿慢线（金叉）且当日收盘价 > 前一日收盘价 × 99% 且 RSI < MIN_RSI_THRESHOLD
    - 卖(-1)：快线下穿慢线（死叉）或 RSI > MAX_RSI_THRESHOLD 或触发 ATR 动态止损（止损价=max(前一日止损价, close-N*ATR14)）

阈值读取 squant.toml 的 thresholds 配置。

本策略只输出买入(1)/卖出(-1)/观望(0)。
"""

import pandas as pd

from core.strategy import Strategy
from core.indicators import (calc_atr, calc_ma, calc_ema, calc_rsi,
                             update_atr_trailing_stop,
                             adjust_atr_stop_multiplier)
from core.thresholds import UNSET, get_threshold


class SG2Strategy(Strategy):
    """MA520 策略：MA5/MA20 交叉（严格买入 + ATR 动态止损保护）

    Attributes:
        fast_period (int): 快线周期，默认 5
        slow_period (int): 慢线周期，默认 20
        use_atr_stop (bool): 是否启用 ATR 动态止损
        use_ema (bool): True 时用 EMA（指数移动平均），False 用 SMA，默认 False
    """

    # 由 generate_signals 内部同步 -sn 过滤（in_pos 仅对实际入场生效）
    integrates_buy_filter = True

    def __init__(self, fast_period=5, slow_period=20, use_ema=False,
                 rsi_buy_max=UNSET, rsi_sell_min=UNSET, use_atr_stop=False,
                 name='SG2'):
        """
        初始化 MA520 策略

        Args:
            fast_period (int): 快速均线周期，默认 5
            slow_period (int): 慢速均线周期，默认 20
            use_atr_stop (bool): 是否启用 ATR 动态止损（止损价=max(前一日止损价, close-N*ATR14)）
            use_ema (bool): True 用 EMA，False 用 SMA
            rsi_buy_max (float): 买入附加条件 RSI < rsi_buy_max（None 表示不启用；
                未传参时读取 squant.toml 的 MIN_RSI_THRESHOLD）
            rsi_sell_min (float): 卖出附加条件 RSI > rsi_sell_min（None 表示不启用；
                未传参时读取 squant.toml 的 MAX_RSI_THRESHOLD）
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.use_atr_stop = bool(use_atr_stop)
        self.atr_stop_multiplier = 2.0
        self.use_ema = use_ema
        self.rsi_buy_max = (get_threshold('MIN_RSI_THRESHOLD')
                            if rsi_buy_max is UNSET else rsi_buy_max)
        self.rsi_sell_min = (get_threshold('MAX_RSI_THRESHOLD')
                             if rsi_sell_min is UNSET else rsi_sell_min)

    def generate_signals(self, df, seqnum=0):
        """
        生成 MA520 交易信号

        - 买(+1)：快线上穿慢线（金叉）且 `close > close.shift(1) * 0.99`（当日跌幅不超过1%）
        - 卖(-1)：快线下穿慢线（死叉）或 RSI > MAX_RSI_THRESHOLD 或触发 ATR 动态止损
        - 其余 → 持有(0)

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列
            seqnum (int): 买入信号序号阈值（0=不过滤）；内部据此同步 in_pos，
                使被过滤的买入不会被视为已入场

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

        # 逐日跟踪持仓与 ATR 动态止损。
        # in_pos 只在买入信号实际生效（通过序号过滤）后置为 True，
        # 避免被过滤的买入导致后续买入被抑制或产生无持仓的孤立卖出。
        atr = df['ATR14'] if 'ATR14' in df.columns else calc_atr(df)
        market_states = df.attrs.get('market_states')
        if market_states is not None and len(market_states) > 0:
            try:
                market_states = market_states.reindex(df.index, method='ffill')
            except Exception:
                market_states = None
        seq_target = seqnum if seqnum and seqnum > 0 else 1
        buy_count = 0
        in_pos = False
        stop_price = None
        for i in range(len(df)):
            c = float(close.iloc[i])
            state = market_states.iloc[i] if market_states is not None else None
            atr_mult = adjust_atr_stop_multiplier(self.atr_stop_multiplier, state)
            rsi_sell = (self.rsi_sell_min is not None
                        and rsi is not None
                        and bool(rsi.iloc[i] > self.rsi_sell_min))
            sell_signal = bool(death_cross.iloc[i]) or rsi_sell
            if not in_pos:
                # 空仓期间的卖出信号同样重置买入计数
                if sell_signal:
                    buy_count = 0
                if bool(golden_cross.iloc[i]):
                    if seqnum and seqnum > 0:
                        # 序号按原始买入信号计数（包含被过滤的买入）
                        buy_count += 1
                        allow = (buy_count == seq_target)
                    else:
                        allow = True
                    if allow:
                        signals.iloc[i] = self.signal_value(1)      # 买入
                        in_pos = True
                        stop_price = update_atr_trailing_stop(
                            c, atr.iloc[i], multiplier=atr_mult)
                        buy_count = 0
            else:
                if self.use_atr_stop:
                    stop_price = update_atr_trailing_stop(
                        c, atr.iloc[i], stop_price, multiplier=atr_mult)
                    if stop_price is not None and c <= stop_price:
                        signals.iloc[i] = self.signal_value(-1)
                        in_pos = False
                        stop_price = None
                        buy_count = 0
                        continue
                if sell_signal:
                    signals.iloc[i] = self.signal_value(-1)     # 死叉或 RSI 超买
                    in_pos = False
                    stop_price = None
                    buy_count = 0

        return signals
