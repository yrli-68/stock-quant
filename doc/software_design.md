# 软件详细设计文档 — stock-quant 股票量化分析工具

> 本文档描述软件的整体架构、模块划分、数据流与各命令的处理流程。
> 策略买卖条件、大盘趋势判定、综合打分等业务规则的权威说明见 `doc/strategy_design_v0.md`。

---

## 1. 概述

### 1.1 软件定位

stock-quant 是一个面向 A 股的命令行量化分析工具，覆盖「数据获取 → 指标计算 → 策略信号 → 回测 → 风险分析 → 图表/报告 → 钉钉通知」的完整链路，主要功能：

- **分析（analyze）**：对指定股票/股票池运行多策略，输出最新交易日买卖信号、回测与风险指标、HTML 报告。
- **扫描（scan）**：对全市场/抽样股票批量运行策略，筛选买入/卖出信号股票，生成汇总信号表。
- **回测（backtest）**：固定使用「单持仓轮动」流程，按策略顺序选股、持仓到卖出。
- **对比（compare）**：对单只股票运行全部策略，输出绩效排名对比。
- **指标（indicators）**：计算并输出全量技术指标（可导出 CSV / 图表）。
- **辅助**：`poolmgr` 管理股票池文件；`daemon.py` / `deploy/service_runner.py` 定时运行。

### 1.2 技术栈

| 类别 | 技术 | 用途 |
|------|------|------|
| 语言 | Python ≥ 3.10 | — |
| CLI | Click | 命令解析、分组、选项 |
| 数值计算 | pandas / numpy / scipy | 指标、回测、风险统计 |
| 可视化 | matplotlib / mplfinance | K 线图、因子子图、权益曲线 |
| 网络 | requests | 新浪/东方财富/Baostock/AkShare 取数 |
| 数据库 | PyMySQL（MySQL） | K 线/财务/估值/交易日历等缓存 |
| 配置 | tomllib（内置） | 读取 `squant.toml` |
| 通知 | requests（钉钉 Webhook） | 信号推送 |

### 1.3 运行方式

```bash
venv/bin/python main.py <command> [options]      # 开发环境
# 或安装后：
stock-quant <command> [options]                  # 入口 main:cli
poolmgr --add 000001 --file input/pool.txt       # 股票池管理入口 poolmgr:poolmgr
```

---

## 2. 目录结构

```
stock-quant/
├── main.py                  # CLI 入口 + 各命令处理流程（约 6600 行，核心编排层）
├── poolmgr.py               # 股票池文件管理 CLI（增删股票代码）
├── daemon.py                # 本地定时守护（读取 service 配置按计划运行）
├── _version.py              # 版本号
├── squant.toml              # 全局配置
├── core/                    # 核心层（与业务/命令解耦）
│   ├── strategy.py          # 策略基类 Strategy（抽象）
│   ├── backtest.py          # 回测引擎 BacktestEngine
│   ├── risk.py              # 风险指标（VaR/CVaR/回撤/卡玛/索提诺等）
│   ├── indicators.py        # 技术指标计算（MA/EMA/MACD/RSI/BOLL/KDJ/SAR/ATR/...）
│   ├── market_trend.py      # 大盘趋势判定（上涨/下跌/过热/过冷/震荡）
│   ├── signal_score.py      # 每日信号综合打分（10 指标，-1/0/+1）
│   ├── data_fetcher.py      # 数据获取（多源测速/回退、DB 优先、内存复权）
│   ├── db.py                # MySQL 访问层（K 线/财务/估值/筹码/交易日历）
│   ├── trading_calendar.py  # A 股周末/节假日判定（缺口过滤用）
│   ├── thresholds.py        # 阈值配置读取
│   ├── config.py            # squant.toml 加载与缓存
│   └── constants.py         # 全局常量
├── strategies/              # 策略实现
│   ├── ma_cross.py          # MA1030
│   ├── macd_strategy.py     # MACD（12/26/9）
│   ├── macd1_strategy.py    # MACD1（6/13/5）
│   ├── bollinger_strategy.py# BOLL
│   ├── boma_strategy.py     # BOMA（BOLL+MACD+DMI 组合）
│   ├── kdj_strategy.py      # KDJ
│   ├── sar_strategy.py      # SAR
│   ├── sg1_strategy.py      # MA310
│   ├── sg2_strategy.py      # MA520
│   ├── sg3_strategy.py      # EMA520
│   ├── quality_value_factor.py # 质价融合（多因子）
│   ├── hold_strategy.py     # 买入并持有（基准）
│   ├── momentum_tiered.py   # 指数：动量分层
│   ├── volatility_timing.py # 指数：波动率择时
│   ├── breadth_confirmation.py # 指数：涨跌比确认
│   └── （历史遗留，未注册） composite_strategy.py / value_factor.py /
│        quality_factor.py / enhanced_macd.py / enhanced_rsi.py
├── visualization/
│   └── charts.py            # ChartGenerator（K 线/因子/权益/组合图表）
├── input/                   # 股票池文本文件（pool.txt / favs.txt / hs300.txt 等）
├── output/                  # 生成的 PNG 图表
├── report/                  # 生成的 HTML 报告 / scan 结果 txt
├── data/                    # SQL DDL、迁移脚本
├── deploy/                  # service_runner.py（系统服务运行器）
└── test/                    # 测试与历史脚本
```

---

## 3. 系统架构与模块分层

### 3.1 分层结构

```
┌─────────────────────────────────────────────────────────────┐
│  表示/编排层  main.py（Click CLI：analyze/scan/backtest/...）│
│              命令参数解析、流程编排、报告生成、通知           │
├─────────────────────────────────────────────────────────────┤
│  策略层       strategies/*.py（继承 core.Strategy）          │
├─────────────────────────────────────────────────────────────┤
│  领域服务层    core/                                         │
│    indicators（指标）  market_trend（大盘趋势）               │
│    signal_score（打分） backtest（回测） risk（风险）         │
├─────────────────────────────────────────────────────────────┤
│  数据访问层    core/data_fetcher.py（网络多源+DB缓存）        │
│               core/db.py（MySQL）  core/config.py（配置）     │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 模块依赖

- `strategies/*` 依赖 `core.strategy`（基类）、`core.indicators`（指标）、`core.thresholds`（阈值）。
- `core.backtest` / `core.risk` 仅依赖 `core.constants`，纯计算、无外部依赖。
- `core.market_trend` 依赖 `core.data_fetcher`（指数数据）、`core.indicators`、`core.db`（涨跌家数）。
- `core.signal_score` 依赖 `core.indicators`。
- `main.py` 是唯一聚合所有层的编排入口，策略注册表（`STRATEGY_MAP` 等）在此定义。

---

## 4. 配置管理（squant.toml）

统一由 `core/config.py` 加载并缓存 `[tool.stock-quant]` 节；`core/thresholds.py` 进一步读取 `[tool.stock-quant.thresholds]`。

| 配置节 | 关键项 | 说明 |
|--------|--------|------|
| `[tool.stock-quant]` | `position_style` | 回测持仓模式 `full`（全仓）/ `fraction`（比例） |
| | `market_trend_index` | 大盘趋势默认指数，默认 `000300` |
| `[tool.stock-quant.thresholds]` | `MAX_RSI_THRESHOLD` | RSI 卖出阈值 |
| | `MIN_RSI_THRESHOLD` | RSI 买入阈值 |
| | `PE_THRESHOLD` | scan 选股 PE 上限 |
| | `BOLL_SQUEEZE_SCALE` / `BOLL_EXTRAWIDE_SCALE` | BOLL 带宽因子（当前停用） |
| | `EMA60_CHECK` / `ADX_CHECK` | BOLL 买入附加过滤开关 |
| `[tool.stock-quant.WEBHOOK]` | `URL` / `key` | 钉钉机器人通知 |
| `[tool.stock-quant.database]` | host/port/user/password/database | MySQL 连接 |
| `[tool.stock-quant.service]` | `SQ_RUN_TIME` / `SQ_STRATEGY` | 定时运行时间与策略 |
| `[tool.stock-quant.composite]` | — | 历史遗留，CLI 不再读取 |

配置读取均在进程内缓存，修改后需重启进程生效。

---

## 5. 数据层

### 5.1 DataFetcher（`core/data_fetcher.py`）

数据获取的核心类，统一入口方法：

| 方法 | 说明 |
|------|------|
| `get_stock_data(symbol, start, end)` | 个股日 K（DB 优先，缺失补网络，内存复权） |
| `get_index_data(symbol, start, end)` | 指数日 K |
| `get_stock_list(sort_key, max_pages)` | 全市场股票列表 |
| `get_hot_stocks(count)` | 热门股（热度打分） |
| `get_adjust_factor(symbol)` / `get_dividend_data` | 复权因子/分红 |
| `get_realtime_quote(symbol)` | 实时行情（当天实时 bar） |

**数据源与回退链**（`--src` 指定）：

| `--src` | 数据源 | 失败回退 |
|---------|--------|----------|
| `auto`（默认） | 启动时对 sina/eastmoney/baostock 并行测速，选最先响应者 | 固定顺序回退 |
| `sina` | 新浪 K 线（原始价，约 1000 条） | baostock → eastmoney → akshare |
| `baostock` | Baostock（原始价，长历史） | sina → eastmoney → akshare |
| `eastmoney` | 东方财富（原始价） | sina → baostock → akshare |
| `akshare` | akshare（长历史兜底） | sina → baostock → eastmoney |

**关键策略**：

- 起始日早于 `2023-01-01` 时自动排除 sina（1000 条上限不足）。
- 结束日为当天 → 直接走网络取全量原始 K 线，不读写 DB；否则 DB 优先，只补缺失区间并回写。
- 所有网络源返回原始价（raw），复权在内存完成（默认前复权 qfq）。
- 数据获取起点 = 分析起点前推 `_ANALYSIS_WARMUP_DAYS`（90 自然日），计算指标后截断回 `start`。

### 5.2 数据库访问（`core/db.py`）

MySQL 缓存层，主要表（对应 `data/stock_quant.sql`）：

| 表 | 用途 |
|----|------|
| `daily_kline` | 日 K 线（含 `adjust_type` 区分 raw/qfq） |
| `trading_calendar` | 交易日历 |
| `market_breadth` | 涨跌家数（advance/decline/adl） |
| `valuation_history` | 估值（pe_ttm/pb 等） |
| `financials` / `shareholder` / `insider_trades` | 财务/股东人数/增减持（质价融合用） |

DB 读超时 20s；交易日期优先级：`trading_calendar` → `market_breadth` → `daily_kline`。DB 连接失败时降级为纯网络取数（CLI 启动时告警）。

### 5.3 交易日历（`core/trading_calendar.py`）

用于 K 线缺口过滤：判断某段日期是否全为周末/法定节假日窗口（含农历规则，依赖 `lunardate`），从而识别「休市缺口」避免不必要的网络补数。

---

## 6. 技术指标计算（`core/indicators.py`）

`add_all_indicators(df)` 一次性计算并挂载全部指标列到 DataFrame，供策略、趋势、打分统一使用。

| 函数 | 产出列 |
|------|--------|
| `calc_ma` / `calc_ema` | `MA5/MA10/MA20/MA30/MA60/MA120`、`EMA5/10/12/26/60` |
| `calc_macd` | `MACD_DIF/DEA/MACD_BAR`（12/26/9） |
| `calc_rsi` | `RSI6/RSI12/RSI14` |
| `calc_bollinger` | `BOLL_UPPER/MIDDLE/LOWER`（20/2） |
| `calc_kdj` | `KDJ_K/KDJ_D/KDJ_J`（9/3/3） |
| `calc_sar` | `SAR` |
| `calc_adx` / `calc_adxr` | `ADX14/PLUS_DI/MINUS_DI`、`ADXR` |
| `calc_atr` | `ATR14` |
| `calc_wr` | `WR14` |
| `calc_obv` | `OBV` |
| `calc_momentum_return` / `calc_historical_volatility` | 动量/波动率（指数策略） |
| `calc_rsi_divergence` | RSI 顶/底背离（大盘趋势过热/过冷用） |
| `adjust_atr_stop_multiplier` | 按趋势调整 ATR 止损倍数（上涨/过热 ×1.2，下跌/过冷 ×0.8） |
| `update_atr_trailing_stop` | ATR 动态止损价 `max(前一日stop, close - N×ATR)` |

---

## 7. 策略框架

### 7.1 策略基类（`core/strategy.py`）

抽象基类 `Strategy`，子类实现 `generate_signals(df)`，返回与 `df.index` 对齐的 `pd.Series`（`1`=买、`-1`=卖、`0`=观望）。基类还提供 `signal_value()`、`signal_residual()`（残值衰减）、`calculate_position()`（信号→持仓）。

策略类可用两个类属性参与统一信号流程（见 7.3）：

- `integrates_buy_filter`（bool）：为 True 时，`-sn` 序号过滤在策略内部同步应用，且 `in_pos` 仅在买入实际生效后置位。
- `supports_seqnum`（bool）：为 True 时支持 `-sn` 序号过滤（当前仅 BOLL、BOMA）。

### 7.2 策略注册表（`main.py`）

| 注册表 | 内容 |
|--------|------|
| `STRATEGY_MAP` | 个股/ETF 通用策略：`ma1030, macd, macd1, boll, boma, kdj, sar, ma310, ma520, ema520, quality_value, hold` |
| `INDEX_STRATEGY_MAP` | 指数专属：`momentum, volatility, breadth` |
| `ETF_STRATEGY_MAP` | ETF 参数变体（如 `ma1030`→MA10/MA40、`macd`→16/32/12、`boll`→2.5σ） |
| `ALL_STRATEGIES` | 全部个股策略 key；`ALL_SELECTION` = 排除 `ma1030/ma310/ma520` 后的 `-g all` 集合 |

`_build_strategy_instance(key, strategy_map, enhance)` 负责实例化并注入 `use_atr_stop` / `atr_stop_multiplier`（来自 `--withdrawsell`）。

### 7.3 统一信号生成流程（`_generate_effective_signals`）

所有命令最终都通过该函数把策略原始信号加工为「最终交易信号」，流程如下：

```
1. 计算大盘趋势状态（按个股市值选沪深300/中证1000，见第 8 节）
2. 调用 strategy.generate_signals() 得到原始信号
   - integrates_buy_filter=True 的策略：内部同步 -sn 序号过滤
3. _mask_signal_warmup() 截断预热期信号
4. _apply_market_regime_to_signals() 应用大盘趋势：
   - 过热日：买入信号置 0（禁止追高）
   - 下跌/过冷日：买入信号置 0（`--trendctl 1`，默认；`--trendctl 0` 关闭）
   - 过冷日：仅抑制 ATR 止损等强制卖出，常规策略卖出保留
```

> 已取消的过滤（历史）：`score` 每日量化评分、`SAR_CHECK` 买入过滤、`--maxholddays` 最大持股天数、全局「当日涨幅 < 9.9%」过滤、`INDEX_CHECK` 全局指数过滤。

### 7.4 各策略简述

| key | 策略 | 核心逻辑 |
|-----|------|----------|
| `ma1030` | MA 双均线 | MA10/MA30 金叉买、死叉卖，附加 RSI 过滤 |
| `macd` / `macd1` | MACD | DIF/DEA 金叉买、死叉卖，ATR 止损（12/26/9 或 6/13/5） |
| `boll` | 布林带 | 下轨反弹买、上轨回落卖，RSI/EMA60/ADX 过滤 |
| `boma` | BOLL+MACD+DMI | 三组买入/卖出条件（`--critera` 控制启用），ATR 止损 |
| `kdj` | KDJ | K/D 金叉买（站上 MA60）、死叉卖 |
| `sar` | SAR | SAR 与收盘价穿越方向 |
| `ma310` / `ma520` / `ema520` | SG 系列 | MA3/10、MA5/20、EMA5/20 金叉买（严格条件）、死叉/RSI/ATR 卖 |
| `quality_value` | 质价融合 | 估值/质量/筹码/增减持/波动率加权 → Z 分数阈值 |
| `hold` | 买入持有 | 基准对照，全程持有 |
| `momentum` / `volatility` / `breadth` | 指数专属 | 动量+均线共振 / 波动率择时 / 涨跌比确认 |

组合/合成策略：`analyze` 内部计算 `PREV`（上一交易日多数表决，不输出）；`backtest` 自动追加 `combo`（复合策略，跟随首个买入策略）。

---

## 8. 大盘趋势判定（`core/market_trend.py`）

按个股总市值选择对应指数（>200 亿用沪深300 `000300.SH`，否则中证1000 `000852.SH`），对指数日线判定逐日趋势状态：

| 状态 | 条件 |
|------|------|
| 上涨 | `(MA5>MA20 且 slope5>0)` 或 `(ADX14>=25 且 +DI>-DI)` 或 `ADR10>1.2` |
| 下跌 | `(MA5<MA20 且 slope5<0)` 或 `(ADX14>=25 且 +DI<-DI)` 或 `ADR10<0.7` |
| 过热 | 上涨 且 `(RSI14>70 或 RSI顶背离)` 且 `ADR10>1.5` 且 `BIAS20>7%` |
| 过冷 | 下跌 且 `(RSI14<30 或 RSI底背离)` 且 `ADR10<0.75` 且 `BIAS20<-7%` |
| 震荡 | 非上述状态 |

关键函数：`market_trend_states(start,end,code)`（逐日状态，供回测）、`analyze_market_trend()`（最新状态+原因，供界面）。ADR 来自 `market_breadth` 涨跌家数，`slope5` 为 5 日收盘价线性回归斜率。结果用 `lru_cache` 缓存。

---

## 9. 每日信号综合打分（`core/signal_score.py`）

`compute_signal_score(df, signals)` 对最新交易日信号计算 10 指标综合打分（每项 `-1/0/+1`），用于 `--verbose` 展示与钉钉通知，不参与买卖信号生成。

| # | 指标 | +1 | -1 |
|---|------|----|----|
| 1 | DMI | ADX14>=20 且 +DI>-DI | ADX14>=20 且 +DI<-DI |
| 2 | MACD | BAR 连续 2 根放大 | BAR 连续 2 根缩短 |
| 3 | OBV | OBV>MA5>MA10 且 价>MA5>MA10 | 反向 |
| 4 | BIAS20 | -3%≤BIAS≤6% | BIAS>8% |
| 5 | RSI | RSI14<40 且金叉 或 <30 | RSI14>60 且死叉 或 >70 |
| 6 | BOLL | 下轨反弹/上轨内 | 上轨回落/下轨内 |
| 7 | WR | 正向 WR>80 | 正向 WR<20 |
| 8 | ATR持仓 | 浮盈且距止损>1×ATR | 距止损<0.5×ATR 或浮亏≤-5% |
| 9 | KDJ | K<20 或 J<0 | D>80 或 J>100 |
| 10 | SAR | close>SAR 且 MA5>MA10 | close<SAR 且 MA5<MA10 |

输出格式 `+3 (++0++00-00)`：前为总分，括号内按上表顺序输出 `+/-/0`。

---

## 10. 回测引擎（`core/backtest.py`）

`BacktestEngine(initial_capital, commission=0.0003, slippage=0.0001, fix_capital)`：

- **持仓模式**：`full` 全仓买卖；`fraction` 按信号比例累加/递减仓位（0~1）。
- **成本模拟**：买入价上浮滑点、卖出价下浮滑点，佣金双向计。
- **加权成本**：`cost_basis` 按加权平均维护，部分减仓时按比例削减，保证分批建仓的收益率计算准确。
- **交易记录** `trades_df`：`entry_date/exit_date/entry_price/exit_price/position/return/holding_days/exit_reason`。
- **期末强平**：回测结束仍持仓时按末收盘价平仓（`exit_reason='end'`）。
- **绩效指标**：总收益、年化、最大回撤、夏普、胜率、盈亏比、买卖次数等（`_compute_metrics`）。

### 10.1 风险分析（`core/risk.py`）

独立的风险指标库：`calc_var`（历史模拟 VaR）、`calc_cvar`、`calc_max_drawdown`、`calc_calmar_ratio`、`calc_sortino_ratio`、`calc_information_ratio`、`calc_beta`、`calc_alpha`，汇总为 `risk_report()`（含年化波动率、偏度、峰度）。

---

## 11. 命令处理流程

### 11.1 通用启动流程

`cli` 分组命令（`main.py` 的 `_TimedGroup`）在每个子命令执行前检查 DB 连接；失败则告警并降级为纯网络取数。

### 11.2 analyze（分析）

```
1. 定时等待（--plan-time）
2. set_primary_source / _set_seqnum / _set_withdrawsell / _set_boma_criteria
3. 确定股票列表：-h 热门股 | -s（支持通配符）| -sf 文件 | 默认 input/pool.txt
4. 解析日期（默认 180 天前 ~ 今天），预加载趋势状态与交易日历
5. 对每只股票：
   a. DataFetcher.get_stock_data（DB 优先 + 网络补数）
   b. add_all_indicators → _truncate_analysis_df 截断预热
   c. 对每个所选策略 _generate_effective_signals 生成信号
   d. _run_strategy → BacktestEngine.run → risk_report
6. 聚合：最新信号表（含综合打分）、策略排名、逐策略风险表
7. 生成 HTML 报告（ChartGenerator 图表嵌入）
8. --notify 时推送钉钉（大盘趋势 + 信号 + 综合打分 + 观望极值）
```

### 11.3 scan（扫描）

```
1. 解析策略列表（multi = 多策略）
2. 打印双指数大盘趋势（000300.SH + 000852.SH）
3. 选股来源：
   - 默认：get_stock_list（按排序字段或随机抽样 6:5:5:2）+ 过滤（PE/ST/低市值等）
   - `-s` / `-sf`：仅分析指定股票（`-s` 为内联代码/名称；`-sf` 为文件，名称优先取自文件），不再从网络补充
4. 扫描：
   - 网络模式：按 Top N 分批 + 最多扩容 2 次
   - 指定 `-s/-sf` 时：扫描完指定股票后，若买入信号数 < `-tN` 直接退出（不补网络）
   - scan 不对大盘趋势做特殊买卖拦截；是否因「下跌/过冷」禁买由各策略按 `--trendctl` 在信号层处理
5. 并行/串行对每只股票运行策略（_scan_single / _scan_multi）
6. 排序（买>卖>观望，同组按综合打分）
7. 输出汇总信号表 / 保存 scan_yyyymmdd.txt / 生成 HTML
8. --notify 推送钉钉（含大盘趋势）
```

### 11.4 backtest（持仓轮动，`_run_onestock_backtest`）

```
1. 预加载双指数趋势状态（000300.SH / 000852.SH）
2. 对每只股票：取数据 → 计算指标 → 逐策略生成信号（传入对应指数趋势）
3. 状态机回放（-n N 允许同时持有至多 N 只，默认 N=1 单持仓）：
   - 卖出：各持仓「该股票+该策略」出现卖出信号时全部卖出
   - 买入（当日无卖出时）：用剩余空位按「策略顺序 + 股票池顺序」买入未持仓且买入信号的股票，直至补满 N 只
     - --fixcapital：每只买入金额固定为 初始资金/N（现金可为负）
     - 非 --fixcapital：每只买入金额 = 可用现金/(N - 当前持股数)
   - 过热日禁止买入；下跌/过冷日按 `--trendctl`（默认 1）禁止买入；过冷日忽略强制卖出
4. 输出交易明细、盈亏、退出原因（ATR动态止损/RSI超买/最大回撤等）
5. --random 时每次完全空仓重抽股票池
```

### 11.5 compare（对比）

对单只股票运行 `ALL_STRATEGIES` 全部策略，输出总收益率/夏普/回撤/交易次数排名表，可生成对比图。

### 11.6 indicators（指标）

获取数据 → `add_all_indicators` → 截断预热 → 输出全量指标（CSV 或屏幕），`--chart` 时生成全部因子子图 K 线并输出 HTML。

---

## 12. 报告与可视化（`visualization/charts.py`）

`ChartGenerator` 提供：

- `plot_kline`：K 线 + 指标叠加（MA/BOLL/SAR 等）+ 买卖信号标注。
- `plot_signal_composite` / `plot_multi_strategy_composite`：单/多策略组合图。
- `plot_equity_curve`：权益曲线。
- `plot_all_indicators`：全因子子图。
- `plot_market_trend`：大盘趋势条带。

HTML 报告（analyze/scan/backtest/compare/indicators）由 `main.py` 内嵌模板生成，图表以 base64 PNG 内联，输出到 `report/`。

---

## 13. 关键处理流程时序

```
用户命令
  │
  ├─> 解析参数（Click）
  │
  ├─> 全局初始化：set_primary_source / 阈值 / 序号 / withdrawsell / boma 条件
  │
  ├─> 获取数据：DataFetcher
  │       ├─ DB 命中？ ──是──> 返回缓存 K 线
  │       └─ 否 → 网络多源取数（测速/回退）→ 回写 DB
  │       └─ 内存复权（qfq）
  │
  ├─> 指标计算：add_all_indicators(df)
  │
  ├─> 大盘趋势：market_trend_states()（指数日线 + 涨跌家数 ADR）
  │
  ├─> 信号生成：_generate_effective_signals(strategy, df)
  │       ├─ strategy.generate_signals(df, seqnum)
  │       ├─ 预热截断 _mask_signal_warmup
  │       └─ 大盘趋势应用 _apply_market_regime_to_signals（过热禁买/过冷抑制强制卖）
  │
  ├─> 回测：BacktestEngine.run(df, signals, position_style)
  │
  ├─> 风险：risk_report(daily_returns, equity_curve)
  │
  ├─> 打分：compute_signal_score(df, signals)（--verbose / --notify）
  │
  ├─> 可视化：ChartGenerator → PNG → 内嵌 HTML
  │
  └─> 输出：控制台表格 + HTML 报告 + 钉钉通知
```

---

## 14. 扩展与维护

### 14.1 新增策略

1. 在 `strategies/` 新增 `xxx_strategy.py`，继承 `core.strategy.Strategy`，实现 `generate_signals(df)`。
2. 在 `main.py` 的 `STRATEGY_MAP`（或 `INDEX_STRATEGY_MAP`）注册 key → (显示名, 类)。
3. 如需要同步 `-sn` 过滤，设置 `integrates_buy_filter = True` / `supports_seqnum = True`。
4. 在 `_SCAN_SHORT_NAMES`、ETF 变体（如需）等位置补充对应条目。

### 14.2 维护要点

- 策略买卖条件、趋势规则、打分规则变更时同步更新 `doc/strategy_design_v0.md`。
- 阈值新增/删除需同步 `core/thresholds.py`、`squant.toml`、`doc/strategy_design_v0.md` 与 `doc/deploy_guid.md`。
- 配置修改后需重启进程（进程内缓存）。
- 数据表结构变更需在 `data/migrations/` 增加迁移脚本。
