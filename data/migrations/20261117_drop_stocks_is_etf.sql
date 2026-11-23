-- stocks 表取消 is_etf 列：是否 ETF 由 asset_type='etf' 判断
ALTER TABLE stocks DROP COLUMN is_etf;
