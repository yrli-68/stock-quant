#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOMA 策略：BOLL 与 MACD 组合信号（当前为可继续迭代的版本）。

买入 = 买入1 OR 买入2
    买入1 = BOLL 下轨反弹 AND (MACD红柱 OR 绿柱变短)
    买入2 = 买入信号MACD1 OR 买入信号MACD2
        买入信号MACD1 = MACD金叉 AND DIF>=0 AND 大盘涨势 AND ADX>20 AND +DI>-DI
        买入信号MACD2 = MACD金叉 AND DIF<0 AND RSI14<30 AND BIAS负乖离 AND ADX>20

卖出 = 卖出1 OR 卖出2
    卖出1 = BOLL 上轨回落 AND (DIF拐头向下 OR MACD红柱连续2根缩短)
    卖出2 = 卖出信号MACD1 OR 卖出信号MACD2
        卖出信号MACD1 = 个股趋势∈{涨势,震荡} AND MACD死叉 AND close<BOLL_MIDDLE
                        AND +DI向下 AND -DI向上 AND ADX14>=20
        卖出信号MACD2 = 个股趋势=跌势 AND MACD死叉
"""

import numpy as np
import pandas as pd

from core.strategy import Strategy
from core.indicators import (calc_adx, calc_adxr, calc_atr, calc_bollinger,
                             calc_macd, calc_rsi, update_atr_trailing_stop,
                             adjust_atr_stop_multiplier)

_SIGNAL_SWITCHES = None

# squant.toml 中 [tool.stock-quant.boma] 的开关名称
SIGNAL_BOLL_BUY = '买入信号BOLL'
SIGNAL_MACD1_BUY = '买入信号MACD1'
SIGNAL_MACD2_BUY = '买入信号MACD2'
SIGNAL_ADX_BUY = '买入信号ADX'
SIGNAL_BOLL1_SELL = '卖出信号BOLL1'
SIGNAL_BOLL2_SELL = '卖出信号BOLL2'
SIGNAL_MACD_SELL = '卖出信号MACD'  # 兼容旧配置：作为 MACD1/MACD2 的总开关
SIGNAL_MACD1_SELL = '卖出信号MACD1'
SIGNAL_MACD2_SELL = '卖出信号MACD2'
SIGNAL_ATR_SELL = '卖出信号ATR'


def set_signal_switches(switches) -> None:
    """设置各买卖信号的独立开关（来自 squant.toml 的 [tool.stock-quant.boma]）。"""
    global _SIGNAL_SWITCHES
    _SIGNAL_SWITCHES = dict(switches) if switches else None


def _load_signal_switches():
    global _SIGNAL_SWITCHES
    if _SIGNAL_SWITCHES is None:
        try:
            from core.config import load_config
            _SIGNAL_SWITCHES = dict((load_config() or {}).get('boma') or {})
        except Exception:
            _SIGNAL_SWITCHES = {}
    return _SIGNAL_SWITCHES


def _sw_enabled(name: str) -> bool:
    """配置开关：缺省为启用；0/false/off/空串表示关闭。"""
    switches = _load_signal_switches()
    if not switches or name not in switches:
        return True
    value = switches.get(name)
    if isinstance(value, bool):
        return value
    try:
        return float(value) != 0
    except (TypeError, ValueError):
        return str(value).strip().lower() not in ('0', 'false', 'off', 'no', '')



def _get_stock_states(df):
    """个股趋势状态序列（优先复用 df.attrs 缓存）。"""
    states = df.attrs.get('stock_trend_states')
    if states is not None and len(states) > 0:
        try:
            return states.reindex(df.index, method='ffill')
        except Exception:
            return None
    try:
        from core.market_trend import classify_stock_trend_series
        states = classify_stock_trend_series(df)
        df.attrs['stock_trend_states'] = states
        return states.reindex(df.index, method='ffill')
    except Exception:
        return None


class BOMAStrategy(Strategy):
    """BOLL 与 MACD 组合策略。"""

    integrates_buy_filter = True

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
            rsi14 = calc_rsi(df, 14)
            ma20 = df['MA20'] if 'MA20' in df.columns else close.rolling(20).mean()
            ma20_val = float(ma20.iloc[pos])
            bias_negative = ((c - ma20_val) / ma20_val < 0) if ma20_val != 0 else False
            market_states = df.attrs.get('market_states')
            if market_states is not None and len(market_states) > 0:
                try:
                    market_states = market_states.reindex(df.index, method='ffill')
                except Exception:
                    market_states = None
            state = (market_states.iloc[pos] if market_states is not None else None)
            mkt_bull = state == '涨势'
            st_states = _get_stock_states(df)
            stk_state = (st_states.iloc[pos]
                         if st_states is not None and len(st_states) > pos
                         else None)
            if value < 0 and state == '跌势' and stk_state == '跌势':
                return ['趋势共振卖出']

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
                if (_sw_enabled(SIGNAL_BOLL_BUY)
                        and lower_rebound and bar_ok and rsi_order
                        and price_above_ma5):
                    reasons.append('买入信号BOLL')
                macd1 = (golden and dif.iloc[pos] >= 0 and mkt_bull
                         and adx.iloc[pos] > 20
                         and pdi.iloc[pos] > mdi.iloc[pos])
                macd2 = (golden and dif.iloc[pos] < 0
                         and rsi14.iloc[pos] < 30 and bias_negative
                         and adx.iloc[pos] > 20)
                if _sw_enabled(SIGNAL_MACD1_BUY) and macd1:
                    reasons.append('买入信号MACD1')
                if _sw_enabled(SIGNAL_MACD2_BUY) and macd2:
                    reasons.append('买入信号MACD2')
                if (_sw_enabled(SIGNAL_ADX_BUY)
                        and adx.iloc[pos] > 25 and cross_up
                        and adxr.iloc[pos] > adx.iloc[pos]
                        and price_above_ma5):
                    reasons.append('买入信号ADX')
            else:
                adx = (df['ADX14'] if 'ADX14' in df.columns
                       else calc_adx(df)['ADX'])
                pdi = df['PLUS_DI'] if 'PLUS_DI' in df.columns else calc_adx(df)['PLUS_DI']
                mdi = df['MINUS_DI'] if 'MINUS_DI' in df.columns else calc_adx(df)['MINUS_DI']
                upper_pullback = (pos > 0
                                  and close.iloc[pos - 1] >= upper.iloc[pos - 1]
                                  and c < float(upper.iloc[pos]))
                break_mid = (pos > 0
                             and close.iloc[pos - 1] >= middle.iloc[pos - 1]
                             and c < float(middle.iloc[pos]))
                adx_falling = pos > 0 and adx.iloc[pos] < adx.iloc[pos - 1]
                dif_falling = pos > 0 and dif.iloc[pos] < dif.iloc[pos - 1]
                is_bull = stk_state == '涨势'
                is_nonbull = stk_state in ('震荡', '跌势')
                if (_sw_enabled(SIGNAL_BOLL1_SELL)
                        and is_bull and break_mid and adx_falling):
                    reasons.append('卖出信号BOLL1')
                if (_sw_enabled(SIGNAL_BOLL2_SELL)
                        and is_nonbull and upper_pullback and dif_falling):
                    reasons.append('卖出信号BOLL2')
                death = (dif.iloc[pos - 1] >= dea.iloc[pos - 1]
                         and dif.iloc[pos] < dea.iloc[pos])
                di_bearish = (pdi.iloc[pos] < pdi.iloc[pos - 1]
                              and mdi.iloc[pos] > mdi.iloc[pos - 1]
                              and adx.iloc[pos] >= 20)
                if (_sw_enabled(SIGNAL_MACD_SELL) and _sw_enabled(SIGNAL_MACD1_SELL)
                        and stk_state in ('涨势', '震荡')
                        and death and c < float(middle.iloc[pos]) and di_bearish):
                    reasons.append('卖出信号MACD1')
                if (_sw_enabled(SIGNAL_MACD_SELL) and _sw_enabled(SIGNAL_MACD2_SELL)
                        and stk_state == '跌势' and death):
                    reasons.append('卖出信号MACD2')
                if (self.use_atr_stop and _sw_enabled(SIGNAL_ATR_SELL)):
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
                        if (stop_price is not None and c < stop_price
                                and c > float(lower.iloc[pos])):
                            reasons.append('卖出信号ATR')
            return reasons
        except Exception:
            return []

    def generate_signals(self, df):
        """生成 BOMA 买入/卖出信号。"""
        close = df['close']
        bb = calc_bollinger(df, period=self.boll_period, std=self.boll_std)
        upper, middle, lower = bb['UPPER'], bb['MIDDLE'], bb['LOWER']

        macd_df = calc_macd(df, fast=self.macd_fast, slow=self.macd_slow,
                            signal=self.macd_signal)
        dif, dea, bar = macd_df['DIF'], macd_df['DEA'], macd_df['MACD']
        rsi6 = calc_rsi(df, 6)
        rsi12 = calc_rsi(df, 12)
        rsi14 = calc_rsi(df, 14)
        adx = df['ADX14'] if 'ADX14' in df.columns else calc_adx(df)['ADX']
        adxr = calc_adxr(df)
        plus_di = df['PLUS_DI'] if 'PLUS_DI' in df.columns else calc_adx(df)['PLUS_DI']
        minus_di = df['MINUS_DI'] if 'MINUS_DI' in df.columns else calc_adx(df)['MINUS_DI']
        rsi_order = rsi6 > rsi12
        ma5 = df['MA5'] if 'MA5' in df.columns else close.rolling(5).mean()
        price_above_ma5 = close > ma5
        ma20 = df['MA20'] if 'MA20' in df.columns else close.rolling(20).mean()
        bias20 = (close - ma20) / ma20.where(ma20 != 0)
        bias_negative = bias20 < 0
        adx_gt20 = adx > 20
        pdi_gt_mdi = plus_di > minus_di

        market_states = df.attrs.get('market_states')
        if market_states is not None and len(market_states) > 0:
            try:
                market_states = market_states.reindex(df.index, method='ffill')
            except Exception:
                market_states = None
        else:
            market_states = None
        if market_states is not None:
            mkt_bull = market_states == '涨势'
        else:
            mkt_bull = pd.Series(False, index=df.index)

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

        buy1 = (lower_rebound & bar_ok & rsi_order & price_above_ma5
                if _sw_enabled(SIGNAL_BOLL_BUY)
                else pd.Series(False, index=df.index))
        buy2_bull = (golden_cross & (dif >= 0) & mkt_bull
                     & adx_gt20 & pdi_gt_mdi)
        buy2_overcold = (golden_cross & (dif < 0)
                         & (rsi14 < 30) & bias_negative & adx_gt20)
        if not _sw_enabled(SIGNAL_MACD1_BUY):
            buy2_bull = pd.Series(False, index=df.index)
        if not _sw_enabled(SIGNAL_MACD2_BUY):
            buy2_overcold = pd.Series(False, index=df.index)
        buy2 = buy2_bull | buy2_overcold
        buy3 = (((adx > 25) & di_cross_up & (adxr > adx)
                 & price_above_ma5)
                if _sw_enabled(SIGNAL_ADX_BUY)
                else pd.Series(False, index=df.index))
        buy_signal = buy1 | buy2 | buy3

        st_states = _get_stock_states(df)
        if st_states is not None:
            st_bull_mid = st_states.isin(['涨势', '震荡'])
            st_bear_high = st_states.isin(['跌势'])
            is_bull = st_states == '涨势'
            is_nonbull = st_states.isin(['震荡', '跌势'])
        else:
            st_bull_mid = pd.Series(False, index=df.index)
            st_bear_high = pd.Series(False, index=df.index)
            is_bull = pd.Series(False, index=df.index)
            is_nonbull = pd.Series(False, index=df.index)
        break_mid = ((close.shift(1) >= middle.shift(1))
                     & (close < middle))
        adx_falling = adx < adx.shift(1)
        dif_falling = dif < dif.shift(1)
        sell1_boll1 = (is_bull & break_mid & adx_falling
                       if _sw_enabled(SIGNAL_BOLL1_SELL)
                       else pd.Series(False, index=df.index))
        sell1_boll2 = (is_nonbull & upper_pullback & dif_falling
                       if _sw_enabled(SIGNAL_BOLL2_SELL)
                       else pd.Series(False, index=df.index))
        sell1 = sell1_boll1 | sell1_boll2
        di_bearish = ((plus_di < plus_di.shift(1))
                      & (minus_di > minus_di.shift(1))
                      & (adx >= 20))
        macd1_on = (_sw_enabled(SIGNAL_MACD_SELL)
                    and _sw_enabled(SIGNAL_MACD1_SELL))
        macd2_on = (_sw_enabled(SIGNAL_MACD_SELL)
                    and _sw_enabled(SIGNAL_MACD2_SELL))
        sell2_macd1 = (st_bull_mid & death_cross & (close < middle) & di_bearish
                       if macd1_on else pd.Series(False, index=df.index))
        sell2_macd2 = (st_bear_high & death_cross
                       if macd2_on else pd.Series(False, index=df.index))
        sell2 = sell2_macd1 | sell2_macd2
        sell_signal = sell1 | sell2

        atr = df['ATR14'] if 'ATR14' in df.columns else calc_atr(df)

        signals = pd.Series(0.0, index=df.index, dtype=float)
        in_pos = False
        stop_price = None

        for i in range(len(df)):
            state = (market_states.iloc[i]
                     if market_states is not None else None)
            stk_state = (st_states.iloc[i]
                         if st_states is not None else None)
            both_down = (state == '跌势' and stk_state == '跌势')
            both_up = (state == '涨势' and stk_state == '涨势')
            either_down = (state == '跌势' or stk_state == '跌势')
            atr_mult = adjust_atr_stop_multiplier(
                self.atr_stop_multiplier, state)
            if not in_pos:
                if (not either_down) and bool(buy_signal.iloc[i]):
                    signals.iloc[i] = self.signal_value(1)
                    in_pos = True
                    stop_price = update_atr_trailing_stop(
                        float(close.iloc[i]), atr.iloc[i],
                        multiplier=atr_mult)
            else:
                stop_price = update_atr_trailing_stop(
                    float(close.iloc[i]), atr.iloc[i], stop_price,
                    multiplier=atr_mult)
                sell3 = (self.use_atr_stop and _sw_enabled(SIGNAL_ATR_SELL)
                         and stop_price is not None
                         and float(close.iloc[i]) < stop_price
                         and float(close.iloc[i]) > float(lower.iloc[i]))
                force_sell = both_down
                suppress_sell = both_up
                if force_sell or ((not suppress_sell)
                                  and (bool(sell_signal.iloc[i]) or sell3)):
                    signals.iloc[i] = self.signal_value(-1)
                    in_pos = False
                    stop_price = None

        return signals
