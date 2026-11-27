# 策略设计文档 — 因子选择与买卖信号生成

> 基于 `strategies/` 目录下各策略源码分析生成，覆盖所有已注册策略。
> 策略注册表见 `main.py` 中的 `STRATEGY_MAP` / `INDEX_STRATEGY_MAP` / `ETF_STRATEGY_MAP`。

---

## 〇、信号约定与总览

### 0.1 信号约定

所有策略 `generate_signals(df)` 返回与行情 `df.index` 对齐的 `pd.Series`，取值如下：

| 信号值 | 含义 | 报告中显示 |
|:---:|---|---|
| `1` | 买入 | 买（绿色/红色加粗，视上下文） |
| `0` | 观望 | 观 |
| `-1` | 卖出 | 卖 |

> **信号值约定**：买入 `+1`、卖出 `-1`、观望 `0`。
> 回测中（fraction 模式）仓位水平 `in_position` 为 0~1 浮点数，按 `±1` 累加/递减并封顶：`in_position<1` 时买入、`>0` 时卖出。
>
> 质价融合策略会额外在 `signals.attrs` 中附加 `combined_score`（综合得分）、`z_score`（Z 分数）与 `stock_type`（标的类型）。
>
> **RSI 过滤已内置**：MA1030 / MACD / BOLL 的买入条件统一附加
> `RSI < MIN_RSI_THRESHOLD`（且条件），卖出条件统一附加 `或 RSI > MAX_RSI_THRESHOLD`（RSI 优先取 `RSI14`，
> 缺失时用 `calc_rsi(df,14)` 现算）。阈值来自 `squant.toml` 的 `[tool.stock-quant.thresholds]`（见 0.4），因此独立的 RSI 策略已删除。
>
> **BOLL EMA60 买入过滤（`EMA60_CHECK`）**：`EMA60_CHECK=1` 时，BOLL 的买入信号附加
> 「当日 `EMA60 < close`」条件；`EMA60_CHECK=0` 时不检查 EMA60。
>
> **BOLL ADX 买入过滤（`ADX_CHECK`）**：`ADX_CHECK=1` 时，BOLL 的买入信号附加
> 「当日 `ADX > 20`」条件；`ADX_CHECK=0` 时不检查 ADX。
>
### 0.1.1 每日信号综合打分（`--verbose`）

`analyze` / `scan` 开启 `--verbose` 时，对股票最新交易日信号额外计算 10 指标综合打分。该评分不参与买卖信号生成。

每个指标独立取 `-1 / 0 / +1`：

| # | 指标 | +1 条件 | -1 条件 | 0 条件 |
|---|------|---------|---------|--------|
| 1 | DMI | `ADX14>=20` 且 `+DI>-DI` | `ADX14>=20` 且 `+DI<-DI` | `ADX14<20` |
| 2 | MACD | `BAR > BAR.shift(1) > BAR.shift(2)` | `BAR < BAR.shift(1) < BAR.shift(2)` | 其余 |
| 3 | OBV | `OBV>OBV_MA5>OBV_MA10` 且 `close>price_MA5>price_MA10` | `OBV<OBV_MA5<OBV_MA10` 且 `close<price_MA5<price_MA10` | 其余 |
| 4 | BIAS20 | `-3% <= BIAS20 <= 6%` | `BIAS20>8%` | 其余 |
| 5 | RSI | `(RSI14<40 且 RSI金叉) 或 RSI14<30` | `(RSI14>60 且 RSI死叉) 或 RSI14>70` | 其余 |
| 6 | BOLL | `close.shift(1)<=LOWER`，或最近5日最大 `close<UPPER` 且 `UPPER>close>MIDDLE` | `close.shift(1)>=UPPER`，或最近5日最小 `close>LOWER` 且 `MIDDLE>close>LOWER` | 其余 |
| 7 | WR | 正向 `WR>80`（超卖） | 正向 `WR<20`（超买） | 其余 |
| 8 | ATR持仓 | 浮盈且距 ATR 止损超过 1 个 ATR | 距止损不足 0.5 个 ATR，或浮亏<=-5% | 其余或无持仓 |
| 9 | KDJ | `K<20` 或 `J<0` | `D>80` 或 `J>100` | 其余 |
| 10 | SAR | `close>SAR` 且 `MA5>MA10` | `close<SAR` 且 `MA5<MA10` | 其余 |

补充口径：

- `OBV_MA5` / `OBV_MA10` 为 OBV 的 5 / 10 日均值；`price_MA5` / `price_MA10` 为收盘价的 5 / 10 日均值。
- MACD 柱连续 2 根放大指 `BAR > BAR.shift(1) > BAR.shift(2)`；连续 2 根缩短指 `BAR < BAR.shift(1) < BAR.shift(2)`。
- BOLL 的“最近5日”包含当前交易日；`LOWER/MIDDLE/UPPER` 为当日布林带下轨/中轨/上轨。
- RSI 金叉/死叉按 `RSI6` 上穿/下穿 `RSI12` 判断，即 `RSI6.shift(1) <= RSI12.shift(1)` 且 `RSI6 > RSI12` 为金叉，反向为死叉。
- ATR 持仓分数用当日组合买入/卖出信号模拟持仓：任一卖出优先，否则任一买入；未持仓时 ATR 分量记 0。
- 内部 `WR14` 为 `-100~0` 方向，正向 WR = `-WR14`，范围 `0~100`；缺少 `WR14` 时用 `calc_wr(df, 14)` 现算。
- KDJ 读取 `KDJ_K` / `KDJ_D` / `KDJ_J`（`add_all_indicators` 已计算），`K<20 或 J<0` 记 +1，`D>80 或 J>100` 记 -1。
- SAR 读取 `SAR` / `MA5` / `MA10`（`add_all_indicators` 已计算），`close>SAR 且 MA5>MA10` 记 +1，`close<SAR 且 MA5<MA10` 记 -1。
- 输出格式：

```text
+3 (++0++00-00)
```

- 最前面为 10 项合计分值；
- 括号内按 DMI、MACD、OBV、BIAS、RSI、BOLL、WR、ATR、KDJ、SAR 顺序输出，`+` 为 +1，`-` 为 -1，`0` 为 0。

实现：`core/signal_score.py:compute_signal_score()`；仅在 `--verbose` 下加入 analyze 最新信号表和 scan 结果表。

analyze 最新信号表后会附备注 `综合打分排位：DMI|MACD|OBV|BIAS| RSI|BOLL|WR|ATR| KDJ|SAR`，说明“综合打分”括号内各位置的指标顺序。

当没有买入/卖出信号时，analyze（`--verbose`）还会额外列示观望股票中综合打分最高和最低的各 3 只；scan（`--verbose`）仍列示最高和最低的各 1 只。

钉钉通知（`--notify`）在标题行之后增加大盘趋势行，如 `沪深300：跌势；中证1000：涨势`（直接显示 涨势/跌势/震荡）。

### 0.2 策略分类总览

| 分类 | 策略（显示名） | 注册 key | 适用标的 |
|------|------|---------|---------|
| 技术指标类 | MA1030 策略 | `ma1030` | 个股 / ETF |
| 技术指标类 | MACD 策略 | `macd` | 个股 / ETF |
| 技术指标类 | MACD1 策略（MACD 参数 6/13/5 版本） | `macd1` | 个股 / ETF |
| 技术指标类 | KDJ 策略 | `kdj` | 个股 / ETF |
| 技术指标类 | SAR 抛物线转向策略 | `sar` | 个股 / ETF |
| 技术指标类 | 布林带策略 | `boll` | 个股 / ETF |
| 技术指标类 | BOMA 策略（BOLL/MACD/DMI 组合信号） | `boma` | 个股 / ETF |
| 多因子类 | 质价融合策略 | `quality_value` | 个股 / ETF |
| 基准对照 | 买入并持有策略 | `hold` | 个股 / ETF |

> `-g all` 展开为 `ALL_SELECTION = ['macd','macd1','boll','boma','kdj','sar','hold']`，**不含 `ma1030` / `quality_value`**（`ma1030` 仍可通过 `-g` 单独指定）。ETF 模式的 `-g all` 另含 `quality_value`。
> 全量注册表 `ALL_STRATEGIES` 仍保留 `ma1030`，供 `compare` 使用。

### 0.3 数据来源（`--src`）

行情数据由 `DataFetcher` 获取，通过 `--src` 指定数据通道：

| `--src` | 取数方式 | 失败回退 |
|:---:|---|---|
| `auto`（默认） | 启动时对 sina / baostock / eastmoney 并行测速，取最先响应的源；起始日早于 `2023-01-01` 时用 baostock / eastmoney / akshare | 按固定顺序回退 |
| `sina` | 新浪 K 线 API（原始价，约 1000 条） | Baostock → 东方财富 → akshare |
| `baostock` | Baostock（`adjustflag=3`，原始价） | 新浪 → 东方财富 → akshare |
| `eastmoney` | 东方财富 K 线 API（`fqt=0`，原始价） | 新浪 → Baostock → akshare |
| `akshare` | akshare 新浪日线（`stock_zh_a_daily`，原始价，长历史） | 新浪 → Baostock → 东方财富 |

- **`auto` 测速规则**：软件启动后并行访问三个站点（sina/eastmoney 取 K 线接口、baostock 取 TCP 连接耗时），一旦有源最先成功返回即中断其余测速并选用该源；其余源只作失败回退，不再做速度排名。`akshare` 作为长历史兜底源排在最后。
- **历史区间限制**：起始日期早于 `2023-01-01` 时，sina 的约 1000 条上限无法覆盖，自动排除 sina，改用 baostock / eastmoney / akshare（后两者可覆盖长历史）。
- **所有网络源只返回原始价**：复权在读取后于内存中完成，默认前复权（qfq）。
- **结束日期为当天**：直接走网络获取全部原始 K 线，不读写数据库。
- **结束日期非当天**：先读数据库原始 K 线（`daily_kline.adjust_type='raw'`），检查起始/结束偏差与 >3 天中间间隙，仅对缺失时段走网络补齐并回写数据库，最后统一内存复权。
- `eastmoney` 不提供股票列表接口；`scan` / 随机抽样会回退到其它通道。
- **回测 HTML 报告**在概要区显示本次实际使用的数据来源（如 `sina`、`db`、`db+network`；批量回测可能显示 `sina, baostock`）。
- 适用于 `analyze` / `scan` / `backtest` / `compare` / `indicators`；`strategy_rank_test.py` 亦支持 `--src` 并透传给 `backtest`。
- 数据获取起点 = 分析/回测 `start` 前推 90 自然日（`_ANALYSIS_WARMUP_DAYS`）以正确计算指标，计算后截断回 `start`。
- **股票列表同样遵守 `--src`**：`get_stock_list()` 首选源失败时自动切换备用源；sina 走行情中心分页，
  baostock 走最近交易日全市场快照。Baostock 不提供总市值，市值排序在该源下不生效并给出警告。
- 实现见 `core/data_fetcher.py`（`set_primary_source` / `_get_a_stock_data` / `get_stock_list`），数据格式详见 `doc/data_structure.md`。

### 0.4 统一阈值配置（`squant.toml` 的 `[tool.stock-quant.thresholds]`）

阈值集中在 `squant.toml` 的 `[tool.stock-quant.thresholds]` 节，策略未显式传参时自动读取（`core/thresholds.py`），**以配置当前值为准**：

| 配置项 | 代码回退默认 | 作用 |
|------|:---:|------|
| `MAX_RSI_THRESHOLD` | 85 | RSI 卖出阈值：`RSI > 该值` → 卖出（**或**条件） |
| `MIN_RSI_THRESHOLD` | 25 | RSI 买入阈值：`RSI < 该值` → 允许买入（**且**条件） |
| `PE_THRESHOLD` | 100 | `scan` 选股 PE 上限：`PE > 该值` 剔除（不参与策略信号） |
| `BOLL_SQUEEZE_SCALE` | 0.2 | BOLL 挤压判定比例因子：`bbw <= bbw_pctl × 该值` |
| `BOLL_EXTRAWIDE_SCALE` | 1.2 | BOLL 极端宽判定比例因子：`bbw >= bbw_pctl × 该值` |
| `EMA60_CHECK` | 0 | BOLL 买入 EMA60 过滤开关：`1`=买入需 `EMA60 < close`；`0`=不检查 |
| `ADX_CHECK` | 0 | BOLL 买入 ADX 过滤开关：`1`=买入需 `ADX > 20`；`0`=不检查 |

- 生效策略：MA1030 / MACD / BOLL。
  `BOLL_SQUEEZE_SCALE` / `BOLL_EXTRAWIDE_SCALE` / `bbw_lookback` 当前不生效（BOLL 带宽过滤已停用）。
- 构造参数显式传入时以传入值为准；ATR 动态止损可通过 `use_atr_stop=False` 禁用。
- 加载结果在进程内缓存，修改配置后需重新启动进程生效。

---

### 0.5 大盘趋势（Market Trend）

大盘趋势用于调整策略原始信号，并在 `backtest` 单持仓轮动执行中提供市场环境判断。实现位于 `core/market_trend.py`，结果按需计算并进程内缓存，不写入数据库。

#### 数据来源

| 数据 | 来源 | 说明 |
|------|------|------|
| 趋势指数 | 沪深300 `000300` / 中证1000 `000852` | 个股按总市值选择：>200亿用沪深300；<200亿或无法取得市值用中证1000；AkShare 作为长历史兜底 |
| 涨跌家数 | `market_breadth` 表 | `universe_code='ALL_A'`，读取 `advance_count` / `decline_count` |
| RSI14 | 指数日线 | 辅助展示（不参与涨势/跌势/震荡判定） |
| ADR(10) | 实时计算 | `ADR10 = 近10日上涨家数之和 / 近10日下跌家数之和`；**不是** `market_breadth.adr_ma10`（后者是日 ADR 的 10 日均值） |
| BIAS20 | 指数日线 | `BIAS20 = (close - MA20) / MA20` |

#### 个股市值对应指数

| 总市值 | 使用指数 |
|--------|----------|
| > 200 亿元 | 沪深300 `000300` |
| <= 200 亿元或无法取得市值 | 中证1000 `000852` |

个股信号过滤等行为使用该股票对应的指数趋势；ATR 止损倍数也按该趋势动态调整。

#### 状态判定

| 状态 | 条件 |
|------|------|
| `涨势` | (`close>MA5>MA60` AND `slope5>0`) OR (`ADX14>=25` AND `+DI>-DI`) OR `ADR10>1.2` |
| `跌势` | (`close<MA5<MA60` AND `slope5<0`) OR (`ADX14>=25` AND `+DI<-DI`) OR `ADR10<0.7` |
| `震荡` | 非涨势、非跌势 |

其中：

- `slope5` 为 5 日收盘价线性回归斜率；
- `ADR10 = 10日上涨家数之和 / 10日下跌家数之和`；ADR 数据缺失时按 `ADR=1` 处理。

状态赋值顺序：

```text
默认震荡
→ 涨势条件成立：涨势
→ 跌势条件成立：跌势
→ 无效数据：按震荡处理
```

> 已取消“过渡”“未知”“超涨”“超跌”状态；不满足涨势/跌势时统一记为“震荡”。
> 若涨势与跌势条件同时成立，按当前赋值顺序由“跌势”覆盖“涨势”。

#### 应用位置

| 位置 | 行为 |
|------|------|
| 所有策略原始信号 | `跌势` 日买入信号置 0；常规策略卖出保留 |
| 大盘趋势控制 | 跌势趋势禁止买入（按个股对应指数：沪深300成分股用 `000300.SH`，其它用 `000852.SH`） |
| `analyze` / `scan` 控制台与 HTML | 同时显示沪深300与中证1000的趋势状态、日期及具体触发原因 |
| `backtest` 单持仓买入 | `跌势` 日禁止买入 |
| `backtest` 单持仓卖出 | `--maxdrawdown [ratio]` 控制 ATR 动态止损 |
| `--maxdrawdown [ratio]`（所有命令） | ATR 动态止损倍数；默认 ratio=2，始终启用 |
| ATR 倍数调整 | 涨势：N×1.2；跌势：N×0.8；其余：N |
| 期末处理 | 期末仍按最后一个交易日收盘强制平仓 |

说明：

- `跌势` 在 `_generate_effective_signals()` 中统一作用于策略原始信号，覆盖 `analyze`、`scan` 和 `backtest` 单持仓流程；
- 个股对应指数趋势为「跌势」时其买入信号置 0；
- `market_trend_states()` 供回测按日取状态，`analyze_market_trend()` 供界面显示最新状态；
- 两个函数均使用 `lru_cache`，数据库刷新后需重启进程或清理缓存才能立即生效。

#### 代码位置

| 功能 | 位置 |
|------|------|
| 配置读取 | `core/market_trend.py:get_market_trend_index()` |
| ADR(10) 计算 | `core/market_trend.py:_fetch_adr_series()` |
| 逐日状态判定 | `core/market_trend.py:classify_market_trend_series()` |
| 区间状态序列 | `core/market_trend.py:market_trend_states()` |
| 最新趋势分析 | `core/market_trend.py:analyze_market_trend()` |
| 涨跌家数读取 | `core/db.py:fetch_market_breadth_counts()` |
| 屏幕/HTML 显示 | `main.py:_format_market_trend()` / `_print_market_trend()` / `_market_trend_html()` |
| 大盘趋势买卖控制 | `main.py:_apply_market_regime_to_signals()`（跌势禁买） |
| `--maxdrawdown` | `main.py:_set_maxdrawdown()` / `_build_strategy_instance()` |
| onestock 买卖行为 | `main.py:_run_onestock_backtest()` |
| 趋势指数配置 | `squant.toml` → `market_trend_index` |

#### 个股趋势（analyze）

- 函数：`core.market_trend.analyze_stock_trend(df, end_date=None)`、`classify_stock_trend_series(df)`。
- 数据源：传入的个股日线 DataFrame（需含 MA5/MA20/MA60、ADX14、RSI14 等；缺失时自动补算指标）。
- 规则：与大盘趋势相同，但**不使用 ADR**；因此：
  - `涨势`：`(close>MA5>MA60 AND slope5>0) OR (ADX14>=25 AND +DI>-DI)`；
  - `跌势`：`(close<MA5<MA60 AND slope5<0) OR (ADX14>=25 AND +DI<-DI)`；
  - 其余为 `震荡`。
- 展示：HTML 汇总报告的“最新信号表”增加 `个股趋势` 栏；单只股票 HTML 报告头部也会显示。

---

### 0.6 买卖相关命令行选项

以下选项直接影响买入/卖出信号的生成、过滤、持仓与风控；未列出的数据源/日期/输出选项见对应命令帮助。

| 选项 | 适用命令 | 作用 | 默认/说明 |
|------|----------|------|-----------|
| `-g, --strategy` | analyze / scan / backtest | 选择策略（逗号分隔，`all` 展开为内置选择集） | 默认 `boma` |
| `--maxdrawdown [ratio]` | analyze / scan / backtest / compare | ATR 动态止损：`stop=max(前一日stop, close-ratio*ATR14)` | 默认 `2`，始终启用；倍数按大盘状态调整：涨势 ×1.2、跌势 ×0.8、震荡 ×1.0 |
| `--limit` | analyze / scan / backtest | 限制买入：ST、当前价≥一年前价×`LIMIT_PRICE`、当前 PE<0 或≥一年前正 PE×`LIMIT_PE`、总市值<`LIMIT_CAPITAL` 亿元 | 默认关闭；阈值见 `[tool.stock-quant.limit]` |
| `-n, --num-positions` | backtest | 同时持有的最大股票数 N | 默认 `1` |
| `--fixcapital` | backtest | 每笔买入金额固定为 `初始资金/N`；现金不足仍买入，现金可为负 | 默认关闭 |
| `-c, --capital` | analyze / scan / backtest / compare | 初始资金（万元） | 默认 `100` |
| `-i, --index` | analyze | 指数分析模式（使用通用策略） | 默认关闭 |

**配置开关**

- `[tool.stock-quant.boma]`：为 BOMA 的每个买卖信号单独设置开关（`1` 启用、`0` 忽略）；未配置默认启用。
- `[tool.stock-quant.limit]`：`LIMIT_ST`（1/0）、`LIMIT_PRICE`、`LIMIT_PE`、`LIMIT_CAPITAL`（亿元）。
- `[tool.stock-quant.thresholds]`：RSI/PE/ADX 等策略阈值。

**组合关系**

- 买入侧：`-g` 选策略 → BOMA 配置开关筛选信号 → 跌势禁买 → `--limit` 个股禁买 → 资金/持仓规则（`-n`、`--fixcapital`、`-c`）。
- 卖出侧：策略自身卖出信号 + `--maxdrawdown` 的 ATR 动态止损。

**backtest 选股顺序**

- 先按 `-g` 的策略顺序；
- 同一策略内，对当日有买入信号的股票按“综合打分”从高到低排序，优先买入最高分；
- 综合打分相同则按股票池顺序（`-sf` 文件顺序 / `-s` 顺序 / `--random` 随机顺序）；
- 当日卖出后空出的仓位可立即再次买入符合条件的股票。

---

## 一、技术指标类策略

### 1.1 MA1030 策略 (MACrossStrategy)

**定位：** 经典趋势跟踪，适用于有明显趋势的市场。

**因子（指标）选择**

| 指标 | 周期 | 说明 |
|------|:---:|------|
| 快线 `fast_ma` | 10 | 短期 SMA，反映近期价格 |
| 慢线 `slow_ma` | 30 | 中期 SMA，反映趋势方向 |
| RSI | 14 | 相对强弱，用于买卖过滤 |

**买卖信号生成**

| 信号 | 条件 | 说明 |
|:---:|---|---|
| 买入 `1` | 金叉（`fast_ma.shift(1) <= slow_ma.shift(1)` 且 `fast_ma > slow_ma`）且 `RSI < MIN_RSI_THRESHOLD` | 金叉 + 未过热 |
| 卖出 `-1` | 死叉（`fast_ma.shift(1) >= slow_ma.shift(1)` 且 `fast_ma < slow_ma`）**或** `RSI > MAX_RSI_THRESHOLD` | 死叉或 RSI 超买 |
| 观望 `0` | 其余 | — |

**参数**

| 参数 | 默认 | 说明 |
|------|:---:|------|
| `fast_period` | 10 | 快线周期 |
| `slow_period` | 30 | 慢线周期 |
| `rsi_buy_max` | 配置 `MIN_RSI_THRESHOLD` | 买入附加条件 `RSI < MIN_RSI_THRESHOLD`（`None` 不启用） |
| `rsi_sell_min` | 配置 `MAX_RSI_THRESHOLD` | 卖出附加条件 `RSI > MAX_RSI_THRESHOLD`（`None` 不启用） |

**设计要点：** 用 `shift(1)` 检测交叉的"边沿"而非"状态"，仅在交叉发生当日出信号，其余交易日保持观望，避免重复信号。买入附加 `RSI < MIN_RSI_THRESHOLD`，以过滤过热追高。ETF 版参数为 MA10/MA40。

---

### 1.2 MACD 策略 (MACDStrategy)

**定位：** 趋势跟踪动量策略，通过快慢 EMA 差值反映趋势强度与方向变化。

**因子（指标）选择**

| 指标 | 计算 | 说明 |
|------|------|------|
| DIF | EMA12 - EMA26 | 快慢线差，动量核心 |
| DEA | DIF 的 9 日 EMA | 信号线 |
| MACD 柱 | 2 × (DIF - DEA) | 动能强弱 |
| RSI | 14 | 相对强弱，用于买卖过滤 |

**买卖信号生成**

| 信号 | 条件 | 说明 |
|:---:|---|---|
| 买入 `1` | `DIF.shift(1) <= DEA.shift(1)` 且 `DIF > DEA` 且 `RSI < MIN_RSI_THRESHOLD` | 金叉 + 未过热 |
| 卖出 `-1` | 死叉（`DIF.shift(1) >= DEA.shift(1)` 且 `DIF < DEA`）**或** `RSI > MAX_RSI_THRESHOLD`；**或** `close <= max(前一日止损价, close-N*ATR14)` | 死叉/RSI 超买或 ATR 止损 |
| 观望 `0` | 其余 | — |

**参数**

| 参数 | 默认 | 说明 |
|------|:---:|------|
| `fast` | 12 | 快速 EMA 周期 |
| `slow` | 26 | 慢速 EMA 周期 |
| `signal` | 9 | DEA 信号线周期 |
| `rsi_buy_max` | 配置 `MIN_RSI_THRESHOLD` | 买入附加条件 `RSI < MIN_RSI_THRESHOLD`（`None` 不启用） |
| `rsi_sell_min` | 配置 `MAX_RSI_THRESHOLD` | 卖出附加条件 `RSI > MAX_RSI_THRESHOLD`（`None` 不启用） |
| `use_atr_stop` | 始终启用（倍数由 --maxdrawdown 提供） | ATR 动态止损：`stop=max(前一日stop, close-N*ATR14)`，`close<=stop` 时卖出 |

**设计要点：** 金叉时 `DIF > DEA` 恒成立，故 `MACD柱 > 0` 为冗余条件已省略；买卖附加 RSI 过滤（`RSI<MIN_RSI_THRESHOLD` / `RSI>MAX_RSI_THRESHOLD`）以抑制过热追高与提前离场。RSI 已并入金叉/死叉定义。买入后逐日更新 ATR 动态止损价，`close <= stop` 时优先卖出。

**MACD1 变体：** `-g macd1` 与 `macd` 的逻辑、过滤和风控完全相同，仅默认参数改为：

```text
fast=6, slow=13, signal=5
```

ETF 专用模式下 `macd1` 同样使用该默认参数。

---

### 1.3 KDJ 策略 (KDJStrategy)

**定位：** 随机振荡指标（KDJ），比较收盘价在近期高低价区间中的位置，捕捉低位金叉/高位死叉的超买超卖反转。

**因子（指标）选择**

| 指标 | 周期 | 说明 |
|------|:---:|------|
| K | n=9, m1=3 | RSV 的平滑线 |
| D | m2=3 | K 的平滑线 |
| J | — | 3K - 2D，反映偏离度 |
| MA60 | 60 | 买入过滤（站上 60 日线） |

**买卖信号生成**

| 信号 | 条件 | 说明 |
|:---:|---|---|
| 买入 `1` | `K.shift(1) <= D.shift(1)` 且 `K > D` 且 `close > MA60` | 金叉 + 站上 60 日线 |
| 卖出 `-1` | `K.shift(1) >= D.shift(1)` 且 `K < D` | 死叉：K 下穿 D |
| 观望 `0` | 其余 | — |

**参数**

| 参数 | 默认 | 说明 |
|------|:---:|------|
| `n` | 9 | RSV 计算周期 |
| `m1` | 3 | K 线平滑周期 |
| `m2` | 3 | D 线平滑周期 |
| `oversold` | 20 | 超卖阈值（K 低于此值视为超卖） |
| `overbought` | 80 | 超买阈值（K 高于此值视为超买） |
| `ma_slow` | 60 | 买入过滤用慢速均线（MA60）周期 |

**设计要点：** 基础买卖判定（交叉边沿 + `close>MA60` 过滤）即为最终逻辑，依赖 `high`/`low`/`close` 三列。

---

### 1.4 布林带策略 (BollingerStrategy)

**定位：** 利用布林带上下轨作为支撑/阻力位的均值回归反转策略。

**因子（指标）选择**

| 指标 | 计算 | 说明 |
|------|------|------|
| 中轨 MIDDLE | 20 日 SMA | 趋势基准 |
| 上轨 UPPER | 中轨 + 2σ | 阻力位 |
| 下轨 LOWER | 中轨 - 2σ | 支撑位 |
| RSI | 14 | 买入/卖出过滤 |

**买卖信号生成**

| 信号 | 条件 | 说明 |
|:---:|---|---|
| 买入 `1` | 下轨反弹（`close.shift(1) <= lower.shift(1)` 且 `close > lower`）**且** `RSI < MIN_RSI_THRESHOLD`；`EMA60_CHECK=1` 时**且** `EMA60 < close`；`ADX_CHECK=1` 时**且** `ADX > 20` | 低位反弹 + 未过热（带宽过滤已停用） |
| 卖出 `-1` | 上轨回落（`close.shift(1) >= upper.shift(1)` 且 `close < upper`）**或** `RSI > MAX_RSI_THRESHOLD`；**或** `close <= max(前一日止损价, close-N*ATR14)` | 上轨回落/RSI 超买或 ATR 止损（不受带宽过滤影响） |
| 观望 `0` | 其余 | — |

**参数**

| 参数 | 默认 | 说明 |
|------|:---:|------|
| `period` | 20 | 中轨周期 |
| `std` | 2 | 标准差倍数 |
| `bbw_lookback` | 10 | 带宽过滤统计回看窗口（当前过滤已停用） |
| `use_atr_stop` | 始终启用（倍数由 --maxdrawdown 提供） | ATR 动态止损：`stop=max(前一日stop, close-N*ATR14)`，`close<=stop` 时卖出 |
| `BOLL_SQUEEZE_SCALE` | 配置（默认 0.2） | 挤压判定比例因子（当前过滤已停用） |
| `BOLL_EXTRAWIDE_SCALE` | 配置（默认 1.2） | 极端宽判定比例因子（当前过滤已停用） |
| `EMA60_CHECK` | 配置（默认 0） | 1=买入要求 `EMA60 < close`；0=不检查 |
| `ADX_CHECK` | 配置（默认 0） | 1=买入要求 `ADX > 20`；0=不检查 |

**设计要点：** 采用"穿越轨道边沿"判定，捕捉价格回到带内的反转时刻。原布林带宽度过滤（非挤压 / 非极端宽）经实测效果不佳，已**停用**（代码保留、过滤行注释），当前逻辑：

- 买入：下轨反弹 **且** `RSI < MIN_RSI_THRESHOLD`（`EMA60_CHECK=1` 时另加 `EMA60 < close`；`ADX_CHECK=1` 时另加 `ADX > 20`）；
- 卖出：上轨回落 **或** `RSI > MAX_RSI_THRESHOLD`，或回撤止损；
- `bbw_lookback` / `BOLL_SQUEEZE_SCALE` / `BOLL_EXTRAWIDE_SCALE` 当前不生效。

---

### 1.5 BOMA 策略 (BOMAStrategy)

**定位：** BOLL、MACD 与 DMI 条件组合策略，后续可继续替换为独立信号规则。

| 信号 | 条件 |
|:---:|---|
| 买入信号BOLL | BOLL 下轨反弹 AND (`MACD红柱` OR `绿柱缩短1根`) AND `RSI6>RSI12` AND `close>MA5` |
| 买入信号MACD1 | `MACD金叉` AND `DIF>=0` AND `大盘涨势` AND `ADX14>20` AND `+DI>-DI` |
| 买入信号MACD2 | `MACD金叉` AND `DIF<0` AND `RSI14<30` AND `BIAS20<0` AND `ADX14>20` |
| 买入信号ADX | `ADX14>25` AND `+DI上穿-DI` AND `ADXR>ADX` AND `close>MA5` |
| 买入 `1` | 买入信号BOLL OR 买入信号MACD1 OR 买入信号MACD2 OR 买入信号ADX |
| 卖出信号BOLL1 | `个股趋势=涨势` AND `跌破布林中轨` AND `ADX回落` |
| 卖出信号BOLL2 | `个股非涨势` AND `BOLL上轨回落` AND `DIF斜率<0` |
| 卖出信号MACD1 | `个股趋势∈{涨势,震荡}` AND `MACD死叉` AND `close < BOLL_MIDDLE` AND `+DI向下` AND `-DI向上` AND `ADX14>=20` |
| 卖出信号MACD2 | `个股趋势=跌势` AND `MACD死叉` |
| 卖出信号ATR | `close < 移动止损价` AND `close > BOLL下轨` |
| 卖出 `-1` | 卖出信号BOLL1 OR 卖出信号BOLL2 OR 卖出信号MACD1 OR 卖出信号MACD2 OR 卖出信号ATR |
| 观望 `0` | 其余 |

**趋势覆盖规则（始终生效，优先于个股原始信号）**

| 大盘趋势 | 个股趋势 | 处理 |
|:---:|:---:|---|
| 跌势 | 跌势 | **强制卖出**（忽略个股原始卖出信号），原因记为 `趋势共振卖出` |
| 涨势 | 涨势 | **忽略个股卖出信号**（持有/观望） |
| 任一为跌势 | — | **忽略个股买入信号**（观望） |


指标定义：

- `大盘涨势`：取自 `df.attrs['market_states']`（个股对应指数的每日趋势状态）；
- `个股趋势`：取自 `classify_stock_trend_series(df)`（同大盘规则但不含 ADR）；包含 涨势/跌势/震荡；
- `BIAS20`：`(close - MA20) / MA20`，`BIAS20<0` 为负乖离；
- `BOLL下轨反弹`：`close.shift(1) <= lower.shift(1)` 且 `close > lower`；
- `BOLL上轨回落`：`close.shift(1) >= upper.shift(1)` 且 `close < upper`；
- `跌破布林中轨`：`close.shift(1) >= middle.shift(1)` 且 `close < middle`；
- `ADX回落`：`ADX14 < ADX14.shift(1)`；
- `DIF斜率<0`：`DIF < DIF.shift(1)`；
- `个股非涨势`：`个股趋势 ∈ {震荡, 跌势}`；
- `MACD红柱`：`MACD_BAR > 0`；
- `绿柱缩短1根`：当前和上一根均为绿柱，且 `MACD_BAR > MACD_BAR.shift(1)`；
- `RSI6>RSI12`：短期 RSI 高于中期 RSI；
- `close>MA5`：收盘价站上 MA5；
- `+DI向下`：`+DI < +DI.shift(1)`；
- `-DI向上`：`-DI > -DI.shift(1)`；
- `+DI上穿-DI`：`+DI.shift(1) <= -DI.shift(1)` 且 `+DI > -DI`；
- `ADXR`：`(ADX14 + ADX14.shift(14)) / 2`。
- `移动止损价`：`stop=max(前一日stop, close-N*ATR14)`，`close < stop` 且 `close > BOLL下轨` 时卖出；N 由 `--maxdrawdown` 提供。

**参数：** `boll_period=20`、`boll_std=2`、`macd_fast=12`、`macd_slow=26`、`macd_signal=9`。

**配置开关：** `squant.toml` 的 `[tool.stock-quant.boma]` 可为每个信号单独设置开关（键需加引号），`1`=启用、`0`=关闭，缺省启用：
```toml
[tool.stock-quant.boma]
"买入信号BOLL" = 1
"买入信号MACD1" = 1
"买入信号MACD2" = 1
"买入信号ADX" = 1
"卖出信号BOLL1" = 1
"卖出信号BOLL2" = 1
"卖出信号MACD1" = 1
"卖出信号MACD2" = 1
"卖出信号ATR" = 1
```
各信号仅由对应配置开关控制：`1` 启用、`0` 忽略；未配置默认启用。

> 说明：当前不再使用 RSI 过滤；卖出信号ATR 使用 ATR 移动止损。买入仍统一应用全局涨幅/SAR/指数/序号过滤。
> 回测 HTML 的退出原因显示为“卖出信号BOLL1/BOLL2/MACD1/MACD2/ATR”或“趋势共振卖出”，多个条件同时满足时用“/”连接。

### 1.6 SAR 策略 (SARStrategy)

**定位：** 抛物线转向指标（Parabolic SAR），通过 SAR 与收盘价的位置切换判断趋势反转。

**指标计算：** 标准 Parabolic SAR，加速因子初始 `0.02`、步长 `0.02`、上限 `0.2`；逐日按极点价（EP）与加速因子（AF）递推，趋势反转时 SAR 跳至上一轮极点价另一侧。

**买卖信号生成**

| 信号 | 条件 | 说明 |
|:---:|---|---|
| 买入 `1` | 上一日 `SAR > close` 且当日 `SAR < close` | SAR 从 K 线上方突破至下方 |
| 卖出 `-1` | 上一日 `SAR < close` 且当日 `SAR > close` | SAR 从 K 线下方突破至上方 |
| 观望 `0` | 其余 | — |

**参数**

| 参数 | 默认 | 说明 |
|------|:---:|------|
| `af_step` | 0.02 | 加速因子步长 |
| `af_max` | 0.2 | 加速因子上限 |

**设计要点：** 仅使用 SAR 与收盘价的穿越方向，不附加 RSI 过滤；`SAR_CHECK` 宏不作用于本策略。依赖 `high`/`low`/`close` 三列。

---

## 二、多因子类策略

### 2.1 质价融合策略 (QualityValueFactorStrategy)

**定位：** 融合估值、质量、筹码、增减持、波动率五个维度，加权生成综合投资价值评分并转换为买卖信号。是唯一依赖基本面/另类数据的多因子策略。

**因子选择（五个维度）**

| 维度 | 权重 | 因子 | 子权重 | 方向 |
|------|:---:|------|:---:|------|
| 估值（价值） | 0.35 | 见下方分型 | 见下方 | 见下方 |
| 质量 | 0.30 | ROE / 现金流质量 / 净利率 / 资产负债率 / 利润增长 | 0.30/0.25/0.20/0.15/0.10 | +/+/+/-/+ |
| 筹码（股东人数） | 0.15 | 股东人数变化 / 人均持股变化 | 0.6/0.4 | 减少好 / 增加好 |
| 增减持 | 0.10 | 近 12 个月累计净增持量 | 1.0 | 增持好 |
| 波动率（低波异象） | 0.10 | HV20 / MaxDD(250) / ATR14/close | 0.40/0.35/0.25 | 越低越好 |

**估值因子分型**

| 标的类型 | 判别 | 因子 | 子权重 |
|------|------|------|:---:|
| broad_index | 宽基指数代码 / ETF | pe_ttm, pb | [0.55, 0.45] |
| growth | 300/301/688 开头 | ps, pcf | [0.55, 0.45] |
| dividend | 默认 | pe_ttm, pb, ps | [0.40, 0.35, 0.25] |

**打分方式：** 各维度分别做分位数打分（估值逐日 / 质量按公告日 + ffill / 筹码按报告期 + ffill / 增减持按公告日 + ffill / 波动率逐日），得分均归一化到 0~1；再按有效维度权重动态归一化加权求和。

**滚动 Z-score 标准化：** 综合得分 combined 集中在小区间（如 0.4~0.7），直接阈值判断容易误判。因此先对 combined 轻度平滑（`rolling(3).mean()`），再做滚动 Z-score 标准化（窗口 20），用 Z 值阈值判定信号：

```
combined_smooth = combined.rolling(3).mean()
z_t = (combined_smooth,t - μ_window) / σ_window
```

**买卖信号生成（基于 Z 值）**

| 信号 | 条件 | 说明 |
|:---:|---|---|
| 买 `1` | `z >= z_buy (1.0)` | 综合评估显著优于常态 |
| 卖 `-1` | `z <= z_sell (-1.0)` | 综合评估显著劣于常态 |
| 观望 `0` | `-1.0 < z < 1.0` | 中性 |

**参数**

| 参数 | 默认 | 说明 |
|------|:---:|------|
| `score_smooth_window` | 3 | 综合得分轻度平滑窗口 |
| `score_z_window` | 20 | 滚动 Z-score 窗口 |
| `z_buy` | 1.0 | 基础买入 Z 阈值 |
| `z_sell` | -1.0 | 基础卖出 Z 阈值 |
| `value_weight` | 0.35 | 估值维度权重 |
| `quality_weight` | 0.30 | 质量维度权重 |
| `shareholder_weight` | 0.15 | 筹码维度权重 |
| `insider_weight` | 0.10 | 增减持维度权重 |
| `volatility_weight` | 0.10 | 波动率维度权重 |

**设计要点：** 单维度数据缺失时按剩余有效维度权重比例重新归一化，宽基指数自动跳过筹码/增减持维度；全部维度缺失时返回全 0 信号。`combined_score`、`z_score`、`z_buy`/`z_sell` 写入 `signals.attrs` 供图表展示（质价子图显示「综合得分Z」曲线与 ±阈值参考线）。

---

## 三、组合类策略

### 3.1 买入并持有策略 (HoldStrategy)

**定位：** 基准对照策略，用于衡量其它策略相对"买入持有"的超额收益。

- 信号：从分析期 start 起全部买入（`1`）并持有到分析期结束（`warmup=0`），无任何卖出信号
- 回测结果（fraction 模式）：1 次买入、0 次卖出，权益曲线即纯买入持有表现
- 参数：`warmup=0`（预热天数，信号从 start 起即可考虑）

---

## 四、信号生成方式汇总对比

| 策略 | 信号类型 | 因子类别 | 频度 | 反转/趋势 |
|------|:---:|------|------|------|
| MA1030 | 交叉边沿 | 双均线 + RSI 过滤 | 日频 | 趋势跟踪 |
| MACD | 交叉边沿 | DIF/DEA + RSI 过滤 | 日频 | 趋势跟踪 |
| KDJ | 交叉边沿 | K/D + MA60 过滤 | 日频 | 均值回归 |
| SAR | 穿越边沿 | SAR 与收盘价位置切换 | 日频 | 趋势跟踪 |
| 布林带 | 轨道穿越边沿 | 布林带 + RSI 过滤（带宽过滤已停用） | 日频 | 均值回归 |
| 质价融合 | Z 值阈值（平滑+滚动标准化） | 五维基本面/另类 | 估值/波动日频，其余低频 ffill | 价值回归 |
| 买入并持有 | 固定信号 | 无（基准对照） | 一次性买入后持有 | 趋势跟踪 |

**共通约定：**

1. 所有技术指标类策略用 `shift(1)` 检测"交叉/穿越边沿"，仅在触发当日出信号，其余为观望。
2. 所有策略信号序列索引与行情 `df.index` 严格对齐，值为 `1/-1/0`。
3. 除质价融合外，其余策略仅依赖 `close`（及可选 `volume`），无外部数据依赖；RSI 过滤优先取 `RSI14`，缺失时按 `close` 用 `calc_rsi` 现算。
4. 全局买入过滤：`EMA60_CHECK=1` 时 BOLL 另加「当日 EMA60 < close」买入条件；`ADX_CHECK=1` 时 BOLL 另加「当日 ADX > 20」买入条件。

---

## 五、遗留策略说明

以下文件为历史版本，**未注册**（未出现在 `STRATEGY_MAP` 等注册表中），保留仅供参考：

| 文件 | 说明 |
|------|------|
| `strategies/value_factor.py` | 早期估值因子策略（PE/PB/PS/PCF），功能并入质价融合策略的估值维度 |
| `strategies/quality_factor.py` | 早期质量因子策略（ROE/现金流/净利率等），功能并入质价融合策略的质量维度 |
| `strategies/enhanced_macd.py` | 已移除（不再注册） |
| `strategies/enhanced_rsi.py` | 已移除（不再注册） |
