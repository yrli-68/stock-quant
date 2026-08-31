#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测引擎模块

提供完整的策略回测框架，支持：
    - 全仓买卖和按信号比例两种持仓模式
    - 佣金和滑点模拟
    - 完整的交易记录追踪
    - 多种绩效指标计算（夏普比率、年化收益、最大回撤、胜率等）
    - 向量化计算以提高性能
"""

import numpy as np
import pandas as pd


class BacktestEngine:
    """
    回测引擎

    用于对交易策略进行历史数据回测，模拟真实交易环境中的
    佣金、滑点等成本，并计算全面的绩效指标。

    Attributes:
        initial_capital (float): 初始资金
        commission (float): 佣金费率
        slippage (float): 滑点比例
        risk_free (float): 无风险利率（用于夏普比率计算）
        trading_days (int): 年交易日数
    """

    def __init__(self, initial_capital=100000, commission=0.0003, slippage=0.0001):
        """
        初始化回测引擎

        Args:
            initial_capital (float): 初始资金，默认 100,000
            commission (float): 佣金费率，默认 0.0003（万三）
            slippage (float): 滑点比例，默认 0.0001（万一）
        """
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.risk_free = 0.03  # 无风险利率，用于夏普比率计算
        self.trading_days = 252  # 年交易日数

    def run(self, df, signals, position_style='fraction'):
        """
        执行回测

        根据行情数据和交易信号模拟交易过程，追踪资金曲线和交易记录，
        并计算各项绩效指标。

        Args:
            df (pd.DataFrame): 行情数据，必须包含 'close' 列
            signals (pd.Series): 交易信号序列，1=买入, -1=卖出, 0=持有
            position_style (str): 持仓模式
                - 'full': 全仓买卖模式，信号≥0.5时全仓买入，信号≤-0.5时全仓卖出
                - 'fraction': 按信号比例模式，仓位水平 in_position 为 0~1 浮点数：
                    弱买(+0.5)/强买(+1) 时 in_position 累加（封顶1）并买入对应比例资金；
                    弱卖(-0.5)/强卖(-1) 时 in_position 递减（下限0）并卖出对应比例持仓；
                    in_position<1 时可加仓，in_position>0 时可减仓

        Returns:
            dict: 回测结果，包含以下键值：
                - total_return (float): 总收益率
                - annual_return (float): 年化收益率
                - max_drawdown (float): 最大回撤
                - sharpe_ratio (float): 夏普比率
                - win_rate (float): 胜率
                - total_trades (int): 总交易次数
                - profit_trades (int): 盈利交易次数
                - loss_trades (int): 亏损交易次数
                - avg_profit (float): 平均盈利
                - avg_loss (float): 平均亏损
                - profit_factor (float): 盈亏比（总盈利/总亏损）
                - cum_returns (pd.Series): 累计收益率序列
                - equity_curve (pd.Series): 权益曲线（净值序列）
                - trades_df (pd.DataFrame): 交易记录明细
                - daily_returns (pd.Series): 日收益率序列
        """
        # 确保数据对齐
        close = df['close'].values
        n = len(close)

        if isinstance(signals, pd.Series):
            signals = signals.values
        signals = np.asarray(signals)

        # 确保信号长度与数据一致
        if len(signals) != n:
            raise ValueError(f"信号长度 ({len(signals)}) 与数据长度 ({n}) 不一致")

        # 初始化追踪变量
        cash = self.initial_capital  # 现金
        position = 0.0  # 持仓数量（股数）
        equity = np.zeros(n)  # 每日权益
        position_value = np.zeros(n)  # 每日持仓市值

        # 交易记录列表
        trades = []
        buy_count = 0  # 买入执行次数
        sell_count = 0  # 卖出执行次数
        in_position = 0.0  # 仓位水平（0~1 浮点数：0=空仓，1=满仓）
        entry_price = 0.0  # 入场价格
        entry_date = None  # 入场日期
        entry_idx = 0  # 入场索引
        shares_held = 0.0  # 持有股数

        for i in range(n):
            signal = signals[i]
            price = close[i]

            # 处理滑点：买入时价格上浮，卖出时价格下浮
            if signal > 0:
                exec_price = price * (1 + self.slippage)
            elif signal < 0:
                exec_price = price * (1 - self.slippage)
            else:
                exec_price = price

            if position_style == 'full':
                # ---- 全仓买卖模式 ----
                if signal >= 0.5 and in_position < 1.0:
                    # 买入信号（含弱买/强买）：全仓买入
                    trade_amount = cash * (1 - self.commission)  # 扣除佣金后的可用资金
                    shares = trade_amount / exec_price
                    shares_held = shares
                    cash -= shares * exec_price * (1 + self.commission)

                    in_position = 1.0
                    buy_count += 1
                    entry_price = exec_price
                    entry_date = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i
                    entry_idx = i

                elif signal <= -0.5 and in_position > 0:
                    # 卖出信号（含弱卖/强卖）：全仓卖出
                    cash += shares_held * exec_price * (1 - self.commission)

                    # 记录交易
                    trade_return = (exec_price - entry_price) / entry_price
                    holding_days = i - entry_idx

                    trades.append({
                        'entry_date': entry_date,
                        'exit_date': df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i,
                        'entry_price': entry_price,
                        'exit_price': exec_price,
                        'position': shares_held,
                        'return': trade_return,
                        'holding_days': holding_days,
                        'exit_reason': 'signal'
                    })

                    in_position = 0.0
                    sell_count += 1
                    shares_held = 0.0

            elif position_style == 'fraction':
                # ---- 按信号比例模式：in_position 为 0~1 的仓位水平 ----
                if signal > 0 and in_position < 1.0:
                    # 买入：仓位水平按信号强度累加（弱买 +0.5 / 强买 +1），封顶 1
                    increment = min(abs(signal), 1.0 - in_position)
                    target_value = cash * increment
                    trade_amount = target_value * (1 - self.commission)
                    shares = trade_amount / exec_price
                    shares_held += shares
                    cash -= shares * exec_price * (1 + self.commission)

                    if in_position == 0.0:
                        # 空仓首次建仓，记录入场信息
                        entry_price = exec_price
                        entry_date = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i
                        entry_idx = i
                    in_position += increment
                    buy_count += 1

                elif signal < 0 and in_position > 0:
                    # 卖出：仓位水平按信号强度递减（弱卖 -0.5 / 强卖 -1），下限 0
                    decrement = min(abs(signal), in_position)
                    sell_fraction = min(1.0, decrement / in_position)
                    shares_to_sell = shares_held * sell_fraction
                    cash += shares_to_sell * exec_price * (1 - self.commission)
                    shares_held -= shares_to_sell

                    in_position -= decrement
                    sell_count += 1

                    if in_position <= 0.0:
                        # 全部平仓：记录交易
                        trade_return = (exec_price - entry_price) / entry_price
                        holding_days = i - entry_idx

                        trades.append({
                            'entry_date': entry_date,
                            'exit_date': df.index[i] if isinstance(df.index, pd.DatetimeIndex) else i,
                            'entry_price': entry_price,
                            'exit_price': exec_price,
                            'position': shares_to_sell,
                            'return': trade_return,
                            'holding_days': holding_days,
                            'exit_reason': 'signal'
                        })

                        in_position = 0.0
                        shares_held = 0.0

            # 计算当日持仓市值和总权益
            if in_position > 0:
                position_value[i] = shares_held * price
            else:
                position_value[i] = 0.0

            equity[i] = cash + position_value[i]

        # 回测结束时如果仍持有仓位，强制平仓
        if in_position > 0:
            final_price = close[-1] * (1 - self.slippage)
            cash += shares_held * final_price * (1 - self.commission)

            trade_return = (final_price - entry_price) / entry_price
            holding_days = n - 1 - entry_idx

            trades.append({
                'entry_date': entry_date,
                'exit_date': df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else n - 1,
                'entry_price': entry_price,
                'exit_price': final_price,
                'position': shares_held,
                'return': trade_return,
                'holding_days': holding_days,
                'exit_reason': 'end'
            })

            equity[-1] = cash

        # 构建权益曲线和日收益率
        equity_curve = pd.Series(equity, index=df.index, name='equity')
        daily_returns = pd.Series(
            np.diff(equity, prepend=self.initial_capital) / self.initial_capital,
            index=df.index,
            name='daily_return'
        )
        # 更准确的日收益率：基于权益变化
        daily_returns = equity_curve.pct_change().fillna(0)

        # 累计收益率
        cum_returns = (equity_curve / self.initial_capital - 1)

        # 构建交易记录 DataFrame
        if trades:
            trades_df = pd.DataFrame(trades)
        else:
            trades_df = pd.DataFrame(columns=[
                'entry_date', 'exit_date', 'entry_price', 'exit_price',
                'position', 'return', 'holding_days', 'exit_reason'
            ])

        # 计算所有绩效指标
        metrics = self._compute_metrics(equity_curve, daily_returns, trades_df)

        # 组装返回结果
        result = {
            'total_return': metrics['total_return'],
            'annual_return': metrics['annual_return'],
            'max_drawdown': metrics['max_drawdown'],
            'sharpe_ratio': metrics['sharpe_ratio'],
            'win_rate': metrics['win_rate'],
            'total_trades': metrics['total_trades'],
            'buy_count': buy_count,
            'sell_count': sell_count,
            'profit_trades': metrics['profit_trades'],
            'loss_trades': metrics['loss_trades'],
            'avg_profit': metrics['avg_profit'],
            'avg_loss': metrics['avg_loss'],
            'profit_factor': metrics['profit_factor'],
            'cum_returns': cum_returns,
            'equity_curve': equity_curve,
            'trades_df': trades_df,
            'daily_returns': daily_returns,
        }

        return result

    def _compute_metrics(self, equity_curve, daily_returns, trades_df):
        """
        计算所有绩效指标（内部方法）

        使用 numpy 向量化计算以提高性能，包括：
            - 总收益率、年化收益率
            - 最大回撤
            - 夏普比率
            - 胜率、盈亏比等交易统计

        Args:
            equity_curve (pd.Series): 权益曲线
            daily_returns (pd.Series): 日收益率序列
            trades_df (pd.DataFrame): 交易记录

        Returns:
            dict: 绩效指标字典
        """
        equity = equity_curve.values

        # ---- 总收益率 ----
        total_return = (equity[-1] - self.initial_capital) / self.initial_capital

        # ---- 年化收益率 ----
        n_days = len(equity)
        annual_return = (1 + total_return) ** (self.trading_days / n_days) - 1

        # ---- 最大回撤 ----
        cumulative_max = np.maximum.accumulate(equity)
        drawdowns = (equity - cumulative_max) / cumulative_max
        max_drawdown = np.abs(np.min(drawdowns))

        # ---- 夏普比率 ----
        rets = daily_returns.values
        # 过滤掉 NaN
        rets = rets[~np.isnan(rets)]
        if len(rets) > 1 and np.std(rets, ddof=1) > 0:
            excess_returns = rets - self.risk_free / self.trading_days
            sharpe_ratio = np.mean(excess_returns) / np.std(rets, ddof=1) * np.sqrt(self.trading_days)
        else:
            sharpe_ratio = 0.0

        # ---- 交易统计 ----
        total_trades = len(trades_df)

        if total_trades > 0:
            trade_returns = trades_df['return'].values
            profit_trades = int(np.sum(trade_returns > 0))
            loss_trades = int(np.sum(trade_returns < 0))
            win_rate = profit_trades / total_trades if total_trades > 0 else 0.0

            # 平均盈利和平均亏损
            profits = trade_returns[trade_returns > 0]
            losses = trade_returns[trade_returns < 0]

            avg_profit = np.mean(profits) if len(profits) > 0 else 0.0
            avg_loss = np.mean(losses) if len(losses) > 0 else 0.0

            # 盈亏比（Profit Factor）：总盈利 / 总亏损的绝对值
            total_profit = np.sum(profits) if len(profits) > 0 else 0.0
            total_loss = np.abs(np.sum(losses)) if len(losses) > 0 else 0.0
            profit_factor = total_profit / total_loss if total_loss > 0 else np.inf
        else:
            profit_trades = 0
            loss_trades = 0
            win_rate = 0.0
            avg_profit = 0.0
            avg_loss = 0.0
            profit_factor = 0.0

        return {
            'total_return': total_return,
            'annual_return': annual_return,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'win_rate': win_rate,
            'total_trades': total_trades,
            'profit_trades': profit_trades,
            'loss_trades': loss_trades,
            'avg_profit': avg_profit,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
        }