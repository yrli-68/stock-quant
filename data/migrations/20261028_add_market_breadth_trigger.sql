-- ============================================================================
-- 触发器：daily_kline 新增数据时更新 market_breadth
-- 目标库：stock_quant (MySQL 8.0+)
--
-- 第一阶段范围:
--   - 仅维护 universe_code = 'ALL_A'
--   - 仅写入/更新 trade_date、total_count
--   - total_count = 该交易日 asset_type='stock' 的去重标的数
--     （按 code+exchange+asset_type 去重，避免不同复权类型重复计数）
--
-- 后续扩展:
--   在 v_total 之后继续统计 advance_count / decline_count / limit_up_count ...
--   等字段，并在 INSERT ... ON DUPLICATE KEY UPDATE 中一并更新即可。
--
-- 应用方式（mysql CLI）:
--   mysql -u smit -p stock_quant < data/migrations/20261028_add_market_breadth_trigger.sql
--
-- 注意:
--   1. 触发器为行级触发，同一交易日批量导入大量 K 线时会多次重算；
--      历史批量导入建议先禁用触发器，导入后再统一重算。
--   2. 仅处理 INSERT。daily_kline 使用 REPLACE 时会“先删后插”，
--      本触发器按重算结果更新，因此 REPLACE 后总数仍正确；
--      纯 DELETE 不会更新 total_count（后续可增加 AFTER DELETE 触发器）。
-- ============================================================================

USE stock_quant;

DROP TRIGGER IF EXISTS trg_daily_kline_ai_breadth;

DELIMITER $$

CREATE TRIGGER trg_daily_kline_ai_breadth
AFTER INSERT ON daily_kline
FOR EACH ROW
BEGIN
    DECLARE v_total INT UNSIGNED DEFAULT 0;

    SELECT COUNT(DISTINCT code, exchange, asset_type)
      INTO v_total
      FROM daily_kline
     WHERE trade_date = NEW.trade_date
       AND asset_type = 'stock';

    INSERT INTO market_breadth (universe_code, trade_date, total_count)
    VALUES ('ALL_A', NEW.trade_date, v_total)
    ON DUPLICATE KEY UPDATE total_count = v_total;
END$$

DELIMITER ;
