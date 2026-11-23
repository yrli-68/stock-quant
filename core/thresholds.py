#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一阈值配置（squant.toml 的 [tool.stock-quant.thresholds] 节）

配置项（均为数值）：
    MAX_RSI_THRESHOLD       RSI 卖出阈值（RSI > 该值 → 卖出，或条件）
    MIN_RSI_THRESHOLD       RSI 买入阈值（RSI < 该值 → 买入，且条件）
    PE_THRESHOLD            scan 选股时 PE 上限（PE > 该值剔除）
    BOLL_SQUEEZE_SCALE      BOLL 挤压判定比例因子（bbw <= bbw_pctl × 该值）
    BOLL_EXTRAWIDE_SCALE    BOLL 极端宽判定比例因子（bbw >= bbw_pctl × 该值）
    EMA60_CHECK             BOLL 买入 EMA60 过滤开关（1=要求当日 EMA60 < close；0=不检查）
    ADX_CHECK               BOLL 买入 ADX 过滤开关（1=要求当日 ADX > 20；0=不检查）

策略未显式传入参数时从此处读取阈值；显式传参（如 None）仍可覆盖。
"""

from core.config import load_config

_DEFAULTS = {
    'MAX_RSI_THRESHOLD': 85.0,
    'MIN_RSI_THRESHOLD': 25.0,
    'PE_THRESHOLD': 100.0,
    'BOLL_SQUEEZE_SCALE': 0.2,
    'BOLL_EXTRAWIDE_SCALE': 1.2,
    'EMA60_CHECK': 0.0,
    'ADX_CHECK': 0.0,
}

# 构造函数中用于表示「未传入，读取配置」的哨兵；None 仍表示「禁用该条件」
UNSET = object()

_cache = None


def load_thresholds(force=False):
    """读取阈值配置；字段缺失或非法时使用默认值。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    vals = dict(_DEFAULTS)
    sec = load_config(force=force).get('thresholds') or {}
    for key in vals:
        if key in sec and sec[key] is not None:
            try:
                vals[key] = float(sec[key])
            except (TypeError, ValueError):
                pass
    _cache = vals
    return vals


def get_threshold(name):
    """按名称读取阈值，未知名称返回 None。"""
    return load_thresholds().get(name, _DEFAULTS.get(name))




def get_ema60_check():
    """BOLL 买入 EMA60 过滤开关：1 表示启用，0 表示关闭。"""
    return bool(get_threshold('EMA60_CHECK'))


def get_adx_check():
    """BOLL 买入 ADX 过滤开关：1 表示启用，0 表示关闭。"""
    return bool(get_threshold('ADX_CHECK'))
