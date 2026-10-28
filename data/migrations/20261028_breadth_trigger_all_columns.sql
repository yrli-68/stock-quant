-- ============================================================================
-- 触发器增强：daily_kline 数据变更时，自动更新 market_breadth 全部栏目
-- 目标库：stock_quant (MySQL 8.0+)
--
-- 设计说明：
--   直接在行级触发器里重算全栏目（含 20/60/120/250 日新高新低、中位数、ADL 等）
--   代价过高（单日约 3 秒，批量写入会成倍放大）。因此采用：
--     1) 触发器只把受影响的 trade_date 写入 market_breadth_dirty（极轻量）；
--     2) 存储过程 recompute_market_breadth 负责单日全栏目重算；
--     3) 事件 ev_market_breadth_sync 每 30 秒批量处理脏日期（每次最多 20 个，
--        并用 GET_LOCK 防止重叠）。
--
-- 覆盖 daily_kline 的 INSERT / UPDATE / DELETE。
-- 单个日期重算失败时跳过该日期、继续处理其余日期（失败日期保留在队列中重试）。
-- 事件调度器需开启：SET GLOBAL event_scheduler = ON;
--
-- 应用方式：
--   mysql -u smit -p stock_quant < data/migrations/20261028_breadth_trigger_all_columns.sql
-- ============================================================================

USE stock_quant;

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
