#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
布林带策略 (Bollinger Bands Strategy)

基于布林带指标的价格触及上下轨来生成交易信号：
    - 价格触及下轨后反弹，且 RSI < MIN_RSI_THRESHOLD，且（可选）EMA60 < close、ADX > 20 → 买入信号（带宽过滤已停用）
    - 价格触及上轨后回落，或 RSI > MAX_RSI_THRESHOLD，或触发 ATR 动态止损（止损价=max(前一日止损价, close-N*ATR14)） → 卖出信号

布林带由中轨（均线）和上下轨（均线 ± K倍标准差）组成，
用于衡量价格的相对高低和波动性。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import (calc_atr, calc_bollinger, calc_rsi, calc_ema,
                             calc_adx, update_atr_trailing_stop,
                             adjust_atr_stop_multiplier)
from core.thresholds import get_threshold, get_ema60_check, get_adx_check


class BollingerStrategy(Strategy):
    """
    布林带突破策略

    利用布林带的上下轨作为支撑和阻力位来生成反转交易信号。
    当价格触及下轨（超卖）后反弹且 RSI < MIN_RSI_THRESHOLD 时买入；当价格触及上轨（超买）
    后回落或 RSI > MAX_RSI_THRESHOLD 时卖出。

    Attributes:
        period (int): 布林带中轨周期，默认 20
        std (float): 标准差倍数，默认 2
        bbw_lookback (int): 布林带宽度过滤的统计回看窗口（默认 10；当前过滤已停用）
        use_atr_stop (bool): 是否启用 ATR 动态止损
    """

    # 由 generate_signals 内部同步 -sn 过滤（in_pos 仅对实际入场生效）
    integrates_buy_filter = True
    # -sn 仅对 BOLL 生效
    supports_seqnum = True

    def __init__(self, period=20, std=2, name='Bollinger', bbw_lookback=10,
                 use_atr_stop=False):
        """
        初始化布林带策略

        Args:
            period (int): 布林带中轨（均线）周期，默认 20
            std (float): 上下轨的标准差倍数，默认 2
            name (str): 策略名称
            bbw_lookback (int): 布林带宽度过滤的回看窗口，默认 10（当前过滤已停用）
            use_atr_stop (bool): 是否启用 ATR 动态止损（止损价=max(前一日止损价, close-N*ATR14)）
        """
        super().__init__(name=name)
        self.period = period
        self.std = std
        self.bbw_lookback = bbw_lookback
        self.use_atr_stop = bool(use_atr_stop)
        self.atr_stop_multiplier = 2.0

    def generate_signals(self, df, seqnum=0):
        """
        生成布林带交易信号

        计算布林带指标，然后检测价格与上下轨的交互：
            - 买入：价格触及下轨后反弹，且 RSI < MIN_RSI_THRESHOLD；
              EMA60_CHECK=1 时还要求 EMA60 < close，ADX_CHECK=1 时还要求 ADX > 20 → 买入(1)
            - 卖出：价格触及上轨后回落，或 RSI > MAX_RSI_THRESHOLD，
              或触发 ATR 动态止损 → 卖出(-1)
            - 其余情况 → 持有(0)

        注：原布林带宽度过滤（非挤压 / 非极端宽）经实测效果不佳，已停用；
        bbw_lookback / BOLL_SQUEEZE_SCALE / BOLL_EXTRAWIDE_SCALE 暂不生效。
        ATR 动态止损不受带宽过滤影响。

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列
            seqnum (int): 买入信号序号阈值（0=不过滤）；内部据此同步 in_pos，
                使被过滤的买入不会被视为已入场

        Returns:
            pd.Series: 交易信号序列，索引与 df 对齐
        """
        # 计算布林带指标
        bb = calc_bollinger(df, period=self.period, std=self.std)
        upper = bb['UPPER']
        lower = bb['LOWER']
        middle = bb['MIDDLE']
        close = df['close']
        # RSI 过滤：优先使用已计算的 RSI14，否则现算
        rsi = df['RSI14'] if 'RSI14' in df.columns else calc_rsi(df, 14)

        # 初始化信号序列
        signals = pd.Series(0.0, index=df.index, dtype=float)

        # 下轨反弹买入：上一期收盘价 <= 下轨，当期收盘价 > 下轨，且 RSI < MIN_RSI_THRESHOLD
        # 表示价格触及下轨支撑后开始反弹，且未处于过热状态
        rsi_buy_max = get_threshold('MIN_RSI_THRESHOLD')
        rsi_sell_min = get_threshold('MAX_RSI_THRESHOLD')

        # 布林带宽度过滤（已停用，保留计算以便恢复）
        bbw = (upper - lower) / middle
        bbw_pctl = bbw.rolling(window=self.bbw_lookback).quantile(0.5)
        squeeze_scale = get_threshold('BOLL_SQUEEZE_SCALE')
        extrawide_scale = get_threshold('BOLL_EXTRAWIDE_SCALE')
        is_squeeze = bbw <= bbw_pctl * squeeze_scale
        is_extraWide = bbw >= bbw_pctl * extrawide_scale

        # 买入：下轨反弹 + RSI 未过热（带宽过滤已停用）
        buy_signal = (((close.shift(1) <= lower.shift(1)) & (close > lower))
                      & (rsi < rsi_buy_max))
        #              & ~(is_squeeze | is_extraWide))  # 无效果，手工屏蔽

        # 卖出：上轨回落 或 RSI > MAX_RSI_THRESHOLD
        sell_signal = (((close.shift(1) >= upper.shift(1)) & (close < upper))
                       | (rsi > rsi_sell_min))

        # EMA60 过滤（EMA60_CHECK=1 时要求 EMA60 < close 才允许买入）
        ema60_ok = None
        if get_ema60_check():
            ema60 = df['EMA60'] if 'EMA60' in df.columns else calc_ema(df, 60)
            ema60_ok = ema60 < close

        # ADX 过滤（ADX_CHECK=1 时要求 ADX > 20 才允许买入）
        adx_ok = None
        if get_adx_check():
            adx = df['ADX14'] if 'ADX14' in df.columns else calc_adx(df)['ADX']
            adx_ok = adx > 20

        # 逐日跟踪持仓与 ATR 动态止损。
        # in_pos 只在买入信号实际生效（通过序号与指数过滤）后置为 True，
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
            if not in_pos:
                # 空仓期间的卖出信号同样重置买入计数
                if bool(sell_signal.iloc[i]):
                    buy_count = 0
                if bool(buy_signal.iloc[i]):
                    if seqnum and seqnum > 0:
                        # 序号按原始买入信号计数（包含被过滤的买入）
                        buy_count += 1
                        allow = (buy_count == seq_target)
                    else:
                        allow = True
                    allow = (allow
                             and (ema60_ok is None or bool(ema60_ok.iloc[i]))
                             and (adx_ok is None or bool(adx_ok.iloc[i])))
                    if allow:
                        signals.iloc[i] = self.signal_value(1)
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
                if bool(sell_signal.iloc[i]):
                    signals.iloc[i] = self.signal_value(-1)
                    in_pos = False
                    stop_price = None
                    buy_count = 0

        return signals