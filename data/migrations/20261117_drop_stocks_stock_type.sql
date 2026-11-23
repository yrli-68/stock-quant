-- stocks 表取消 stock_type 列：标的类型信息由 asset_type 列替代
ALTER TABLE stocks DROP COLUMN stock_type;
