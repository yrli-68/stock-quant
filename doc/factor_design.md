# 质量价值融合策略 — 软件设计文档

> 基于 `strategies/quality_value_factor.py` 源码分析生成。
> v2: 新增股东人数和增减持因子。

---

## 一、架构概览

```
行情数据 df (OHLCV + 技术指标)
         │
         └──→ QualityValueFactorStrategy.generate_signals(df)
                  │
                  ├── 标的分类 (_classify_stock)
                  │
                  ├── 估值维度 → _compute_value_score      (逐日)
                  │        └── akshare 东方财富/百度/乐咕乐股
                  │
                  ├── 质量维度 → _compute_quality_score     (报告期+ffill)
                  │        └── akshare 财务分析指标
                  │
                  ├── 筹码维度 → _compute_shareholder_score (报告期+ffill)
                  │        └── akshare 股东人数变化
                  │
                  ├── 增减持维度 → _compute_insider_score    (公告日+ffill)
                  │        └── akshare 大股东增减持历史
                  │
                  └── 加权融合 → 阈值 → 信号 (1/0/-1)
```

## 二、因子配置

### 2.1 估值因子 (价值维度) — 权重 40%

| 类型 | 判别 | 因子 | 子权重 |
|------|------|------|:---:|
| broad_index | 代码在 BROAD_INDEX_CODES | pe_ttm, pb | [0.55, 0.45] |
| growth | 300/301/688 开头 | ps, pcf | [0.55, 0.45] |
| dividend | 默认 | pe_ttm, pb, ps | [0.40, 0.35, 0.25] |

### 2.2 质量因子 — 权重 35%

| 因子 | 关键词 | 子权重 | 方向 |
|------|--------|:---:|------|
| ROE | 净资产收益率 | 0.30 | positive |
| 现金流质量 | 经营现金净流量与净利润的比率 | 0.25 | positive |
| 净利率 | 销售净利率 | 0.20 | positive |
| 资产负债率 | 资产负债率 | 0.15 | negative |
| 利润稳定性 | 净利润增长率 | 0.10 | positive |

### 2.3 筹码因子 (股东人数) — 权重 15%

| 因子 | 子权重 | 打分逻辑 |
|------|:---:|---------|
| 股东人数变化 | 0.6 | 增幅映射: -30%→0.9, 0%→0.5, +30%→0.1 |
| 人均持股变化 | 0.4 | 增幅映射: +30%→0.9, 0%→0.5, -30%→0.1 |

数据源: `ak.stock_hold_num_cninfo(date=report_date)`，取近 3 年每半年报告期

### 2.4 增减持因子 — 权重 10%

数据源: `ak.stock_shareholder_change_ths(symbol)`，获取大股东/高管全部增减持记录

打分: 每个公告日计算过去 12 个月累计净增持量，sigmoid 对数映射为 0~1 得分

### 2.5 阈值

| 参数 | 默认值 |
|------|:------:|
| buy_threshold | 0.6 |
| sell_threshold | 0.4 |

---

## 三、数据获取

### 3.1 估值数据

```
_fetch_valuation(symbol, stock_type)
│
├── _fetch_index_valuation(symbol)        ← broad_index
│   ├── ak.stock_index_pe_lg(index_name)  → 乐咕乐股 PE-TTM
│   └── ak.stock_index_pb_lg(index_name)  → 乐咕乐股 PB
│
└── _fetch_stock_valuation(symbol)        ← 个股/ETF
    ├── 主源: ak.stock_value_em(symbol)   → 东方财富 (PE/PB/PS/PCF)
    └── 备选: ak.stock_zh_valuation_baidu → 百度估值
```

缓存 `_valuation_cache`：同次运行同一股票只请求一次。

### 3.2 财务数据

```
_fetch_financials(symbol)
│
└── ak.stock_financial_analysis_indicator(symbol, start_year='2016')
    → DataFrame，每行一个报告期
```

缓存 `_financials_cache`：同次运行只请求一次。

---

## 四、分位数打分

### 4.1 价值维度打分 (逐日)

对行情数据的**每一个交易日**执行：

```
对于 date 在 df.index 中的每一天:
│
├── 1. 截取截止到 date 的估值历史序列 hist_val
├── 2. 若历史数据 < 60 条 → 跳过
│
├── 3. 对每个估值因子:
│   ├── current = val_df.loc[date, factor]
│   ├── pct = (hist_val[factor] < current).sum() / len(hist_val)
│   │
│   ├── 方向调整:
│   │   ├── PE/PB/PS/PCF → pct = 1 - pct (反向: 高分位=高估值=差)
│   │   └── EY/股息率     → 不变        (正向: 高分位=高收益=好)
│   │
│   └── 加权累加
│
└── 4. 归一化 → value_score ∈ [0, 1]
```

**返回值:** 与 df.index 对齐的 `pd.Series`，有效日期为 0~1 得分，无效为 NaN。

### 4.2 质量维度打分 (报告期 + ffill)

以**财务报告日期**为评分点：

```
对于每个财务报告日 idx:
│
├── 对每个质量因子:
│   ├── series = fin_df[col].iloc[:idx+1]
│   ├── pct = (series < current).sum() / len(series)
│   ├── negative 方向 → pct = 1 - pct
│   └── 加权累加
│
├── 形成 (报告日期, score) 序列
│
└── score_aligned = score_series.reindex(df.index, method='ffill')
    → 前向填充到每个交易日
```

**返回值:** 与 df.index 对齐的 `pd.Series`。

### 4.3 信号形态特点

- **价值维度:** 可能日频变化（估值数据每日更新）
- **质量维度:** 阶梯状变化（报告期间 ffill 保持不变），作为"慢变量"稳压

---

## 五、融合与信号生成

### 5.1 四维加权融合

```
对每个交易日 i:
│
├── vw = has_value      ? value_weight      : 0
├── qw = has_quality    ? quality_weight    : 0
├── sw = has_shareholder? shareholder_weight: 0
├── iw = has_insider    ? insider_weight    : 0
│
├── total_w = vw + qw + sw + iw
├── 若 total_w == 0 → 跳过
│
└── combined[i] = (v_score×vw + q_score×qw + s_score×sw + i_score×iw) / total_w
```

核心设计：**单维度缺失时自动按有效维度的权重比例重新归一化**。宽基指数（broad_index）自动跳过筹码和增减持维度。

### 5.2 信号转换（滚动 Z-score 标准化）

综合得分 combined 集中在小范围，直接阈值判断易误判。信号转换改为**先平滑再滚动 Z-score 标准化**：

```
combined_smooth = combined.rolling(3).mean()
z_t = (combined_smooth,t - μ_window) / σ_window     # μ/σ 为过去 20 期滚动均值/标准差
```

| 条件 | 信号 | 含义 |
|------|:----:|------|
| `z ≥ z_buy (1.0)` | **0.5** | 综合评估显著优于常态 → 弱买 |
| `z ≤ z_sell (-1.0)` | **-0.5** | 综合评估显著劣于常态 → 弱卖 |
| 其余 | **0** | 中性 → 观望 |

增强级别（`quality_value1`）用更严格 Z 阈值：`z ≥ 2.0` 出强买（**1**）、`z ≤ -2.0` 出强卖（**-1**）。

### 5.3 信号形态特点

- **估值维度:** 日频变化，对价格波动最敏感
- **质量维度:** 季度阶梯状（ffill），"慢变量"稳压
- **筹码维度:** 半年度阶梯状（ffill），极低频但高权重变化
- **增减持维度:** 公告日阶梯状（ffill），事件驱动型低频信号

---

## 六、健壮性设计

1. **四重缓存:** 估值/财务/股东人数/增减持数据各自独立缓存
2. **单维度降级:** 任一维度缺失时，其余维度独立打分，动态调整权重比例
3. **宽基指数豁免:** broad_index 类型自动跳过筹码和增减持维度（指数无对应数据）
4. **数据不足跳过:** 估值 < 60 条 / 质量 < 4 期 → 对应维度置空
5. **API 优雅降级:** 估值主源(东方财富)失败 → 备选(百度)；各 API 失败 → 对应维度置空
6. **空数据安全:** 全部维度缺失时返回全 0 信号序列
7. **分位数除零保护:** 加权总权重为 0 时跳过该日期
8. **增减持解析健壮:** 支持 亿/万/股 单位及 减持/增持 前缀自动识别

---

## 七、与综合策略的集成

在 `stock-quant.json` 中配置 `quality_value` 权重，作为综合策略的一个子策略参与加权投票：

```json
{
    "composite": {
        "threshold": 0.25,
        "strategies": {
            "ma_cross": 0.18,
            "macd": 0.18,
            "rsi": 0.18,
            "bollinger": 0.18,
            "quality_value": 0.16,
            "momentum": 0.04,
            "volatility": 0.04,
            "breadth": 0.04
        }
    }
}
```

策略注册 key 为 `quality_value`，中文名称"质量价值融合策略"，ETF 模式复用为"ETF质量价值融合策略"（`stock_type='auto'` 自动适配）。
