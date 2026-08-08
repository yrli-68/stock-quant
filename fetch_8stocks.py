#!/usr/bin/env python3
"""为8只推荐买入股票获取数据"""
import sys, os, json, requests, time
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STOCKS = [
    ('688293', '奥浦迈'),
    ('688710', '益诺思'),
    ('688202', '美迪西'),
    ('688180', '君实生物'),
    ('688235', '百济神州'),
    ('300558', '贝达药业'),
    ('301080', '百普赛斯'),
    ('002437', '誉衡药业'),
]

os.makedirs('/workspace/stock_quant/data', exist_ok=True)

for code, name in STOCKS:
    data_file = f'/workspace/stock_quant/data/{code}_final.csv'
    if os.path.exists(data_file):
        df = pd.read_csv(data_file, parse_dates=['date'], index_col='date')
        if len(df) >= 100:
            print(f'{code} {name}: 已有 {len(df)} 条数据，跳过')
            continue

    prefix = 'sh' if code.startswith(('6','9')) else 'sz'
    sina_code = f'{prefix}{code}'
    
    # 尝试获取日线数据
    try:
        url = f'https://quotes.sina.com.cn/cn/api/json_v2.php/CN_MarketData.getKLineData?symbol={sina_code}&scale=240&ma=no&datalen=600'
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}, timeout=15)
        data = resp.json()
        
        if len(data) < 50:
            print(f'{code} {name}: 数据不足({len(data)}条)，跳过')
            continue
        
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['day'])
        df['open'] = pd.to_numeric(df['open'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])
        df['close'] = pd.to_numeric(df['close'])
        df['volume'] = pd.to_numeric(df['volume'])
        df = df[['date','open','high','low','close','volume']].set_index('date').sort_index()
        df.to_csv(data_file)
        print(f'{code} {name}: 获取 {len(df)} 条, {df.index[0].strftime("%Y-%m-%d")}~{df.index[-1].strftime("%Y-%m-%d")}')
    except Exception as e:
        print(f'{code} {name}: 失败 - {e}')

print('\n数据获取完成')