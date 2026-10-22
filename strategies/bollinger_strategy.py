#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
布林带策略 (Bollinger Bands Strategy)

基于布林带指标的价格触及上下轨来生成交易信号：
    - 价格触及下轨后反弹，且 RSI < MIN_RSI_THRESHOLD，且（可选）EMA60 < close、ADX > 20 → 买入信号（带宽过滤已停用）
    - 价格触及上轨后回落，或 RSI > MAX_RSI_THRESHOLD，或买入后自最高收盘价回撤≥MAX_DRAWDWON_THRESHOLD → 卖出信号

布林带由中轨（均线）和上下轨（均线 ± K倍标准差）组成，
用于衡量价格的相对高低和波动性。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_bollinger, calc_rsi, calc_ema, calc_adx
from core.thresholds import (UNSET, get_threshold, get_max_drawdown_threshold,
                             get_ema60_check, get_adx_check)


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
        max_drawdown (float): 买入后自最高收盘价的最大回撤止损阈值，默认 0.15
    """

    # 由 generate_signals 内部同步 -sn 与涨幅过滤（in_pos 仅对实际入场生效）
    integrates_buy_filter = True
    # -sn 仅对 BOLL 生效
    supports_seqnum = True

    def __init__(self, period=20, std=2, name='Bollinger', bbw_lookback=10,
                 max_drawdown=UNSET):
        """
        初始化布林带策略

        Args:
            period (int): 布林带中轨（均线）周期，默认 20
            std (float): 上下轨的标准差倍数，默认 2
            name (str): 策略名称
            bbw_lookback (int): 布林带宽度过滤的回看窗口，默认 10（当前过滤已停用）
            max_drawdown (float): 最大回撤止损阈值（0.15 表示 15%；None 表示不启用；
                未传参时读取 pyproject.toml 的 MAX_DRAWDWON_THRESHOLD）
        """
        super().__init__(name=name)
        self.period = period
        self.std = std
        self.bbw_lookback = bbw_lookback
        self.max_drawdown = (get_max_drawdown_threshold()
                             if max_drawdown is UNSET else max_drawdown)

    def generate_signals(self, df, seqnum=0, max_buy_gain=None, sar_ok=None,
                         index_ok=None):
        """
        生成布林带交易信号

        计算布林带指标，然后检测价格与上下轨的交互：
            - 买入：价格触及下轨后反弹，且 RSI < MIN_RSI_THRESHOLD；
              EMA60_CHECK=1 时还要求 EMA60 < close，ADX_CHECK=1 时还要求 ADX > 20 → 买入(1)
            - 卖出：价格触及上轨后回落，或 RSI > MAX_RSI_THRESHOLD，
              或买入后自最高收盘价回撤 ≥ MAX_DRAWDWON_THRESHOLD → 卖出(-1)
            - 其余情况 → 持有(0)

        注：原布林带宽度过滤（非挤压 / 非极端宽）经实测效果不佳，已停用；
        bbw_lookback / BOLL_SQUEEZE_SCALE / BOLL_EXTRAWIDE_SCALE 暂不生效。
        最大回撤止损（回撤 ≥ max_drawdown）不受带宽过滤影响。

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
            if not in_pos:
                # 空仓期间的卖出信号同样重置买入计数
                if bool(sell_signal.iloc[i]):
                    buy_count = 0
                if bool(buy_signal.iloc[i]):
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
                             and (ema60_ok is None or bool(ema60_ok.iloc[i]))
                             and (adx_ok is None or bool(adx_ok.iloc[i]))
                             and (index_ok is None or bool(index_ok.iloc[i])))
                    if allow:
                        signals.iloc[i] = self.signal_value(1)
                        in_pos = True
                        peak = c
                        buy_count = 0
            else:
                if c > peak:
                    peak = c
                dd = (peak - c) / peak if peak > 0 else 0.0
                if self.max_drawdown is not None and dd >= self.max_drawdown:
                    signals.iloc[i] = self.signal_value(-1)       # 回撤止损
                    in_pos = False
                    buy_count = 0
                elif bool(sell_signal.iloc[i]):
                    signals.iloc[i] = self.signal_value(-1)
                    in_pos = False
                    buy_count = 0

        return signals