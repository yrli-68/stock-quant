#!/usr/bin/env python3
"""获取胜宏科技(300476)历史数据"""
import sys, os, json, requests, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

code = '300476'
name = '胜宏科技'
data_file = f'/workspace/stock_quant/data/{code}_final.csv'

# 尝试获取数据
prefix = 'sz'
sina_code = f'{prefix}{code}'
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

print(f'{name}({code}): {len(df)}条, {df.index[0].strftime("%Y-%m-%d")}~{df.index[-1].strftime("%Y-%m-%d")}')
print(f'最新收盘: {df["close"].iloc[-1]:.2f}  区间涨跌: {(df["close"].iloc[-1]/df["close"].iloc[0]-1)*100:+.2f}%')