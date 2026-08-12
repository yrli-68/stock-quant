# 个股数据来源及存储结构

> 基于 `core/data_fetcher.py`、`core/indicators.py`、`main.py` 源码分析生成。

---

## 一、整体数据流

```
main.py: _analyze_single(symbol, ...)
│
├── 1. _resolve_symbol(symbol)              → 股票名称
│       ├── suggest3.sinajs.cn/suggest      → type=11 搜索
│       └── hq.sinajs.cn/list={code}        → 实时行情取名称(回退)
│
├── 2. DataFetcher.get_stock_data(...)      → 历史日K线(qfq)
│       └── _get_a_stock_data
│           ├── quotes.sina.com.cn/cn/api/json_v2.php/
│           │   CN_MarketData.getKLineData
│           │   ?symbol=sh600036&scale=240&ma=no&datalen=1000
│           │   → JSON 数组，每元素含 day/open/high/low/close/volume
│           │
│           └── finance.sina.com.cn/realstock/company/
│               sh600036/qfq.js
│               → 复权因子累乘序列
│               → pd.merge_asof 匹配后乘到 OHLC
│
├── 3. DataFetcher.get_realtime_quote(...)   → 实时行情(盘中)
│       └── hq.sinajs.cn/list=sh600036
│           → CSV: 名称,今开,昨收,现价,最高,最低,...,成交量,成交额
│           → 追加入 df 末行(开盘时段)
│
├── 4. add_all_indicators(df)                → 本地计算技术指标
│
├── 5. strategy.generate_signals(df)         → 本地计算策略信号
│       └── 内部按需调用 akshare 拉取:
│           ├── 估值: ak.stock_value_em / ak.stock_zh_valuation_baidu
│           ├── 财务: ak.stock_financial_analysis_indicator
│           ├── 筹码: ak.stock_hold_num_cninfo
│           └── 增减持: ak.stock_shareholder_change_ths
│
├── 6. BacktestEngine.run(df, signals)       → 回测 + 风险分析
│
└── 7. ChartGenerator                        → 图表 + HTML 报告
```

---

## 二、K线数据来源

### 2.1 历史日K线

**API:** `https://quotes.sina.com.cn/cn/api/json_v2.php/CN_MarketData.getKLineData`

| 参数 | 值 | 说明 |
|------|-----|------|
| `symbol` | `sh600036` | 新浪格式代码 |
| `scale` | `240` | 日线 |
| `ma` | `no` | 不返回均线 |
| `datalen` | `1000` | 最多返回条数 |

**返回格式:**
```json
[
  {"day":"2026-01-02","open":"35.00","high":"35.50","low":"34.80","close":"35.20","volume":"12345678"},
  ...
]
```

**字段到 DataFrame 映射:**
| JSON 字段 | DataFrame 列 | 类型 |
|-----------|-------------|------|
| `day` | `date` (设为索引) | `datetime64` |
| `open` | `open` | `float64` |
| `high` | `high` | `float64` |
| `low` | `low` | `float64` |
| `close` | `close` | `float64` |
| `volume` | `volume` | `float64` (手) |

**限制:** 最多 1000 条，约 4 年数据。时间范围外由日期过滤截断。

### 2.2 复权处理

**复权因子 API:** `https://finance.sina.com.cn/realstock/company/{sh600036}/{qfq}.js`

返回 JS 格式:
```javascript
var sh600036qfq = {
  "total": 32,
  "data": [
    {"d":"2023-06-15", "f":"1.0000"},
    {"d":"2024-06-20", "f":"1.0853"},
    ...
  ]
}
```

**复权算法:**
- `factor_df` 以 `d`(日期) 为索引, `f`(因子) 为值
- `pd.merge_asof(df, factor_df, direction='backward')`: 对每个交易日匹配 ≤ 该日期的最近因子
- `open/high/low/close × factor` → 复权价格
- 前复权(qfq): 最新因子=1.0，历史因子逐渐增大
- 后复权(hfq): 最早因子=1.0，最新因子逐渐增大

### 2.3 实时行情

**API:** `https://hq.sinajs.cn/list=sh600036`

**返回格式:** CSV (var hq_str_xxx="...")
```
招商银行,35.880,35.500,36.200,36.500,35.600, ...
  name   open   prev   price  high   low
```

**返回字段 (dict):**
| 键 | 含义 |
|-----|------|
| `name` | 证券名称 |
| `open` | 今日开盘 |
| `prev_close` | 昨日收盘 |
| `price` | 当前价 |
| `high` | 今日最高 |
| `low` | 今日最低 |
| `volume` | 成交量(手) |
| `amount` | 成交额 |
| `change` | 涨跌额 |
| `change_pct` | 涨跌幅% |
| `timestamp` | 请求时间 |

**盘中使用:** 开盘时间内获取的实时数据追加入 `df` 末行，参与技术指标计算和策略信号生成。

---

## 三、标准化输出结构

`_standardize_dataframe(df)` 输出:

| 列 | 类型 | 必需 | 来源 |
|----|------|:---:|------|
| `(index)` | `DatetimeIndex` | ✓ | 新浪 `day` |
| `open` | `float64` | ✓ | 新浪, qfq复权 |
| `high` | `float64` | ✓ | 新浪, qfq复权 |
| `low` | `float64` | ✓ | 新浪, qfq复权 |
| `close` | `float64` | ✓ | 新浪, qfq复权 |
| `volume` | `float64` | ✓ | 新浪 |
| `amount` | `float64` | | 成交额(部分源有) |
| `amplitude` | `float64` | | 振幅(部分源有) |
| `change_pct` | `float64` | | 涨跌幅(部分源有) |
| `change` | `float64` | | 涨跌额(部分源有) |
| `turnover_rate` | `float64` | | 换手率(部分源有) |

**元数据:** `df.attrs['symbol'] = '600036'`

---

## 四、技术指标 (`add_all_indicators`)

全部本地计算(pandas/numpy)，不依赖网络:

| 新增列 | 计算函数 | 依赖列 |
|--------|---------|--------|
| `MA5` / `MA10` / `MA20` / `MA60` | `calc_ma(period)` | `close` |
| `EMA12` / `EMA26` | `calc_ema(period)` | `close` |
| `MACD_DIF` / `MACD_DEA` / `MACD_BAR` | `calc_macd()` | `close` |
| `RSI14` | `calc_rsi()` | `close` |
| `BOLL_UPPER` / `BOLL_MIDDLE` / `BOLL_LOWER` | `calc_bollinger()` | `close` |
| `KDJ_K` / `KDJ_D` / `KDJ_J` | `calc_kdj()` | `close`, `high`, `low` |
| `ATR14` | `calc_atr()` | `high`, `low`, `close` |
| `OBV` | `calc_obv()` | `close`, `volume` |
| `CCI20` | `calc_cci()` | `high`, `low`, `close` |
| `WR14` | `calc_wr()` | `high`, `low`, `close` |
| `VOL_MA5` | `calc_volume_ma()` | `volume` |
| `VWAP` | `calc_vwap()` | `high`, `low`, `close`, `volume` |

额外指标(个别脚本添加):
| `HV20` | `calc_historical_volatility(20)` | `close` |
| `MOM60` | `calc_momentum_return(60)` | `close` |

---

## 五、策略用外部数据

QualityValueFactorStrategy 内置缓存(aKshare):

| 维度 | API | 返回类型 | 缓存机制 |
|------|-----|---------|---------|
| 估值 | `ak.stock_value_em(symbol)` | 时序 PE/PB/PS/PCF | 实例级 `_valuation_cache: {symbol: df}` |
| | → `ak.stock_zh_valuation_baidu` (备选) | | |
| 指数估值 | `ak.stock_index_pe_lg(name)` + `_pb_lg` | PE/PB 历史 | 同上 |
| 财务 | `ak.stock_financial_analysis_indicator(symbol)` | 报告期 ROE/净利率/负债率等 | 实例级 `_financials_cache` |
| 股东人数 | `ak.stock_hold_num_cninfo(date=xxx)` | 全市场 5000+ 行, 按代码筛选 | **类级** `_holder_date_cache` |
| 增减持 | `ak.stock_shareholder_change_ths(symbol)` | 大股东/高管交易记录 | 实例级 `_insider_cache` |

---

## 六、存储特点

### 6.1 不持久化
- K线数据和指标数据**不在本地文件系统存储**
- 每次运行 `analyze` 命令均重新从网络拉取
- DataFrame 在 Python 进程内存中传递, 无中间存储

### 6.2 缓存策略
| 缓存类型 | 作用域 | 生命周期 |
|---------|-------|---------|
| `_valuation_cache` | 实例级 dict | 同次 `analyze` 运行 |
| `_financials_cache` | 实例级 dict | 同次运行 |
| `_shareholder_cache` | 实例级 dict | 同次运行 |
| `_insider_cache` | 实例级 dict | 同次运行 |
| `_holder_date_cache` | 类级 dict | 同次 Python 进程, 跨实例共享 |

### 6.3 输出文件
| 路径 | 类型 | 生成时机 |
|------|------|---------|
| `output/{symbol}_*.png` | 图表 PNG | `_run_single_analysis` |
| `output/{symbol}_report.html` | 个股报告 | 单股分析末 |
| `output/report.html` | 批量汇总 | 批量分析末 |

### 6.4 输入文件
| 文件 | 格式 | 用途 |
|------|------|------|
| `input/stock-quant.json` | JSON | 自选股列表 + 综合策略权重 |
| `input/favs.json` | JSON 数组 | 自选股(回退) |
