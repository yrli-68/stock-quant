#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MACD 策略 (MACD Strategy)

基于 MACD 指标的金叉死叉来生成交易信号：
    - DIF 上穿 DEA（金叉）且 RSI < MIN_RSI_THRESHOLD → 买入信号
    - DIF 下穿 DEA（死叉）或 RSI > MAX_RSI_THRESHOLD，或买入后自最高收盘价回撤 ≥ MAX_DRAWDWON_THRESHOLD → 卖出信号

MACD 是一种趋势跟踪动量指标，通过快慢 EMA 的差值来反映
价格趋势的强度和方向变化。
"""

import pandas as pd
import numpy as np

from core.strategy import Strategy
from core.indicators import calc_macd, calc_ma, calc_rsi
from core.thresholds import UNSET, get_threshold, get_max_drawdown_threshold


class MACDStrategy(Strategy):
    """
    MACD 交易策略

    利用 MACD 指标的 DIF 线与 DEA 线的交叉来生成买卖信号。
    当 DIF 从下方上穿 DEA 且 MACD 柱状线为正时买入；
    当 DIF 从上方下穿 DEA 时卖出。

    Attributes:
        fast (int): 快速 EMA 周期，默认 12
        slow (int): 慢速 EMA 周期，默认 26
        signal (int): 信号线 DEA 周期，默认 9
        ma_period (int): 增强过滤用趋势快速均线周期，默认 20
        ma_slow (int): 增强过滤用趋势慢速均线周期，默认 60
        max_drawdown (float): 买入后自最高收盘价的最大回撤止损阈值，默认 0.15
    """

    integrates_buy_filter = True

    def __init__(self, fast=12, slow=26, signal=9, ma_period=20, ma_slow=60,
                 rsi_buy_max=UNSET, rsi_sell_min=UNSET, max_drawdown=UNSET,
                 name='MACD'):
        """
        初始化 MACD 策略

        Args:
            fast (int): 快速 EMA 周期，默认 12
            slow (int): 慢速 EMA 周期，默认 26
            signal (int): 信号线 DEA 的 EMA 周期，默认 9
            ma_period (int): 增强过滤用趋势快速均线周期，默认 20
            ma_slow (int): 增强过滤用趋势慢速均线周期，默认 60
            rsi_buy_max (float): 买入附加条件 RSI < rsi_buy_max（None 表示不启用；
                未传参时读取 stock-quant.json 的 MIN_RSI_THRESHOLD）
            rsi_sell_min (float): 卖出附加条件 RSI > rsi_sell_min（None 表示不启用；
                未传参时读取 stock-quant.json 的 MAX_RSI_THRESHOLD）
            max_drawdown (float): 最大回撤止损阈值（0.15 表示 15%；None 表示不启用；
                未传参时读取 stock-quant.json 的 MAX_DRAWDWON_THRESHOLD）
            name (str): 策略名称
        """
        super().__init__(name=name)
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.ma_period = ma_period
        self.ma_slow = ma_slow
        self.rsi_buy_max = (get_threshold('MIN_RSI_THRESHOLD')
                            if rsi_buy_max is UNSET else rsi_buy_max)
        self.rsi_sell_min = (get_threshold('MAX_RSI_THRESHOLD')
                             if rsi_sell_min is UNSET else rsi_sell_min)
        self.max_drawdown = (get_max_drawdown_threshold()
                             if max_drawdown is UNSET else max_drawdown)

    def generate_signals(self, df, seqnum=0, max_buy_gain=None, sar_ok=None,
                         index_ok=None):
        """
        生成 MACD 交易信号

        计算 MACD 指标（DIF、DEA、MACD 柱），然后检测交叉信号：
            - DIF 上穿 DEA 且 RSI < MIN_RSI_THRESHOLD → 买入(1)
            - DIF 下穿 DEA 或 RSI > MAX_RSI_THRESHOLD，或买入后自最高收盘价
              回撤 ≥ MAX_DRAWDWON_THRESHOLD → 卖出(-1)
            - 其余情况 → 持有(0)

        增强级别 1（enhance>=1，`-g1 macd`）：金叉分级 + 金叉/死叉次日延续确认
            - 金叉日（满足 MA60/放量/次日确认等任一条件）→ 买入(1)
            - 死叉日（满足 MA60/放量/次日确认等任一条件）→ 卖出(-1)
        （弱买/强买已合并为「买」1，弱卖/强卖已合并为「卖」-1）

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列（增强级还需 'volume' 列）
            seqnum (int): 兼容接口，MACD 不使用买入序号过滤
            max_buy_gain (float): 当日涨幅达到该值时取消买入（None=不限制）
            sar_ok (pd.Series): 买入所需的 SAR 条件（True=允许买入；None=不检查）
            index_ok (pd.Series): 买入所需的上证指数条件（True=允许买入；None=不检查）

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

        # 基础版（enhance==0）→ 简单金叉/死叉信号
        if self.enhance < 1:
            signals = pd.Series(0.0, index=df.index, dtype=float)
            signals[golden_cross] = self.signal_value(1)     # +1
            signals[death_cross] = self.signal_value(-1)     # -1
            return self._apply_position_risk(signals, df, max_buy_gain, sar_ok,
                                             index_ok)

        # ===== 增强级别 1：金叉分级 + 逐日延续确认（逐日顺序计算）=====
        close = df['close']
        ma_slow = calc_ma(df, self.ma_slow)
        if 'volume' in df.columns:
            vol_ma20 = df['volume'].rolling(20).mean()
            vol_ok = df['volume'] > vol_ma20
        else:
            vol_ok = pd.Series(False, index=df.index)
        ret = close.pct_change()  # (close-close.shift)/close.shift

        up_ma = close > ma_slow          # 买入确认：站上 MA60
        down_ma = close < ma_slow        # 卖出确认：跌破 MA60

        # 前一天是否为金叉/死叉（次日延续确认的触发条件）
        golden_yest = golden_cross.shift(1).fillna(False)
        death_yest = death_cross.shift(1).fillna(False)

        n = len(df)
        signals = pd.Series(0.0, index=df.index, dtype=float)
        for i in range(n):
            if golden_cross.iloc[i]:
                signals.iloc[i] = 1.0                       # 买入
            elif death_cross.iloc[i]:
                signals.iloc[i] = -1.0                      # 卖出
            elif golden_yest.iloc[i]:
                # 金叉次日延续确认 → 买入
                prev = signals.iloc[i - 1] if i > 0 else 0.0
                small_chg = ret.iloc[i] < 0.03
                if small_chg and ((up_ma.iloc[i] and bool(vol_ok.iloc[i])) or prev >= 1.0):
                    signals.iloc[i] = 1.0
            elif death_yest.iloc[i]:
                # 死叉次日延续确认 → 卖出
                prev = signals.iloc[i - 1] if i > 0 else 0.0
                small_chg = ret.iloc[i] < 0.03
                if small_chg and ((down_ma.iloc[i] and bool(vol_ok.iloc[i])) or prev <= -1.0):
                    signals.iloc[i] = -1.0

        return self._apply_position_risk(signals, df, max_buy_gain, sar_ok,
                                             index_ok)

    def _apply_position_risk(self, signals, df, max_buy_gain=None, sar_ok=None,
                             index_ok=None):
        """跟踪买入后的最高收盘价，追加回撤止损并同步涨幅过滤"""
        close = df['close']
        gains = close.pct_change() if max_buy_gain is not None else None
        out = signals.copy()
        in_pos = False
        peak = 0.0
        for i in range(len(out)):
            c = float(close.iloc[i])
            sig = float(out.iloc[i])
            if not in_pos:
                if sig > 0:
                    gain_ok = True
                    if gains is not None:
                        g = float(gains.iloc[i])
                        gain_ok = (not np.isnan(g)) and g < max_buy_gain
                    if (gain_ok and (sar_ok is None or bool(sar_ok.iloc[i]))
                            and (index_ok is None or bool(index_ok.iloc[i]))):
                        in_pos = True
                        peak = c
                    else:
                        out.iloc[i] = 0.0
            else:
                if c > peak:
                    peak = c
                dd = (peak - c) / peak if peak > 0 else 0.0
                if self.max_drawdown is not None and dd >= self.max_drawdown:
                    out.iloc[i] = self.signal_value(-1)
                    in_pos = False
                elif sig < 0:
                    in_pos = False
                elif sig > 0 and gains is not None:
                    g = float(gains.iloc[i])
                    if np.isnan(g) or g >= max_buy_gain:
                        out.iloc[i] = 0.0
        return out