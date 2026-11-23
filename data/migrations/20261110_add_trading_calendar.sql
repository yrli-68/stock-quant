-- 交易日历：网络 K 线/日历数据回写后供缺口检查优先读取
CREATE TABLE IF NOT EXISTS trading_calendar (
    trade_date    DATE NOT NULL,
    source        VARCHAR(30) DEFAULT 'kline',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 可选：从现有 market_breadth 回填历史交易日
INSERT IGNORE INTO trading_calendar (trade_date, source)
SELECT trade_date, 'market_breadth'
FROM market_breadth
WHERE universe_code = 'ALL_A';
