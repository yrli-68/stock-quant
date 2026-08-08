#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
风险分析模块

提供全面的风险度量指标计算函数，所有函数均可独立调用。
使用 numpy/scipy 进行数值计算，支持向量化操作以提高性能。

包含的风险指标：
    - VaR (Value at Risk): 历史模拟法在险价值
    - CVaR (Conditional VaR): 条件在险价值 / 预期亏损
    - 最大回撤及回撤区间
    - 卡玛比率 (Calmar Ratio)
    - 索提诺比率 (Sortino Ratio)
    - 信息比率 (Information Ratio)
    - Beta 系数
    - Jensen's Alpha
    - 综合风险报告
"""

import numpy as np
import pandas as pd
from scipy import stats


def calc_var(returns, confidence=0.95):
    """
    历史模拟法计算在险价值 (VaR)

    在给定置信水平下，投资组合在特定时间段内的最大预期损失。
    使用历史模拟法，基于收益率的经验分布计算分位数。

    Args:
        returns (pd.Series or np.ndarray): 收益率序列
        confidence (float): 置信水平，默认 0.95 (95%)

    Returns:
        float: VaR 值（正值表示损失）

    Example:
        >>> calc_var(daily_returns, 0.95)
        0.023  # 表示有95%的把握，单日最大损失不超过2.3%
    """
    returns = np.asarray(returns)
    var = -np.percentile(returns, (1 - confidence) * 100)
    return var


def calc_cvar(returns, confidence=0.95):
    """
    计算条件在险价值 (CVaR / Expected Shortfall)

    CVaR 衡量的是损失超过 VaR 时的平均损失，弥补了 VaR 无法
    衡量尾部风险大小的缺陷。

    Args:
        returns (pd.Series or np.ndarray): 收益率序列
        confidence (float): 置信水平，默认 0.95 (95%)

    Returns:
        float: CVaR 值（正值表示损失）

    Example:
        >>> calc_cvar(daily_returns, 0.95)
        0.035  # 表示在最差的5%情况下，平均损失为3.5%
    """
    returns = np.asarray(returns)
    var_threshold = -calc_var(returns, confidence)
    tail_losses = returns[returns <= -var_threshold]
    if len(tail_losses) == 0:
        return var_threshold
    cvar = -np.mean(tail_losses)
    return cvar


def calc_max_drawdown(equity_curve):
    """
    计算最大回撤及回撤区间

    最大回撤衡量投资组合从峰值到谷底的最大跌幅，是评估
    策略风险的重要指标。

    Args:
        equity_curve (pd.Series or np.ndarray): 权益曲线（净值序列）

    Returns:
        dict: 包含以下键值：
            - max_drawdown (float): 最大回撤比例（正值）
            - peak_idx (int): 峰值位置（索引）
            - trough_idx (int): 谷底位置（索引）
            - peak_value (float): 峰值净值
            - trough_value (float): 谷底净值
            - start_date: 峰值日期（如果 equity_curve 有 DatetimeIndex）
            - end_date: 谷底日期（如果 equity_curve 有 DatetimeIndex）
    """
    equity = np.asarray(equity_curve)
    cumulative_max = np.maximum.accumulate(equity)
    drawdowns = (cumulative_max - equity) / cumulative_max

    max_dd = np.max(drawdowns)
    trough_idx = np.argmax(drawdowns)
    peak_idx = np.argmax(equity[:trough_idx + 1]) if trough_idx > 0 else 0

    result = {
        'max_drawdown': max_dd,
        'peak_idx': peak_idx,
        'trough_idx': trough_idx,
        'peak_value': equity[peak_idx],
        'trough_value': equity[trough_idx],
    }

    # 如果输入是 Series 且有 DatetimeIndex，则提取日期
    if isinstance(equity_curve, pd.Series) and isinstance(equity_curve.index, pd.DatetimeIndex):
        result['start_date'] = equity_curve.index[peak_idx]
        result['end_date'] = equity_curve.index[trough_idx]

    return result


def calc_calmar_ratio(annual_return, max_drawdown):
    """
    计算卡玛比率 (Calmar Ratio)

    卡玛比率 = 年化收益率 / 最大回撤
    衡量单位回撤风险所带来的收益，比率越高表示风险调整后收益越好。

    Args:
        annual_return (float): 年化收益率（小数形式，如 0.15 表示15%）
        max_drawdown (float): 最大回撤（正值，如 0.20 表示20%）

    Returns:
        float: 卡玛比率，如果最大回撤为0则返回 inf
    """
    if max_drawdown == 0:
        return np.inf
    return annual_return / max_drawdown


def calc_sortino_ratio(returns, risk_free=0.03):
    """
    计算索提诺比率 (Sortino Ratio)

    索提诺比率 = (年化收益率 - 无风险利率) / 下行标准差
    与夏普比率不同，索提诺比率只考虑下行波动（负收益的波动），
    更准确地衡量投资者关心的下方风险。

    Args:
        returns (pd.Series or np.ndarray): 收益率序列
        risk_free (float): 无风险利率，默认 0.03 (3%)

    Returns:
        float: 索提诺比率
    """
    returns = np.asarray(returns)
    excess_returns = returns - risk_free / 252  # 日化无风险利率

    # 下行标准差：只考虑负收益的波动
    downside_returns = returns[returns < 0]
    if len(downside_returns) < 2:
        return 0.0

    downside_std = np.std(downside_returns, ddof=1)
    if downside_std == 0:
        return 0.0

    annual_excess_return = np.mean(excess_returns) * 252
    annual_downside_std = downside_std * np.sqrt(252)

    return annual_excess_return / annual_downside_std


def calc_information_ratio(returns, benchmark_returns):
    """
    计算信息比率 (Information Ratio)

    信息比率 = (组合年化收益 - 基准年化收益) / 跟踪误差年化值
    衡量主动管理带来的超额收益相对于跟踪误差的比率。

    Args:
        returns (pd.Series or np.ndarray): 策略收益率序列
        benchmark_returns (pd.Series or np.ndarray): 基准收益率序列

    Returns:
        float: 信息比率
    """
    returns = np.asarray(returns)
    benchmark_returns = np.asarray(benchmark_returns)

    # 超额收益
    excess_returns = returns - benchmark_returns

    # 跟踪误差（超额收益的标准差）
    tracking_error = np.std(excess_returns, ddof=1)
    if tracking_error == 0:
        return 0.0

    annual_excess = np.mean(excess_returns) * 252
    annual_tracking_error = tracking_error * np.sqrt(252)

    return annual_excess / annual_tracking_error


def calc_beta(returns, benchmark_returns):
    """
    计算 Beta 系数

    Beta 衡量策略收益相对于基准收益的系统性风险。
    Beta > 1: 策略波动大于市场
    Beta < 1: 策略波动小于市场
    Beta = 1: 策略与市场同步波动

    Args:
        returns (pd.Series or np.ndarray): 策略收益率序列
        benchmark_returns (pd.Series or np.ndarray): 基准收益率序列

    Returns:
        float: Beta 系数
    """
    returns = np.asarray(returns)
    benchmark_returns = np.asarray(benchmark_returns)

    # 对齐长度
    min_len = min(len(returns), len(benchmark_returns))
    returns = returns[-min_len:]
    benchmark_returns = benchmark_returns[-min_len:]

    covariance = np.cov(returns, benchmark_returns, ddof=1)[0, 1]
    benchmark_variance = np.var(benchmark_returns, ddof=1)

    if benchmark_variance == 0:
        return 0.0

    return covariance / benchmark_variance


def calc_alpha(returns, benchmark_returns, risk_free=0.03):
    """
    计算 Jensen's Alpha

    Alpha 衡量策略相对于基准的超额收益（考虑了风险调整）。
    Alpha > 0: 策略表现优于基准（经风险调整后）
    Alpha < 0: 策略表现劣于基准

    公式: Alpha = (Rp - Rf) - Beta * (Rm - Rf)
    其中 Rp=策略收益, Rf=无风险利率, Rm=基准收益

    Args:
        returns (pd.Series or np.ndarray): 策略收益率序列
        benchmark_returns (pd.Series or np.ndarray): 基准收益率序列
        risk_free (float): 无风险利率，默认 0.03 (3%)

    Returns:
        float: 年化 Alpha 值
    """
    returns = np.asarray(returns)
    benchmark_returns = np.asarray(benchmark_returns)

    beta = calc_beta(returns, benchmark_returns)

    avg_return = np.mean(returns)
    avg_benchmark = np.mean(benchmark_returns)

    daily_rf = risk_free / 252

    # 日度 Alpha，然后年化
    daily_alpha = (avg_return - daily_rf) - beta * (avg_benchmark - daily_rf)
    annual_alpha = daily_alpha * 252

    return annual_alpha


def risk_report(returns, equity_curve, benchmark_returns=None):
    """
    生成综合风险报告

    汇总所有风险指标，提供一个全面的策略风险评估。

    Args:
        returns (pd.Series or np.ndarray): 策略收益率序列
        equity_curve (pd.Series or np.ndarray): 权益曲线（净值序列）
        benchmark_returns (pd.Series or np.ndarray, optional): 基准收益率序列

    Returns:
        dict: 包含所有风险指标的字典，键值包括：
            - var_95: 95%置信水平 VaR
            - cvar_95: 95%置信水平 CVaR
            - max_drawdown: 最大回撤
            - max_drawdown_start: 回撤开始日期（如有）
            - max_drawdown_end: 回撤结束日期（如有）
            - calmar_ratio: 卡玛比率
            - sortino_ratio: 索提诺比率
            - annual_volatility: 年化波动率
            - skewness: 收益率偏度
            - kurtosis: 收益率峰度
            - information_ratio: 信息比率（仅当提供基准时）
            - beta: Beta 系数（仅当提供基准时）
            - alpha: Jensen's Alpha（仅当提供基准时）
    """
    returns = np.asarray(returns)

    # 基本统计量
    annual_return = np.mean(returns) * 252
    annual_volatility = np.std(returns, ddof=1) * np.sqrt(252)

    # VaR 和 CVaR
    var_95 = calc_var(returns, 0.95)
    cvar_95 = calc_cvar(returns, 0.95)

    # 最大回撤
    dd_info = calc_max_drawdown(equity_curve)
    max_dd = dd_info['max_drawdown']

    # 卡玛比率
    calmar = calc_calmar_ratio(annual_return, max_dd)

    # 索提诺比率
    sortino = calc_sortino_ratio(returns)

    # 偏度和峰度
    skewness = stats.skew(returns)
    kurtosis = stats.kurtosis(returns)

    report = {
        'var_95': var_95,
        'cvar_95': cvar_95,
        'max_drawdown': max_dd,
        'max_drawdown_start': dd_info.get('start_date', None),
        'max_drawdown_end': dd_info.get('end_date', None),
        'calmar_ratio': calmar,
        'sortino_ratio': sortino,
        'annual_return': annual_return,
        'annual_volatility': annual_volatility,
        'skewness': skewness,
        'kurtosis': kurtosis,
    }

    # 如果提供了基准收益率，计算基准相关指标
    if benchmark_returns is not None:
        report['information_ratio'] = calc_information_ratio(returns, benchmark_returns)
        report['beta'] = calc_beta(returns, benchmark_returns)
        report['alpha'] = calc_alpha(returns, benchmark_returns)

    return report