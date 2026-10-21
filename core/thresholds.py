#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一阈值配置（input/stock-quant.json 的 thresholds 节）

配置项（均为数值）：
    MAX_DRAWDWON_THRESHOLD  最大回撤止损阈值（百分比，15 表示 15%）
    MAX_RSI_THRESHOLD       RSI 卖出阈值（RSI > 该值 → 卖出，或条件）
    MIN_RSI_THRESHOLD       RSI 买入阈值（RSI < 该值 → 买入，且条件）
    PE_THRESHOLD            scan 选股时 PE 上限（PE > 该值剔除）
    BOLL_SQUEEZE_SCALE      BOLL 挤压判定比例因子（bbw <= bbw_pctl × 该值）
    BOLL_EXTRAWIDE_SCALE    BOLL 极端宽判定比例因子（bbw >= bbw_pctl × 该值）
    SAR_CHECK               买入 SAR 过滤开关（1=除 SAR 外的策略要求当日 SAR < close；0=不检查）
    EMA60_CHECK             BOLL 买入 EMA60 过滤开关（1=要求当日 EMA60 < close；0=不检查）
    ADX_CHECK               BOLL 买入 ADX 过滤开关（1=要求当日 ADX > 20；0=不检查）
    INDEX_CHECK             全局买入指数过滤开关（0=不检查；1=上证指数收盘 > 上证指数 5 日均线；
                            2=上证指数收盘 > 上证指数 5 日均线 或 个股收盘 > 个股 5 日均线）

策略未显式传入参数时从此处读取阈值；显式传参（如 None）仍可覆盖。
"""

import json
import logging
import os

_DEFAULTS = {
    'MAX_DRAWDWON_THRESHOLD': 15.0,
    'MAX_RSI_THRESHOLD': 85.0,
    'MIN_RSI_THRESHOLD': 25.0,
    'PE_THRESHOLD': 100.0,
    'BOLL_SQUEEZE_SCALE': 0.2,
    'BOLL_EXTRAWIDE_SCALE': 1.2,
    'SAR_CHECK': 0.0,
    'EMA60_CHECK': 0.0,
    'ADX_CHECK': 0.0,
    'INDEX_CHECK': 0.0,
}

# 构造函数中用于表示「未传入，读取配置」的哨兵；None 仍表示「禁用该条件」
UNSET = object()

_cache = None


def _config_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, 'input', 'stock-quant.json')


def load_thresholds(force=False):
    """读取阈值配置；文件缺失、字段缺失或非法时使用默认值。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    vals = dict(_DEFAULTS)
    try:
        with open(_config_path(), 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        sec = cfg.get('thresholds') or {}
        for key in vals:
            if key in sec and sec[key] is not None:
                vals[key] = float(sec[key])
    except Exception as exc:
        logging.getLogger(__name__).debug('读取 thresholds 配置失败，使用默认值: %s', exc)
    _cache = vals
    return vals


def get_threshold(name):
    """按名称读取阈值，未知名称返回 None。"""
    return load_thresholds().get(name, _DEFAULTS.get(name))


def get_max_drawdown_threshold():
    """最大回撤止损阈值（小数，例如 0.15）。"""
    return get_threshold('MAX_DRAWDWON_THRESHOLD') / 100.0


def get_sar_check():
    """买入 SAR 过滤开关：1 表示启用，0 表示关闭。"""
    return bool(get_threshold('SAR_CHECK'))


def get_ema60_check():
    """BOLL 买入 EMA60 过滤开关：1 表示启用，0 表示关闭。"""
    return bool(get_threshold('EMA60_CHECK'))


def get_adx_check():
    """BOLL 买入 ADX 过滤开关：1 表示启用，0 表示关闭。"""
    return bool(get_threshold('ADX_CHECK'))


def get_index_check_mode():
    """全局买入指数过滤模式：0=关闭，1=仅指数，2=指数或个股。"""
    try:
        mode = int(round(float(get_threshold('INDEX_CHECK'))))
    except (TypeError, ValueError):
        return 0
    return mode if mode in (0, 1, 2) else 0


def get_index_check():
    """全局买入指数过滤开关：非 0 表示启用。"""
    return get_index_check_mode() > 0
