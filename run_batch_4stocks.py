#!/usr/bin/env python3
"""4只股票批量量化分析"""
import sys, os, warnings, json
import pandas as pd, numpy as np, requests
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

STOCKS = [
    ('300747', '锐科激光', 'sz'),
    ('002602', '世纪华通', 'sz'),
    ('001323', '慕思股份', 'sz'),
    ('002039', '黔源电力', 'sz'),
]

STOCK_WEIGHT = 0.20
INDEX_WEIGHT = 0.0667

def pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    return f'{v*100:.2f}%'

def fmt(v, f='.2f'):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    if isinstance(v, float) and np.isinf(v): return '∞'
    return f'{v:{f}}'

def fetch_data(code, prefix):
    sina_code = f'{prefix}{code}'
    url = f'https://quotes.sina.com.cn/cn/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen=600'
    resp = requests.get(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://finance.sina.com.cn/'
    }, timeout=15)
    data = resp.json()
    if not data or not isinstance(data, list):
        raise ValueError(f'数据为空')
    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['day'])
    df['open'] = pd.to_numeric(df['open'])
    df['high'] = pd.to_numeric(df['high'])
    df['low'] = pd.to_numeric(df['low'])
    df['close'] = pd.to_numeric(df['close'])
    df['volume'] = pd.to_numeric(df['volume'])
    df = df[['date','open','high','low','close','volume']].set_index('date').sort_index()
    return df

def analyze_stock(code, name, prefix):
    df = fetch_data(code, prefix)
    latest = df.iloc[-1]
    change = (latest['close'] - df.iloc[-2]['close']) / df.iloc[-2]['close']
    total_ret = (latest['close'] - df.iloc[0]['close']) / df.iloc[0]['close']

    df = add_all_indicators(df)
    df['HV20'] = calc_historical_volatility(df, 20)
    df['MOM60'] = calc_momentum_return(df, 60)

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

    all_results = {}
    all_signals = {}
    all_signal_names = {}

    for sk, (sname, s) in stock_strategies.items():
        sig = s.generate_signals(df)
        engine = BacktestEngine(initial_capital=100000)
        r = engine.run(df, sig, position_style='fraction')
        risk = risk_report(r['daily_returns'].dropna(), r['equity_curve'])
        r.update(risk)
        all_results[sk] = r
        all_signals[sk] = sig
        all_signal_names[sk] = sname

    for sk, (sname, s) in index_strategies.items():
        sig = s.generate_signals(df)
        engine = BacktestEngine(initial_capital=100000)
        r = engine.run(df, sig, position_style='fraction')
        risk = risk_report(r['daily_returns'].dropna(), r['equity_curve'])
        r.update(risk)
        all_results[sk] = r
        all_signals[sk] = sig
        all_signal_names[sk] = sname

    sig_all = composite_all.generate_signals(df)
    engine = BacktestEngine(initial_capital=100000)
    r_all = engine.run(df, sig_all, position_style='fraction')
    risk_all = risk_report(r_all['daily_returns'].dropna(), r_all['equity_curve'])
    r_all.update(risk_all)
    all_results['composite_all'] = r_all
    all_signals['composite_all'] = sig_all
    all_signal_names['composite_all'] = '综合(全量加权)'

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
            'key': sk, 'name': all_signal_names[sk],
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

    return output, verdict, v_color

# ===== 主流程 =====
print('='*80)
print('  4只股票批量量化分析')
print('='*80)

all_outputs = []
success_count = 0
fail_count = 0

for i, (code, name, prefix) in enumerate(STOCKS):
    print(f'\n[{i+1}/4] 分析 {name}({code})...', end=' ', flush=True)
    try:
        output, verdict, v_color = analyze_stock(code, name, prefix)
        json_path = f'/workspace/stock_quant/data/{code}_result.json'
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        all_outputs.append(output)
        success_count += 1
        print(f'OK  评分:{output["score"]:+.3f}  {verdict}  ({output["buy_count"]}买/{output["sell_count"]}卖/{output["hold_count"]}观)')
    except Exception as e:
        fail_count += 1
        print(f'FAIL: {str(e)[:80]}')

# ===== 详细输出 =====
for output in all_outputs:
    print(f'\n{"="*80}')
    print(f'  {output["name"]}({output["code"]}) 详细分析')
    print(f'{"="*80}')
    print(f'  收盘价: {output["close"]}  日涨跌: {output["change"]*100:+.2f}%  区间收益: {output["total_ret"]*100:+.2f}%')
    print(f'  数据: {output["start_date"]} ~ {output["end_date"]} ({output["data_rows"]}条)')
    print(f'  综合结论: {output["verdict"]}  加权评分: {output["score"]:+.4f}')
    print(f'  信号分布: {output["buy_count"]}买/{output["sell_count"]}卖/{output["hold_count"]}观')
    print(f'  策略信号:')
    for s in output['strategies']:
        sig_icon = {1: '买入', -1: '卖出', 0: '观望'}[s['signal']]
        w = f' (权重:{s["weight"]})' if s['weight'] else ''
        print(f'    [{s["category"]}] {s["name"]:12s} {sig_icon}{w}')
        if s['total_trades'] > 0:
            print(f'      → 收益{pct(s["total_return"])} 年化{pct(s["annual_return"])} 夏普{fmt(s["sharpe_ratio"])} 回撤{pct(s["max_drawdown"])} 胜率{pct(s["win_rate"])} 交易{s["total_trades"]}次')
    print(f'  关键指标:')
    for r in output['reasons']:
        print(f'    - {r}')

summary = {
    'generated': '2026-08-08',
    'success': success_count,
    'fail': fail_count,
    'stocks': all_outputs,
}
with open('/workspace/stock_quant/data/batch_4_summary.json', 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print(f'\n{"="*80}')
print(f'  完成! 成功 {success_count}/{len(STOCKS)}，失败 {fail_count}')
print(f'{"="*80}')