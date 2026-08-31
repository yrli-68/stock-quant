#!/usr/bin/env python3
"""A股全面扫描 v2 — 板块分散，寻找10只买入推荐股票"""
import sys, os, warnings, json, requests, time, random
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

STOCK_WEIGHT = 0.20
INDEX_WEIGHT = 0.0667

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://vip.stock.finance.sina.com.cn/'
}

def pct(v):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    return f'{v*100:.2f}%'

def fmt(v, f='.2f'):
    if v is None or (isinstance(v, float) and np.isnan(v)): return 'N/A'
    if isinstance(v, float) and np.isinf(v): return '∞'
    return f'{v:{f}}'

# ===== 1. 获取广泛股票列表 =====
print('='*80)
print('  A股全面扫描 v2 — 板块分散寻找买入机会')
print('='*80)
print('\n[1/4] 获取A股列表...')

all_stocks = []
for page in range(1, 11):
    url = f'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=100&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=auto'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        data = json.loads(resp.text)
        for item in data:
            code = item.get('code', '')
            name = item.get('name', '')
            volume = float(item.get('volume', 0))
            if code and volume > 300000:
                all_stocks.append((code, name, volume))
    except Exception as e:
        pass

# 去重
seen = set()
unique_stocks = []
for c, n, v in all_stocks:
    if c not in seen:
        seen.add(c)
        unique_stocks.append((c, n, v))
all_stocks = unique_stocks

# 排除ST、*ST、N、C开头新股
skip_prefixes = ('ST', '*ST', 'N', 'C')
# 排除已分析过的12只
already_analyzed = {'002920','000021','000049','000725','002008','300763','300496','601138','300476','300726','002432','600547'}
candidates = [(c, n, v) for c, n, v in all_stocks
              if c not in already_analyzed
              and not any(n.startswith(p) for p in skip_prefixes)
              and not c.startswith(('8','4'))]  # 排除北交所/三板

# 按成交量排序，取前500只
candidates.sort(key=lambda x: x[2], reverse=True)
candidates = candidates[:500]
print(f'  有效候选: {len(candidates)} 只（成交量前500，排除ST/已分析）')

# 板块映射（基于代码段和常见分类）
SECTOR_MAP = {
    '银行': ['601398','601939','601288','601988','600036','600016','601328','601166','600000','600015','601818','601009','002142','601169','601229','600926','601838','601997','002839','600908'],
    '保险证券': ['601318','601628','601601','601336','600030','601211','601688','600837','601066','600999','601878','002797','600061','601236','002673','601375','601881','601162','002926','600909'],
    '白酒消费': ['600519','000858','000568','002304','600809','600702','000596','603369','600779','002568','000799','600132','600600','000729','002461','600559','603589','000930','600199','603198'],
    '医药生物': ['600276','000661','300122','300347','002007','603259','300760','600196','002821','300529','603392','300003','002001','600085','300595','002422','300015','000538','300142','002252'],
    '半导体芯片': ['002371','603501','688981','688256','688012','603986','688396','600703','002049','688008','002156','300782','688536','688037','688052','300661','688047','688153','688608','688234'],
    '新能源': ['300750','601012','002459','600438','601615','002129','688599','300274','300316','601877','688390','002340','688005','300450','688063','300763','300118','002074','688779','300568'],
    '电力能源': ['600900','601985','600011','600023','600025','600886','600674','600795','600025','600483','601222','000591','000690','000875','600509','000899','000027','600578','000600','000883'],
    '汽车产业链': ['600104','000625','601238','002594','600741','000800','600733','601689','002920','600699','600480','000887','002050','603786','600660','002126','000338','600418','000951','601799'],
    '有色金属': ['601899','600489','603993','600547','000426','000630','000960','000603','002155','600362','600711','000975','002460','000831','601600','600219','000807','600988','002428','000688'],
    '军工航天': ['600893','600760','600862','000768','002013','600118','600391','600879','600316','600372','000738','600765','002025','300114','000733','600184','300034','600435','002179','300696'],
    '软件IT': ['002230','600588','002410','300033','300624','002439','300454','300369','000977','300253','300773','002368','300525','300674','002912','300598','300663','300379','002261','300579'],
    '通信5G': ['000063','600050','601728','002281','600487','002396','300502','300308','300394','000988','300570','002583','300628','300548','000070','002313','300442','300615','002796','300563'],
    '化工材料': ['600309','002601','600352','603260','002648','600143','600486','000902','002497','300699','002064','600803','002409','002250','002326','300285','600989','002408','600141','000830'],
    '地产基建': ['000002','600048','001979','600383','600606','600340','000656','600325','600585','601668','601390','601800','601186','600170','000069','002271','600031','000425','600176','002372'],
    '家电家居': ['000333','002032','000651','600690','002050','002242','002508','603486','002959','603868','603043','002705','003023','603355','002614','002084','002790','002851','603515','603208'],
    '交通运输': ['601111','600029','600115','601006','601919','600009','600221','600004','600018','000089','600897','002120','002352','600233','002468','603056','600125','600377','601872','600798'],
    '农林牧渔': ['002714','000876','600438','002311','002385','002157','000998','600598','300498','002567','000860','002100','300087','002041','000713','600371','600354','002746','300761','002688'],
    '游戏传媒': ['002555','603444','002624','300418','300251','002602','300315','300494','002425','300052','600633','300113','300182','002174','002558','603533','300770','002354','603258','300571'],
    '智能制造': ['300124','603160','002747','002698','300450','688017','300024','002527','300222','002979','688188','300124','688003','002444','300400','688777','300161','300567','688686','688559'],
}

# 构建代码->板块反向映射
CODE_SECTOR = {}
for sector, codes in SECTOR_MAP.items():
    for c in codes:
        CODE_SECTOR[c] = sector

# 为没有映射的股票推断板块
def guess_sector(code, name):
    if code in CODE_SECTOR:
        return CODE_SECTOR[code]
    if code.startswith('6'):
        if code.startswith('601') and '银行' in name: return '银行'
        if code.startswith('601') and ('证券' in name or '保险' in name): return '保险证券'
        if code.startswith('601') and ('电' in name or '能' in name or '煤' in name): return '电力能源'
        if code.startswith('601') and ('航' in name or '船' in name or '港' in name): return '交通运输'
        if code.startswith('600') and ('药' in name or '医' in name or '生物' in name): return '医药生物'
        if code.startswith('600') and ('酒' in name or '食品' in name or '饮料' in name): return '白酒消费'
        if code.startswith('600') and ('有色' in name or '黄金' in name or '矿业' in name): return '有色金属'
        if code.startswith('600') and ('化工' in name or '材料' in name): return '化工材料'
        if code.startswith('600') and ('地产' in name or '城建' in name or '园区' in name): return '地产基建'
        if code.startswith('600') and ('汽车' in name or '客车' in name): return '汽车产业链'
        if code.startswith('600') and ('军工' in name or '航天' in name or '航空' in name): return '军工航天'
        if code.startswith('600') and ('软件' in name or '信息' in name or '科技' in name): return '软件IT'
        if code.startswith('600') and ('家电' in name or '电器' in name): return '家电家居'
        if code.startswith('600') and ('农业' in name or '畜牧' in name or '种子' in name): return '农林牧渔'
        if code.startswith('600') and ('传媒' in name or '游戏' in name or '出版' in name): return '游戏传媒'
        if code.startswith('600') and ('通信' in name or '光纤' in name or '5G' in name): return '通信5G'
        if code.startswith('600') and ('设备' in name or '制造' in name or '机械' in name): return '智能制造'
        return '主板综合'
    if code.startswith('3'):
        if any(k in name for k in ['药','医','生物','基因']): return '医药生物'
        if any(k in name for k in ['芯片','半导体','微','晶']): return '半导体芯片'
        if any(k in name for k in ['新能源','光伏','锂','电池','储能']): return '新能源'
        if any(k in name for k in ['软件','信息','数据','网','云']): return '软件IT'
        if any(k in name for k in ['通信','5G','光','讯']): return '通信5G'
        if any(k in name for k in ['军工','航','飞','防务']): return '军工航天'
        if any(k in name for k in ['汽车','车','驾']): return '汽车产业链'
        if any(k in name for k in ['传媒','游戏','文化','娱乐']): return '游戏传媒'
        if any(k in name for k in ['设备','制造','机器','智能','自动化']): return '智能制造'
        if any(k in name for k in ['化工','材料','化纤','涂料']): return '化工材料'
        return '创业板综合'
    if code.startswith('0'):
        if any(k in name for k in ['药','医','生物','基因']): return '医药生物'
        if any(k in name for k in ['酒','食品','饮料','乳']): return '白酒消费'
        if any(k in name for k in ['芯片','半导体','微','电子']): return '半导体芯片'
        if any(k in name for k in ['新能源','光伏','锂','电池','储能']): return '新能源'
        if any(k in name for k in ['软件','信息','数据','网','云']): return '软件IT'
        if any(k in name for k in ['通信','5G','光','讯']): return '通信5G'
        if any(k in name for k in ['军工','航','飞','防务']): return '军工航天'
        if any(k in name for k in ['汽车','车','驾']): return '汽车产业链'
        if any(k in name for k in ['化工','材料','化纤','涂料']): return '化工材料'
        if any(k in name for k in ['传媒','游戏','文化','娱乐']): return '游戏传媒'
        if any(k in name for k in ['地产','城','开发']): return '地产基建'
        if any(k in name for k in ['家电','电器']): return '家电家居'
        if any(k in name for k in ['农业','畜牧','种子','食品']): return '农林牧渔'
        if any(k in name for k in ['设备','制造','机器','智能','自动化']): return '智能制造'
        return '中小板综合'
    return '其他'

# 计算板块分布
sector_counts = {}
for c, n, v in candidates:
    s = guess_sector(c, n)
    sector_counts[s] = sector_counts.get(s, 0) + 1

print(f'  板块分布: {len(sector_counts)} 个板块')
for s, cnt in sorted(sector_counts.items(), key=lambda x: -x[1]):
    print(f'    {s}: {cnt}只')

# ===== 2. 快速扫描 — 只计算信号 =====
print(f'\n[2/4] 快速扫描 {len(candidates)} 只股票...')

# 策略实例（复用）
stock_strategies = {
    'ma_cross': MACrossStrategy(),
    'macd': MACDStrategy(),
    'rsi': RSIStrategy(),
    'bollinger': BollingerStrategy(),
}
index_strategies = {
    'momentum': MomentumTieredStrategy(),
    'volatility': VolatilityTimingStrategy(),
    'breadth': BreadthConfirmationStrategy(),
}

buy_candidates = []

for i, (code, name, vol) in enumerate(candidates):
    if i % 50 == 0:
        print(f'  进度: {i}/{len(candidates)} (已找到 {len(buy_candidates)} 只候选)')
    
    if len(buy_candidates) >= 200:
        break
    
    try:
        # 获取数据
        prefix = 'sh' if code.startswith(('6','9')) else 'sz'
        url = f'https://quotes.sina.com.cn/cn/api/json_v2.php/CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen=300'
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}, timeout=8)
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
        df['HV20'] = calc_historical_volatility(df, 20)
        df['MOM60'] = calc_momentum_return(df, 60)
        
        latest = df.iloc[-1]
        
        # 生成信号
        buy_count = 0
        sell_count = 0
        signal_detail = {}
        
        for sk, s in stock_strategies.items():
            sig = s.generate_signals(df)
            last = sig.iloc[-1]
            signal_detail[sk] = last
            if last == 1: buy_count += 1
            elif last == -1: sell_count += 1
        
        for sk, s in index_strategies.items():
            sig = s.generate_signals(df)
            last = sig.iloc[-1]
            signal_detail[sk] = last
            if last == 1: buy_count += 1
            elif last == -1: sell_count += 1
        
        # 加权评分
        score = 0
        for sk in stock_strategies:
            score += signal_detail.get(sk, 0) * STOCK_WEIGHT
        for sk in index_strategies:
            score += signal_detail.get(sk, 0) * INDEX_WEIGHT
        
        # 筛选条件：至少2个买入 + 0卖出 + 评分>0
        if buy_count >= 2 and sell_count == 0 and score > 0:
            sector = guess_sector(code, name)
            change = (latest['close'] - df.iloc[-2]['close']) / df.iloc[-2]['close']
            total_ret = (latest['close'] - df.iloc[0]['close']) / df.iloc[0]['close']
            
            buy_candidates.append({
                'code': code, 'name': name, 'sector': sector,
                'close': float(latest['close']), 'change': float(change),
                'total_ret': float(total_ret), 'score': float(score),
                'buy_count': buy_count, 'sell_count': sell_count,
                'signal_detail': signal_detail,
                'ma5': float(latest['MA5']), 'ma20': float(latest['MA20']),
                'ma60': float(latest['MA60']), 'rsi': float(latest['RSI14']),
                'hv20': float(latest['HV20']), 'mom60': float(latest['MOM60']),
                'macd_dif': float(latest['MACD_DIF']), 'macd_dea': float(latest['MACD_DEA']),
                'macd_bar': float(latest['MACD_BAR']),
                'df': df,  # 保留DataFrame供后续回测
            })
    except:
        continue

print(f'  扫描完成，找到 {len(buy_candidates)} 只候选股票')

# ===== 3. 板块分散精选 + 完整回测 =====
print(f'\n[3/4] 板块分散精选 + 完整回测...')

# 按评分排序
buy_candidates.sort(key=lambda x: x['score'] + (x['buy_count'] - 2) * 0.1, reverse=True)

# 板块分散选取
selected = []
used_sectors = {}

for cand in buy_candidates:
    sector = cand['sector']
    # 每个板块最多选2只
    if used_sectors.get(sector, 0) >= 2:
        continue
    if len(selected) >= 30:
        break
    selected.append(cand)
    used_sectors[sector] = used_sectors.get(sector, 0) + 1

print(f'  板块分散后选取 {len(selected)} 只（{len(used_sectors)} 个板块）')

# 对精选股票运行完整回测
final_results = []

for i, cand in enumerate(selected):
    print(f'  回测 {i+1}/{len(selected)}: {cand["name"]}({cand["code"]}) [{cand["sector"]}]...', end=' ')
    df = cand.pop('df')
    
    try:
        # 运行完整回测
        all_sub_strategies = [
            MACrossStrategy(), MACDStrategy(), RSIStrategy(), BollingerStrategy(),
            MomentumTieredStrategy(), VolatilityTimingStrategy(), BreadthConfirmationStrategy(),
        ]
        all_weights = [STOCK_WEIGHT]*4 + [INDEX_WEIGHT]*3
        composite_all = CompositeStrategy(all_sub_strategies, weights=all_weights, threshold=0.285, name='CompositeAll')
        
        sig_all = composite_all.generate_signals(df)
        engine = BacktestEngine(initial_capital=100000)
        r_all = engine.run(df, sig_all, position_style='fraction')
        risk_all = risk_report(r_all['daily_returns'].dropna(), r_all['equity_curve'])
        r_all.update(risk_all)
        
        cand['composite_return'] = r_all.get('total_return', 0) or 0
        cand['composite_sharpe'] = r_all.get('sharpe_ratio', 0) or 0
        cand['composite_winrate'] = r_all.get('win_rate', 0) or 0
        cand['composite_trades'] = r_all.get('total_trades', 0) or 0
        cand['composite_maxdd'] = r_all.get('max_drawdown', 0) or 0
        cand['composite_annual'] = r_all.get('annual_return', 0) or 0
        
        # 计算各策略回测收益
        strategy_returns = {}
        for sk, s in {**stock_strategies, **index_strategies}.items():
            sig = s.generate_signals(df)
            e = BacktestEngine(initial_capital=100000)
            r = e.run(df, sig, position_style='fraction')
            strategy_returns[sk] = r.get('total_return', 0) or 0
        cand['strategy_returns'] = strategy_returns
        
        final_results.append(cand)
        print(f'OK 综合收益{pct(cand["composite_return"])}')
    except Exception as e:
        print(f'SKIP: {str(e)[:40]}')

# 按综合评分排序，选Top10
final_results.sort(key=lambda x: x['score'] * 0.7 + (x.get('composite_return', 0) or 0) * 0.3, reverse=True)

# 最终精选：确保板块分散
final_10 = []
final_sectors = {}

for r in final_results:
    s = r['sector']
    if final_sectors.get(s, 0) >= 1:  # 每个板块最多1只
        continue
    if len(final_10) >= 10:
        break
    final_10.append(r)
    final_sectors[s] = final_sectors.get(s, 0) + 1

# 如果不够10只，放宽限制
if len(final_10) < 10:
    for r in final_results:
        if r in final_10:
            continue
        s = r['sector']
        if final_sectors.get(s, 0) >= 2:
            continue
        if len(final_10) >= 10:
            break
        final_10.append(r)
        final_sectors[s] = final_sectors.get(s, 0) + 1

print(f'\n[4/4] 最终精选 {len(final_10)} 只买入推荐股票')

# 生成详细原因
for r in final_10:
    reasons = []
    if r['close'] > r['ma5']: reasons.append('站上MA5')
    else: reasons.append('跌破MA5')
    if r['close'] > r['ma20']: reasons.append('站上MA20')
    else: reasons.append('跌破MA20')
    if r['close'] > r['ma60']: reasons.append('站上MA60')
    else: reasons.append('跌破MA60')
    if r['macd_dif'] > r['macd_dea']: reasons.append('MACD金叉')
    else: reasons.append('MACD死叉')
    if r['rsi'] > 70: reasons.append(f'RSI超买({r["rsi"]:.1f})')
    elif r['rsi'] < 30: reasons.append(f'RSI超卖({r["rsi"]:.1f})')
    else: reasons.append(f'RSI中性({r["rsi"]:.1f})')
    if not np.isnan(r['hv20']): reasons.append(f'波动率{r["hv20"]*100:.1f}%')
    if not np.isnan(r['mom60']): reasons.append(f'60日动量{r["mom60"]*100:+.2f}%')
    r['reasons'] = reasons

# 输出结果
print()
print('='*90)
print('  推荐买入的10只股票 (板块分散)')
print('='*90)
print(f'  {"排名":<5s}{"股票":<10s}{"代码":<8s}{"板块":<12s}{"收盘":<8s}{"评分":<8s}{"买入":<5s}{"综合收益":<10s}{"夏普":<8s}')
print(f'  {"-"*86}')
for i, r in enumerate(final_10, 1):
    print(f'  {i:<5d}{r["name"]:<10s}{r["code"]:<8s}{r["sector"]:<12s}{r["close"]:<8.2f}{r["score"]:<+8.3f}{r["buy_count"]}/7{"":<1s}{pct(r.get("composite_return")):<10s}{fmt(r.get("composite_sharpe")):<8s}')

print()
for i, r in enumerate(final_10, 1):
    print(f'  【{i}. {r["name"]}({r["code"]})】 {r["sector"]} | 收盘:{r["close"]:.2f} | 评分:{r["score"]:+.3f} | {r["buy_count"]}/7买入')
    print(f'    综合回测: 收益{pct(r.get("composite_return"))} 夏普{fmt(r.get("composite_sharpe"))} 最大回撤{pct(r.get("composite_maxdd"))}')
    print(f'    技术面: {"; ".join(r["reasons"])}')
    sig_text = []
    for sk in ['ma_cross','macd','rsi','bollinger','momentum','volatility','breadth']:
        v = r['signal_detail'].get(sk, 0)
        t = {1:'买入',-1:'卖出',0:'观望'}.get(v,'?')
        sig_text.append(f'{sk}={t}')
    print(f'    信号: {" | ".join(sig_text)}')

# 保存JSON
output = {
    'generated': '2026-08-08',
    'config': '个股策略各20%(合计80%)，指数策略各6.67%(合计20%)，已取消综合(个股)',
    'total_scanned': len(candidates),
    'candidates_found': len(buy_candidates),
    'final_count': len(final_10),
    'sectors': list(final_sectors.keys()),
    'stocks': final_10,
}

with open('/workspace/stock_quant/data/scanner_v2_top10.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2, default=str)

print(f'\n  结果已保存到 data/scanner_v2_top10.json')
print(f'  {"="*90}')