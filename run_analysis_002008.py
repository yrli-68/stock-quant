#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大族激光 (002008) 股票量化综合分析"""

import sys, os, warnings
import pandas as pd
import numpy as np
from datetime import datetime

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.indicators import add_all_indicators
from core.backtest import BacktestEngine
from core.risk import risk_report
from strategies.ma_cross import MACrossStrategy
from strategies.macd_strategy import MACDStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.bollinger_strategy import BollingerStrategy
from strategies.composite_strategy import CompositeStrategy
from visualization.charts import ChartGenerator

SYMBOL = '002008'
STOCK_NAME = '大族激光'
DATA_FILE = '/workspace/stock_quant/data/002008_final.csv'
CHART_DIR = '/workspace/stock_quant/charts/002008'
INITIAL_CAPITAL = 100000

STRATEGY_MAP = {
    'ma_cross': ('均线交叉策略', MACrossStrategy),
    'macd': ('MACD策略', MACDStrategy),
    'rsi': ('RSI策略', RSIStrategy),
    'bollinger': ('布林带策略', BollingerStrategy),
    'composite': ('综合策略', CompositeStrategy),
}

def pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 'N/A'
    return f'{v * 100:.2f}%'

def fmt(v, fmt_str='.2f'):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 'N/A'
    if isinstance(v, float) and np.isinf(v):
        return '∞'
    return f'{v:{fmt_str}}'

def create_strategy(sk):
    if sk == 'composite':
        return CompositeStrategy([MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy()], threshold=0.3)
    return STRATEGY_MAP[sk][1]()

def run_strategy(sk, df, capital):
    strategy = create_strategy(sk)
    signals = strategy.generate_signals(df)
    engine = BacktestEngine(initial_capital=capital)
    result = engine.run(df, signals, position_style='fraction')
    result['strategy_name'] = STRATEGY_MAP[sk][0]
    result['strategy_key'] = sk
    risk = risk_report(result['daily_returns'].dropna(), result['equity_curve'])
    result.update(risk)
    return result, signals

def align_for_charts(df):
    df = df.copy()
    for old, new in [('BOLL_UPPER','BB_Upper'),('BOLL_LOWER','BB_Lower'),('BOLL_MIDDLE','BB_Middle'),
                     ('MACD_DIF','MACD'),('MACD_DEA','MACD_Signal'),('MACD_BAR','MACD_Hist'),
                     ('RSI14','RSI'),('KDJ_K','K'),('KDJ_D','D'),('KDJ_J','J'),('volume','Volume')]:
        if old in df.columns:
            df[new] = df[old]
    for col in ['open','high','low','close']:
        if col in df.columns:
            df[col.capitalize()] = df[col]
    return df

def main():
    os.makedirs(CHART_DIR, exist_ok=True)
    
    print('=' * 60)
    print(f'  大族激光 ({SYMBOL}) 股票量化综合分析')
    print('=' * 60)
    print()
    
    # 1. 加载数据
    print('[1/5] 加载数据...')
    df = pd.read_csv(DATA_FILE, parse_dates=['date'], index_col='date')
    print(f'    数据: {len(df)} 条, {df.index[0].strftime("%Y-%m-%d")} ~ {df.index[-1].strftime("%Y-%m-%d")}')
    
    start_p = df['close'].iloc[0]
    end_p = df['close'].iloc[-1]
    print(f'    起始价: {start_p:.2f}  最新价: {end_p:.2f}  区间涨跌: {pct((end_p-start_p)/start_p)}')
    print(f'    最高: {df["close"].max():.2f}  最低: {df["close"].min():.2f}')
    print(f'    日均成交量: {df["volume"].mean()/10000:.0f}万手')
    
    # 2. 计算指标
    print('\n[2/5] 计算技术指标...')
    df = add_all_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = latest['close']
    
    print(f'    MA5={latest["MA5"]:.2f} MA10={latest["MA10"]:.2f} MA20={latest["MA20"]:.2f} MA60={latest["MA60"]:.2f}')
    print(f'    MACD: DIF={latest["MACD_DIF"]:.2f} DEA={latest["MACD_DEA"]:.2f} BAR={latest["MACD_BAR"]:.2f}')
    print(f'    RSI14={latest["RSI14"]:.2f}  KDJ: K={latest["KDJ_K"]:.2f} D={latest["KDJ_D"]:.2f} J={latest["KDJ_J"]:.2f}')
    print(f'    布林: 上={latest["BOLL_UPPER"]:.2f} 中={latest["BOLL_MIDDLE"]:.2f} 下={latest["BOLL_LOWER"]:.2f}')
    
    # 3. 回测
    print('\n[3/5] 运行策略回测...')
    all_results = {}
    all_signals = {}
    for sk in STRATEGY_MAP:
        print(f'    {STRATEGY_MAP[sk][0]}...')
        result, signals = run_strategy(sk, df, INITIAL_CAPITAL)
        all_results[sk] = result
        all_signals[sk] = signals
    
    # 4. 图表
    print('\n[4/5] 生成图表...')
    chart_gen = ChartGenerator(output_dir=CHART_DIR)
    df_chart = align_for_charts(df)
    
    try:
        print(f'    仪表盘: {chart_gen.plot_indicators_dashboard(df_chart, title=f"{STOCK_NAME}({SYMBOL}) 技术指标仪表盘")}')
    except: pass
    try:
        print(f'    K线图: {chart_gen.plot_kline_with_indicators(df_chart, title=f"{STOCK_NAME}({SYMBOL}) K线图")}')
    except: pass
    try:
        cd = {STRATEGY_MAP[sk][0]: all_results[sk] for sk in STRATEGY_MAP}
        print(f'    策略对比: {chart_gen.plot_compare_strategies(cd)}')
    except: pass
    try:
        best_sk = max(all_results, key=lambda x: all_results[x].get('total_return', -999) or -999)
        print(f'    权益曲线: {chart_gen.plot_equity_curve(all_results[best_sk], title=f"{STOCK_NAME}({SYMBOL}) {STRATEGY_MAP[best_sk][0]}")}')
        print(f'    买卖信号: {chart_gen.plot_signal_on_price(df_chart, all_signals[best_sk], title=f"{STOCK_NAME}({SYMBOL}) {STRATEGY_MAP[best_sk][0]} 信号")}')
    except: pass
    try:
        print(f'    月度热力图: {chart_gen.plot_monthly_returns_heatmap(all_results[best_sk].get("daily_returns"))}')
    except: pass
    
    # 5. 报告
    print('\n[5/5] 回测结果:')
    print()
    
    for sk in STRATEGY_MAP:
        r = all_results[sk]
        print('-' * 50)
        print(f'  【{STRATEGY_MAP[sk][0]}】')
        print('-' * 50)
        print(f'    总收益率: {pct(r.get("total_return"))}    年化: {pct(r.get("annual_return"))}')
        print(f'    最大回撤: {pct(r.get("max_drawdown"))}    夏普: {fmt(r.get("sharpe_ratio"))}    索提诺: {fmt(r.get("sortino_ratio"))}')
        print(f'    胜率: {pct(r.get("win_rate"))}    交易次数: {r.get("total_trades")}    盈利因子: {fmt(r.get("profit_factor"))}')
        print(f'    平均盈利: {fmt(r.get("avg_profit"))}    平均亏损: {fmt(r.get("avg_loss"))}')
        print(f'    VaR(95%): {pct(r.get("var_95"))}    CVaR(95%): {pct(r.get("cvar_95"))}')
    
    # 排名
    print()
    print('=' * 60)
    print('  策略综合排名（按总收益率）')
    print('=' * 60)
    rankings = sorted(all_results.items(), key=lambda x: x[1].get('total_return', -999) or -999, reverse=True)
    print(f'  {"排名":<6s}{"策略":<14s}{"总收益率":<10s}{"夏普":<8s}{"最大回撤":<10s}{"胜率":<8s}{"交易次数":<8s}')
    print('  ' + '-' * 64)
    for i, (sk, r) in enumerate(rankings, 1):
        print(f'  {i:<6d}{STRATEGY_MAP[sk][0]:<14s}{pct(r.get("total_return")):<10s}{fmt(r.get("sharpe_ratio")):<8s}{pct(r.get("max_drawdown")):<10s}{pct(r.get("win_rate")):<8s}{str(r.get("total_trades")):<8s}')
    
    best = rankings[0]
    print()
    print(f'  最佳策略: {STRATEGY_MAP[best[0]][0]}  总收益: {pct(best[1].get("total_return"))}  夏普: {fmt(best[1].get("sharpe_ratio"))}')
    
    # 8月7日信号
    print()
    print('=' * 60)
    print(f'  2026-08-07 最新买卖信号')
    print('=' * 60)
    print(f'  收盘价: {close:.2f}  涨跌: {((close-prev["close"])/prev["close"]*100):.2f}%')
    print()
    
    for sk in STRATEGY_MAP:
        sig = all_signals[sk].iloc[-1]
        if sig == 1:
            action = '🔴 买入'
        elif sig == -1:
            action = '🟢 卖出'
        else:
            action = '⚪ 持有/观望'
        print(f'  {action}    {STRATEGY_MAP[sk][0]}')
    
    print()
    print(f'  图表目录: {CHART_DIR}/')
    print(f'  完成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

if __name__ == '__main__':
    main()