-- ============================================================================
-- 迁移：新增市场广度表与宽基指数日线指标表
-- 目标库：stock_quant (MySQL 8.0+)
-- 执行前请备份数据库。
-- ============================================================================

USE stock_quant;

-- 11. 市场广度 (按统计口径 + 交易日)
--     ADL: 以所选基期为 0，逐日累计(上涨家数 - 下跌家数)
--     ADR: 上涨家数 / 下跌家数
--     OBOS: 上涨家数 - 下跌家数
CREATE TABLE IF NOT EXISTS market_breadth (
    universe_code      VARCHAR(32) NOT NULL COMMENT '统计口径: ALL_A/SH_A/SZ_A/BJ_A/GEM/STAR/HS300...',
    trade_date         DATE NOT NULL,
    advance_count      INT UNSIGNED NOT NULL DEFAULT 0,
    decline_count      INT UNSIGNED NOT NULL DEFAULT 0,
    flat_count         INT UNSIGNED NOT NULL DEFAULT 0,
    total_count        INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '纳入统计的资产数',
    limit_up_count     INT UNSIGNED NOT NULL DEFAULT 0,
    limit_down_count   INT UNSIGNED NOT NULL DEFAULT 0,
    new_high_20        INT UNSIGNED NOT NULL DEFAULT 0,
    new_low_20         INT UNSIGNED NOT NULL DEFAULT 0,
    new_high_60        INT UNSIGNED NOT NULL DEFAULT 0,
    new_low_60         INT UNSIGNED NOT NULL DEFAULT 0,
    new_high_120       INT UNSIGNED NOT NULL DEFAULT 0,
    new_low_120        INT UNSIGNED NOT NULL DEFAULT 0,
    new_high_250       INT UNSIGNED NOT NULL DEFAULT 0,
    new_low_250        INT UNSIGNED NOT NULL DEFAULT 0,
    up_volume          DOUBLE,
    down_volume        DOUBLE,
    total_volume       DOUBLE,
    total_amount       DOUBLE,
    avg_change_pct     DOUBLE,
    median_change_pct  DOUBLE,
    up_ratio           DOUBLE COMMENT '上涨家数/总家数',
    down_ratio         DOUBLE COMMENT '下跌家数/总家数',
    adr                DOUBLE COMMENT 'ADR=上涨家数/下跌家数',
    adl                BIGINT COMMENT 'ADL=基期起累计(上涨家数-下跌家数)',
    obos               INT COMMENT 'OBOS=上涨家数-下跌家数',
    adr_ma10           DOUBLE,
    adl_ma20           DOUBLE,
    obos_ma10          DOUBLE,
    source             VARCHAR(30),
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (universe_code, trade_date),
    INDEX idx_breadth_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 12. 宽基指数日线与技术指标
--     asset_type 固定为 index；指数估值仍存放在 valuation_history。
CREATE TABLE IF NOT EXISTS broad_index_daily (
    code           VARCHAR(10) NOT NULL,
    exchange       VARCHAR(8) NOT NULL DEFAULT '',
    asset_type     ENUM('stock', 'index', 'etf', 'fund', 'bond')
                   NOT NULL DEFAULT 'index',
    trade_date     DATE NOT NULL,
    open           DOUBLE NOT NULL,
    high           DOUBLE NOT NULL,
    low            DOUBLE NOT NULL,
    close          DOUBLE NOT NULL,
    pre_close      DOUBLE,
    change_val     DOUBLE,
    change_pct     DOUBLE,
    amplitude      DOUBLE,
    volume         DOUBLE,
    amount         DOUBLE,
    turnover_rate  DOUBLE,
    ma5            DOUBLE,
    ma10           DOUBLE,
    ma20           DOUBLE,
    ma30           DOUBLE,
    ma60           DOUBLE,
    ma120          DOUBLE,
    ema10          DOUBLE,
    ema12          DOUBLE,
    ema26          DOUBLE,
    ema30          DOUBLE,
    macd_dif       DOUBLE,
    macd_dea       DOUBLE,
    macd_bar       DOUBLE,
    rsi14          DOUBLE,
    boll_upper     DOUBLE,
    boll_middle    DOUBLE,
    boll_lower     DOUBLE,
    kdj_k          DOUBLE,
    kdj_d          DOUBLE,
    kdj_j          DOUBLE,
    adx14          DOUBLE,
    plus_di        DOUBLE,
    minus_di       DOUBLE,
    atr14          DOUBLE,
    obv            DOUBLE,
    cci20          DOUBLE,
    wr14           DOUBLE,
    vol_ma5        DOUBLE,
    vwap           DOUBLE,
    hv20           DOUBLE,
    mom60          DOUBLE,
    sar            DOUBLE,
    source         VARCHAR(30),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (code, exchange, asset_type, trade_date),
    CONSTRAINT chk_broad_index_asset_type CHECK (asset_type = 'index'),
    INDEX idx_broad_index_trade_date (trade_date, exchange)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
