#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOMA 策略：BOLL 与 MACD 组合信号（当前为可继续迭代的版本）。

买入 = 买入1 OR 买入2
    买入1 = BOLL 下轨反弹 AND (MACD红柱 OR 绿柱变短)
    买入2 = MACD金叉 AND 收盘价在 BOLL 中线之下

卖出 = 卖出1 OR 卖出2
    卖出1 = BOLL 上轨回落 AND (DIF拐头向下 OR MACD红柱连续2根缩短)
    卖出2 = MACD死叉 AND 收盘价跌破 BOLL 中线
"""

import numpy as np
import pandas as pd

from core.strategy import Strategy
from core.indicators import (calc_adx, calc_adxr, calc_atr, calc_bollinger,
                             calc_macd, calc_rsi, update_atr_trailing_stop,
                             adjust_atr_stop_multiplier)

_ACTIVE_CRITERIA = None


def set_active_criteria(criteria) -> None:
    """设置 BOMA 生效条件；None 表示全部生效。"""
    global _ACTIVE_CRITERIA
    _ACTIVE_CRITERIA = set(criteria) if criteria else None


def _enabled(name: str) -> bool:
    return _ACTIVE_CRITERIA is None or name in _ACTIVE_CRITERIA


class BOMAStrategy(Strategy):
    """BOLL 与 MACD 组合策略。"""

    integrates_buy_filter = True
    supports_seqnum = True

    def __init__(self, boll_period=20, boll_std=2, macd_fast=12,
                 macd_slow=26, macd_signal=9, name='BOMA',
                 use_atr_stop=False):
        super().__init__(name=name)
        self.use_atr_stop = bool(use_atr_stop)
        self.atr_stop_multiplier = 2.0
        self.boll_period = boll_period
        self.boll_std = boll_std
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal

    def latest_signal_reasons(self, df, signals):
        """返回最新信号实际触发的条件名列表（用于 analyze/scan 报告）。"""
        if df is None or len(df) == 0 or signals is None or len(signals) == 0:
            return []
        try:
            pos = len(df) - 1
            value = float(signals.iloc[-1])
            if value == 0:
                return []
            close = pd.to_numeric(df['close'], errors='coerce')
            c = float(close.iloc[pos])
            bb = calc_bollinger(df, period=self.boll_period, std=self.boll_std)
            upper, middle, lower = bb['UPPER'], bb['MIDDLE'], bb['LOWER']
            md = calc_macd(df, fast=self.macd_fast, slow=self.macd_slow,
                           signal=self.macd_signal)
            dif, dea, bar = md['DIF'], md['DEA'], md['MACD']
            rsi6 = calc_rsi(df, 6)
            rsi12 = calc_rsi(df, 12)
            ma5 = df['MA5'] if 'MA5' in df.columns else close.rolling(5).mean()
            rsi_order = rsi6.iloc[pos] > rsi12.iloc[pos]
            price_above_ma5 = c > float(ma5.iloc[pos])

            reasons = []
            if value > 0:
                lower_rebound = (close.iloc[pos - 1] <= lower.iloc[pos - 1]
                                 and c > float(lower.iloc[pos]))
                red_bar = bar.iloc[pos] > 0
                green_short_1 = (bar.iloc[pos] < 0 and bar.iloc[pos - 1] < 0
                                 and bar.iloc[pos] > bar.iloc[pos - 1])
                bar_ok = red_bar or green_short_1
                golden = (dif.iloc[pos - 1] <= dea.iloc[pos - 1]
                          and dif.iloc[pos] > dea.iloc[pos])
                adx = (df['ADX14'] if 'ADX14' in df.columns
                       else calc_adx(df)['ADX'])
                pdi = df['PLUS_DI'] if 'PLUS_DI' in df.columns else calc_adx(df)['PLUS_DI']
                mdi = df['MINUS_DI'] if 'MINUS_DI' in df.columns else calc_adx(df)['MINUS_DI']
                adxr = calc_adxr(df)
                cross_up = (pdi.iloc[pos - 1] <= mdi.iloc[pos - 1]
                            and pdi.iloc[pos] > mdi.iloc[pos])
                if _enabled('b1') and lower_rebound and bar_ok and rsi_order and price_above_ma5:
                    reasons.append('买入信号BOLL')
                if (_enabled('b2') and golden
                        and c < float(middle.iloc[pos])
                        and rsi_order and price_above_ma5):
                    reasons.append('买入信号MACD')
                if (_enabled('b3') and adx.iloc[pos] > 25 and cross_up
                        and adxr.iloc[pos] > adx.iloc[pos]
                        and price_above_ma5):
                    reasons.append('买入信号ADX')
            else:
                upper_pullback = (close.iloc[pos - 1] >= upper.iloc[pos - 1]
                                  and c < float(upper.iloc[pos]))
                dif_turn = (dif.iloc[pos - 1] > dif.iloc[pos - 2]
                            and dif.iloc[pos] < dif.iloc[pos - 1])
                red_shrink = (bar.iloc[pos] > 0 and bar.iloc[pos - 1] > 0
                              and bar.iloc[pos] < bar.iloc[pos - 1])
                if _enabled('s1') and upper_pullback and (dif_turn or red_shrink):
                    reasons.append('卖出信号BOLL')
                adx = (df['ADX14'] if 'ADX14' in df.columns
                       else calc_adx(df)['ADX'])
                pdi = df['PLUS_DI'] if 'PLUS_DI' in df.columns else calc_adx(df)['PLUS_DI']
                mdi = df['MINUS_DI'] if 'MINUS_DI' in df.columns else calc_adx(df)['MINUS_DI']
                death = (dif.iloc[pos - 1] >= dea.iloc[pos - 1]
                         and dif.iloc[pos] < dea.iloc[pos])
                di_bearish = (pdi.iloc[pos] < pdi.iloc[pos - 1]
                              and mdi.iloc[pos] > mdi.iloc[pos - 1]
                              and adx.iloc[pos] >= 20)
                if (_enabled('s2') and death
                        and c < float(middle.iloc[pos]) and di_bearish):
                    reasons.append('卖出信号MACD')
                if self.use_atr_stop and _enabled('s3'):
                    last_sell = None
                    for j in range(pos - 1, -1, -1):
                        if float(signals.iloc[j]) < 0:
                            last_sell = j
                            break
                    start = 0 if last_sell is None else last_sell + 1
                    entry = None
                    for j in range(start, pos):
                        if float(signals.iloc[j]) > 0:
                            entry = j
                            break
                    if entry is not None:
                        atr = (df['ATR14'] if 'ATR14' in df.columns
                               else calc_atr(df))
                        states = df.attrs.get('market_states')
                        if states is not None and len(states) > 0:
                            try:
                                states = states.reindex(df.index, method='ffill')
                            except Exception:
                                states = None
                        stop_price = None
                        for j in range(entry, pos + 1):
                            st = states.iloc[j] if states is not None else None
                            mult = adjust_atr_stop_multiplier(
                                self.atr_stop_multiplier, st)
                            stop_price = update_atr_trailing_stop(
                                close.iloc[j], atr.iloc[j], stop_price,
                                multiplier=mult)
                        if stop_price is not None and c < stop_price:
                            reasons.append('卖出信号ATR')
            return reasons
        except Exception:
            return []

    def generate_signals(self, df, seqnum=0):
        """生成 BOMA 买入/卖出信号。"""
        close = df['close']
        bb = calc_bollinger(df, period=self.boll_period, std=self.boll_std)
        upper, middle, lower = bb['UPPER'], bb['MIDDLE'], bb['LOWER']

        macd_df = calc_macd(df, fast=self.macd_fast, slow=self.macd_slow,
                            signal=self.macd_signal)
        dif, dea, bar = macd_df['DIF'], macd_df['DEA'], macd_df['MACD']
        rsi6 = calc_rsi(df, 6)
        rsi12 = calc_rsi(df, 12)
        adx = df['ADX14'] if 'ADX14' in df.columns else calc_adx(df)['ADX']
        adxr = calc_adxr(df)
        plus_di = df['PLUS_DI'] if 'PLUS_DI' in df.columns else calc_adx(df)['PLUS_DI']
        minus_di = df['MINUS_DI'] if 'MINUS_DI' in df.columns else calc_adx(df)['MINUS_DI']
        rsi_order = rsi6 > rsi12
        ma5 = df['MA5'] if 'MA5' in df.columns else close.rolling(5).mean()
        price_above_ma5 = close > ma5

        lower_rebound = ((close.shift(1) <= lower.shift(1))
                         & (close > lower))
        upper_pullback = ((close.shift(1) >= upper.shift(1))
                          & (close < upper))

        red_bar = bar > 0
        green_bar = bar < 0
        green_shortening_1 = (green_bar & (bar.shift(1) < 0)
                              & (bar > bar.shift(1)))
        bar_ok = red_bar | green_shortening_1

        golden_cross = ((dif.shift(1) <= dea.shift(1)) & (dif > dea))
        death_cross = ((dif.shift(1) >= dea.shift(1)) & (dif < dea))
        di_cross_up = ((plus_di.shift(1) <= minus_di.shift(1))
                       & (plus_di > minus_di))
        di_cross_down = ((plus_di.shift(1) >= minus_di.shift(1))
                         & (plus_di < minus_di))

        dif_turn_down = ((dif.shift(1) > dif.shift(2))
                         & (dif < dif.shift(1)))
        red_shrink_1 = (red_bar & (bar.shift(1) > 0)
                        & (bar < bar.shift(1)))

        buy1 = (lower_rebound & bar_ok & rsi_order & price_above_ma5
                if _enabled('b1') else pd.Series(False, index=df.index))
        buy2 = ((golden_cross & (close < middle) & rsi_order
                 & price_above_ma5) if _enabled('b2')
                else pd.Series(False, index=df.index))
        buy3 = (((adx > 25) & di_cross_up & (adxr > adx)
                 & price_above_ma5) if _enabled('b3')
                else pd.Series(False, index=df.index))
        buy_signal = buy1 | buy2 | buy3

        sell1 = (upper_pullback & (dif_turn_down | red_shrink_1)
                 if _enabled('s1') else pd.Series(False, index=df.index))
        di_bearish = ((plus_di < plus_di.shift(1))
                      & (minus_di > minus_di.shift(1))
                      & (adx >= 20))
        sell2 = ((death_cross & (close < middle) & di_bearish)
                 if _enabled('s2') else pd.Series(False, index=df.index))
        sell_signal = sell1 | sell2

        market_states = df.attrs.get('market_states')
        if market_states is not None and len(market_states) > 0:
            try:
                market_states = market_states.reindex(df.index, method='ffill')
            except Exception:
                market_states = None
        else:
            market_states = None
        atr = df['ATR14'] if 'ATR14' in df.columns else calc_atr(df)

        signals = pd.Series(0.0, index=df.index, dtype=float)
        seq_target = seqnum if seqnum and seqnum > 0 else 1
        buy_count = 0
        in_pos = False
        stop_price = None

        for i in range(len(df)):
            state = (market_states.iloc[i]
                     if market_states is not None else None)
            atr_mult = adjust_atr_stop_multiplier(
                self.atr_stop_multiplier, state)
            if not in_pos:
                if bool(sell_signal.iloc[i]):
                    buy_count = 0
                if bool(buy_signal.iloc[i]):
                    if seqnum and seqnum > 0:
                        buy_count += 1
                        allow = (buy_count == seq_target)
                    else:
                        allow = True
                    if allow:
                        signals.iloc[i] = self.signal_value(1)
                        in_pos = True
                        stop_price = update_atr_trailing_stop(
                            float(close.iloc[i]), atr.iloc[i],
                            multiplier=atr_mult)
                        buy_count = 0
            else:
                stop_price = update_atr_trailing_stop(
                    float(close.iloc[i]), atr.iloc[i], stop_price,
                    multiplier=atr_mult)
                sell3 = (self.use_atr_stop and _enabled('s3')
                         and stop_price is not None
                         and float(close.iloc[i]) < stop_price)
                if bool(sell_signal.iloc[i]) or sell3:
                    signals.iloc[i] = self.signal_value(-1)
                    in_pos = False
                    stop_price = None
                    buy_count = 0

        return signals
