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
├── 2. DataFetcher.get_stock_data(...)      → 历史日K线(原始价入库/出库, 内存前复权)
│       └── _get_a_stock_data
│           ├── quotes.sina.com.cn/cn/api/json_v2.php/
│           │   CN_MarketData.getKLineData
│           │   ?symbol=sh600036&scale=240&ma=no&datalen=1000
│           │   → JSON 数组，每元素含 day/open/high/low/close/volume（原始价）
│           │
│           ├── 数据库 daily_kline (adjust_type='raw')
│           │   → 结束日非当天时优先读库，缺失时段网络补齐后回写原始价
│           │
│           └── finance.sina.com.cn/realstock/company/
│               sh600036/qfq.js
│               → 复权因子累乘序列
│               → 内存中 pd.merge_asof 匹配后换算 OHLC（默认前复权）
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
- 网络与数据库均只保存/返回**原始价格**（`daily_kline.adjust_type='raw'`）
- 读取后用复权因子在**内存中**换算，默认前复权(qfq)
- `factor_df` 以 `d`(日期) 为索引, `f`(因子) 为值
- `pd.merge_asof(df, factor_df, direction='backward')`: 对每个交易日匹配 ≤ 该日期的最近因子
- 前复权(qfq): `open/high/low/close / factor`，最新因子=1.0，历史因子逐渐增大
- 后复权(hfq): `open/high/low/close × factor`，最早因子=1.0，最新因子逐渐增大
- 不复权(raw): 直接使用原始价格

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
| `open` | `float64` | ✓ | 原始价, 读取后内存 qfq |
| `high` | `float64` | ✓ | 原始价, 读取后内存 qfq |
| `low` | `float64` | ✓ | 原始价, 读取后内存 qfq |
| `close` | `float64` | ✓ | 原始价, 读取后内存 qfq |
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
| `SAR` | `calc_sar()` | `high`, `low`, `close` |
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
| 财务 | `ak.stock_financial_analysis_indicator(symbol)` | 报告期 ROE/净利率/负债率等（公告日需另补） | 实例级 `_financials_cache` |
| 股东人数 | `ak.stock_hold_num_cninfo(date=xxx)` | 全市场 5000+ 行, 按代码筛选 | **类级** `_holder_date_cache` |
| 增减持 | `ak.stock_shareholder_change_ths(symbol)` | 大股东/高管交易记录 | 实例级 `_insider_cache` |

---

## 七、MySQL 数据表设计

> DDL 定义位于 `data/stock_quant.sql`，基于 MySQL 8.0+。

### 7.0 设计原则

- **统一资产标识**: 所有表使用 `(code, exchange, asset_type)` 三元组唯一标识资产
- **资产类型**: `stock` / `index` / `etf` / `fund` / `bond`
- **字符集**: `utf8mb4`，引擎: `InnoDB`
- **复权类型**: `daily_kline` 固定存原始价 `adjust_type='raw'`，复权在内存中完成（默认 qfq）；指标表仍按 `adjust_type` 区分

---

### 7.1 资产基本信息表 (`stocks`)

存储股票/指数/ETF等资产的基本信息。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `code` | VARCHAR(10) | PK, NOT NULL | 股票代码 (如 600036) |
| `exchange` | VARCHAR(8) | PK, NOT NULL | 交易所 (SH/SZ/BJ) |
| `asset_type` | ENUM | PK, NOT NULL | 资产类型 |
| `name` | VARCHAR(50) | NOT NULL | 资产名称 |
| `market` | VARCHAR(4) | | 市场板块 |
| `stock_type` | VARCHAR(20) | DEFAULT 'dividend' | 股票类型 |
| `is_etf` | TINYINT | DEFAULT 0 | 是否ETF |
| `listed_date` | DATE | | 上市日期 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | TIMESTAMP | ON UPDATE CURRENT_TIMESTAMP | 更新时间 |

**主键:** `(code, exchange, asset_type)`

---

### 7.2 日K线表 (`daily_kline`)

存储每日 OHLCV 行情数据（**原始价格，不复权**）。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INT | PK, AUTO_INCREMENT | 自增主键 |
| `code` | VARCHAR(10) | NOT NULL | 股票代码 |
| `exchange` | VARCHAR(8) | NOT NULL | 交易所 |
| `asset_type` | ENUM | NOT NULL | 资产类型 |
| `trade_date` | DATE | NOT NULL | 交易日期 |
| `open` | DOUBLE | NOT NULL | 开盘价（原始价） |
| `high` | DOUBLE | NOT NULL | 最高价（原始价） |
| `low` | DOUBLE | NOT NULL | 最低价（原始价） |
| `close` | DOUBLE | NOT NULL | 收盘价（原始价） |
| `volume` | DOUBLE | NOT NULL | 成交量(手) |
| `amount` | DOUBLE | | 成交额 |
| `amplitude` | DOUBLE | | 振幅 |
| `change_pct` | DOUBLE | | 涨跌幅(%) |
| `change_val` | DOUBLE | | 涨跌额 |
| `turnover_rate` | DOUBLE | | 换手率 |
| `adjust_type` | VARCHAR(10) | NOT NULL, DEFAULT 'raw' | 固定 'raw'（读取后内存复权） |
| `is_realtime` | TINYINT | DEFAULT 0 | 是否实时行情 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**唯一键:** `(code, exchange, asset_type, trade_date, adjust_type)`
**索引:** `(trade_date, exchange, asset_type)`

> 说明：本表只落库原始价。查询后由 `_apply_in_memory_adjust` 使用复权因子在内存中换算为前复权价（默认），避免入库存复权价导致因子变动后历史数据失真。

---

### 7.3 除权除息事件表 (`adjust_events`)

存储分红送转等除权除息事件。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INT | PK, AUTO_INCREMENT | 自增主键 |
| `code` | VARCHAR(10) | NOT NULL | 股票代码 |
| `exchange` | VARCHAR(8) | NOT NULL | 交易所 |
| `asset_type` | ENUM | NOT NULL | 资产类型 |
| `ex_date` | DATE | NOT NULL | 除权除息日 |
| `cash_per_share` | DOUBLE | DEFAULT 0 | 每股现金分红(元) |
| `stock_per_share` | DOUBLE | DEFAULT 0 | 每股送转股数 |
| `plan_desc` | VARCHAR(100) | | 分红方案描述 |
| `record_date` | DATE | | 股权登记日 |
| `cash_date` | DATE | | 现金红利发放日 |
| `announce_date` | DATE | | 公告日期 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**唯一键:** `(code, exchange, asset_type, ex_date)`
**索引:** `(ex_date, asset_type)`

**方案解析规则:**
| 方案描述 | cash_per_share | stock_per_share |
|---------|----------------|-----------------|
| `10派10.03元` | 1.003 | 0.0 |
| `10转4派7元` | 0.7 | 0.4 |
| `10送2股转3股派1元` | 0.1 | 0.5 |
| `不分配不转增` | 0.0 | 0.0 |

---

### 7.4 技术指标表 (`daily_indicators`)

存储每日计算的技术指标。

| 字段组 | 字段 | 说明 |
|--------|------|------|
| **基础** | `code`, `exchange`, `asset_type`, `trade_date`, `adjust_type` | 资产标识 |
| **均线** | `ma5`, `ma10`, `ma20`, `ma30`, `ma60` | 移动平均线 |
| **EMA** | `ema10`, `ema12`, `ema26`, `ema30` | 指数移动平均 |
| **MACD** | `macd_dif`, `macd_dea`, `macd_bar` | 异同移动平均线 |
| **RSI** | `rsi14` | 相对强弱指标 |
| **布林带** | `boll_upper`, `boll_middle`, `boll_lower` | BOLL上下轨 |
| **KDJ** | `kdj_k`, `kdj_d`, `kdj_j` | 随机指标 |
| **DMI** | `adx14`, `plus_di`, `minus_di` | 趋势指标 |
| **其他** | `atr14`, `obv`, `cci20`, `wr14`, `vol_ma5`, `vwap` | 辅助指标 |
| **扩展** | `hv20`, `mom60` | 波动率/动量 |
| **时间** | `created_at` | 创建时间 |

**唯一键:** `(code, exchange, asset_type, trade_date, adjust_type)`
**索引:** `(trade_date, exchange, asset_type)`

---

### 7.5 估值历史表 (`valuation_history`)

存储历史估值数据。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INT | PK, AUTO_INCREMENT | 自增主键 |
| `code` | VARCHAR(10) | NOT NULL | 股票代码 |
| `exchange` | VARCHAR(8) | NOT NULL | 交易所 |
| `asset_type` | ENUM | NOT NULL | 资产类型 |
| `val_date` | DATE | NOT NULL | 估值日期 |
| `pe_ttm` | DOUBLE | | 市盈率(TTM) |
| `pb` | DOUBLE | | 市净率 |
| `ps` | DOUBLE | | 市销率 |
| `pcf` | DOUBLE | | 市现率 |
| `ey` | DOUBLE | | 盈利收益率 (1/PE) |
| `dividend_yield` | DOUBLE | | 股息率 |
| `source` | VARCHAR(30) | | 数据来源 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**唯一键:** `(code, exchange, asset_type, val_date)`
**索引:** `(val_date, asset_type)`

---

### 7.6 财务报告表 (`financial_reports`)

存储财务指标，支持修订版本管理。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INT | PK, AUTO_INCREMENT | 自增主键 |
| `code` | VARCHAR(10) | NOT NULL | 股票代码 |
| `exchange` | VARCHAR(8) | NOT NULL | 交易所 |
| `asset_type` | ENUM | NOT NULL | 资产类型 |
| `report_date` | DATE | NOT NULL | 报告期 |
| `announce_date` | DATE | | 公告日 (回测对齐依据) |
| `data_version` | INT | NOT NULL, DEFAULT 1 | 修订版本 |
| `roe` | DOUBLE | | 净资产收益率 |
| `cashflow_net_profit_ratio` | DOUBLE | | 现金流净利润比 |
| `net_margin` | DOUBLE | | 净利率 |
| `debt_ratio` | DOUBLE | | 资产负债率 |
| `profit_growth` | DOUBLE | | 利润增长率 |
| `gross_margin` | DOUBLE | | 毛利率 |
| `roa` | DOUBLE | | 总资产收益率 |
| `revenue_growth` | DOUBLE | | 营收增长率 |
| `eps` | DOUBLE | | 每股收益 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**唯一键:** `(code, exchange, asset_type, report_date, data_version)`
**索引:**
- `(code, exchange, asset_type, announce_date, report_date)` - 回测查询
- `(report_date, asset_type)` - 按报告期查询

> **重要:** 回测取数必须按 `announce_date <= 当前交易日` 对齐，禁止按 `report_date` 直接取数。

---

### 7.7 股东人数表 (`shareholder_history`)

存储股东人数变化，用于分析筹码集中度。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INT | PK, AUTO_INCREMENT | 自增主键 |
| `code` | VARCHAR(10) | NOT NULL | 股票代码 |
| `exchange` | VARCHAR(8) | NOT NULL | 交易所 |
| `asset_type` | ENUM | NOT NULL | 资产类型 |
| `change_date` | DATE | NOT NULL | 变动日期 |
| `holder_count` | DOUBLE | | 股东户数 |
| `prev_holder_count` | DOUBLE | | 上期股东户数 |
| `holder_change_pct` | DOUBLE | | 股东户数变动(%) |
| `avg_holding` | DOUBLE | | 户均持股 |
| `prev_avg_holding` | DOUBLE | | 上期户均持股 |
| `avg_holding_change_pct` | DOUBLE | | 户均持股变动(%) |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**唯一键:** `(code, exchange, asset_type, change_date)`
**索引:** `(change_date, asset_type)`

---

### 7.8 增减持记录表 (`insider_trades`)

存储大股东/高管增减持记录。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INT | PK, AUTO_INCREMENT | 自增主键 |
| `code` | VARCHAR(10) | NOT NULL | 股票代码 |
| `exchange` | VARCHAR(8) | NOT NULL | 交易所 |
| `asset_type` | ENUM | NOT NULL | 资产类型 |
| `announce_date` | DATE | NOT NULL | 公告日期 |
| `shareholder` | VARCHAR(100) | | 股东名称 |
| `change_amount` | DOUBLE | | 变动股数 |
| `change_text` | VARCHAR(50) | | 变动描述 |
| `trade_price` | DOUBLE | | 交易价格 |
| `remaining` | DOUBLE | | 剩余股数 |
| `trade_period` | VARCHAR(50) | | 交易期间 |
| `trade_method` | VARCHAR(50) | | 交易方式 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**唯一键:** `(code, exchange, asset_type, announce_date, shareholder, change_amount)`
**索引:** `(announce_date, asset_type)`

---

### 7.9 策略信号表 (`strategy_signals`)

存储策略生成的买卖信号。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INT | PK, AUTO_INCREMENT | 自增主键 |
| `code` | VARCHAR(10) | NOT NULL | 股票代码 |
| `exchange` | VARCHAR(8) | NOT NULL | 交易所 |
| `asset_type` | ENUM | NOT NULL | 资产类型 |
| `trade_date` | DATE | NOT NULL | 交易日期 |
| `strategy_key` | VARCHAR(30) | NOT NULL | 策略标识 |
| `signal` | TINYINT | NOT NULL | 信号 (1=买入, -1=卖出, 0=持有) |
| `score` | DOUBLE | | 信号评分 |
| `params_hash` | VARCHAR(64) | NOT NULL | 参数哈希 |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

**唯一键:** `(code, exchange, asset_type, trade_date, strategy_key, params_hash)`
**索引:** `(strategy_key, trade_date, signal)`

---

### 7.10 回测结果表 (`backtest_results`)

存储回测绩效汇总。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | INT | PK, AUTO_INCREMENT | 自增主键 |
| `code` | VARCHAR(10) | NOT NULL | 股票代码 |
| `exchange` | VARCHAR(8) | NOT NULL | 交易所 |
| `asset_type` | ENUM | NOT NULL | 资产类型 |
| `strategy_key` | VARCHAR(30) | NOT NULL | 策略标识 |
| `start_date` | DATE | NOT NULL | 回测起始日 |
| `end_date` | DATE | NOT NULL | 回测结束日 |
| `capital` | DOUBLE | NOT NULL, DEFAULT 100000 | 初始资金 |
| `total_return` | DOUBLE | | 总收益率 |
| `annual_return` | DOUBLE | | 年化收益 |
| `max_drawdown` | DOUBLE | | 最大回撤 |
| `sharpe_ratio` | DOUBLE | | 夏普比率 |
| `win_rate` | DOUBLE | | 胜率 |
| `total_trades` | INT | | 总交易次数 |
| `profit_trades` | INT | | 盈利次数 |
| `loss_trades` | INT | | 亏损次数 |
| `profit_factor` | DOUBLE | | 盈亏比 |
| `annual_volatility` | DOUBLE | | 年化波动率 |
| `calmar_ratio` | DOUBLE | | 卡尔玛比率 |
| `sortino_ratio` | DOUBLE | | 索提诺比率 |
| `var_95` | DOUBLE | | VaR(95%) |
| `cvar_95` | DOUBLE | | CVaR(95%) |
| `avg_profit` | DOUBLE | | 平均盈利 |
| `avg_loss` | DOUBLE | | 平均亏损 |
| `avg_holding_days` | DOUBLE | | 平均持仓天数 |
| `params_hash` | VARCHAR(64) | NOT NULL | 参数哈希 |
| `run_time` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 运行时间 |

**唯一键:** `(code, exchange, asset_type, strategy_key, start_date, end_date, params_hash)`
**索引:** `(strategy_key, start_date, end_date)`

---

### 7.11 市场广度表 (`market_breadth`)

存储市场广度指标(涨跌家数等)。

| 字段 | 类型 | 说明 |
|------|------|------|
| `universe_code` | VARCHAR(32) | 统计口径 (ALL_A/SH_A/SZ_A/HS300...) |
| `trade_date` | DATE | 交易日期 |
| `advance_count` | INT | 上涨家数 |
| `decline_count` | INT | 下跌家数 |
| `flat_count` | INT | 平盘家数 |
| `total_count` | INT | 纳入统计总数 |
| `limit_up_count` | INT | 涨停家数 |
| `limit_down_count` | INT | 跌停家数 |
| `new_high_20/60/120/250` | INT | 新高家数(N日) |
| `new_low_20/60/120/250` | INT | 新低家数(N日) |
| `up_volume` | DOUBLE | 上涨成交量 |
| `down_volume` | DOUBLE | 下跌成交量 |
| `total_volume` | DOUBLE | 总成交量 |
| `total_amount` | DOUBLE | 总成交额 |
| `avg_change_pct` | DOUBLE | 平均涨跌幅 |
| `median_change_pct` | DOUBLE | 中位数涨跌幅 |
| `up_ratio` | DOUBLE | 上涨占比 |
| `down_ratio` | DOUBLE | 下跌占比 |
| `adr` | DOUBLE | ADR=上涨/下跌 |
| `adl` | BIGINT | ADL累计线 |
| `obos` | INT | OBOS=上涨-下跌 |
| `adr_ma10` | DOUBLE | ADR移动平均 |
| `adl_ma20` | DOUBLE | ADL移动平均 |
| `obos_ma10` | DOUBLE | OBOS移动平均 |
| `source` | VARCHAR(30) | 数据来源 |
| `created_at` | TIMESTAMP | 创建时间 |
| `updated_at` | TIMESTAMP | 更新时间 |

**主键:** `(universe_code, trade_date)`
**索引:** `(trade_date)`

---

### 7.12 宽基指数日线表 (`broad_index_daily`)

存储指数日线行情及技术指标。

| 字段组 | 字段 | 说明 |
|--------|------|------|
| **基础** | `code`, `exchange`, `asset_type`(固定=index), `trade_date` | 资产标识 |
| **行情** | `open`, `high`, `low`, `close`, `pre_close`, `volume`, `amount` | OHLCV |
| **涨跌** | `change_val`, `change_pct`, `amplitude`, `turnover_rate` | 涨跌指标 |
| **均线** | `ma5`, `ma10`, `ma20`, `ma30`, `ma60`, `ma120` | 移动平均 |
| **EMA** | `ema10`, `ema12`, `ema26`, `ema30` | 指数移动平均 |
| **MACD** | `macd_dif`, `macd_dea`, `macd_bar` | 异同移动平均 |
| **其他** | `rsi14`, `boll_*`, `kdj_*`, `adx14`, `atr14`, `obv`, `cci20`, `wr14` | 技术指标 |
| **扩展** | `vwap`, `hv20`, `mom60`, `sar` | 扩展指标 |
| **元数据** | `source`, `created_at`, `updated_at` | 来源与时间 |

**主键:** `(code, exchange, asset_type, trade_date)`
**约束:** `CHECK (asset_type = 'index')`
**索引:** `(trade_date, exchange)`

---

### 7.13 视图

#### v_daily_full - K线与指标合并视图

```sql
SELECT k.*, i.*
FROM daily_kline k
LEFT JOIN daily_indicators i
  ON k.code = i.code
  AND k.exchange = i.exchange
  AND k.asset_type = i.asset_type
  AND k.trade_date = i.trade_date
  AND k.adjust_type = i.adjust_type;
```

#### v_latest_signals - 最新信号视图

获取每只股票每个策略的最新信号。

```sql
SELECT s.*
FROM strategy_signals s
WHERE s.trade_date = (
    SELECT MAX(ss.trade_date)
    FROM strategy_signals ss
    WHERE ss.code = s.code
      AND ss.exchange = s.exchange
      AND ss.asset_type = s.asset_type
      AND ss.strategy_key = s.strategy_key
      AND ss.params_hash = s.params_hash
);
```

---

## 八、存储特点

### 8.1 数据库持久化（主数据仓库）
- 数据库配置位于 `pyproject.toml` 的 `[tool.stock-quant.database]`，DDL 位于 `data/stock_quant.sql`，存量库升级脚本位于 `data/migrations/`。
- 资产统一使用 `(code, exchange, asset_type)` 标识，`asset_type` 区分 `stock / index / etf / fund / bond`。
- `daily_kline`、`daily_indicators` 等行情表均带 `exchange` 与 `asset_type`；指标表额外带 `adjust_type`。
- `daily_kline` 固定只写原始价（`adjust_type='raw'`）；读取后由 `_apply_in_memory_adjust` 在内存中做前复权。
- `financial_reports` 使用 `report_date` 表示报告期、`announce_date` 表示公告日、`data_version` 表示修订版本；回测按 `announce_date <= 当前交易日` 对齐。
- `core/db.py` 的 `_DB_MODE` 默认仍为 `0`，但 K 线缓存流程通过 `force=True` 主动读写数据库（`daily_kline`）；未配置数据库时自动回退纯网络。

### 8.2 缓存策略
| 缓存类型 | 作用域 | 生命周期 |
|---------|-------|---------|
| `_valuation_cache` | 实例级 dict | 同次 `analyze` 运行 |
| `_financials_cache` | 实例级 dict | 同次运行 |
| `_shareholder_cache` | 实例级 dict | 同次运行 |
| `_insider_cache` | 实例级 dict | 同次运行 |
| `_holder_date_cache` | 类级 dict | 同次 Python 进程, 跨实例共享 |

### 8.3 输出文件
| 路径 | 类型 | 生成时机 |
|------|------|---------|
| `output/{symbol}_*.png` | 图表 PNG | `_run_single_analysis` |
| `output/{symbol}_report.html` | 个股报告 | 单股分析末 |
| `output/report.html` | 批量汇总 | 批量分析末 |

### 8.4 输入文件
| 文件 | 格式 | 用途 |
|------|------|------|
| `pyproject.toml` | TOML | 软件配置：版本、阈值、服务、Webhook、数据库、综合权重 |
| `input/favs.json` | JSON 数组 | 自选股(回退) |
