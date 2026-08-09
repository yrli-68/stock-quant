#!/usr/bin/env python3
"""京东方A(000725) 前复权价格量化分析"""
import sys, os, warnings, json
import pandas as pd, numpy as np
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.data_fetcher import DataFetcher
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

STOCK_WEIGHT = 0.20
INDEX_WEIGHT = 0.0667

def pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    return f'{v*100:.2f}%'

def fmt(v, f='.2f'):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    if isinstance(v, float) and np.isinf(v): return '∞'
    return f'{v:{f}}'

# ===== 主分析 =====
STOCK_CODE = '000725'
STOCK_NAME = '京东方A'

print('='*80)
print(f'  {STOCK_NAME}({STOCK_CODE}) 前复权(qfq)量化分析')
print('='*80)

# 使用 DataFetcher 获取前复权数据
print('\n[1] 获取前复权数据...')
fetcher = DataFetcher()
df = fetcher.get_stock_data(STOCK_CODE, '2024-01-01', '2026-08-09', adjust='qfq')

if df.empty:
    print('ERROR: 无法获取数据')
    sys.exit(1)

print(f'  数据获取成功: {len(df)} 条, {df.index[0].strftime("%Y-%m-%d")} ~ {df.index[-1].strftime("%Y-%m-%d")}')

# 同时也获取不复权数据和除权除息数据用于对比
print('\n  获取不复权数据和除权除息数据...')
df_raw = fetcher.get_stock_data(STOCK_CODE, '2024-01-01', '2026-08-09', adjust='')
div_df = fetcher.get_dividend_data(STOCK_CODE)
print(f'  除权除息记录: {len(div_df)} 条')

# 计算技术指标
print('\n[2] 计算技术指标...')
df = add_all_indicators(df)
df['HV20'] = calc_historical_volatility(df, 20)
df['MOM60'] = calc_momentum_return(df, 60)
print('  指标计算完成')

# 构建策略
stock_strategies = {
    'ma_cross': ('均线交叉', MACrossStrategy()),
    'macd': ('MACD', MACDStrategy()),
    'rsi': ('RSI', RSIStrategy()),
    'bollinger': ('布林带', BollingerStrategy()),
}
index_strategies = {
    'momentum': ('动量分层', MomentumTieredStrategy()),
    'volatility': ('波动率择时', VolatilityTimingStrategy()),
    'breadth': ('涨跌比确认', BreadthConfirmationStrategy()),
}

all_sub_strategies = [
    MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy(),
    MomentumTieredStrategy(), VolatilityTimingStrategy(), BreadthConfirmationStrategy(),
]
all_weights = [STOCK_WEIGHT]*4 + [INDEX_WEIGHT]*3
composite_all = CompositeStrategy(all_sub_strategies, weights=all_weights, threshold=0.285, name='CompositeAll')

# 运行所有策略
print('\n[3] 运行策略回测...')
all_results = {}
all_signals = {}
all_signal_names = {}

for sk, (sname, s) in stock_strategies.items():
    sig = s.generate_signals(df)
    engine = BacktestEngine(initial_capital=100000)
    r = engine.run(df, sig)
    risk = risk_report(r['daily_returns'].dropna(), r['equity_curve'])
    r.update(risk)
    all_results[sk] = r
    all_signals[sk] = sig
    all_signal_names[sk] = sname
    print(f'  [{sname}] 信号={sig.iloc[-1]}, 收益={pct(r.get("total_return"))}, 夏普={fmt(r.get("sharpe_ratio"))}')

for sk, (sname, s) in index_strategies.items():
    sig = s.generate_signals(df)
    engine = BacktestEngine(initial_capital=100000)
    r = engine.run(df, sig)
    risk = risk_report(r['daily_returns'].dropna(), r['equity_curve'])
    r.update(risk)
    all_results[sk] = r
    all_signals[sk] = sig
    all_signal_names[sk] = sname
    print(f'  [{sname}] 信号={sig.iloc[-1]}, 收益={pct(r.get("total_return"))}, 夏普={fmt(r.get("sharpe_ratio"))}')

sig_all = composite_all.generate_signals(df)
engine = BacktestEngine(initial_capital=100000)
r_all = engine.run(df, sig_all)
risk_all = risk_report(r_all['daily_returns'].dropna(), r_all['equity_curve'])
r_all.update(risk_all)
all_results['composite_all'] = r_all
all_signals['composite_all'] = sig_all
all_signal_names['composite_all'] = '综合(全量加权)'
print(f'  [综合(全量加权)] 信号={sig_all.iloc[-1]}, 收益={pct(r_all.get("total_return"))}, 夏普={fmt(r_all.get("sharpe_ratio"))}')

# ===== 信号汇总 =====
print('\n[4] 信号汇总...')
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

weights_map = {k: STOCK_WEIGHT for k in stock_strategies}
weights_map.update({k: INDEX_WEIGHT for k in index_strategies})
score = sum(s['signal'] * weights_map.get(s['key'], 0.14) for s in strategy_signal_list)

if buy_count >= 5: verdict = '强烈买入'; v_color = 'green'
elif buy_count >= 3: verdict = '买入'; v_color = 'green'
elif buy_count >= 2 and sell_count <= 1: verdict = '偏多买入'; v_color = 'green'
elif sell_count >= 5: verdict = '强烈卖出'; v_color = 'red'
elif sell_count >= 3: verdict = '卖出'; v_color = 'red'
elif sell_count >= 2 and buy_count <= 1: verdict = '偏空卖出'; v_color = 'red'
elif buy_count >= 1: verdict = '谨慎偏多'; v_color = 'amber'
else: verdict = '观望'; v_color = 'amber'

latest = df.iloc[-1]
change = (latest['close'] - df.iloc[-2]['close']) / df.iloc[-2]['close']
total_ret = (latest['close'] - df.iloc[0]['close']) / df.iloc[0]['close']

# 技术理由
reasons = []
if latest['close'] > latest['MA5']: reasons.append(f'站上MA5({latest["MA5"]:.2f})')
else: reasons.append(f'跌破MA5({latest["MA5"]:.2f})')
if latest['close'] > latest['MA20']: reasons.append(f'站上MA20({latest["MA20"]:.2f})')
else: reasons.append(f'跌破MA20({latest["MA20"]:.2f})')
if latest['close'] > latest['MA60']: reasons.append(f'站上MA60({latest["MA60"]:.2f})')
else: reasons.append(f'跌破MA60({latest["MA60"]:.2f})')
if latest['MACD_DIF'] > latest['MACD_DEA']: reasons.append('MACD金叉中')
else: reasons.append('MACD死叉中')
rsi_val = latest['RSI14']
if rsi_val > 70: reasons.append(f'RSI超买({rsi_val:.1f})')
elif rsi_val < 30: reasons.append(f'RSI超卖({rsi_val:.1f})')
else: reasons.append(f'RSI中性({rsi_val:.1f})')
if latest['KDJ_J'] > 100: reasons.append(f'KDJ超买(J={latest["KDJ_J"]:.1f})')
elif latest['KDJ_J'] < 0: reasons.append(f'KDJ超卖(J={latest["KDJ_J"]:.1f})')
if latest['close'] > latest['BOLL_UPPER']: reasons.append('价格突破布林上轨')
elif latest['close'] < latest['BOLL_LOWER']: reasons.append('价格跌破布林下轨')
hv = latest['HV20']
if not np.isnan(hv): reasons.append(f'20日波动率{hv*100:.1f}%')

# 除权除息信息
dividend_info = []
if not div_df.empty:
    for _, row in div_df.iterrows():
        dividend_info.append({
            'ex_date': str(row['ex_date'].date()) if hasattr(row['ex_date'], 'date') else str(row['ex_date'])[:10],
            'plan': row['plan'],
            'cash_per_share': float(row['cash_per_share']),
            'stock_per_share': float(row['stock_per_share']),
        })

# 前复权 vs 不复权收盘价对比（最近30天）
qfq_vs_raw = []
for dt in df.index[-30:]:
    qfq_close = float(df.loc[dt, 'close'])
    if dt in df_raw.index:
        raw_close = float(df_raw.loc[dt, 'close'])
        qfq_vs_raw.append({
            'date': dt.strftime('%Y-%m-%d'),
            'qfq': round(qfq_close, 2),
            'raw': round(raw_close, 2),
            'ratio': round(qfq_close / raw_close, 4) if raw_close != 0 else 0,
        })

output = {
    'code': STOCK_CODE,
    'name': STOCK_NAME,
    'adjust_type': 'qfq',
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
    'dividend_info': dividend_info,
    'qfq_vs_raw': qfq_vs_raw,
    'strategies': [],
    'indicators': {
        'MA5': float(latest['MA5']), 'MA10': float(latest['MA10']),
        'MA20': float(latest['MA20']), 'MA60': float(latest['MA60']),
        'MACD_DIF': float(latest['MACD_DIF']), 'MACD_DEA': float(latest['MACD_DEA']),
        'MACD_BAR': float(latest['MACD_BAR']), 'RSI14': float(latest['RSI14']),
        'BOLL_UPPER': float(latest['BOLL_UPPER']), 'BOLL_MIDDLE': float(latest['BOLL_MIDDLE']),
        'BOLL_LOWER': float(latest['BOLL_LOWER']),
        'KDJ_K': float(latest['KDJ_K']), 'KDJ_D': float(latest['KDJ_D']),
        'KDJ_J': float(latest['KDJ_J']), 'CCI20': float(latest['CCI20']),
        'WR14': float(latest['WR14']), 'ATR14': float(latest['ATR14']),
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
        'weight': weights_map.get(sk, None),
    })

# 保存JSON
json_path = f'/workspace/stock_quant/data/{STOCK_CODE}_qfq_result.json'
os.makedirs(os.path.dirname(json_path), exist_ok=True)
with open(json_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

# ===== 详细输出 =====
print(f'\n{"="*80}')
print(f'  {STOCK_NAME}({STOCK_CODE}) 前复权量化分析结果')
print(f'{"="*80}')
print(f'  收盘价(前复权): {output["close"]:.2f}')
print(f'  日涨跌: {output["change"]*100:+.2f}%')
print(f'  区间累计收益: {output["total_ret"]*100:+.2f}%')
print(f'  数据范围: {output["start_date"]} ~ {output["end_date"]} ({output["data_rows"]}条)')
print(f'  综合结论: {verdict}  加权评分: {score:+.4f}')
print(f'  信号分布: {buy_count}买/{sell_count}卖/{hold_count}观')
print(f'  策略信号明细:')
for s in output['strategies']:
    sig_icon = {1: '买入', -1: '卖出', 0: '观望'}[s['signal']]
    w = f' (权重:{s["weight"]})' if s['weight'] else ''
    print(f'    [{s["category"]}] {s["name"]:12s} {sig_icon}{w}')
    if s['total_trades'] > 0:
        print(f'      → 收益{pct(s["total_return"])} 年化{pct(s["annual_return"])} 夏普{fmt(s["sharpe_ratio"])} 回撤{pct(s["max_drawdown"])} 胜率{pct(s["win_rate"])} 交易{s["total_trades"]}次')
print(f'  关键技术指标:')
for r in output['reasons']:
    print(f'    - {r}')

# 前复权 vs 不复权对比
print(f'\n  前复权 vs 不复权 收盘价对比 (最近5天):')
for item in qfq_vs_raw[-5:]:
    print(f'    {item["date"]}: qfq={item["qfq"]:.2f}  raw={item["raw"]:.2f}  ratio={item["ratio"]:.4f}')

# 除权除息历史
if dividend_info:
    print(f'\n  除权除息历史:')
    for d in dividend_info:
        print(f'    {d["ex_date"]}: {d["plan"]} (每股分红{d["cash_per_share"]}元, 送转{d["stock_per_share"]}股)')

print(f'\n  结果已保存至: {json_path}')
print(f'{"="*80}')