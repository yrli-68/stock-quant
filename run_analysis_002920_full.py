#!/usr/bin/env python3
"""德赛西威(002920) 全策略量化分析 — 个股5策略 + 指数3策略 + 加权综合"""
import sys, os, warnings, json, requests
import pandas as pd, numpy as np
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.indicators import add_all_indicators, calc_historical_volatility, calc_momentum_return
from core.backtest import BacktestEngine
from core.risk import risk_report
from strategies.ma_cross import MACrossStrategy
from strategies.macd_strategy import MACDStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.bollinger_strategy import BollingerStrategy
from strategies.composite_strategy import CompositeStrategy
from strategies.momentum_tiered import MomentumTieredStrategy
from strategies.volatility_timing import VolatilityTimingStrategy
from strategies.breadth_confirmation import BreadthConfirmationStrategy

def pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    return f'{v*100:.2f}%'

def fmt(v, f='.2f'):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    if isinstance(v, float) and np.isinf(v): return '∞'
    return f'{v:{f}}'

code = '002920'
name = '德赛西威'
data_file = f'/workspace/stock_quant/data/{code}_latest.csv'

print('='*80)
print(f'  {name}({code}) 全策略量化分析')
print('='*80)

# ===== 1. 获取数据 =====
print('\n[1/5] 获取数据...')
sina_code = f'sz{code}'
url = f'https://quotes.sina.com.cn/cn/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen=600'
resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}, timeout=15)
data = resp.json()
df = pd.DataFrame(data)
df['date'] = pd.to_datetime(df['day'])
df['open'] = pd.to_numeric(df['open'])
df['high'] = pd.to_numeric(df['high'])
df['low'] = pd.to_numeric(df['low'])
df['close'] = pd.to_numeric(df['close'])
df['volume'] = pd.to_numeric(df['volume'])
df = df[['date','open','high','low','close','volume']].set_index('date').sort_index()
df.to_csv(data_file)
latest = df.iloc[-1]
change = (latest['close'] - df.iloc[-2]['close']) / df.iloc[-2]['close']
total_ret = (latest['close'] - df.iloc[0]['close']) / df.iloc[0]['close']
print(f'  数据: {len(df)}条  {df.index[0].date()} ~ {df.index[-1].date()}')
print(f'  最新收盘: {latest["close"]:.2f}  当日涨跌: {change:+.2%}  区间涨幅: {total_ret:+.2%}')

# ===== 2. 计算指标 =====
print('\n[2/5] 计算技术指标...')
df = add_all_indicators(df)
df['HV20'] = calc_historical_volatility(df, 20)
df['MOM60'] = calc_momentum_return(df, 60)
print(f'  共 {len(df.columns)} 项指标')

# ===== 3. 定义所有策略 =====
# 个股策略
stock_strategies = {
    'ma_cross': ('均线交叉', MACrossStrategy()),
    'macd': ('MACD', MACDStrategy()),
    'rsi': ('RSI', RSIStrategy()),
    'bollinger': ('布林带', BollingerStrategy()),
    'composite_stock': ('综合(个股)', CompositeStrategy(
        [MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy()], threshold=0.3)),
}

# 指数策略
index_strategies = {
    'momentum': ('动量分层', MomentumTieredStrategy()),
    'volatility': ('波动率择时', VolatilityTimingStrategy()),
    'breadth': ('涨跌比确认', BreadthConfirmationStrategy()),
}

# 全量综合策略：个股权重80% 指数权重20%
# 8个子策略，个股各16%，指数各6.67%
all_sub_strategies = [
    MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy(),
    MomentumTieredStrategy(), VolatilityTimingStrategy(), BreadthConfirmationStrategy(),
]
all_weights = [0.16, 0.16, 0.16, 0.16, 0.0667, 0.0667, 0.0667]  # 个股80% 指数20%
composite_all = CompositeStrategy(all_sub_strategies, weights=all_weights, threshold=0.285, name='CompositeAll')

# 个股综合（原版，作为对比）
composite_stock_only = CompositeStrategy(
    [MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy()], threshold=0.3, name='CompositeStock')

# ===== 4. 运行所有策略 =====
print('\n[3/5] 运行策略回测...')

all_signal_names = {}
all_signals = {}
all_results = {}

# 个股策略
for sk, (sname, s) in stock_strategies.items():
    sig = s.generate_signals(df)
    engine = BacktestEngine(initial_capital=100000)
    r = engine.run(df, sig)
    risk = risk_report(r['daily_returns'].dropna(), r['equity_curve'])
    r.update(risk)
    all_results[sk] = r
    all_signals[sk] = sig
    all_signal_names[sk] = sname
    last_sig = sig.iloc[-1]
    sig_text = {1: '买入', -1: '卖出', 0: '观望'}[last_sig]
    print(f'  {sname:<12s} 信号:{sig_text:<4s} 收益:{pct(r.get("total_return")):<10s} 夏普:{fmt(r.get("sharpe_ratio")):<8s} 胜率:{pct(r.get("win_rate")):<8s} 交易:{r.get("total_trades"):>3d}次')

# 指数策略
for sk, (sname, s) in index_strategies.items():
    sig = s.generate_signals(df)
    engine = BacktestEngine(initial_capital=100000)
    r = engine.run(df, sig)
    risk = risk_report(r['daily_returns'].dropna(), r['equity_curve'])
    r.update(risk)
    all_results[sk] = r
    all_signals[sk] = sig
    all_signal_names[sk] = sname
    last_sig = sig.iloc[-1]
    sig_text = {1: '买入', -1: '卖出', 0: '观望'}[last_sig]
    print(f'  {sname:<12s} 信号:{sig_text:<4s} 收益:{pct(r.get("total_return")):<10s} 夏普:{fmt(r.get("sharpe_ratio")):<8s} 胜率:{pct(r.get("win_rate")):<8s} 交易:{r.get("total_trades"):>3d}次')

# 全量综合策略
sig_all = composite_all.generate_signals(df)
engine = BacktestEngine(initial_capital=100000)
r_all = engine.run(df, sig_all)
risk_all = risk_report(r_all['daily_returns'].dropna(), r_all['equity_curve'])
r_all.update(risk_all)
all_results['composite_all'] = r_all
all_signals['composite_all'] = sig_all
all_signal_names['composite_all'] = '综合(全量加权)'
last_sig_all = sig_all.iloc[-1]
sig_all_text = {1: '买入', -1: '卖出', 0: '观望'}[last_sig_all]
print(f'  {"综合(全量加权)":<12s} 信号:{sig_all_text:<4s} 收益:{pct(r_all.get("total_return")):<10s} 夏普:{fmt(r_all.get("sharpe_ratio")):<8s} 胜率:{pct(r_all.get("win_rate")):<8s} 交易:{r_all.get("total_trades"):>3d}次')

# ===== 5. 综合判断 =====
print('\n[4/5] 综合判断...')

# 收集所有策略（不含全量综合策略自身）的当前信号
strategy_signal_list = []
for sk in list(stock_strategies.keys()) + list(index_strategies.keys()):
    sig_val = all_signals[sk].iloc[-1]
    strategy_signal_list.append({
        'key': sk,
        'name': all_signal_names[sk],
        'signal': sig_val,
        'category': '个股' if sk in stock_strategies else '指数',
    })

buy_count = sum(1 for s in strategy_signal_list if s['signal'] == 1)
sell_count = sum(1 for s in strategy_signal_list if s['signal'] == -1)
hold_count = sum(1 for s in strategy_signal_list if s['signal'] == 0)

# 加权评分（个股策略权重更高）
score = 0
weights_map = {k: 0.16 for k in stock_strategies}
weights_map.update({k: 0.0667 for k in index_strategies})
for s in strategy_signal_list:
    score += s['signal'] * weights_map.get(s['key'], 0.125)

# 综合判断
if buy_count >= 6:
    verdict = '强烈买入'
    v_color = 'green'
elif buy_count >= 4:
    verdict = '买入'
    v_color = 'green'
elif buy_count >= 2 and sell_count <= 1:
    verdict = '偏多买入'
    v_color = 'green'
elif sell_count >= 6:
    verdict = '强烈卖出'
    v_color = 'red'
elif sell_count >= 4:
    verdict = '卖出'
    v_color = 'red'
elif sell_count >= 2 and buy_count <= 1:
    verdict = '偏空卖出'
    v_color = 'red'
elif buy_count >= 1:
    verdict = '谨慎偏多'
    v_color = 'amber'
else:
    verdict = '观望'
    v_color = 'amber'

# 生成原因分析
reasons = []
latest = df.iloc[-1]

# 趋势分析
if latest['close'] > latest['MA5']:
    reasons.append(f'站上MA5({latest["MA5"]:.2f})')
else:
    reasons.append(f'跌破MA5({latest["MA5"]:.2f})')
if latest['close'] > latest['MA20']:
    reasons.append(f'站上MA20({latest["MA20"]:.2f})')
else:
    reasons.append(f'跌破MA20({latest["MA20"]:.2f})')
if latest['close'] > latest['MA60']:
    reasons.append(f'站上MA60({latest["MA60"]:.2f})')
else:
    reasons.append(f'跌破MA60({latest["MA60"]:.2f})')

# MACD
if latest['MACD_DIF'] > latest['MACD_DEA']:
    reasons.append('MACD金叉中')
else:
    reasons.append('MACD死叉中')

# RSI
rsi_val = latest['RSI14']
if rsi_val > 70:
    reasons.append(f'RSI超买({rsi_val:.1f})')
elif rsi_val < 30:
    reasons.append(f'RSI超卖({rsi_val:.1f})')
else:
    reasons.append(f'RSI中性({rsi_val:.1f})')

# KDJ
if latest['KDJ_J'] > 100:
    reasons.append(f'KDJ超买(J={latest["KDJ_J"]:.1f})')
elif latest['KDJ_J'] < 0:
    reasons.append(f'KDJ超卖(J={latest["KDJ_J"]:.1f})')

# 布林位置
if latest['close'] > latest['BOLL_UPPER']:
    reasons.append('价格突破布林上轨')
elif latest['close'] < latest['BOLL_LOWER']:
    reasons.append('价格跌破布林下轨')
else:
    bb_pos = (latest['close'] - latest['BOLL_LOWER']) / (latest['BOLL_UPPER'] - latest['BOLL_LOWER'])
    if bb_pos > 0.7:
        reasons.append('布林高位运行')
    elif bb_pos < 0.3:
        reasons.append('布林低位运行')

# 波动率
hv = latest['HV20']
if not np.isnan(hv):
    reasons.append(f'20日波动率{hv*100:.1f}%')

print(f'\n  加权评分: {score:+.3f}')
print(f'  买入信号: {buy_count}  卖出信号: {sell_count}  观望: {hold_count}')
print(f'  综合建议: 【{verdict}】')
print(f'  原因: {"; ".join(reasons)}')

# ===== 6. 保存结果JSON =====
print('\n[5/5] 保存结果...')
import json as _json
output = {
    'code': code, 'name': name,
    'data_rows': len(df),
    'start_date': str(df.index[0].date()),
    'end_date': str(df.index[-1].date()),
    'close': float(latest['close']),
    'change': float(change),
    'total_ret': float(total_ret),
    'score': float(score),
    'buy_count': buy_count,
    'sell_count': sell_count,
    'hold_count': hold_count,
    'verdict': verdict,
    'v_color': v_color,
    'reasons': reasons,
    'strategies': [],
    'indicators': {
        'MA5': float(latest['MA5']),
        'MA10': float(latest['MA10']),
        'MA20': float(latest['MA20']),
        'MA60': float(latest['MA60']),
        'MACD_DIF': float(latest['MACD_DIF']),
        'MACD_DEA': float(latest['MACD_DEA']),
        'MACD_BAR': float(latest['MACD_BAR']),
        'RSI14': float(latest['RSI14']),
        'BOLL_UPPER': float(latest['BOLL_UPPER']),
        'BOLL_MIDDLE': float(latest['BOLL_MIDDLE']),
        'BOLL_LOWER': float(latest['BOLL_LOWER']),
        'KDJ_K': float(latest['KDJ_K']),
        'KDJ_D': float(latest['KDJ_D']),
        'KDJ_J': float(latest['KDJ_J']),
        'CCI20': float(latest['CCI20']),
        'WR14': float(latest['WR14']),
        'ATR14': float(latest['ATR14']),
        'HV20': float(hv) if not np.isnan(hv) else 0,
        'MOM60': float(latest['MOM60']) if not np.isnan(latest['MOM60']) else 0,
    },
}

for sk in list(stock_strategies.keys()) + list(index_strategies.keys()) + ['composite_all']:
    r = all_results[sk]
    sig = all_signals[sk]
    last_sig = sig.iloc[-1]
    output['strategies'].append({
        'key': sk,
        'name': all_signal_names[sk],
        'category': '个股' if sk in stock_strategies else ('指数' if sk in index_strategies else '综合'),
        'signal': int(last_sig),
        'signal_text': {1: '买入', -1: '卖出', 0: '观望'}[last_sig],
        'total_return': float(r.get('total_return', 0)) if r.get('total_return') is not None else None,
        'annual_return': float(r.get('annual_return', 0)) if r.get('annual_return') is not None else None,
        'sharpe_ratio': float(r.get('sharpe_ratio', 0)) if r.get('sharpe_ratio') is not None else None,
        'max_drawdown': float(r.get('max_drawdown', 0)) if r.get('max_drawdown') is not None else None,
        'win_rate': float(r.get('win_rate', 0)) if r.get('win_rate') is not None else None,
        'total_trades': int(r.get('total_trades', 0)) if r.get('total_trades') is not None else 0,
        'profit_trades': int(r.get('profit_trades', 0)) if r.get('profit_trades') is not None else 0,
        'loss_trades': int(r.get('loss_trades', 0)) if r.get('loss_trades') is not None else 0,
        'profit_factor': float(r.get('profit_factor', 0)) if r.get('profit_factor') is not None else None,
        'avg_profit': float(r.get('avg_profit', 0)) if r.get('avg_profit') is not None else None,
        'avg_loss': float(r.get('avg_loss', 0)) if r.get('avg_loss') is not None else None,
        'annual_volatility': float(r.get('annual_volatility', 0)) if r.get('annual_volatility') is not None else None,
        'sortino_ratio': float(r.get('sortino_ratio', 0)) if r.get('sortino_ratio') is not None else None,
        'calmar_ratio': float(r.get('calmar_ratio', 0)) if r.get('calmar_ratio') is not None else None,
        'var_95': float(r.get('var_95', 0)) if r.get('var_95') is not None else None,
        'cvar_95': float(r.get('cvar_95', 0)) if r.get('cvar_95') is not None else None,
        'skewness': float(r.get('skewness', 0)) if r.get('skewness') is not None else None,
        'kurtosis': float(r.get('kurtosis', 0)) if r.get('kurtosis') is not None else None,
        'weight': weights_map.get(sk, 0.125),
    })

with open('/workspace/stock_quant/data/002920_full_result.json', 'w', encoding='utf-8') as f:
    _json.dump(output, f, ensure_ascii=False, indent=2)

print(f'  结果已保存到 data/002920_full_result.json')
print(f'\n  {"="*80}')
print(f'  分析完成! 综合建议: 【{verdict}】')
print(f'  {"="*80}')