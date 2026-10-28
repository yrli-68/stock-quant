# 因子设计文档 — 所有因子的计算与用途

> 基于 `core/indicators.py`（技术指标）、`core/indicators.classify_trend`（趋势状态）与
> `strategies/quality_value_factor.py`（基本面/另类因子）源码分析生成。
> 覆盖 `add_all_indicators(df)` 产出的全部技术指标列及质价融合策略的五维基本面因子。

---

## 一、因子总览

### 1.1 因子分层

| 层级 | 来源 | 频率 | 用途 |
|------|------|------|------|
| 技术指标因子 | `core/indicators.add_all_indicators` | 逐日 | 技术类策略（均线/EMA/MACD/RSI/BOLL/KDJ）与指数专属策略 |
| 趋势状态因子 | `classify_trend` | 逐日（基于 ADX/DMI/MA/斜率 综合） | 轮动/组合模式的风控与选股过滤 |
| 基本面因子（估值/质量/筹码/增减持） | `quality_value_factor` 各 `_compute_*` | 日频(估值/波动) / 低频 ffill(其余) | 质价融合策略（`quality_value`） |
| 另类因子（波动率低波异象） | `quality_value_factor._compute_volatility_score` | 逐日 | 质价融合策略第 5 维 |

### 1.2 `add_all_indicators` 输出列清单（37 列指标 + OHLCV）

| 族 | 列 |
|----|----|
| 均线 MA | MA5 MA10 MA20 MA30 MA60 MA120 |
| 指数均线 EMA | EMA10 EMA12 EMA26 EMA30 |
| MACD | MACD_DIF MACD_DEA MACD_BAR |
| RSI | RSI14 |
| 布林带 BOLL | BOLL_UPPER BOLL_MIDDLE BOLL_LOWER |
| KDJ | KDJ_K KDJ_D KDJ_J |
| SAR | SAR |
| DMI（趋势） | ADX14 PLUS_DI MINUS_DI |
| ATR | ATR14 |
| 量能 | OBV VOL_MA5 VWAP |
| 摆动 | CCI20 WR14 |
| 波动/动量 | HV20 MOM60 |

---

## 二、技术指标因子（计算与用途）

> 计算函数见 `core/indicators.py`；`add_all_indicators(df)` 一次性为 DataFrame 追加全部列。

### 2.1 均线族（MA / EMA）— 趋势跟随

**MA（简单移动平均）**
```
MA_n = close.rolling(n).mean()          # n 期收盘价算术平均
```
- `MA5/10/20/30/60/120` 分别对应短/中/长周期市场成本。
- 用途：
  - `ma_cross`：MA10 上穿/下穿 MA30 → 金叉买/死叉卖（参数化，可改 MA5/MA20 等）。
  - `volatility_timing`：跌破 MA20 为多头防线失守 → 卖出确认。
  - `classify_trend`：`MA20 > MA60 > MA120`（多头排列）/反向（空头排列）。
  - 图表主图叠加。

**EMA（指数移动平均）**
```
EMA_n: α=2/(n+1)，EMA_t = α·close_t + (1−α)·EMA_{t-1}   # ewm(span=n, adjust=False)
```
- 对近期价格赋予更高权重，响应快于同周期 MA。
- 用途：
  - `ema_cross`：EMA10 上穿/下穿 EMA30 → 金叉买/死叉卖。
  - MACD 内部即由 EMA12/EMA26 派生。
  - 图表叠加。

### 2.2 MACD（趋势动量）

```
DIF      = EMA12 − EMA26
DEA      = EMA9(DIF)
MACD柱   = 2 × (DIF − DEA)
```
- `macd_strategy`：DIF 上穿 DEA（金叉）→ 买；DIF 下穿 DEA（死叉）→ 卖。
- 柱状图为 DIF/DEA 距离的放大，直观反映动能强弱与背离（见 `calc_macd_divergence`）。

### 2.3 布林带 BOLL（波动通道 / 均值回归）

```
MIDDLE = MA20
UPPER  = MIDDLE + 2·std(close,20)
LOWER  = MIDDLE − 2·std(close,20)
```
- `bollinger_strategy`：价格跌破下轨后回升上穿下轨 → 买；突破上轨后回落 → 卖。
- `volatility_timing` 亦参考布林通道。

### 2.4 RSI（超买超卖 / 均值回归）

```
delta = close.diff()
avg_gain = max(delta,0) 的 Wilder 平滑(α=1/14)
avg_loss = |min(delta,0)| 的 Wilder 平滑(α=1/14)
RS = avg_gain/avg_loss；RSI = 100 − 100/(1+RS)
```
- `rsi_strategy`：RSI 自超卖区(<30)上穿 30 → 买；自超买区(>70)下穿 70 → 卖。

### 2.5 KDJ（随机指标 / 均值回归）

```
RSV = (close − Low9)/(High9 − Low9) × 100     # 空值填 50
K   = RSV 的 EMA(α=1/3，即 com=2)
D   = K 的 EMA(α=1/3)
J   = 3K − 2D
```
- `kdj_strategy`：K 上穿 D（金叉）→ 买；K 下穿 D（死叉）→ 卖。

### 2.6 ATR（真实波幅 / 波动尺度）

```
TR = max(H−L, |H−close_t-1|, |L−close_t-1|)
ATR14 = TR 的 ewm(span=14).mean()
```
- 衡量单日真实波动区间，天然价格量纲（可直接除价格归一化）。
- 用途：
  - `quality_value` 波动率维度子因子：`ATR14/close` 越低越好（低波异象）。
  - 与 HV20 一起构成波动类辅助因子。

### 2.7 ADX / DMI（趋势强度与方向）

```
+DM = up_move 且 > down_move 且 >0 时取 up_move，否则 0   # up_move=H.diff()
−DM = down_move 且 > up_move 且 >0 时取 down_move，否则 0 # down_move=−L.diff()
TR  = max(H−L, |H−close_t-1|, |L−close_t-1|)
ATR = TR 的 Wilder 平滑(α=1/14)
+DI = 100·smooth(+DM)/ATR
−DI = 100·smooth(−DM)/ATR
DX  = 100·|+DI−(−DI)| / (+DI + −DI)
ADX = DX 的 Wilder 平滑(α=1/14)
```
- `ADX` 衡量趋势**强度**（>25 有趋势，<25 盘整）；`+DI/−DI` 衡量趋势**方向**。
- 用途：
  - `classify_trend`：强势需 `ADX≥25 且 +DI>−DI`；弱势需 `ADX≥25 且 −DI>+DI`；`ADX<25` 判横盘。
  - 轮动/组合模式的风控与选股过滤（见 main.py 中 `classify_trend` 调用）。
  - 保留为后续趋势类策略的辅助因子。

### 2.8 OBV（能量潮 / 量价关系）

```
close 上涨日: OBV += volume
close 下跌日: OBV -= volume
close 平盘日: OBV 不变
```
- 累积量能方向，验证价格走势是否有量能配合（量价背离检测）。
- 保留为后续量能辅助因子。

### 2.9 CCI（顺势指标）

```
TP  = (H+L+C)/3
MA  = TP 的 MA20
MD  = TP 20 日窗口的 |TP−mean| 的均值
CCI = (TP − MA) / (0.015·MD)
```
- 以统计偏差衡量价格偏离典型价格的幅度，正负超 ±100 表示进入强势/弱势区。
- 与 WR 同为摆动类超买超卖辅助因子。

### 2.10 WR（威廉指标）

```
WR14 = (High14 − close) / (High14 − Low14) × (−100)
```
- 类似 RSI 的超买超卖摆动指标（取值 −100~0），数值越接近 0 越超买、越接近 −100 越超卖。

### 2.11 VOL_MA5 / VWAP（量能）

```
VOL_MA5 = volume.rolling(5).mean()
TP       = (H+L+C)/3
VWAP     = cumsum(TP·volume) / cumsum(volume)
```
- `VOL_MA5`：5 日量均线，配合成交量观察放量/缩量。
- `VWAP`：全期成交量加权均价，衡量机构/主力资金的平均成本线。

### 2.12 HV20（历史波动率）

```
HV20 = std(ln(close_t/close_t-1), 20) × √250     # 年化（按 250 个交易日/年）
```
- `volatility_timing`：`HV<15%` 低位缩量后突破 → 买；`HV>30%` 且跌破 MA20 → 卖。
- `quality_value` 波动率维度子因子（HV20 越低越好）。
- 低波动后的均值回归/突破是择时要点。

### 2.13 MOM60（动量收益率）

```
MOM60 = close / close.shift(60) − 1
```
- `momentum_tiered`（指数专属）：`MOM60>0 且 MA20>MA60` → 买；`MOM60<0` → 卖。
- 衡量 60 日累计涨幅，捕捉"强者恒强"。

---

## 三、趋势状态判定（classify_trend）

> 综合多个技术因子输出三类状态：**强势 / 弱势 / 横盘震荡**。
> 供轮动/组合模式在买入/卖出决策时过滤（例如避免在"弱势"状态买入）。

| 状态 | 条件 |
|------|------|
| 强势 | `MA20>MA60>MA120` 且 `ADX14≥25` 且 `+DI>−DI` 且 60 日价格斜率>0 且 `MACD_DIF>0` |
| 弱势 | `MA20<MA60<MA120` 且 `ADX14≥25` 且 `−DI>+DI` 且 60 日价格斜率<0 且 `MACD_DIF<0` |
| 横盘震荡 | `ADX<25` / 均线缠绕(`|MA20−MA60|/MA60<3%`) / 归一化斜率≈0 / 60 日振幅<1.3 满足任意 2 条 |

---

## 四、基本面与另类因子（quality_value 五维）

> 质价融合策略（`quality_value`）将五维分位数得分加权融合为综合得分，再做滚动 Z-score 标准化出信号。

### 4.1 五维权重

| 维度 | 权重 | 打分频率 |
|------|:---:|---------|
| 估值（价值） | 0.35 | 逐日 |
| 质量 | 0.30 | 公告日 + ffill |
| 筹码（股东人数） | 0.15 | 报告期 + ffill |
| 增减持 | 0.10 | 公告日 + ffill |
| 波动率（低波异象） | 0.10 | 逐日 |

### 4.2 估值维度（价值因子）

按标的类型选择因子及子权重：

| 类型 | 判别 | 因子 | 子权重 |
|------|------|------|:---:|
| broad_index | 宽基指数代码/ETF | pe_ttm, pb | [0.55, 0.45] |
| growth | 300/301/688 开头 | ps, pcf | [0.55, 0.45] |
| dividend | 默认 | pe_ttm, pb, ps | [0.40, 0.35, 0.25] |

数据源：`ak.stock_index_pe_lg/pb_lg`（指数，乐咕乐股）、`ak.stock_value_em`（个股，东方财富）、备用 `ak.stock_zh_valuation_baidu`。
打分：对每个估值因子，取截至当日的历史序列求当前值分位数；PE/PB/PS/PCF 反向（高=差），收益率类正向。

### 4.3 质量维度（质量因子）

| 因子 | 关键词 | 子权重 | 方向 |
|------|--------|:---:|------|
| ROE | 净资产收益率 | 0.30 | positive |
| 现金流质量 | 经营现金净流量与净利润的比率 | 0.25 | positive |
| 净利率 | 销售净利率 | 0.20 | positive |
| 资产负债率 | 资产负债率 | 0.15 | negative |
| 利润稳定性 | 净利润增长率 | 0.10 | positive |

数据源：`ak.stock_financial_analysis_indicator`。按 `announce_date` 排序求分位数，再 `ffill` 到交易日；`announce_date` 缺失的记录不参与回测打分，不使用 `report_date` 代替。

> 注意：`ak.stock_financial_analysis_indicator` 当前只返回报告期，不直接返回公告日。主数据仓库需要通过公告数据补充 `announce_date`；未补充的记录在回测中会被跳过，以避免前视偏差。

### 4.4 筹码维度（股东人数）

| 因子 | 子权重 | 打分 |
|------|:---:|------|
| 股东人数变化 | 0.6 | 增幅映射：−30%→0.9，0%→0.5，+30%→0.1 |
| 人均持股变化 | 0.4 | 增幅映射：+30%→0.9，0%→0.5，−30%→0.1 |

数据源：`ak.stock_hold_num_cninfo`（近 3 年每半年报告期）。人数减少 → 筹码集中 → 利好。

### 4.5 增减持维度（内部人交易）

- 数据源：`ak.stock_shareholder_change_ths`。
- 每个公告日计算过去 12 个月累计净增持量，sigmoid 对数映射为 0~1 得分；增持 → 利好。

### 4.6 波动率维度（低波异象）

| 子因子 | 子权重 | 打分方式 |
|------|:---:|---------|
| HV20（20 日年化波动率） | 0.40 | 分位数，越低越好 |
| 250 日最大回撤 | 0.35 | 回撤幅度映射，越低越好 |
| ATR14/close（归一化波幅） | 0.25 | 分位数，越低越好 |

数据源：行情逐日计算。低波动、低回撤标的长期收益更稳（低波异象）。

### 4.7 加权融合与信号生成（滚动 Z-score）

```
combined[i] = Σ(维度得分 × 有效维度权重) / Σ有效权重     # 缺失维度自动按比例重归一化
combined_smooth = combined.rolling(3).mean()
z_t = (combined_smooth,t − μ_20) / σ_20
```

| 条件 | 信号 |
|------|:----:|
| `z ≥ z_buy (1.0)` | 买 (1) |
| `z ≤ z_sell (−1.0)` | 卖 (-1) |
| 其余 | 0 观望 |


---

## 五、健壮性设计

1. **技术指标确定性**：`add_all_indicators` 纯函数、逐行计算，同输入必同输出（前端数据预热 90 天保证首段指标有效）。
2. **基本面四重缓存**：估值/财务/股东人数/增减持各自独立缓存，同次运行只请求一次。
3. **单维度降级**：任一基本面维度缺失时，其余维度独立打分并按有效维度动态重归一化。
4. **宽基指数豁免**：broad_index 自动跳过筹码与增减持维度（指数无对应数据）。
5. **数据不足跳过**：估值 <60 条 / 质量 <4 期 → 对应维度置空。
6. **API 优雅降级**：估值主源失败 → 备用源；各 API 失败 → 对应维度置空。
7. **空数据安全**：全部维度缺失返回全 0 信号；Z-score 标准差为 0 时取 0 并裁剪到 [−3, 3]。

---

## 六、因子与策略对照

| 策略 | 主要因子 |
|------|---------|
| `ma_cross` | MA10/MA30 交叉 |
| `sg1` | MA3/MA10 交叉（严格买入，无强信号） |
| `sg2` | MA5/MA20 交叉（严格买入，无强信号） |
| `sg3` | EMA5/EMA20 交叉（严格买入，无强信号） |
| `ema_cross` | EMA10/EMA30 交叉 |
| `macd` | DIF/DEA 金叉死叉 |
| `rsi` | RSI14 超买超卖（30/70） |
| `bollinger` | 上下轨穿越 |
| `kdj` | K/D 金叉死叉 + `close>MA60` |
| `quality_value` | 估值/质量/筹码/增减持/波动五维 |
| `hold` | 无（基准买入持有） |
| `momentum`（指数） | MOM60 + MA20/MA60 |
| `volatility`（指数） | HV20 + MA20 + BOLL |
| `breadth`（指数） | 涨跌比 + 量比 + MA5 |
| `composite` | 上述策略加权 + 仓位水平 → 平滑 → Z-score |
| 轮动/组合模式 | `classify_trend`（ADX/DMI/MA 等）用于选股与风控 |

## 七、因子组合实践
**ADX/DMI 动向指标** ：
*ADX＞25*：市场存在明显趋势（单边上涨 / 下跌），DI 交叉信号可信度高
*ADX＜20*：无趋势，震荡市，DI 交叉大量假信号，DMI 基本失效
1. 过滤其他指标信号：RSI/KDJ 金叉，但 ADX＜20，属于震荡噪音，可以降低仓位；ADX＞25，趋势行情，优先跟随趋势。
2. 识别趋势结束：趋势运行一段时间后 ADX 见顶回落，警惕行情拐点。
3. 不适合：横盘震荡，ADX 长期低位，DI 来回交叉，信号杂乱。

*先用 DMI‑ADX 定环境*：
ADX＞25：存在趋势 → 优先 CCI 突破信号，少用 WR、KDJ 抄顶摸底；
ADX＜20：震荡环境 → 可用 WR、KDJ、CCI 做高抛低吸，OBV 验证量能。
   
**WR 威廉指标**：
和 RSI/KDJ 逻辑相似，但是 WR 反应极度灵敏，毛刺多，反复来回穿越区间。
WR 进入‑80~‑100 超卖区后向上拐头：反弹机会
WR 进入0~‑20 超买区后向下拐头：回落风险
震荡市场，短线择时，只做辅助过滤，极少单独作为主信号，常`配合 BOLL、MACD` 一起用

**OBV 能量潮** 
*顶背离*：价格创新高，OBV 不创新高 → 股价在涨，但成交量资金跟不上，上涨动能不足，警惕见顶。
*底背离*：价格创新低，OBV 不再创新低 → 股价下跌，但资金悄悄在承接，有见底可能性。
1. 验证趋势真假：价格涨，OBV 不跟，上涨虚；价格跌，OBV 不创新低，抛压衰竭。
2. 过滤假突破：K 线向上突破，OBV 没有同步向上突破，大概率假突破。
3. 适合个股，对权重大盘股效果更好；对高度控盘庄股容易失真。

*入场共振*：
震荡市：BOLL 下轨 + CCI＜‑100 + WR 进入‑80~‑100 超卖 + OBV 底背离，反弹胜率提升。
趋势市：DMI +DI 上穿‑DI + ADX＞25 + CCI 上穿 + 100 + OBV 同步创新高，顺势做多。