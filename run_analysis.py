#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
德赛西威 (002920) 股票量化综合分析脚本

流程: 加载数据 -> 计算指标 -> 运行策略 -> 回测 -> 风险分析 -> 生成图表 -> 输出报告
"""

import sys
import os
import warnings
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

# ============================================================================
# 配置
# ============================================================================
SYMBOL = '002920'
STOCK_NAME = '德赛西威'
DATA_FILE = '/workspace/stock_quant/data/002920_final.csv'
CHART_DIR = '/workspace/stock_quant/charts'
INITIAL_CAPITAL = 100000

STRATEGY_MAP = {
    'ma_cross': ('均线交叉策略', MACrossStrategy),
    'macd': ('MACD策略', MACDStrategy),
    'rsi': ('RSI策略', RSIStrategy),
    'bollinger': ('布林带策略', BollingerStrategy),
    'composite': ('综合策略', CompositeStrategy),
}

# ============================================================================
# 辅助函数
# ============================================================================

def print_separator(char='=', width=60):
    print(char * width)

def print_header(title):
    print()
    print_separator('=')
    print(f'  {title}')
    print(f'  股票: {STOCK_NAME} ({SYMBOL})')
    print_separator('=')
    print()

def print_section(title):
    print(f'\n  --- {title} ---')

def pct(v):
    """格式化百分比"""
    if v is None or np.isnan(v) if isinstance(v, float) else False:
        return 'N/A'
    return f'{v * 100:.2f}%'

def fmt(v, fmt_str='.2f'):
    """格式化浮点数"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 'N/A'
    if isinstance(v, float) and np.isinf(v):
        return '∞'
    return f'{v:{fmt_str}}'

def align_columns_for_charts(df):
    """为图表模块添加兼容的列名"""
    df = df.copy()
    # 布林带列名映射
    if 'BOLL_UPPER' in df.columns:
        df['BB_Upper'] = df['BOLL_UPPER']
        df['BB_Lower'] = df['BOLL_LOWER']
        df['BB_Middle'] = df['BOLL_MIDDLE']
    # MACD列名映射
    if 'MACD_DIF' in df.columns:
        df['MACD'] = df['MACD_DIF']
        df['MACD_Signal'] = df['MACD_DEA']
        df['MACD_Hist'] = df['MACD_BAR']
    # RSI列名映射
    if 'RSI14' in df.columns:
        df['RSI'] = df['RSI14']
    # KDJ列名映射
    if 'KDJ_K' in df.columns:
        df['K'] = df['KDJ_K']
        df['D'] = df['KDJ_D']
        df['J'] = df['KDJ_J']
    # 成交量列名映射
    if 'volume' in df.columns:
        df['Volume'] = df['volume']
    if 'VOL_MA5' in df.columns:
        df['Volume_MA5'] = df['VOL_MA5']
    # OHLCV大写映射
    for col in ['open', 'high', 'low', 'close']:
        if col in df.columns:
            df[col.capitalize()] = df[col]
    return df


def create_strategy(strategy_key):
    """创建策略实例"""
    if strategy_key == 'composite':
        # 复合策略需要子策略
        sub_strategies = [
            MACrossStrategy(),
            MACDStrategy(),
            RSIStrategy(),
            BollingerStrategy(),
        ]
        return CompositeStrategy(sub_strategies, threshold=0.3)
    else:
        strategy_class = STRATEGY_MAP[strategy_key][1]
        return strategy_class()


def run_single_strategy(strategy_key, df, capital):
    """运行单个策略并返回结果"""
    strategy_name = STRATEGY_MAP[strategy_key][0]
    strategy = create_strategy(strategy_key)
    signals = strategy.generate_signals(df)
    engine = BacktestEngine(initial_capital=capital)
    result = engine.run(df, signals)
    result['strategy_name'] = strategy_name
    result['strategy_key'] = strategy_key
    
    # 风险分析
    risk = risk_report(result['daily_returns'].dropna(), result['equity_curve'])
    result.update(risk)
    
    return result, signals


# ============================================================================
# 主流程
# ============================================================================

def main():
    print_header('德赛西威 (002920) 股票量化综合分析')
    
    # ================================================================
    # 1. 加载数据
    # ================================================================
    print('  [1/5] 正在加载历史数据...')
    df = pd.read_csv(DATA_FILE, parse_dates=['date'], index_col='date')
    print(f'        数据条数: {len(df)}')
    print(f'        时间范围: {df.index[0].strftime("%Y-%m-%d")} ~ {df.index[-1].strftime("%Y-%m-%d")}')
    print(f'        价格范围: {df["close"].min():.2f} ~ {df["close"].max():.2f}')
    
    # 基本统计
    start_price = df['close'].iloc[0]
    end_price = df['close'].iloc[-1]
    total_return = (end_price - start_price) / start_price
    max_price = df['close'].max()
    min_price = df['close'].min()
    avg_volume = df['volume'].mean()
    
    print(f'        起始价: {start_price:.2f}')
    print(f'        最新价: {end_price:.2f}')
    print(f'        区间涨跌: {pct(total_return)}')
    print(f'        最高价: {max_price:.2f}   最低价: {min_price:.2f}')
    print(f'        日均成交量: {avg_volume/10000:.0f}万手')
    
    # ================================================================
    # 2. 计算技术指标
    # ================================================================
    print('\n  [2/5] 正在计算技术指标...')
    df = add_all_indicators(df)
    print(f'        已计算 {len(df.columns)} 项指标')
    
    # 最新指标快照
    latest = df.iloc[-1]
    print_section('最新技术指标快照')
    indicators_snapshot = [
        ('收盘价', f'{latest["close"]:.2f}'),
        ('MA5', f'{latest.get("MA5", np.nan):.2f}'),
        ('MA10', f'{latest.get("MA10", np.nan):.2f}'),
        ('MA20', f'{latest.get("MA20", np.nan):.2f}'),
        ('MA60', f'{latest.get("MA60", np.nan):.2f}'),
        ('MACD_DIF', f'{latest.get("MACD_DIF", np.nan):.2f}'),
        ('MACD_DEA', f'{latest.get("MACD_DEA", np.nan):.2f}'),
        ('MACD柱', f'{latest.get("MACD_BAR", np.nan):.2f}'),
        ('RSI14', f'{latest.get("RSI14", np.nan):.2f}'),
        ('KDJ_K', f'{latest.get("KDJ_K", np.nan):.2f}'),
        ('KDJ_D', f'{latest.get("KDJ_D", np.nan):.2f}'),
        ('KDJ_J', f'{latest.get("KDJ_J", np.nan):.2f}'),
        ('布林上轨', f'{latest.get("BOLL_UPPER", np.nan):.2f}'),
        ('布林中轨', f'{latest.get("BOLL_MIDDLE", np.nan):.2f}'),
        ('布林下轨', f'{latest.get("BOLL_LOWER", np.nan):.2f}'),
        ('ATR14', f'{latest.get("ATR14", np.nan):.2f}'),
        ('CCI20', f'{latest.get("CCI20", np.nan):.2f}'),
        ('WR14', f'{latest.get("WR14", np.nan):.2f}'),
    ]
    for name, val in indicators_snapshot:
        print(f'    {name:<12s}: {val}')
    
    # 技术面解读
    print_section('技术面简要解读')
    close = latest['close']
    ma5 = latest.get('MA5', np.nan)
    ma20 = latest.get('MA20', np.nan)
    ma60 = latest.get('MA60', np.nan)
    rsi = latest.get('RSI14', np.nan)
    macd_dif = latest.get('MACD_DIF', np.nan)
    macd_dea = latest.get('MACD_DEA', np.nan)
    macd_bar = latest.get('MACD_BAR', np.nan)
    kdj_k = latest.get('KDJ_K', np.nan)
    kdj_d = latest.get('KDJ_D', np.nan)
    boll_upper = latest.get('BOLL_UPPER', np.nan)
    boll_lower = latest.get('BOLL_LOWER', np.nan)
    boll_mid = latest.get('BOLL_MIDDLE', np.nan)
    
    # 均线判断
    if close > ma5 > ma20 > ma60:
        print('    均线: 多头排列 (偏多)')
    elif close < ma5 < ma20 < ma60:
        print('    均线: 空头排列 (偏空)')
    else:
        print('    均线: 交叉缠绕 (震荡)')
    
    # RSI判断
    if not np.isnan(rsi):
        if rsi > 70:
            print(f'    RSI: {rsi:.1f} - 超买区域 (偏空)')
        elif rsi < 30:
            print(f'    RSI: {rsi:.1f} - 超卖区域 (偏多)')
        elif rsi > 50:
            print(f'    RSI: {rsi:.1f} - 偏强区域')
        else:
            print(f'    RSI: {rsi:.1f} - 偏弱区域')
    
    # MACD判断
    if not np.isnan(macd_dif) and not np.isnan(macd_dea):
        if macd_dif > macd_dea and macd_bar > 0:
            print(f'    MACD: DIF在DEA上方，红柱 (偏多)')
        elif macd_dif < macd_dea and macd_bar < 0:
            print(f'    MACD: DIF在DEA下方，绿柱 (偏空)')
        else:
            print(f'    MACD: 方向不明')
    
    # KDJ判断
    if not np.isnan(kdj_k) and not np.isnan(kdj_d):
        if kdj_k > 80:
            print(f'    KDJ: K={kdj_k:.1f} - 超买区域')
        elif kdj_k < 20:
            print(f'    KDJ: K={kdj_k:.1f} - 超卖区域')
        else:
            print(f'    KDJ: K={kdj_k:.1f} - 中性区域')
    
    # 布林带判断
    if not np.isnan(boll_upper) and not np.isnan(boll_lower):
        bb_width = (boll_upper - boll_lower) / boll_mid * 100
        if close > boll_upper * 0.98:
            print(f'    布林带: 价格接近上轨 (带宽{bb_width:.1f}%)')
        elif close < boll_lower * 1.02:
            print(f'    布林带: 价格接近下轨 (带宽{bb_width:.1f}%)')
        else:
            print(f'    布林带: 价格在通道内 (带宽{bb_width:.1f}%)')
    
    # ================================================================
    # 3. 运行策略回测
    # ================================================================
    print('\n  [3/5] 正在运行策略回测...')
    
    chart_gen = ChartGenerator(output_dir=CHART_DIR)
    
    all_results = {}
    all_signals = {}
    all_charts = {}
    
    for sk, (sname, sclass) in STRATEGY_MAP.items():
        print(f'        运行: {sname}...')
        result, signals = run_single_strategy(sk, df, INITIAL_CAPITAL)
        all_results[sk] = result
        all_signals[sk] = signals
    
    # ================================================================
    # 4. 生成图表
    # ================================================================
    print('\n  [4/5] 正在生成图表...')
    
    # 为图表准备兼容列名
    df_chart = align_columns_for_charts(df)
    
    # 技术指标仪表盘
    try:
        path = chart_gen.plot_indicators_dashboard(df_chart, title=f'{STOCK_NAME}({SYMBOL}) 技术指标仪表盘')
        print(f'        仪表盘: {path}')
    except Exception as e:
        print(f'        仪表盘生成失败: {e}')
    
    # K线+指标
    try:
        path = chart_gen.plot_kline_with_indicators(df_chart, title=f'{STOCK_NAME}({SYMBOL}) K线图与技术指标')
        print(f'        K线图: {path}')
    except Exception as e:
        print(f'        K线图生成失败: {e}')
    
    # 策略权益曲线对比
    try:
        compare_data = {STRATEGY_MAP[sk][0]: all_results[sk] for sk in STRATEGY_MAP}
        path = chart_gen.plot_compare_strategies(compare_data)
        print(f'        策略对比: {path}')
    except Exception as e:
        print(f'        策略对比图生成失败: {e}')
    
    # 最佳策略的信号图
    best_sk = max(all_results, key=lambda x: all_results[x].get('total_return', -999) or -999)
    best_result = all_results[best_sk]
    best_signals = all_signals[best_sk]
    best_name = STRATEGY_MAP[best_sk][0]
    
    try:
        path = chart_gen.plot_equity_curve(best_result, title=f'{STOCK_NAME}({SYMBOL}) {best_name} 权益曲线')
        print(f'        权益曲线: {path}')
    except Exception as e:
        print(f'        权益曲线生成失败: {e}')
    
    try:
        path = chart_gen.plot_signal_on_price(df_chart, best_signals, 
                                               title=f'{STOCK_NAME}({SYMBOL}) {best_name} 买卖信号')
        print(f'        买卖信号: {path}')
    except Exception as e:
        print(f'        买卖信号图生成失败: {e}')
    
    try:
        trades_df = best_result.get('trades_df', pd.DataFrame())
        if trades_df is not None and len(trades_df) > 0:
            path = chart_gen.plot_trade_distribution(trades_df)
            print(f'        交易分布: {path}')
    except Exception as e:
        print(f'        交易分布图生成失败: {e}')
    
    # 月度收益热力图
    try:
        daily_returns = best_result.get('daily_returns', None)
        if daily_returns is not None:
            path = chart_gen.plot_monthly_returns_heatmap(daily_returns)
            print(f'        月度收益热力图: {path}')
    except Exception as e:
        print(f'        月度收益热力图生成失败: {e}')
    
    # 风险热力图
    try:
        risk_metrics = {}
        for sk in STRATEGY_MAP:
            r = all_results[sk]
            risk_metrics[STRATEGY_MAP[sk][0]] = {
                '总收益率': r.get('total_return', 0) or 0,
                '夏普比率': r.get('sharpe_ratio', 0) or 0,
                '最大回撤': -(r.get('max_drawdown', 0) or 0),
                '胜率': r.get('win_rate', 0) or 0,
                '盈利因子': r.get('profit_factor', 0) or 0,
            }
        path = chart_gen.plot_risk_heatmap(risk_metrics)
        print(f'        风险热力图: {path}')
    except Exception as e:
        print(f'        风险热力图生成失败: {e}')
    
    # ================================================================
    # 5. 输出综合分析报告
    # ================================================================
    print('\n  [5/5] 策略回测结果:')
    print()
    
    # 打印每个策略的详细结果
    for sk in STRATEGY_MAP:
        sname = STRATEGY_MAP[sk][0]
        r = all_results[sk]
        
        print_separator('-')
        print(f'  【{sname}】')
        print_separator('-')
        print(f'    总收益率:     {pct(r.get("total_return"))}')
        print(f'    年化收益率:   {pct(r.get("annual_return"))}')
        print(f'    最大回撤:     {pct(r.get("max_drawdown"))}')
        print(f'    夏普比率:     {fmt(r.get("sharpe_ratio"))}')
        print(f'    索提诺比率:   {fmt(r.get("sortino_ratio"))}')
        print(f'    卡玛比率:     {fmt(r.get("calmar_ratio"))}')
        print(f'    年化波动率:   {pct(r.get("annual_volatility"))}')
        print(f'    胜率:         {pct(r.get("win_rate"))}')
        print(f'    总交易次数:   {r.get("total_trades", "N/A")}')
        print(f'    盈利交易:     {r.get("profit_trades", "N/A")}')
        print(f'    亏损交易:     {r.get("loss_trades", "N/A")}')
        print(f'    平均盈利:     {fmt(r.get("avg_profit"))}')
        print(f'    平均亏损:     {fmt(r.get("avg_loss"))}')
        print(f'    盈利因子:     {fmt(r.get("profit_factor"))}')
        print(f'    VaR(95%):     {pct(r.get("var_95"))}')
        print(f'    CVaR(95%):    {pct(r.get("cvar_95"))}')
    
    # 策略排名
    print()
    print_separator('=')
    print('  【策略综合排名】（按总收益率）')
    print_separator('=')
    
    rankings = sorted(all_results.items(), 
                      key=lambda x: x[1].get('total_return', -999) or -999, 
                      reverse=True)
    
    print(f'  {"排名":<6s}{"策略":<16s}{"总收益率":<12s}{"夏普比率":<10s}{"最大回撤":<12s}{"胜率":<10s}{"交易次数":<8s}')
    print('  ' + '-' * 74)
    for i, (sk, r) in enumerate(rankings, 1):
        name = STRATEGY_MAP[sk][0]
        print(f'  {i:<6d}{name:<16s}{pct(r.get("total_return")):<12s}{fmt(r.get("sharpe_ratio")):<10s}{pct(r.get("max_drawdown")):<12s}{pct(r.get("win_rate")):<10s}{str(r.get("total_trades", "N/A")):<8s}')
    
    # 最佳策略推荐
    print()
    print_separator('=')
    best_name = STRATEGY_MAP[rankings[0][0]][0]
    best_r = rankings[0][1]
    print(f'  🏆 最佳策略: {best_name}')
    print(f'     总收益率: {pct(best_r.get("total_return"))}')
    print(f'     夏普比率: {fmt(best_r.get("sharpe_ratio"))}')
    print(f'     最大回撤: {pct(best_r.get("max_drawdown"))}')
    print_separator('=')
    
    # 综合结论
    print()
    print('  【综合分析结论】')
    print()
    
    # 基于指标和回测结果给出结论
    # 1. 趋势判断
    if close > ma5 > ma20:
        trend = '短期处于上升趋势'
    elif close < ma5 < ma20:
        trend = '短期处于下降趋势'
    else:
        trend = '短期处于震荡整理格局'
    
    # 2. 策略表现总结
    profitable_count = sum(1 for sk, r in rankings if (r.get('total_return') or 0) > 0)
    total_strategies = len(rankings)
    avg_sharpe = np.mean([r.get('sharpe_ratio') or 0 for _, r in rankings])
    
    print(f'    1. 趋势判断: {trend}')
    print(f'    2. 策略表现: {profitable_count}/{total_strategies} 个策略实现正收益')
    print(f'    3. 平均夏普比率: {avg_sharpe:.2f}')
    
    if total_return > 0:
        print(f'    4. 区间涨幅: 德赛西威在分析区间内上涨 {pct(total_return)}，整体表现良好')
    else:
        print(f'    4. 区间跌幅: 德赛西威在分析区间内下跌 {pct(-total_return)}')
    
    if profitable_count >= 3:
        print(f'    5. 量化策略在德赛西威上整体有效，多种策略均能捕捉到趋势机会')
    elif profitable_count >= 1:
        print(f'    5. 部分策略有效，建议选择排名靠前的策略进行跟踪')
    else:
        print(f'    5. 当前市场环境下策略表现不佳，建议等待趋势明朗后再介入')
    
    print()
    print(f'  图表已保存至: {CHART_DIR}/')
    print(f'  分析完成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print()

if __name__ == '__main__':
    main()