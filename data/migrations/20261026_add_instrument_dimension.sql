-- ============================================================================
-- 迁移：引入资产维度 code + exchange + asset_type
-- 目标库：stock_quant (MySQL 8.0+)
-- 执行前请备份数据库。
-- ============================================================================

USE stock_quant;

-- ----------------------------------------------------------------------------
-- 1. 资产主表
-- ----------------------------------------------------------------------------
ALTER TABLE stocks
    ADD COLUMN exchange VARCHAR(8) NOT NULL DEFAULT '' AFTER code,
    ADD COLUMN asset_type ENUM('stock', 'index', 'etf', 'fund', 'bond')
        NOT NULL DEFAULT 'stock' AFTER exchange;

UPDATE stocks
SET exchange = CASE
    WHEN code REGEXP '^(6|9|5)' THEN 'SH'
    WHEN code REGEXP '^(0|1|2|3)' THEN 'SZ'
    WHEN code REGEXP '^(4|8)' THEN 'BJ'
    ELSE ''
END
WHERE exchange = '';

ALTER TABLE stocks
    DROP PRIMARY KEY,
    ADD PRIMARY KEY (code, exchange, asset_type);

-- ----------------------------------------------------------------------------
-- 2. 日K线
-- ----------------------------------------------------------------------------
ALTER TABLE daily_kline
    ADD COLUMN exchange VARCHAR(8) NOT NULL DEFAULT '' AFTER code,
    ADD COLUMN asset_type ENUM('stock', 'index', 'etf', 'fund', 'bond')
        NOT NULL DEFAULT 'stock' AFTER exchange;

UPDATE daily_kline
SET exchange = CASE
    WHEN code REGEXP '^(6|9|5)' THEN 'SH'
    WHEN code REGEXP '^(0|1|2|3)' THEN 'SZ'
    WHEN code REGEXP '^(4|8)' THEN 'BJ'
    ELSE ''
END
WHERE exchange = '';

UPDATE daily_kline
SET adjust_type = 'qfq'
WHERE adjust_type IS NULL OR adjust_type = '';

ALTER TABLE daily_kline
    MODIFY COLUMN adjust_type VARCHAR(10) NOT NULL DEFAULT 'qfq',
    DROP INDEX uk_kline,
    DROP INDEX idx_kline_code_date,
    ADD UNIQUE KEY uk_kline
        (code, exchange, asset_type, trade_date, adjust_type),
    ADD INDEX idx_kline_trade_date (trade_date, exchange, asset_type);

-- ----------------------------------------------------------------------------
-- 3. 除权除息事件
-- ----------------------------------------------------------------------------
ALTER TABLE adjust_events
    ADD COLUMN exchange VARCHAR(8) NOT NULL DEFAULT '' AFTER code,
    ADD COLUMN asset_type ENUM('stock', 'index', 'etf', 'fund', 'bond')
        NOT NULL DEFAULT 'stock' AFTER exchange;

UPDATE adjust_events
SET exchange = CASE
    WHEN code REGEXP '^(6|9|5)' THEN 'SH'
    WHEN code REGEXP '^(0|1|2|3)' THEN 'SZ'
    WHEN code REGEXP '^(4|8)' THEN 'BJ'
    ELSE ''
END
WHERE exchange = '';

ALTER TABLE adjust_events
    DROP INDEX uk_ex_div,
    ADD UNIQUE KEY uk_ex_div (code, exchange, asset_type, ex_date),
    ADD INDEX idx_ex_div_date (ex_date, asset_type);

-- ----------------------------------------------------------------------------
-- 4. 技术指标
-- ----------------------------------------------------------------------------
ALTER TABLE daily_indicators
    ADD COLUMN exchange VARCHAR(8) NOT NULL DEFAULT '' AFTER code,
    ADD COLUMN asset_type ENUM('stock', 'index', 'etf', 'fund', 'bond')
        NOT NULL DEFAULT 'stock' AFTER exchange,
    ADD COLUMN adjust_type VARCHAR(10) NOT NULL DEFAULT 'qfq' AFTER trade_date;

UPDATE daily_indicators
SET exchange = CASE
    WHEN code REGEXP '^(6|9|5)' THEN 'SH'
    WHEN code REGEXP '^(0|1|2|3)' THEN 'SZ'
    WHEN code REGEXP '^(4|8)' THEN 'BJ'
    ELSE ''
END
WHERE exchange = '';

ALTER TABLE daily_indicators
    DROP INDEX uk_indicator,
    DROP INDEX idx_indicators_code_date,
    ADD UNIQUE KEY uk_indicator
        (code, exchange, asset_type, trade_date, adjust_type),
    ADD INDEX idx_indicators_trade_date (trade_date, exchange, asset_type);

-- ----------------------------------------------------------------------------
-- 5. 估值历史
-- ----------------------------------------------------------------------------
ALTER TABLE valuation_history
    ADD COLUMN exchange VARCHAR(8) NOT NULL DEFAULT '' AFTER code,
    ADD COLUMN asset_type ENUM('stock', 'index', 'etf', 'fund', 'bond')
        NOT NULL DEFAULT 'stock' AFTER exchange;

UPDATE valuation_history
SET exchange = CASE
    WHEN code REGEXP '^(6|9|5)' THEN 'SH'
    WHEN code REGEXP '^(0|1|2|3)' THEN 'SZ'
    WHEN code REGEXP '^(4|8)' THEN 'BJ'
    ELSE ''
END
WHERE exchange = '';

ALTER TABLE valuation_history
    DROP INDEX uk_valuation,
    DROP INDEX idx_valuation_code_date,
    ADD UNIQUE KEY uk_valuation (code, exchange, asset_type, val_date),
    ADD INDEX idx_valuation_val_date (val_date, asset_type);

-- ----------------------------------------------------------------------------
-- 6. 财务报告：公告日 + 修订版本
-- ----------------------------------------------------------------------------
ALTER TABLE financial_reports
    ADD COLUMN exchange VARCHAR(8) NOT NULL DEFAULT '' AFTER code,
    ADD COLUMN asset_type ENUM('stock', 'index', 'etf', 'fund', 'bond')
        NOT NULL DEFAULT 'stock' AFTER exchange,
    ADD COLUMN announce_date DATE AFTER report_date,
    ADD COLUMN data_version INT NOT NULL DEFAULT 1 AFTER announce_date;

UPDATE financial_reports
SET exchange = CASE
    WHEN code REGEXP '^(6|9|5)' THEN 'SH'
    WHEN code REGEXP '^(0|1|2|3)' THEN 'SZ'
    WHEN code REGEXP '^(4|8)' THEN 'BJ'
    ELSE ''
END
WHERE exchange = '';

ALTER TABLE financial_reports
    DROP INDEX uk_financial,
    DROP INDEX idx_financials_code_date,
    ADD UNIQUE KEY uk_financial
        (code, exchange, asset_type, report_date, data_version),
    ADD INDEX idx_financials_announce
        (code, exchange, asset_type, announce_date, report_date),
    ADD INDEX idx_financials_report_date (report_date, asset_type);

-- ----------------------------------------------------------------------------
-- 7. 股东人数
-- ----------------------------------------------------------------------------
ALTER TABLE shareholder_history
    ADD COLUMN exchange VARCHAR(8) NOT NULL DEFAULT '' AFTER code,
    ADD COLUMN asset_type ENUM('stock', 'index', 'etf', 'fund', 'bond')
        NOT NULL DEFAULT 'stock' AFTER exchange;

UPDATE shareholder_history
SET exchange = CASE
    WHEN code REGEXP '^(6|9|5)' THEN 'SH'
    WHEN code REGEXP '^(0|1|2|3)' THEN 'SZ'
    WHEN code REGEXP '^(4|8)' THEN 'BJ'
    ELSE ''
END
WHERE exchange = '';

ALTER TABLE shareholder_history
    DROP INDEX uk_shareholder,
    DROP INDEX idx_shareholder_code_date,
    ADD UNIQUE KEY uk_shareholder (code, exchange, asset_type, change_date),
    ADD INDEX idx_shareholder_change_date (change_date, asset_type);

-- ----------------------------------------------------------------------------
-- 8. 增减持
-- ----------------------------------------------------------------------------
ALTER TABLE insider_trades
    ADD COLUMN exchange VARCHAR(8) NOT NULL DEFAULT '' AFTER code,
    ADD COLUMN asset_type ENUM('stock', 'index', 'etf', 'fund', 'bond')
        NOT NULL DEFAULT 'stock' AFTER exchange;

UPDATE insider_trades
SET exchange = CASE
    WHEN code REGEXP '^(6|9|5)' THEN 'SH'
    WHEN code REGEXP '^(0|1|2|3)' THEN 'SZ'
    WHEN code REGEXP '^(4|8)' THEN 'BJ'
    ELSE ''
END
WHERE exchange = '';

ALTER TABLE insider_trades
    DROP INDEX uk_insider,
    DROP INDEX idx_insider_code_date,
    ADD UNIQUE KEY uk_insider
        (code, exchange, asset_type, announce_date, shareholder, change_amount),
    ADD INDEX idx_insider_announce_date (announce_date, asset_type);

-- ----------------------------------------------------------------------------
-- 9. 策略信号
-- ----------------------------------------------------------------------------
ALTER TABLE strategy_signals
    ADD COLUMN exchange VARCHAR(8) NOT NULL DEFAULT '' AFTER code,
    ADD COLUMN asset_type ENUM('stock', 'index', 'etf', 'fund', 'bond')
        NOT NULL DEFAULT 'stock' AFTER exchange;

UPDATE strategy_signals
SET exchange = CASE
    WHEN code REGEXP '^(6|9|5)' THEN 'SH'
    WHEN code REGEXP '^(0|1|2|3)' THEN 'SZ'
    WHEN code REGEXP '^(4|8)' THEN 'BJ'
    ELSE ''
END
WHERE exchange = '';

UPDATE strategy_signals SET params_hash = '' WHERE params_hash IS NULL;

ALTER TABLE strategy_signals
    MODIFY COLUMN params_hash VARCHAR(64) NOT NULL DEFAULT '',
    DROP INDEX uk_signal,
    DROP INDEX idx_signals_code_date,
    DROP INDEX idx_signals_strategy,
    ADD UNIQUE KEY uk_signal
        (code, exchange, asset_type, trade_date, strategy_key, params_hash),
    ADD INDEX idx_signals_strategy_date (strategy_key, trade_date, `signal`);

-- ----------------------------------------------------------------------------
-- 10. 回测结果
-- ----------------------------------------------------------------------------
ALTER TABLE backtest_results
    ADD COLUMN exchange VARCHAR(8) NOT NULL DEFAULT '' AFTER code,
    ADD COLUMN asset_type ENUM('stock', 'index', 'etf', 'fund', 'bond')
        NOT NULL DEFAULT 'stock' AFTER exchange;

UPDATE backtest_results
SET exchange = CASE
    WHEN code REGEXP '^(6|9|5)' THEN 'SH'
    WHEN code REGEXP '^(0|1|2|3)' THEN 'SZ'
    WHEN code REGEXP '^(4|8)' THEN 'BJ'
    ELSE ''
END
WHERE exchange = '';

UPDATE backtest_results SET asset_type = 'index' WHERE is_index = 1;
UPDATE backtest_results SET params_hash = '' WHERE params_hash IS NULL;

ALTER TABLE backtest_results
    MODIFY COLUMN params_hash VARCHAR(64) NOT NULL DEFAULT '',
    DROP INDEX uk_backtest,
    DROP INDEX idx_backtest_code_strategy,
    ADD UNIQUE KEY uk_backtest
        (code, exchange, asset_type, strategy_key, start_date, end_date, params_hash),
    ADD INDEX idx_backtest_strategy_date (strategy_key, start_date, end_date),
    DROP COLUMN is_index;

-- ----------------------------------------------------------------------------
-- 11. 重建视图
-- ----------------------------------------------------------------------------
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
