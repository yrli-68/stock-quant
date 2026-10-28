-- ============================================================================
-- 历史回填：market_breadth（universe_code='ALL_A'）全部栏目
-- 目标库：stock_quant (MySQL 8.0+，依赖 CTE / 窗口函数)
--
-- 性能提示：
--   纯 SQL 版本使用 20/60/120/250 日滚动窗口，MySQL 执行较慢（约数分钟）。
--   推荐使用等价的 pandas 实现（秒级完成）：
--       python3 test/backfill_market_breadth.py --apply
--
-- 数据来源：daily_kline（asset_type='stock' AND adjust_type='raw'）
-- 应用方式：
--   mysql -u smit -p stock_quant < data/migrations/20261028_backfill_market_breadth.sql
--
-- 计算口径：
--   change_pct        = (今收 - 昨收) / 昨收 * 100（按标的 LAG 计算）
--   advance/decline   = change_pct > 0 / < 0
--   flat              = change_pct = 0
--   limit_up/down     = 按板块近似阈值：30/68 用 ±19.8，8/4 用 ±29.8，其余 ±9.8
--                       （未区分 ST 的 ±5%，为近似值）
--   new_high/low_N    = 收盘价创 N 个交易日新高/新低（要求至少 N 条历史）
--   up/down_volume    = 上涨/下跌标的成交量之和
--   total_volume      = 全部成交量之和
--   total_amount      = SUM(amount)，缺失时用 volume*close 估算
--   avg/median_change = 涨跌幅均值 / 中位数
--   up/down_ratio     = 上涨/下跌家数 ÷ 总家数
--   adr               = 上涨家数 ÷ 下跌家数
--   obos              = 上涨家数 - 下跌家数
--   adl               = 从最早交易日起累计 obos
--   adr_ma10/adl_ma20/obos_ma10 = 对应序列的 N 日移动平均
--
-- 注意：
--   - daily_kline 为原始价，除权除息日前后 change_pct / 新高低会有跳空失真。
--   - 结果按 (universe_code, trade_date) UPSERT，可重复执行。
-- ============================================================================

USE stock_quant;

INSERT INTO market_breadth (
    universe_code, trade_date,
    advance_count, decline_count, flat_count, total_count,
    limit_up_count, limit_down_count,
    new_high_20, new_low_20, new_high_60, new_low_60,
    new_high_120, new_low_120, new_high_250, new_low_250,
    up_volume, down_volume, total_volume, total_amount,
    avg_change_pct, median_change_pct,
    up_ratio, down_ratio, adr, adl, obos,
    adr_ma10, adl_ma20, obos_ma10, source
)
WITH
base AS (
    SELECT
        code, exchange, asset_type, trade_date, close, volume, amount,
        LAG(close) OVER (
            PARTITION BY code, exchange, asset_type ORDER BY trade_date
        ) AS prev_close
    FROM daily_kline
    WHERE asset_type = 'stock' AND adjust_type = 'raw'
),
r AS (
    SELECT
        code, exchange, asset_type, trade_date, close, volume, amount,
        CASE
            WHEN prev_close IS NULL OR prev_close = 0 THEN NULL
            ELSE (close - prev_close) / prev_close * 100
        END AS chg_pct
    FROM base
),
agg AS (
    SELECT
        trade_date,
        SUM(CASE WHEN chg_pct > 0 THEN 1 ELSE 0 END) AS advance_count,
        SUM(CASE WHEN chg_pct < 0 THEN 1 ELSE 0 END) AS decline_count,
        SUM(CASE WHEN chg_pct = 0 THEN 1 ELSE 0 END) AS flat_count,
        COUNT(DISTINCT code, exchange, asset_type) AS total_count,
        SUM(CASE
            WHEN code LIKE '30%' OR code LIKE '68%' THEN chg_pct >= 19.8
            WHEN code LIKE '8%'  OR code LIKE '4%'  THEN chg_pct >= 29.8
            ELSE chg_pct >= 9.8
        END) AS limit_up_count,
        SUM(CASE
            WHEN code LIKE '30%' OR code LIKE '68%' THEN chg_pct <= -19.8
            WHEN code LIKE '8%'  OR code LIKE '4%'  THEN chg_pct <= -29.8
            ELSE chg_pct <= -9.8
        END) AS limit_down_count,
        SUM(CASE WHEN chg_pct > 0 THEN volume ELSE 0 END) AS up_volume,
        SUM(CASE WHEN chg_pct < 0 THEN volume ELSE 0 END) AS down_volume,
        SUM(volume) AS total_volume,
        SUM(COALESCE(amount, volume * close)) AS total_amount,
        AVG(chg_pct) AS avg_change_pct
    FROM r
    GROUP BY trade_date
),
median AS (
    SELECT trade_date, AVG(chg_pct) AS median_change_pct
    FROM (
        SELECT
            trade_date, chg_pct,
            ROW_NUMBER() OVER (
                PARTITION BY trade_date ORDER BY chg_pct
            ) AS rn,
            COUNT(*) OVER (PARTITION BY trade_date) AS cnt
        FROM r
        WHERE chg_pct IS NOT NULL
    ) x
    WHERE rn IN (FLOOR((cnt + 1) / 2), CEIL((cnt + 1) / 2))
    GROUP BY trade_date
),
highlow AS (
    SELECT
        trade_date,
        SUM(CASE WHEN rn >= 20  AND close >= mx20  THEN 1 ELSE 0 END) AS new_high_20,
        SUM(CASE WHEN rn >= 20  AND close <= mn20  THEN 1 ELSE 0 END) AS new_low_20,
        SUM(CASE WHEN rn >= 60  AND close >= mx60  THEN 1 ELSE 0 END) AS new_high_60,
        SUM(CASE WHEN rn >= 60  AND close <= mn60  THEN 1 ELSE 0 END) AS new_low_60,
        SUM(CASE WHEN rn >= 120 AND close >= mx120 THEN 1 ELSE 0 END) AS new_high_120,
        SUM(CASE WHEN rn >= 120 AND close <= mn120 THEN 1 ELSE 0 END) AS new_low_120,
        SUM(CASE WHEN rn >= 250 AND close >= mx250 THEN 1 ELSE 0 END) AS new_high_250,
        SUM(CASE WHEN rn >= 250 AND close <= mn250 THEN 1 ELSE 0 END) AS new_low_250
    FROM (
        SELECT
            code, exchange, asset_type, trade_date, close,
            ROW_NUMBER() OVER (
                PARTITION BY code, exchange, asset_type ORDER BY trade_date
            ) AS rn,
            MAX(close) OVER (
                PARTITION BY code, exchange, asset_type ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ) AS mx20,
            MIN(close) OVER (
                PARTITION BY code, exchange, asset_type ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ) AS mn20,
            MAX(close) OVER (
                PARTITION BY code, exchange, asset_type ORDER BY trade_date
                ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
            ) AS mx60,
            MIN(close) OVER (
                PARTITION BY code, exchange, asset_type ORDER BY trade_date
                ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
            ) AS mn60,
            MAX(close) OVER (
                PARTITION BY code, exchange, asset_type ORDER BY trade_date
                ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
            ) AS mx120,
            MIN(close) OVER (
                PARTITION BY code, exchange, asset_type ORDER BY trade_date
                ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
            ) AS mn120,
            MAX(close) OVER (
                PARTITION BY code, exchange, asset_type ORDER BY trade_date
                ROWS BETWEEN 249 PRECEDING AND CURRENT ROW
            ) AS mx250,
            MIN(close) OVER (
                PARTITION BY code, exchange, asset_type ORDER BY trade_date
                ROWS BETWEEN 249 PRECEDING AND CURRENT ROW
            ) AS mn250
        FROM daily_kline
        WHERE asset_type = 'stock' AND adjust_type = 'raw'
    ) w
    GROUP BY trade_date
),
daily AS (
    SELECT
        a.trade_date,
        a.advance_count, a.decline_count, a.flat_count, a.total_count,
        a.limit_up_count, a.limit_down_count,
        h.new_high_20, h.new_low_20, h.new_high_60, h.new_low_60,
        h.new_high_120, h.new_low_120, h.new_high_250, h.new_low_250,
        a.up_volume, a.down_volume, a.total_volume, a.total_amount,
        a.avg_change_pct, m.median_change_pct,
        a.advance_count / NULLIF(a.total_count, 0) AS up_ratio,
        a.decline_count / NULLIF(a.total_count, 0) AS down_ratio,
        a.advance_count / NULLIF(a.decline_count, 0) AS adr,
        a.advance_count - a.decline_count AS obos
    FROM agg a
    LEFT JOIN median m ON m.trade_date = a.trade_date
    LEFT JOIN highlow h ON h.trade_date = a.trade_date
),
with_adl AS (
    SELECT d.*, SUM(d.obos) OVER (ORDER BY d.trade_date) AS adl
    FROM daily d
),
final AS (
    SELECT
        w.*,
        AVG(w.adr) OVER (
            ORDER BY w.trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
        ) AS adr_ma10,
        AVG(w.adl) OVER (
            ORDER BY w.trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS adl_ma20,
        AVG(w.obos) OVER (
            ORDER BY w.trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
        ) AS obos_ma10
    FROM with_adl w
)
SELECT
    'ALL_A', trade_date,
    advance_count, decline_count, flat_count, total_count,
    limit_up_count, limit_down_count,
    new_high_20, new_low_20, new_high_60, new_low_60,
    new_high_120, new_low_120, new_high_250, new_low_250,
    up_volume, down_volume, total_volume, total_amount,
    avg_change_pct, median_change_pct,
    up_ratio, down_ratio, adr, adl, obos,
    adr_ma10, adl_ma20, obos_ma10,
    'backfill'
FROM final
ON DUPLICATE KEY UPDATE
    advance_count = VALUES(advance_count),
    decline_count = VALUES(decline_count),
    flat_count = VALUES(flat_count),
    total_count = VALUES(total_count),
    limit_up_count = VALUES(limit_up_count),
    limit_down_count = VALUES(limit_down_count),
    new_high_20 = VALUES(new_high_20),
    new_low_20 = VALUES(new_low_20),
    new_high_60 = VALUES(new_high_60),
    new_low_60 = VALUES(new_low_60),
    new_high_120 = VALUES(new_high_120),
    new_low_120 = VALUES(new_low_120),
    new_high_250 = VALUES(new_high_250),
    new_low_250 = VALUES(new_low_250),
    up_volume = VALUES(up_volume),
    down_volume = VALUES(down_volume),
    total_volume = VALUES(total_volume),
    total_amount = VALUES(total_amount),
    avg_change_pct = VALUES(avg_change_pct),
    median_change_pct = VALUES(median_change_pct),
    up_ratio = VALUES(up_ratio),
    down_ratio = VALUES(down_ratio),
    adr = VALUES(adr),
    adl = VALUES(adl),
    obos = VALUES(obos),
    adr_ma10 = VALUES(adr_ma10),
    adl_ma20 = VALUES(adl_ma20),
    obos_ma10 = VALUES(obos_ma10),
    source = VALUES(source);
