#!/usr/bin/env python3
"""批量扫描A股，寻找买入信号最强的10只股票"""
import sys, os, warnings, json, requests
import pandas as pd, numpy as np
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.indicators import add_all_indicators
from core.backtest import BacktestEngine
from strategies.ma_cross import MACrossStrategy
from strategies.macd_strategy import MACDStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.bollinger_strategy import BollingerStrategy
from strategies.composite_strategy import CompositeStrategy

SM = {
    'ma_cross': MACrossStrategy,
    'macd': MACDStrategy,
    'rsi': RSIStrategy,
    'bollinger': BollingerStrategy,
    'composite': (lambda: CompositeStrategy([MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy()], threshold=0.3)),
}

def pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    return f'{v*100:.2f}%'

def fmt(v, f='.2f'):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    if isinstance(v, float) and np.isinf(v): return '∞'
    return f'{v:{f}}'

# ===== 1. 获取股票列表 =====
print('='*70)
print('  批量扫描A股 - 寻找买入信号')
print('='*70)
print('\n[1/3] 获取活跃股票列表...')

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://vip.stock.finance.sina.com.cn/'
}

# 获取多页数据（每页100只，共取5页=500只）
all_stocks = []
for page in range(1, 6):
    url = f'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=100&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=auto'
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        data = json.loads(resp.text)
        for item in data:
            code = item.get('code', '')
            name = item.get('name', '')
            volume = float(item.get('volume', 0))
            if code and volume > 500000:  # 成交量>50万手
                all_stocks.append((code, name, volume))
    except Exception as e:
        print(f'  第{page}页失败: {e}')

print(f'  获取到 {len(all_stocks)} 只活跃股票（成交量>50万手）')

# ===== 2. 批量扫描 =====
print(f'\n[2/3] 扫描分析中...')

# 排除ST、*ST、已分析的股票
skip_prefixes = ('ST', '*ST', 'N', 'C')
already_have = {'002920','000049','002008','000725','300763','000021','301308','300496','601138','600486','002468','002130','002432','600547','301075'}
candidates = [(c, n, v) for c, n, v in all_stocks if c not in already_have and not any(n.startswith(p) for p in skip_prefixes)]
print(f'  有效候选: {len(candidates)} 只')

buy_results = []

for i, (code, name, vol) in enumerate(candidates):
    if len(buy_results) >= 10 and i > 30:
        break  # 已经找到10只且至少扫描了30只
    
    if i % 10 == 0:
        print(f'  进度: {i+1}/{len(candidates)} (已找到 {len(buy_results)} 只买入)')
    
    try:
        # 获取数据
        prefix = 'sh' if code.startswith(('6','9')) else 'sz'
        sina_code = f'{prefix}{code}'
        url = f'https://quotes.sina.com.cn/cn/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen=300'
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}, timeout=10)
        data = resp.json()
        
        if len(data) < 60:
            continue
        
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['day'])
        df['open'] = pd.to_numeric(df['open'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])
        df['close'] = pd.to_numeric(df['close'])
        df['volume'] = pd.to_numeric(df['volume'])
        df = df[['date','open','high','low','close','volume']].set_index('date').sort_index()
        
        # 计算指标
        df = add_all_indicators(df)
        
        # 运行策略
        buy_count = 0
        sell_count = 0
        strategy_results = {}
        
        for sk, sclass in SM.items():
            try:
                if sk == 'composite':
                    s = CompositeStrategy([MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy()], threshold=0.3)
                else:
                    s = sclass()
                sig = s.generate_signals(df)
                last_sig = sig.iloc[-1]
                if last_sig == 1:
                    buy_count += 1
                elif last_sig == -1:
                    sell_count += 1
            except:
                pass
        
        # 至少2个策略买入、无卖出信号
        if buy_count >= 2 and sell_count == 0:
            # 跑完整回测
            results = {}
            for sk in SM:
                try:
                    if sk == 'composite':
                        s = CompositeStrategy([MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy()], threshold=0.3)
                    else:
                        s = SM[sk]()
                    sig = s.generate_signals(df)
                    e = BacktestEngine(initial_capital=100000)
                    r = e.run(df, sig)
                    results[sk] = r
                except:
                    pass
            
            if results:
                # 找最佳策略
                best_sk = max(results, key=lambda x: results[x].get('total_return', -999) or -999)
                best_r = results[best_sk]
                
                latest = df.iloc[-1]
                
                buy_results.append({
                    'code': code, 'name': name, 'close': latest['close'],
                    'buy_count': buy_count, 'best_strategy': best_sk,
                    'best_return': best_r.get('total_return'),
                    'best_sharpe': best_r.get('sharpe_ratio'),
                    'macd_dif': latest['MACD_DIF'], 'macd_dea': latest['MACD_DEA'],
                    'rsi': latest['RSI14'], 'ma5': latest['MA5'], 'ma20': latest['MA20'],
                    'ma60': latest['MA60'],
                })
                print(f'    ✅ {name}({code}) 买入信号{buy_count}个 最佳:{best_sk} 收益{pct(best_r.get("total_return"))}')
    except Exception as e:
        continue

# ===== 3. 排序输出 =====
print(f'\n[3/3] 结果汇总')
buy_results.sort(key=lambda x: x['buy_count'] * 100 + (x['best_return'] or 0), reverse=True)
top10 = buy_results[:10]

print()
print('='*90)
print('  推荐买入的10只股票 (2026-08-07)')
print('='*90)
print(f'  {"排名":<5s}{"股票":<12s}{"代码":<8s}{"收盘":<10s}{"买入信号":<10s}{"最佳策略":<10s}{"回测收益":<10s}{"夏普":<8s}')
print(f'  {"-"*86}')
for i, s in enumerate(top10, 1):
    print(f'  {i:<5d}{s["name"]:<12s}{s["code"]:<8s}{s["close"]:<10.2f}{str(s["buy_count"])+"/5":<10s}{s["best_strategy"]:<10s}{pct(s["best_return"]):<10s}{fmt(s["best_sharpe"]):<8s}')

print()
print('='*90)
print('  详细分析')
print('='*90)
for i, s in enumerate(top10, 1):
    print(f'\n  【{i}. {s["name"]}({s["code"]})】 收盘:{s["close"]:.2f}  买入信号:{s["buy_count"]}/5')
    print(f'    最佳策略: {s["best_strategy"]}  回测收益: {pct(s["best_return"])}  夏普: {fmt(s["best_sharpe"])}')
    print(f'    MA5={s["ma5"]:.2f} MA20={s["ma20"]:.2f} MA60={s["ma60"]:.2f} | RSI={s["rsi"]:.2f}')
    print(f'    MACD: DIF={s["macd_dif"]:.2f} DEA={s["macd_dea"]:.2f}')

if len(top10) < 10:
    print(f'\n  ⚠ 仅找到 {len(top10)} 只符合条件的股票，可放宽筛选条件获取更多')