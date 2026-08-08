#!/usr/bin/env python3
"""宏达电子 (300726) 量化分析"""
import sys, os, warnings
import pandas as pd, numpy as np
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

SYMBOL = '300726'
STOCK_NAME = '宏达电子'
DATA_FILE = '/workspace/stock_quant/data/300726_final.csv'
CHART_DIR = '/workspace/stock_quant/charts/300726'

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
    r = e.run(df, sig)
    r['sname'] = SM[sk][0]
    r['skey'] = sk
    r2 = risk_report(r['daily_returns'].dropna(), r['equity_curve'])
    r.update(r2)
    return r, sig

def align(df):
    df = df.copy()
    m = [('BOLL_UPPER','BB_Upper'),('BOLL_LOWER','BB_Lower'),('BOLL_MIDDLE','BB_Middle'),
         ('MACD_DIF','MACD'),('MACD_DEA','MACD_Signal'),('MACD_BAR','MACD_Hist'),
         ('RSI14','RSI'),('KDJ_K','K'),('KDJ_D','D'),('KDJ_J','J'),('volume','Volume')]
    for o,n in m:
        if o in df.columns: df[n] = df[o]
    for c in ['open','high','low','close']:
        if c in df.columns: df[c.capitalize()] = df[c]
    return df

os.makedirs(CHART_DIR, exist_ok=True)
print('='*60)
print(f'  宏达电子 ({SYMBOL}) 量化分析')
print('='*60)

df = pd.read_csv(DATA_FILE, parse_dates=['date'], index_col='date')
print(f'\n[1] 数据: {len(df)}条 {df.index[0].strftime("%Y-%m-%d")}~{df.index[-1].strftime("%Y-%m-%d")}')
sp, ep = df['close'].iloc[0], df['close'].iloc[-1]
print(f'    起始:{sp:.2f} 最新:{ep:.2f} 涨跌:{pct((ep-sp)/sp)}')
print(f'    最高:{df["close"].max():.2f} 最低:{df["close"].min():.2f}')

df = add_all_indicators(df)
latest = df.iloc[-1]
prev = df.iloc[-2]
print(f'\n[2] 指标: MA5={latest["MA5"]:.2f} MA20={latest["MA20"]:.2f} MA60={latest["MA60"]:.2f}')
print(f'    MACD: DIF={latest["MACD_DIF"]:.2f} DEA={latest["MACD_DEA"]:.2f} BAR={latest["MACD_BAR"]:.2f}')
print(f'    RSI14={latest["RSI14"]:.2f} KDJ: K={latest["KDJ_K"]:.2f} D={latest["KDJ_D"]:.2f} J={latest["KDJ_J"]:.2f}')
print(f'    布林: 上={latest["BOLL_UPPER"]:.2f} 中={latest["BOLL_MIDDLE"]:.2f} 下={latest["BOLL_LOWER"]:.2f}')

print('\n[3] 回测...')
results, signals = {}, {}
for sk in SM:
    print(f'    {SM[sk][0]}...')
    results[sk], signals[sk] = run(sk, df, 100000)

print('\n[4] 图表...')
cg = ChartGenerator(output_dir=CHART_DIR)
dfc = align(df)
try: print(f'    仪表盘: {cg.plot_indicators_dashboard(dfc, title=f"{STOCK_NAME}({SYMBOL})")}')
except: pass
try: print(f'    K线: {cg.plot_kline_with_indicators(dfc, title=f"{STOCK_NAME}({SYMBOL})")}')
except: pass
try:
    cd = {SM[sk][0]: results[sk] for sk in SM}
    print(f'    对比: {cg.plot_compare_strategies(cd)}')
except: pass
best_sk = max(results, key=lambda x: results[x].get('total_return', -999) or -999)
try: print(f'    权益: {cg.plot_equity_curve(results[best_sk], title=f"{STOCK_NAME} {SM[best_sk][0]}")}')
except: pass
try: print(f'    信号: {cg.plot_signal_on_price(dfc, signals[best_sk], title=f"{STOCK_NAME} {SM[best_sk][0]}")}')
except: pass
try: print(f'    月度: {cg.plot_monthly_returns_heatmap(results[best_sk].get("daily_returns"))}')
except: pass

print('\n[5] 结果:')
for sk in SM:
    r = results[sk]
    print(f'\n  【{SM[sk][0]}】')
    print(f'    总收益:{pct(r.get("total_return"))} 年化:{pct(r.get("annual_return"))} 回撤:{pct(r.get("max_drawdown"))}')
    print(f'    夏普:{fmt(r.get("sharpe_ratio"))} 索提诺:{fmt(r.get("sortino_ratio"))} 胜率:{pct(r.get("win_rate"))}')
    print(f'    交易:{r.get("total_trades")}次 盈利因子:{fmt(r.get("profit_factor"))}')

print('\n' + '='*60)
print('  排名')
print('='*60)
rankings = sorted(results.items(), key=lambda x: x[1].get('total_return', -999) or -999, reverse=True)
print(f'  {"排名":<6s}{"策略":<10s}{"总收益":<10s}{"夏普":<8s}{"回撤":<10s}{"胜率":<8s}{"交易":<6s}')
print('  '+'-'*58)
for i,(sk,r) in enumerate(rankings,1):
    print(f'  {i:<6d}{SM[sk][0]:<10s}{pct(r.get("total_return")):<10s}{fmt(r.get("sharpe_ratio")):<8s}{pct(r.get("max_drawdown")):<10s}{pct(r.get("win_rate")):<8s}{str(r.get("total_trades")):<6s}')

print(f'\n  最佳: {SM[rankings[0][0]][0]} 收益:{pct(rankings[0][1].get("total_return"))} 夏普:{fmt(rankings[0][1].get("sharpe_ratio"))}')

close = latest['close']
print('\n' + '='*60)
print(f'  2026-08-07 买卖信号 (收盘:{close:.2f} 涨跌:{((close-prev["close"])/prev["close"]*100):.2f}%)')
print('='*60)
for sk in SM:
    sig = signals[sk].iloc[-1]
    a = {1:'🔴 买入', -1:'🟢 卖出', 0:'⚪ 持有/观望'}[sig]
    print(f'  {a}    {SM[sk][0]}')

print(f'\n图表: {CHART_DIR}/')
print(f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')