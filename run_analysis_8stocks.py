#!/usr/bin/env python3
"""图中8只股票批量量化分析：高争民爆 璞泰来 凯旺科技 金力永磁 泓博医药 华明装备 隆盛科技 福龙马"""
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
    ('002827', '高争民爆'),
    ('603659', '璞泰来'),
    ('301182', '凯旺科技'),
    ('300748', '金力永磁'),
    ('301230', '泓博医药'),
    ('002270', '华明装备'),
    ('300680', '隆盛科技'),
    ('603686', '福龙马'),
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
    r = e.run(df, sig)
    r2 = risk_report(r['daily_returns'].dropna(), r['equity_curve'])
    r.update(r2)
    return r, sig

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

    # 技术指标
    print(f'  MA5={latest["MA5"]:.2f} MA20={latest["MA20"]:.2f} MA60={latest["MA60"]:.2f}')
    print(f'  MACD: DIF={latest["MACD_DIF"]:.2f} DEA={latest["MACD_DEA"]:.2f} BAR={latest["MACD_BAR"]:.2f}')
    print(f'  RSI14={latest["RSI14"]:.2f} KDJ: K={latest["KDJ_K"]:.2f} D={latest["KDJ_D"]:.2f} J={latest["KDJ_J"]:.2f}')
    print(f'  布林: 上={latest["BOLL_UPPER"]:.2f} 中={latest["BOLL_MIDDLE"]:.2f} 下={latest["BOLL_LOWER"]:.2f}')
    print(f'  CCI={latest["CCI20"]:.2f} WR={latest["WR14"]:.2f}  ATR={latest["ATR14"]:.2f}')

    # 回测
    results, signals = {}, {}
    for sk in SM:
        results[sk], signals[sk] = run(sk, df, 100000)

    rankings = sorted(results.items(), key=lambda x: x[1].get('total_return', -999) or -999, reverse=True)

    print(f'\n  {"策略":<10s}{"总收益":<10s}{"年化":<10s}{"夏普":<8s}{"回撤":<10s}{"胜率":<8s}{"交易":<6s}{"信号":<10s}')
    print(f'  {"-"*68}')
    for sk, r in rankings:
        sig = signals[sk].iloc[-1]
        a = {1:'买入', -1:'卖出', 0:'观望'}[sig]
        print(f'  {SM[sk][0]:<10s}{pct(r.get("total_return")):<10s}{pct(r.get("annual_return")):<10s}{fmt(r.get("sharpe_ratio")):<8s}{pct(r.get("max_drawdown")):<10s}{pct(r.get("win_rate")):<8s}{str(r.get("total_trades")):<6s}{a:<10s}')

    best = rankings[0]
    best_sig = signals[best[0]].iloc[-1]

    # 综合判断
    macd_dif = latest['MACD_DIF']
    macd_dea = latest['MACD_DEA']
    rsi = latest['RSI14']
    kdj_j = latest['KDJ_J']
    kdj_k = latest['KDJ_K']
    kdj_d = latest['KDJ_D']
    ma5 = latest['MA5']
    ma20 = latest['MA20']
    ma60 = latest['MA60']
    cci = latest['CCI20']
    wr = latest['WR14']

    # 多维度信号评分
    score = 0
    reasons = []
    # MACD
    if macd_dif > macd_dea and macd_dif > 0:
        score += 2; reasons.append('MACD多头强势')
    elif macd_dif > macd_dea:
        score += 1; reasons.append('MACD偏多')
    elif macd_dif < macd_dea and macd_dif < 0:
        score -= 2; reasons.append('MACD空头强势')
    else:
        score -= 1; reasons.append('MACD偏空')
    # RSI
    if rsi < 30:
        score += 2; reasons.append('RSI超卖(反弹)')
    elif rsi > 70:
        score -= 2; reasons.append('RSI超买(回调)')
    elif 40 <= rsi <= 60:
        score += 0; reasons.append('RSI中性')
    elif rsi > 60:
        score += 1; reasons.append('RSI偏强')
    else:
        score -= 1; reasons.append('RSI偏弱')
    # KDJ
    if kdj_j < 0:
        score += 2; reasons.append('KDJ超卖')
    elif kdj_j > 100:
        score -= 2; reasons.append('KDJ超买')
    # 均线
    if close > ma5 > ma20 > ma60:
        score += 3; reasons.append('均线多头排列')
    elif close > ma5 > ma20:
        score += 2; reasons.append('均线偏多')
    elif close < ma5 < ma20 < ma60:
        score -= 3; reasons.append('均线空头排列')
    elif close < ma5 < ma20:
        score -= 2; reasons.append('均线偏空')
    elif close > ma60:
        score += 1; reasons.append('站上MA60')
    elif close < ma60:
        score -= 1; reasons.append('跌破MA60')
    # 布林带
    if close <= latest['BOLL_LOWER']:
        score += 2; reasons.append('触及布林下轨')
    elif close >= latest['BOLL_UPPER']:
        score -= 2; reasons.append('触及布林上轨')
    elif close < latest['BOLL_MIDDLE']:
        score -= 1; reasons.append('布林中轨下方')
    else:
        score += 1; reasons.append('布林中轨上方')
    # CCI
    if cci < -100:
        score += 1; reasons.append('CCI超卖')
    elif cci > 100:
        score -= 1; reasons.append('CCI超买')

    # 策略信号统计
    buy_count = sum(1 for sk in SM if signals[sk].iloc[-1] == 1)
    sell_count = sum(1 for sk in SM if signals[sk].iloc[-1] == -1)
    hold_count = sum(1 for sk in SM if signals[sk].iloc[-1] == 0)

    # 综合裁决
    if buy_count >= 4:
        verdict = '强烈买入'
    elif buy_count >= 3:
        verdict = '买入'
    elif buy_count >= 2 and sell_count <= 1:
        verdict = '偏多买入'
    elif sell_count >= 4:
        verdict = '强烈卖出'
    elif sell_count >= 3:
        verdict = '卖出'
    elif sell_count >= 2 and buy_count <= 1:
        verdict = '偏空卖出'
    elif buy_count >= 1:
        verdict = '谨慎偏多'
    elif hold_count >= 4:
        verdict = '观望'
    else:
        verdict = '观望'

    print(f'\n  综合评分: {score:+d}  买入信号:{buy_count}  卖出信号:{sell_count}  观望:{hold_count}')
    print(f'  综合建议: 【{verdict}】')
    print(f'  技术面: {"; ".join(reasons)}')

    all_summary.append({
        'name': name, 'code': code, 'close': close, 'change': change,
        'total_ret': total_ret, 'best_strategy': SM[best[0]][0],
        'best_return': best[1].get('total_return'), 'best_sharpe': best[1].get('sharpe_ratio'),
        'best_annual': best[1].get('annual_return'),
        'buy': buy_count, 'sell': sell_count, 'hold': hold_count,
        'verdict': verdict, 'score': score, 'reasons': '; '.join(reasons),
        'macd_dif': macd_dif, 'macd_dea': macd_dea, 'rsi': rsi,
        'ma5': ma5, 'ma20': ma20, 'ma60': ma60,
        'kdj_k': kdj_k, 'kdj_d': kdj_d, 'kdj_j': kdj_j,
        'cci': cci, 'wr': wr,
    })

# ===== 最终汇总 =====
print('\n\n')
print('=' * 100)
print('  8只股票量化综合分析汇总表')
print('=' * 100)
print(f'  {"股票":<10s}{"收盘":<8s}{"当日%":<8s}{"区间%":<10s}{"评分":<6s}{"最佳策略":<10s}{"最佳收益":<10s}{"最佳夏普":<8s}{"买入":<5s}{"卖出":<5s}{"综合建议":<12s}')
print(f'  {"-"*98}')
for s in sorted(all_summary, key=lambda x: x['score'], reverse=True):
    print(f'  {s["name"]:<10s}{s["close"]:<8.2f}{s["change"]:<+8.2f}{s["total_ret"]:<+10.2f}{s["score"]:<+6d}{s["best_strategy"]:<10s}{pct(s["best_return"]):<10s}{fmt(s["best_sharpe"]):<8s}{s["buy"]:<5d}{s["sell"]:<5d}{s["verdict"]:<12s}')

print(f'\n  {"="*100}')
print(f'  各股票详细买卖建议')
print(f'  {"="*100}')
for s in sorted(all_summary, key=lambda x: x['score'], reverse=True):
    print(f'\n  【{s["name"]}({s["code"]})】 评分:{s["score"]:+d} → {s["verdict"]}')
    print(f'    收盘:{s["close"]:.2f}  当日涨跌:{s["change"]:+.2f}%  区间涨跌:{s["total_ret"]:+.2f}%')
    print(f'    最佳策略:{s["best_strategy"]}(收益{pct(s["best_return"])} 年化{pct(s["best_annual"])} 夏普{fmt(s["best_sharpe"])})')
    print(f'    策略信号: 买入{s["buy"]}个 卖出{s["sell"]}个 观望{s["hold"]}个')
    print(f'    技术面: {s["reasons"]}')
    print(f'    MA5={s["ma5"]:.2f} MA20={s["ma20"]:.2f} MA60={s["ma60"]:.2f} | RSI={s["rsi"]:.2f} | KDJ K={s["kdj_k"]:.2f} D={s["kdj_d"]:.2f} J={s["kdj_j"]:.2f}')
    print(f'    MACD DIF={s["macd_dif"]:.2f} DEA={s["macd_dea"]:.2f} | CCI={s["cci"]:.2f} WR={s["wr"]:.2f}')