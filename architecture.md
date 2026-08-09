# 股票量化分析软件 — 架构与使用文档

> 生成日期：2026-08-08

## 一、项目概览

这是一个基于 **Python** 的 A 股/美股量化分析工具，核心流程为：**数据获取 → 指标计算 → 策略信号 → 回测 → 风险分析 → 可视化 → 报告输出**。

**技术栈**：`akshare` / `yfinance`（数据）、`pandas` / `numpy`（计算）、`matplotlib` / `mplfinance`（图表）、`scipy`（风险统计）、`click`（CLI）、`tabulate`（表格）。

---

## 二、架构分层

```
stock-quant/
├── core/                  # 核心引擎层（可复用、无业务耦合）
│   ├── data_fetcher.py    # 数据获取（A股akshare / 美股yfinance）
│   ├── indicators.py      # 技术指标（MA/EMA/MACD/RSI/布林带等）
│   ├── strategy.py        # 策略抽象基类（ABC）
│   ├── backtest.py        # 回测引擎（佣金/滑点/绩效指标）
│   └── risk.py            # 风险度量（VaR/CVaR/回撤/夏普等）
├── strategies/            # 策略实现层（继承 Strategy 基类）
│   ├── ma_cross.py        # 双均线交叉
│   ├── macd_strategy.py   # MACD
│   ├── rsi_strategy.py    # RSI
│   ├── bollinger_strategy.py # 布林带
│   ├── composite_strategy.py # 复合策略（加权投票）
│   ├── momentum_tiered.py # 动量分层（指数）
│   ├── volatility_timing.py # 波动率择时（指数）
│   └── breadth_confirmation.py # 涨跌比确认（指数）
├── visualization/
│   └── charts.py          # 图表生成（K线/资金曲线/多策略对比）
├── main.py                # CLI 交互主入口
├── fetch_*.py             # 数据抓取脚本
├── run_*.py               # 各类分析/扫描/回测脚本
└── requirements.txt
```

### 分层设计要点
- **`core/` 为纯逻辑层**，不依赖具体股票或业务，可独立复用。
- **`strategies/` 通过抽象基类 `Strategy` 统一接口**，新增策略只需实现 `generate_signals(df)` 返回信号序列（`1`=买入、`-1`=卖出、`0`=持有）。
- **`run_*.py` 为业务脚本层**，负责组装各模块完成具体任务（单股分析、批量分析、全市场扫描等）。

---

## 三、核心模块职责

### 1. 数据层 `core/data_fetcher.py`
- `DataFetcher` 类统一封装数据源：**A股用 akshare，美股用 yfinance**。
- 自动识别代码类型（纯数字=A股，字母=美股），并标准化 `sh/sz` 前缀。
- 提供历史行情、实时行情、指数数据、股票列表等接口。

### 2. 指标层 `core/indicators.py`
- 纯函数式设计，输入 DataFrame 输出 Series/DataFrame，索引自动对齐。
- 提供 `calc_ma`、`calc_ema`、`calc_macd`、`calc_rsi`、`calc_bollinger` 及 `add_all_indicators`（一键批量添加）、`calc_historical_volatility`、`calc_momentum_return` 等。

### 3. 策略层 `core/strategy.py` + `strategies/`
- `Strategy` 抽象基类定义 `generate_signals()`（抽象）和 `calculate_position()`（持仓转换）。
- **5 个个股策略** + **3 个指数策略**。
- `CompositeStrategy` 通过**加权投票**融合多个子策略信号，降低单一策略噪声。

### 4. 回测层 `core/backtest.py`
- `BacktestEngine` 支持**全仓**和**按比例**两种持仓模式。
- 模拟**佣金（默认万三）**和**滑点（默认万一）**。
- 输出完整绩效：总/年化收益、最大回撤、夏普比率、胜率、盈亏比、交易明细、资金曲线等。

### 5. 风险层 `core/risk.py`
- 提供 `calc_var`（在险价值）、`calc_cvar`（条件在险价值）、`calc_max_drawdown`、卡玛/索提诺/信息比率、Beta、Alpha，以及综合 `risk_report`。

### 6. 可视化 `visualization/charts.py`
- 基于 matplotlib/mplfinance，自动配置中文字体。
- 生成 K 线图、资金曲线、多策略对比图等。

---

## 四、使用方法

### 1. 环境准备
```bash
pip install -r requirements.txt
```

### 2. 交互式 CLI（推荐入口）
```bash
python main.py
```
进入交互菜单，可进行单股分析、策略回测、策略对比等。

### 3. 单股完整分析脚本
每个 `run_analysis_*.py` 针对特定股票，流程固定：加载数据 → 计算指标 → 运行策略 → 回测 → 风险分析 → 生成图表 → 输出报告。
```bash
python run_analysis.py            # 德赛西威(002920)
python run_analysis_000021_v2.py  # 深科技
python run_analysis_300726.py     # 宏达电子
```

### 4. 批量分析
```bash
python run_batch_analysis.py      # 6只股票
python run_batch_8stocks.py       # 8只股票
python run_batch_12stocks.py      # 12只股票
```

### 5. 全市场扫描（选股）
```bash
python run_scanner.py             # 扫描A股，找买入信号最强10只
python run_scanner_v2.py          # v2：板块分散选股
```

### 6. 数据抓取
```bash
python fetch_8stocks.py           # 为指定股票抓取日线数据
python fetch_300476.py
```

### 7. 输出 JSON 供外部系统使用
```bash
python run_kaiwang.py             # 凯旺科技，输出JSON格式结果
```

---

## 五、数据流

```mermaid
flowchart LR
    A[数据源<br/>akshare/yfinance/新浪] --> B[DataFetcher]
    B --> C[indicators<br/>技术指标]
    C --> D[Strategy<br/>生成信号]
    D --> E[BacktestEngine<br/>回测]
    E --> F[risk<br/>风险分析]
    F --> G[charts<br/>可视化]
    G --> H[报告/JSON输出]
```

---

## 六、设计亮点与注意事项

### 设计亮点
- ✅ 分层清晰，`core` 与 `strategies` 解耦，易于扩展新策略。
- ✅ 数据源自动切换（A股/美股），代码标准化处理。
- ✅ 回测考虑交易成本（佣金+滑点），结果更贴近真实。
- ✅ 复合策略加权投票机制，提升信号稳健性。

### 注意事项
- ⚠️ 部分脚本硬编码了数据路径 `/workspace/stock_quant/data/`，与当前工作目录 `/home/abc/myproj/stock-quant` 不一致，运行前需确认数据文件位置。
- ⚠️ 扫描脚本依赖新浪公开接口，可能受网络/接口变动影响。
- ⚠️ 各 `run_*.py` 脚本存在较多重复代码（如 `pct`/`fmt` 工具函数），可考虑抽取到公共模块。
