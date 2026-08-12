-- ============================================================================
-- stock_quant 数据库 DDL (MySQL)
-- ============================================================================

USE stock_quant;

-- 1. 股票基本信息
CREATE TABLE stocks (
    code         VARCHAR(10) PRIMARY KEY,
    name         VARCHAR(50) NOT NULL,
    market       VARCHAR(4),
    stock_type   VARCHAR(20) DEFAULT 'dividend',
    is_etf       TINYINT DEFAULT 0,
    listed_date  DATE,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. 日K线 (OHLCV)
CREATE TABLE daily_kline (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    code         VARCHAR(10) NOT NULL,
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
    adjust_type  VARCHAR(10) DEFAULT 'qfq',
    is_realtime  TINYINT DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_kline (code, trade_date, adjust_type),
    INDEX idx_kline_code_date (code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. 除权除息事件 (原始事件，可分推导任意复权类型)
CREATE TABLE adjust_events (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    code            VARCHAR(10) NOT NULL,
    ex_date         DATE NOT NULL,              -- 除权除息日
    cash_per_share  DOUBLE DEFAULT 0,           -- 每股现金分红 (元)
    stock_per_share DOUBLE DEFAULT 0,           -- 每股送转股数
    plan_desc       VARCHAR(100),               -- 分红方案描述 (如 "10派10.03元")
    record_date     DATE,                       -- 股权登记日
    cash_date       DATE,                       -- 现金红利发放日
    announce_date   DATE,                       -- 公告日期
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_ex_div (code, ex_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. 技术指标 (每日)
CREATE TABLE daily_indicators (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    code           VARCHAR(10) NOT NULL,
    trade_date     DATE NOT NULL,
    -- 均线
    ma5            DOUBLE, ma10           DOUBLE, ma20           DOUBLE, ma60           DOUBLE,
    -- EMA
    ema12          DOUBLE, ema26          DOUBLE,
    -- MACD
    macd_dif       DOUBLE, macd_dea       DOUBLE, macd_bar       DOUBLE,
    -- RSI
    rsi14          DOUBLE,
    -- 布林带
    boll_upper     DOUBLE, boll_middle    DOUBLE, boll_lower     DOUBLE,
    -- KDJ
    kdj_k          DOUBLE, kdj_d          DOUBLE, kdj_j          DOUBLE,
    -- 其他
    atr14          DOUBLE, obv            DOUBLE, cci20          DOUBLE,
    wr14           DOUBLE, vol_ma5        DOUBLE, vwap           DOUBLE,
    -- 扩展
    hv20           DOUBLE,
    mom60          DOUBLE,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_indicator (code, trade_date),
    INDEX idx_indicators_code_date (code, trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. 估值历史 (PE/PB/PS/PCF)
CREATE TABLE valuation_history (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    code           VARCHAR(10) NOT NULL,
    val_date       DATE NOT NULL,
    pe_ttm         DOUBLE,
    pb             DOUBLE,
    ps             DOUBLE,
    pcf            DOUBLE,
    ey             DOUBLE,
    dividend_yield DOUBLE,
    source         VARCHAR(30),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_valuation (code, val_date),
    INDEX idx_valuation_code_date (code, val_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6. 财务报告 (报告期数据)
CREATE TABLE financial_reports (
    id                     INT AUTO_INCREMENT PRIMARY KEY,
    code                   VARCHAR(10) NOT NULL,
    report_date            DATE NOT NULL,
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
    UNIQUE KEY uk_financial (code, report_date),
    INDEX idx_financials_code_date (code, report_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 7. 股东人数变化 (筹码集中度)
CREATE TABLE shareholder_history (
    id                     INT AUTO_INCREMENT PRIMARY KEY,
    code                   VARCHAR(10) NOT NULL,
    change_date            DATE NOT NULL,
    holder_count           DOUBLE,
    prev_holder_count      DOUBLE,
    holder_change_pct      DOUBLE,
    avg_holding            DOUBLE,
    prev_avg_holding       DOUBLE,
    avg_holding_change_pct DOUBLE,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_shareholder (code, change_date),
    INDEX idx_shareholder_code_date (code, change_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 8. 大股东/高管增减持记录
CREATE TABLE insider_trades (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    code           VARCHAR(10) NOT NULL,
    announce_date  DATE NOT NULL,
    shareholder    VARCHAR(100),
    change_amount  DOUBLE,
    change_text    VARCHAR(50),
    trade_price    DOUBLE,
    remaining      DOUBLE,
    trade_period   VARCHAR(50),
    trade_method   VARCHAR(50),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_insider (code, announce_date, shareholder, change_amount),
    INDEX idx_insider_code_date (code, announce_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 9. 策略信号
CREATE TABLE strategy_signals (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    code           VARCHAR(10) NOT NULL,
    trade_date     DATE NOT NULL,
    strategy_key   VARCHAR(30) NOT NULL,
    `signal`       TINYINT NOT NULL,
    score          DOUBLE,
    params_hash    VARCHAR(64),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_signal (code, trade_date, strategy_key, params_hash),
    INDEX idx_signals_code_date (code, trade_date),
    INDEX idx_signals_strategy (strategy_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 10. 回测结果 (单次分析汇总)
CREATE TABLE backtest_results (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    code            VARCHAR(10) NOT NULL,
    strategy_key    VARCHAR(30) NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    capital         DOUBLE NOT NULL DEFAULT 100000,
    is_index        TINYINT DEFAULT 0,
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
    params_hash     VARCHAR(64),
    run_time        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_backtest (code, strategy_key, start_date, end_date, params_hash),
    INDEX idx_backtest_code_strategy (code, strategy_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================================
-- 视图: 合并 K线 + 指标
-- ============================================================================
CREATE OR REPLACE VIEW v_daily_full AS
SELECT 
    k.code, k.trade_date,
    k.open, k.high, k.low, k.close, k.volume, k.amount,
    k.amplitude, k.change_pct, k.change_val, k.turnover_rate,
    i.ma5, i.ma10, i.ma20, i.ma60,
    i.ema12, i.ema26,
    i.macd_dif, i.macd_dea, i.macd_bar,
    i.rsi14,
    i.boll_upper, i.boll_middle, i.boll_lower,
    i.kdj_k, i.kdj_d, i.kdj_j,
    i.atr14, i.obv, i.cci20, i.wr14, i.vol_ma5, i.vwap,
    i.hv20, i.mom60
FROM daily_kline k
LEFT JOIN daily_indicators i ON k.code = i.code AND k.trade_date = i.trade_date;

-- ============================================================================
-- 视图: 各股票最新信号
-- ============================================================================
CREATE OR REPLACE VIEW v_latest_signals AS
SELECT 
    s.code, s.strategy_key, s.signal, s.score, s.trade_date
FROM strategy_signals s
WHERE s.trade_date = (
    SELECT MAX(ss.trade_date) 
    FROM strategy_signals ss 
    WHERE ss.code = s.code AND ss.strategy_key = s.strategy_key
);
