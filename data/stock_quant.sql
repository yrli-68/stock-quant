-- ============================================================================
-- stock_quant 数据库 DDL (MySQL 8.0+)
-- 定位：量化主数据仓库
-- 资产维度：code + exchange + asset_type
--   asset_type: stock / index / etf / fund / bond
-- ============================================================================

CREATE DATABASE IF NOT EXISTS stock_quant
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE stock_quant;

-- 1. 资产基本信息（股票/指数/ETF/基金/债券统一主表）
CREATE TABLE stocks (
    code         VARCHAR(10) NOT NULL,
    exchange     VARCHAR(8) NOT NULL DEFAULT '',
    asset_type   ENUM('stock', 'index', 'etf', 'fund', 'bond')
                 NOT NULL DEFAULT 'stock',
    name         VARCHAR(50) NOT NULL,
    market       VARCHAR(4),
    stock_type   VARCHAR(20) DEFAULT 'dividend',
    is_etf       TINYINT DEFAULT 0,
    listed_date  DATE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (code, exchange, asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. 日K线 (OHLCV)
CREATE TABLE daily_kline (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    code         VARCHAR(10) NOT NULL,
    exchange     VARCHAR(8) NOT NULL DEFAULT '',
    asset_type   ENUM('stock', 'index', 'etf', 'fund', 'bond')
                 NOT NULL DEFAULT 'stock',
    trade_date   DATE NOT NULL,
    open         DOUBLE NOT NULL,
    high         DOUBLE NOT NULL,
    low          DOUBLE NOT NULL,
    close        DOUBLE NOT NULL,
    volume       DOUBLE NOT NULL,
    amount       DOUBLE,
    amplitude    DOUBLE,
    change_pct   DOUBLE,
    change_val   DOUBLE,
    turnover_rate DOUBLE,
    adjust_type  VARCHAR(10) NOT NULL DEFAULT 'raw',
    is_realtime  TINYINT DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_kline (code, exchange, asset_type, trade_date, adjust_type),
    INDEX idx_kline_trade_date (trade_date, exchange, asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. 除权除息事件 (原始事件，可分推导任意复权类型)
CREATE TABLE adjust_events (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    code            VARCHAR(10) NOT NULL,
    exchange        VARCHAR(8) NOT NULL DEFAULT '',
    asset_type      ENUM('stock', 'index', 'etf', 'fund', 'bond')
                    NOT NULL DEFAULT 'stock',
    ex_date         DATE NOT NULL,              -- 除权除息日
    cash_per_share  DOUBLE DEFAULT 0,           -- 每股现金分红 (元)
    stock_per_share DOUBLE DEFAULT 0,           -- 每股送转股数
    plan_desc       VARCHAR(100),               -- 分红方案描述 (如 "10派10.03元")
    record_date     DATE,                       -- 股权登记日
    cash_date       DATE,                       -- 现金红利发放日
    announce_date   DATE,                       -- 公告日期
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_ex_div (code, exchange, asset_type, ex_date),
    INDEX idx_ex_div_date (ex_date, asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. 技术指标 (每日；指标依赖复权价格，必须带 adjust_type)
CREATE TABLE daily_indicators (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    code           VARCHAR(10) NOT NULL,
    exchange       VARCHAR(8) NOT NULL DEFAULT '',
    asset_type     ENUM('stock', 'index', 'etf', 'fund', 'bond')
                   NOT NULL DEFAULT 'stock',
    trade_date     DATE NOT NULL,
    adjust_type    VARCHAR(10) NOT NULL DEFAULT 'qfq',
    -- 均线
    ma5            DOUBLE, ma10           DOUBLE, ma20           DOUBLE, ma30           DOUBLE,
    ma60           DOUBLE,
    -- EMA
    ema10          DOUBLE, ema12          DOUBLE, ema26          DOUBLE, ema30          DOUBLE,
    -- MACD
    macd_dif       DOUBLE, macd_dea       DOUBLE, macd_bar       DOUBLE,
    -- RSI
    rsi14          DOUBLE,
    -- 布林带
    boll_upper     DOUBLE, boll_middle    DOUBLE, boll_lower     DOUBLE,
    -- KDJ
    kdj_k          DOUBLE, kdj_d          DOUBLE, kdj_j          DOUBLE,
    -- DMI（趋势辅助因子）
    adx14          DOUBLE, plus_di        DOUBLE, minus_di       DOUBLE,
    -- 其他
    atr14          DOUBLE, obv            DOUBLE, cci20          DOUBLE,
    wr14           DOUBLE, vol_ma5        DOUBLE, vwap           DOUBLE,
    -- 扩展
    hv20           DOUBLE,
    mom60          DOUBLE,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_indicator
        (code, exchange, asset_type, trade_date, adjust_type),
    INDEX idx_indicators_trade_date (trade_date, exchange, asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. 估值历史 (PE/PB/PS/PCF)
CREATE TABLE valuation_history (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    code           VARCHAR(10) NOT NULL,
    exchange       VARCHAR(8) NOT NULL DEFAULT '',
    asset_type     ENUM('stock', 'index', 'etf', 'fund', 'bond')
                   NOT NULL DEFAULT 'stock',
    val_date       DATE NOT NULL,
    pe_ttm         DOUBLE,
    pb             DOUBLE,
    ps             DOUBLE,
    pcf            DOUBLE,
    ey             DOUBLE,
    dividend_yield DOUBLE,
    source         VARCHAR(30),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_valuation (code, exchange, asset_type, val_date),
    INDEX idx_valuation_val_date (val_date, asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6. 财务报告 (报告期 + 公告日 + 修订版本)
--    回测取数必须按 announce_date <= 当前交易日，禁止按 report_date 直接取数。
CREATE TABLE financial_reports (
    id                     INT AUTO_INCREMENT PRIMARY KEY,
    code                   VARCHAR(10) NOT NULL,
    exchange               VARCHAR(8) NOT NULL DEFAULT '',
    asset_type             ENUM('stock', 'index', 'etf', 'fund', 'bond')
                           NOT NULL DEFAULT 'stock',
    report_date            DATE NOT NULL,          -- 报告期
    announce_date          DATE,                   -- 公告日；为空表示尚不可用于回测
    data_version           INT NOT NULL DEFAULT 1, -- 修订版本
    roe                    DOUBLE,
    cashflow_net_profit_ratio DOUBLE,
    net_margin             DOUBLE,
    debt_ratio             DOUBLE,
    profit_growth          DOUBLE,
    gross_margin           DOUBLE,
    roa                    DOUBLE,
    revenue_growth         DOUBLE,
    eps                    DOUBLE,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_financial
        (code, exchange, asset_type, report_date, data_version),
    INDEX idx_financials_announce
        (code, exchange, asset_type, announce_date, report_date),
    INDEX idx_financials_report_date (report_date, asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 7. 股东人数变化 (筹码集中度)
CREATE TABLE shareholder_history (
    id                     INT AUTO_INCREMENT PRIMARY KEY,
    code                   VARCHAR(10) NOT NULL,
    exchange               VARCHAR(8) NOT NULL DEFAULT '',
    asset_type             ENUM('stock', 'index', 'etf', 'fund', 'bond')
                           NOT NULL DEFAULT 'stock',
    change_date            DATE NOT NULL,
    holder_count           DOUBLE,
    prev_holder_count      DOUBLE,
    holder_change_pct      DOUBLE,
    avg_holding            DOUBLE,
    prev_avg_holding       DOUBLE,
    avg_holding_change_pct DOUBLE,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_shareholder (code, exchange, asset_type, change_date),
    INDEX idx_shareholder_change_date (change_date, asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 8. 大股东/高管增减持记录
CREATE TABLE insider_trades (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    code           VARCHAR(10) NOT NULL,
    exchange       VARCHAR(8) NOT NULL DEFAULT '',
    asset_type     ENUM('stock', 'index', 'etf', 'fund', 'bond')
                   NOT NULL DEFAULT 'stock',
    announce_date  DATE NOT NULL,
    shareholder    VARCHAR(100),
    change_amount  DOUBLE,
    change_text    VARCHAR(50),
    trade_price    DOUBLE,
    remaining      DOUBLE,
    trade_period   VARCHAR(50),
    trade_method   VARCHAR(50),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_insider
        (code, exchange, asset_type, announce_date, shareholder, change_amount),
    INDEX idx_insider_announce_date (announce_date, asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 9. 策略信号
CREATE TABLE strategy_signals (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    code           VARCHAR(10) NOT NULL,
    exchange       VARCHAR(8) NOT NULL DEFAULT '',
    asset_type     ENUM('stock', 'index', 'etf', 'fund', 'bond')
                   NOT NULL DEFAULT 'stock',
    trade_date     DATE NOT NULL,
    strategy_key   VARCHAR(30) NOT NULL,
    `signal`       TINYINT NOT NULL,
    score          DOUBLE,
    params_hash    VARCHAR(64) NOT NULL DEFAULT '',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_signal
        (code, exchange, asset_type, trade_date, strategy_key, params_hash),
    INDEX idx_signals_strategy_date (strategy_key, trade_date, `signal`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 10. 回测结果 (单次分析汇总)
CREATE TABLE backtest_results (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    code            VARCHAR(10) NOT NULL,
    exchange        VARCHAR(8) NOT NULL DEFAULT '',
    asset_type      ENUM('stock', 'index', 'etf', 'fund', 'bond')
                    NOT NULL DEFAULT 'stock',
    strategy_key    VARCHAR(30) NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    capital         DOUBLE NOT NULL DEFAULT 100000,
    -- 绩效指标
    total_return    DOUBLE,
    annual_return   DOUBLE,
    max_drawdown    DOUBLE,
    sharpe_ratio    DOUBLE,
    win_rate        DOUBLE,
    total_trades    INT,
    profit_trades   INT,
    loss_trades     INT,
    profit_factor   DOUBLE,
    annual_volatility DOUBLE,
    calmar_ratio    DOUBLE,
    sortino_ratio   DOUBLE,
    var_95          DOUBLE,
    cvar_95         DOUBLE,
    avg_profit      DOUBLE,
    avg_loss        DOUBLE,
    avg_holding_days DOUBLE,
    params_hash     VARCHAR(64) NOT NULL DEFAULT '',
    run_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_backtest
        (code, exchange, asset_type, strategy_key, start_date, end_date, params_hash),
    INDEX idx_backtest_strategy_date (strategy_key, start_date, end_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 11. 市场广度 (按统计口径 + 交易日)
--     ADL: 以所选基期为 0，逐日累计(上涨家数 - 下跌家数)
--     ADR: 上涨家数 / 下跌家数
--     OBOS: 上涨家数 - 下跌家数
CREATE TABLE market_breadth (
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
CREATE TABLE broad_index_daily (
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

-- ============================================================================
-- 视图: 合并 K线 + 指标
-- ============================================================================
CREATE OR REPLACE VIEW v_daily_full AS
SELECT
    k.code, k.exchange, k.asset_type, k.trade_date, k.adjust_type,
    k.open, k.high, k.low, k.close, k.volume, k.amount,
    k.amplitude, k.change_pct, k.change_val, k.turnover_rate,
    i.ma5, i.ma10, i.ma20, i.ma30, i.ma60,
    i.ema10, i.ema12, i.ema26, i.ema30,
    i.macd_dif, i.macd_dea, i.macd_bar,
    i.rsi14,
    i.boll_upper, i.boll_middle, i.boll_lower,
    i.kdj_k, i.kdj_d, i.kdj_j,
    i.adx14, i.plus_di, i.minus_di,
    i.atr14, i.obv, i.cci20, i.wr14, i.vol_ma5, i.vwap,
    i.hv20, i.mom60
FROM daily_kline k
LEFT JOIN daily_indicators i
    ON  k.code = i.code
    AND k.exchange = i.exchange
    AND k.asset_type = i.asset_type
    AND k.trade_date = i.trade_date
    AND k.adjust_type = i.adjust_type;

-- ============================================================================
-- 视图: 各资产最新信号
-- ============================================================================
CREATE OR REPLACE VIEW v_latest_signals AS
SELECT
    s.code, s.exchange, s.asset_type, s.strategy_key, s.params_hash,
    s.`signal`, s.score, s.trade_date
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

-- ============================================================================
-- 触发器增强: daily_kline 数据变更时自动更新 market_breadth 全部栏目
--   触发器仅入队脏日期；事件每 30 秒调用存储过程批量重算。
-- ============================================================================

-- 1. 脏日期队列
CREATE TABLE IF NOT EXISTS market_breadth_dirty (
    universe_code VARCHAR(32) NOT NULL DEFAULT 'ALL_A',
    trade_date DATE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (universe_code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. 清理旧触发器
DROP TRIGGER IF EXISTS trg_daily_kline_ai_breadth;
DROP TRIGGER IF EXISTS trg_daily_kline_ai_breadth_full;
DROP TRIGGER IF EXISTS trg_daily_kline_au_breadth_full;
DROP TRIGGER IF EXISTS trg_daily_kline_ad_breadth_full;

-- 3. 入队触发器
DELIMITER $$

CREATE TRIGGER trg_daily_kline_ai_breadth_full
AFTER INSERT ON daily_kline
FOR EACH ROW
BEGIN
    INSERT IGNORE INTO market_breadth_dirty (universe_code, trade_date)
    VALUES ('ALL_A', NEW.trade_date);
END$$

CREATE TRIGGER trg_daily_kline_au_breadth_full
AFTER UPDATE ON daily_kline
FOR EACH ROW
BEGIN
    INSERT IGNORE INTO market_breadth_dirty (universe_code, trade_date)
    VALUES ('ALL_A', OLD.trade_date);
    IF NEW.trade_date <> OLD.trade_date THEN
        INSERT IGNORE INTO market_breadth_dirty (universe_code, trade_date)
        VALUES ('ALL_A', NEW.trade_date);
    END IF;
END$$

CREATE TRIGGER trg_daily_kline_ad_breadth_full
AFTER DELETE ON daily_kline
FOR EACH ROW
BEGIN
    INSERT IGNORE INTO market_breadth_dirty (universe_code, trade_date)
    VALUES ('ALL_A', OLD.trade_date);
END$$

DELIMITER ;

-- 4. 单日全栏目重算存储过程
DROP PROCEDURE IF EXISTS recompute_market_breadth;
DELIMITER $$

CREATE PROCEDURE recompute_market_breadth(IN p_date DATE, IN p_universe VARCHAR(32))
BEGIN

    DECLARE v_total INT DEFAULT 0;
    DECLARE v_adv INT DEFAULT 0;
    DECLARE v_dec INT DEFAULT 0;
    DECLARE v_flat INT DEFAULT 0;
    DECLARE v_limit_up INT DEFAULT 0;
    DECLARE v_limit_down INT DEFAULT 0;
    DECLARE v_up_vol DOUBLE DEFAULT 0;
    DECLARE v_down_vol DOUBLE DEFAULT 0;
    DECLARE v_total_vol DOUBLE DEFAULT 0;
    DECLARE v_total_amt DOUBLE DEFAULT 0;
    DECLARE v_avg DOUBLE DEFAULT NULL;
    DECLARE v_median DOUBLE DEFAULT NULL;
    DECLARE v_nh20 INT DEFAULT 0;
    DECLARE v_nl20 INT DEFAULT 0;
    DECLARE v_nh60 INT DEFAULT 0;
    DECLARE v_nl60 INT DEFAULT 0;
    DECLARE v_nh120 INT DEFAULT 0;
    DECLARE v_nl120 INT DEFAULT 0;
    DECLARE v_nh250 INT DEFAULT 0;
    DECLARE v_nl250 INT DEFAULT 0;
    DECLARE v_up_ratio DOUBLE DEFAULT NULL;
    DECLARE v_down_ratio DOUBLE DEFAULT NULL;
    DECLARE v_adr DOUBLE DEFAULT NULL;
    DECLARE v_obos INT DEFAULT 0;
    DECLARE v_prev_adl BIGINT DEFAULT NULL;
    DECLARE v_adl BIGINT DEFAULT 0;
    DECLARE v_adr_ma10 DOUBLE DEFAULT NULL;
    DECLARE v_adl_ma20 DOUBLE DEFAULT NULL;
    DECLARE v_obos_ma10 DOUBLE DEFAULT NULL;


    DROP TEMPORARY TABLE IF EXISTS tmp_bd;
    CREATE TEMPORARY TABLE tmp_bd (
        code VARCHAR(10) NOT NULL,
        exchange VARCHAR(8) NOT NULL,
        asset_type VARCHAR(10) NOT NULL,
        close DOUBLE NOT NULL,
        volume DOUBLE NOT NULL,
        amount DOUBLE,
        chg_pct DOUBLE,
        hist_count INT NOT NULL DEFAULT 0,
        KEY idx_tmp_bd (code, exchange, asset_type)
    ) ENGINE=InnoDB;

    INSERT INTO tmp_bd
    SELECT d.code, d.exchange, d.asset_type, d.close, d.volume,
           COALESCE(d.amount, d.volume * d.close),
           CASE WHEN p.close IS NULL OR p.close = 0 THEN NULL
                ELSE (d.close - p.close) / p.close * 100 END,
           (SELECT COUNT(*) FROM daily_kline w
             WHERE w.code=d.code AND w.exchange=d.exchange
               AND w.asset_type=d.asset_type AND w.adjust_type='raw'
               AND w.trade_date <= p_date)
    FROM daily_kline d
    LEFT JOIN daily_kline p
      ON p.code=d.code AND p.exchange=d.exchange
     AND p.asset_type=d.asset_type AND p.adjust_type='raw'
     AND p.trade_date = (
         SELECT MAX(x.trade_date) FROM daily_kline x
         WHERE x.code=d.code AND x.exchange=d.exchange
           AND x.asset_type=d.asset_type AND x.adjust_type='raw'
           AND x.trade_date < d.trade_date)
    WHERE d.trade_date = p_date
      AND d.asset_type='stock' AND d.adjust_type='raw';

    SELECT COUNT(*),
           COALESCE(SUM(CASE WHEN chg_pct > 0 THEN 1 ELSE 0 END),0),
           COALESCE(SUM(CASE WHEN chg_pct < 0 THEN 1 ELSE 0 END),0),
           COALESCE(SUM(CASE WHEN chg_pct = 0 THEN 1 ELSE 0 END),0),
           COALESCE(SUM(CASE
               WHEN code LIKE '30%' OR code LIKE '68%' THEN chg_pct >= 19.8
               WHEN code LIKE '8%' OR code LIKE '4%' THEN chg_pct >= 29.8
               ELSE chg_pct >= 9.8 END),0),
           COALESCE(SUM(CASE
               WHEN code LIKE '30%' OR code LIKE '68%' THEN chg_pct <= -19.8
               WHEN code LIKE '8%' OR code LIKE '4%' THEN chg_pct <= -29.8
               ELSE chg_pct <= -9.8 END),0),
           COALESCE(SUM(CASE WHEN chg_pct > 0 THEN volume ELSE 0 END),0),
           COALESCE(SUM(CASE WHEN chg_pct < 0 THEN volume ELSE 0 END),0),
           COALESCE(SUM(volume),0),
           COALESCE(SUM(amount),0),
           AVG(chg_pct)
      INTO v_total, v_adv, v_dec, v_flat, v_limit_up, v_limit_down,
           v_up_vol, v_down_vol, v_total_vol, v_total_amt, v_avg
      FROM tmp_bd;

    IF v_total = 0 THEN
        DELETE FROM market_breadth
         WHERE universe_code=p_universe AND trade_date=p_date;
        DROP TEMPORARY TABLE IF EXISTS tmp_bd;
    ELSE
        SELECT AVG(chg_pct) INTO v_median
        FROM (
            SELECT chg_pct,
                   ROW_NUMBER() OVER (ORDER BY chg_pct) AS rn,
                   COUNT(*) OVER () AS cnt
            FROM tmp_bd WHERE chg_pct IS NOT NULL
        ) x
        WHERE rn IN (FLOOR((cnt+1)/2), CEIL((cnt+1)/2));

        SELECT
        COALESCE(SUM(CASE WHEN t.hist_count >= 20 AND t.close >= (SELECT MAX(z.close) FROM (
                SELECT close FROM daily_kline y
                WHERE y.code=t.code AND y.exchange=t.exchange
                  AND y.asset_type=t.asset_type AND y.adjust_type='raw'
                  AND y.trade_date <= p_date
                ORDER BY y.trade_date DESC LIMIT 20) z) THEN 1 ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN t.hist_count >= 20 AND t.close <= (SELECT MIN(z.close) FROM (
                SELECT close FROM daily_kline y
                WHERE y.code=t.code AND y.exchange=t.exchange
                  AND y.asset_type=t.asset_type AND y.adjust_type='raw'
                  AND y.trade_date <= p_date
                ORDER BY y.trade_date DESC LIMIT 20) z) THEN 1 ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN t.hist_count >= 60 AND t.close >= (SELECT MAX(z.close) FROM (
                SELECT close FROM daily_kline y
                WHERE y.code=t.code AND y.exchange=t.exchange
                  AND y.asset_type=t.asset_type AND y.adjust_type='raw'
                  AND y.trade_date <= p_date
                ORDER BY y.trade_date DESC LIMIT 60) z) THEN 1 ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN t.hist_count >= 60 AND t.close <= (SELECT MIN(z.close) FROM (
                SELECT close FROM daily_kline y
                WHERE y.code=t.code AND y.exchange=t.exchange
                  AND y.asset_type=t.asset_type AND y.adjust_type='raw'
                  AND y.trade_date <= p_date
                ORDER BY y.trade_date DESC LIMIT 60) z) THEN 1 ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN t.hist_count >= 120 AND t.close >= (SELECT MAX(z.close) FROM (
                SELECT close FROM daily_kline y
                WHERE y.code=t.code AND y.exchange=t.exchange
                  AND y.asset_type=t.asset_type AND y.adjust_type='raw'
                  AND y.trade_date <= p_date
                ORDER BY y.trade_date DESC LIMIT 120) z) THEN 1 ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN t.hist_count >= 120 AND t.close <= (SELECT MIN(z.close) FROM (
                SELECT close FROM daily_kline y
                WHERE y.code=t.code AND y.exchange=t.exchange
                  AND y.asset_type=t.asset_type AND y.adjust_type='raw'
                  AND y.trade_date <= p_date
                ORDER BY y.trade_date DESC LIMIT 120) z) THEN 1 ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN t.hist_count >= 250 AND t.close >= (SELECT MAX(z.close) FROM (
                SELECT close FROM daily_kline y
                WHERE y.code=t.code AND y.exchange=t.exchange
                  AND y.asset_type=t.asset_type AND y.adjust_type='raw'
                  AND y.trade_date <= p_date
                ORDER BY y.trade_date DESC LIMIT 250) z) THEN 1 ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN t.hist_count >= 250 AND t.close <= (SELECT MIN(z.close) FROM (
                SELECT close FROM daily_kline y
                WHERE y.code=t.code AND y.exchange=t.exchange
                  AND y.asset_type=t.asset_type AND y.adjust_type='raw'
                  AND y.trade_date <= p_date
                ORDER BY y.trade_date DESC LIMIT 250) z) THEN 1 ELSE 0 END),0)
        INTO v_nh20, v_nl20, v_nh60, v_nl60, v_nh120, v_nl120, v_nh250, v_nl250
        FROM tmp_bd t;

        SET v_obos = v_adv - v_dec;
        SET v_up_ratio = IF(v_total=0, NULL, v_adv / v_total);
        SET v_down_ratio = IF(v_total=0, NULL, v_dec / v_total);
        SET v_adr = IF(v_dec=0, NULL, v_adv / v_dec);

        SET v_prev_adl = (
            SELECT adl FROM market_breadth
             WHERE universe_code=p_universe AND trade_date < p_date
             ORDER BY trade_date DESC LIMIT 1);
        SET v_adl = COALESCE(v_prev_adl, 0) + v_obos;

        SELECT (COALESCE(SUM(adr),0) + COALESCE(v_adr,0)) /
               NULLIF(COUNT(adr) + IF(v_adr IS NULL,0,1),0)
          INTO v_adr_ma10
          FROM (SELECT adr FROM market_breadth
                 WHERE universe_code=p_universe AND trade_date < p_date
                 ORDER BY trade_date DESC LIMIT 9) m;

        SELECT (COALESCE(SUM(adl),0) + COALESCE(v_adl,0)) /
               NULLIF(COUNT(adl) + IF(v_adl IS NULL,0,1),0)
          INTO v_adl_ma20
          FROM (SELECT adl FROM market_breadth
                 WHERE universe_code=p_universe AND trade_date < p_date
                 ORDER BY trade_date DESC LIMIT 19) m;

        SELECT (COALESCE(SUM(obos),0) + COALESCE(v_obos,0)) /
               NULLIF(COUNT(obos) + IF(v_obos IS NULL,0,1),0)
          INTO v_obos_ma10
          FROM (SELECT obos FROM market_breadth
                 WHERE universe_code=p_universe AND trade_date < p_date
                 ORDER BY trade_date DESC LIMIT 9) m;

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
        ) VALUES (
            p_universe, p_date,
            v_adv, v_dec, v_flat, v_total,
            v_limit_up, v_limit_down,
            v_nh20, v_nl20, v_nh60, v_nl60,
            v_nh120, v_nl120, v_nh250, v_nl250,
            v_up_vol, v_down_vol, v_total_vol, v_total_amt,
            v_avg, v_median,
            v_up_ratio, v_down_ratio, v_adr, v_adl, v_obos,
            v_adr_ma10, v_adl_ma20, v_obos_ma10, 'trigger'
        ) ON DUPLICATE KEY UPDATE
            advance_count=VALUES(advance_count),
            decline_count=VALUES(decline_count),
            flat_count=VALUES(flat_count),
            total_count=VALUES(total_count),
            limit_up_count=VALUES(limit_up_count),
            limit_down_count=VALUES(limit_down_count),
            new_high_20=VALUES(new_high_20), new_low_20=VALUES(new_low_20),
            new_high_60=VALUES(new_high_60), new_low_60=VALUES(new_low_60),
            new_high_120=VALUES(new_high_120), new_low_120=VALUES(new_low_120),
            new_high_250=VALUES(new_high_250), new_low_250=VALUES(new_low_250),
            up_volume=VALUES(up_volume), down_volume=VALUES(down_volume),
            total_volume=VALUES(total_volume), total_amount=VALUES(total_amount),
            avg_change_pct=VALUES(avg_change_pct),
            median_change_pct=VALUES(median_change_pct),
            up_ratio=VALUES(up_ratio), down_ratio=VALUES(down_ratio),
            adr=VALUES(adr), adl=VALUES(adl), obos=VALUES(obos),
            adr_ma10=VALUES(adr_ma10), adl_ma20=VALUES(adl_ma20),
            obos_ma10=VALUES(obos_ma10), source=VALUES(source);

        DROP TEMPORARY TABLE IF EXISTS tmp_bd;
    END IF;
END
$$
DELIMITER ;

-- 5. 批量处理脏日期
DROP PROCEDURE IF EXISTS process_market_breadth_dirty;
DELIMITER $$
CREATE PROCEDURE process_market_breadth_dirty()
BEGIN
    DECLARE v_u VARCHAR(32);
    DECLARE v_d DATE;
    DECLARE v_done INT DEFAULT 0;
    DECLARE v_err INT DEFAULT 0;
    DECLARE cur CURSOR FOR
        SELECT universe_code, trade_date FROM tmp_dirty_dates ORDER BY trade_date;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;

    IF GET_LOCK('market_breadth_sync', 0) = 1 THEN
        DROP TEMPORARY TABLE IF EXISTS tmp_dirty_dates;
        CREATE TEMPORARY TABLE tmp_dirty_dates AS
            SELECT universe_code, trade_date FROM market_breadth_dirty
            ORDER BY trade_date LIMIT 20;

        OPEN cur;
        read_loop: LOOP
            FETCH cur INTO v_u, v_d;
            IF v_done THEN LEAVE read_loop; END IF;
            SET v_err = 0;
            BEGIN
                DECLARE CONTINUE HANDLER FOR SQLEXCEPTION SET v_err = 1;
                CALL recompute_market_breadth(v_d, v_u);
            END;
            IF v_err = 0 THEN
                DELETE FROM market_breadth_dirty
                 WHERE universe_code=v_u AND trade_date=v_d;
            END IF;
        END LOOP;
        CLOSE cur;
        DROP TEMPORARY TABLE IF EXISTS tmp_dirty_dates;
        DO RELEASE_LOCK('market_breadth_sync');
    END IF;
END
$$
DELIMITER ;

-- 6. 定时事件
DROP EVENT IF EXISTS ev_market_breadth_sync;
CREATE EVENT ev_market_breadth_sync
ON SCHEDULE EVERY 30 SECOND
DO CALL process_market_breadth_dirty();
