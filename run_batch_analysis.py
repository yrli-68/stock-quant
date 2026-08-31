#!/usr/bin/env python3
"""批量分析6只股票: 德赛电池 京东方 锦浪科技 深科技 中科创达 工业富联"""
import sys, os, warnings
import pandas as pd, numpy as np
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

STOCKS = [
    ('000049', '德赛电池'),
    ('000725', '京东方'),
    ('300763', '锦浪科技'),
    ('000021', '深科技'),
    ('300496', '中科创达'),
    ('601138', '工业富联'),
]

SM = {
    'ma_cross': ('均线交叉', MACrossStrategy),
    'macd': ('MACD', MACDStrategy),
    'rsi': ('RSI', RSIStrategy),
    'bollinger': ('布林带', BollingerStrategy),
    'composite': ('综合', CompositeStrategy),
}

def pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    return f'{v*100:.2f}%'

def fmt(v, f='.2f'):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    if isinstance(v, float) and np.isinf(v): return '∞'
    return f'{v:{f}}'

def mk_strategy(sk):
    if sk == 'composite':
        return CompositeStrategy([MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy()], threshold=0.3)
    return SM[sk][1]()

def run(sk, df, cap):
    s = mk_strategy(sk)
    sig = s.generate_signals(df)
    e = BacktestEngine(initial_capital=cap)
    r = e.run(df, sig, position_style='fraction')
    r2 = risk_report(r['daily_returns'].dropna(), r['equity_curve'])
    r.update(r2)
    return r, sig

# ===== 汇总数据 =====
all_summary = []

for code, name in STOCKS:
    data_file = f'/workspace/stock_quant/data/{code}_final.csv'
    df = pd.read_csv(data_file, parse_dates=['date'], index_col='date')
    df = add_all_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = latest['close']
    change = (close - prev['close']) / prev['close'] * 100
    sp = df['close'].iloc[0]
    total_ret = (close - sp) / sp * 100

    print(f'\n{"="*60}')
    print(f'  {name}({code})  收盘:{close:.2f}  当日:{change:+.2f}%  区间:{total_ret:+.2f}%')
    print(f'{"="*60}')

    # 技术指标解读
    print(f'  MA5={latest["MA5"]:.2f} MA20={latest["MA20"]:.2f} MA60={latest["MA60"]:.2f}')
    print(f'  MACD: DIF={latest["MACD_DIF"]:.2f} DEA={latest["MACD_DEA"]:.2f} BAR={latest["MACD_BAR"]:.2f}')
    print(f'  RSI14={latest["RSI14"]:.2f} KDJ: K={latest["KDJ_K"]:.2f} D={latest["KDJ_D"]:.2f} J={latest["KDJ_J"]:.2f}')

    # 回测
    results, signals = {}, {}
    for sk in SM:
        results[sk], signals[sk] = run(sk, df, 100000)

    rankings = sorted(results.items(), key=lambda x: x[1].get('total_return', -999) or -999, reverse=True)

    # 打印回测
    print(f'\n  {"策略":<10s}{"总收益":<10s}{"夏普":<8s}{"回撤":<10s}{"胜率":<8s}{"交易":<6s}{"信号":<10s}')
    print(f'  {"-"*58}')
    for sk, r in rankings:
        sig = signals[sk].iloc[-1]
        a = {1:'🔴买入', -1:'🟢卖出', 0:'⚪观望'}[sig]
        print(f'  {SM[sk][0]:<10s}{pct(r.get("total_return")):<10s}{fmt(r.get("sharpe_ratio")):<8s}{pct(r.get("max_drawdown")):<10s}{pct(r.get("win_rate")):<8s}{str(r.get("total_trades")):<6s}{a:<10s}')

    best = rankings[0]
    best_sig = signals[best[0]].iloc[-1]
    best_action = {1:'买入', -1:'卖出', 0:'持有/观望'}[best_sig]
    
    # 综合判断
    macd_dif = latest['MACD_DIF']
    macd_dea = latest['MACD_DEA']
    rsi = latest['RSI14']
    kdj_j = latest['KDJ_J']
    ma5 = latest['MA5']
    ma20 = latest['MA20']
    ma60 = latest['MA60']
    
    reasons = []
    if macd_dif > macd_dea: reasons.append('MACD偏多')
    else: reasons.append('MACD偏空')
    if rsi > 70: reasons.append('RSI超买')
    elif rsi < 30: reasons.append('RSI超卖')
    if kdj_j > 100: reasons.append('KDJ超买')
    elif kdj_j < 0: reasons.append('KDJ超卖')
    if close > ma5 > ma20: reasons.append('均线多头')
    elif close < ma5 < ma20: reasons.append('均线空头')
    
    # 信号计数
    buy_count = sum(1 for sk in SM if signals[sk].iloc[-1] == 1)
    sell_count = sum(1 for sk in SM if signals[sk].iloc[-1] == -1)
    hold_count = sum(1 for sk in SM if signals[sk].iloc[-1] == 0)
    
    if buy_count >= 3:
        verdict = '偏多买入'
    elif sell_count >= 3:
        verdict = '偏空卖出'
    elif buy_count >= 1:
        verdict = '谨慎偏多'
    else:
        verdict = '观望为主'
    
    all_summary.append({
        'name': name, 'code': code, 'close': close, 'change': change,
        'total_ret': total_ret, 'best_strategy': SM[best[0]][0],
        'best_return': best[1].get('total_return'), 'best_sharpe': best[1].get('sharpe_ratio'),
        'buy': buy_count, 'sell': sell_count, 'hold': hold_count,
        'verdict': verdict, 'reasons': ','.join(reasons),
        'macd_dif': macd_dif, 'macd_dea': macd_dea, 'rsi': rsi,
        'ma5': ma5, 'ma20': ma20, 'ma60': ma60,
    })

# ===== 最终汇总 =====
print('\n\n')
print('=' * 90)
print('  6只股票综合分析汇总表')
print('=' * 90)
print(f'  {"股票":<10s}{"收盘":<8s}{"当日%":<8s}{"区间%":<10s}{"最佳策略":<10s}{"最佳收益":<10s}{"最佳夏普":<8s}{"买入":<5s}{"卖出":<5s}{"观望":<5s}{"综合建议":<10s}')
print(f'  {"-"*88}')
for s in all_summary:
    print(f'  {s["name"]:<10s}{s["close"]:<8.2f}{s["change"]:<+8.2f}{s["total_ret"]:<+10.2f}{s["best_strategy"]:<10s}{pct(s["best_return"]):<10s}{fmt(s["best_sharpe"]):<8s}{s["buy"]:<5d}{s["sell"]:<5d}{s["hold"]:<5d}{s["verdict"]:<10s}')

print(f'\n  {"="*90}')
print(f'  各股票买卖建议详情')
print(f'  {"="*90}')
for s in all_summary:
    print(f'\n  【{s["name"]}({s["code"]})】 {s["verdict"]}')
    print(f'    收盘:{s["close"]:.2f}  当日涨跌:{s["change"]:+.2f}%  区间涨跌:{s["total_ret"]:+.2f}%')
    print(f'    最佳策略:{s["best_strategy"]}(收益{pct(s["best_return"])} 夏普{fmt(s["best_sharpe"])})')
    print(f'    信号分布: 买入{s["buy"]} 卖出{s["sell"]} 观望{s["hold"]}')
    print(f'    技术面: {s["reasons"]}')
    print(f'    MA5={s["ma5"]:.2f} MA20={s["ma20"]:.2f} MA60={s["ma60"]:.2f} | RSI={s["rsi"]:.2f} | MACD DIF={s["macd_dif"]:.2f} DEA={s["macd_dea"]:.2f}')