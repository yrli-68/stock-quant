"""
更新 adjust_events 表中 stock_per_share 字段的脚本

从 plan_desc 中解析送转股数并更新 stock_per_share 字段。
"""

import re
import logging

from core.db import get_connection, set_db_mode

set_db_mode(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


def parse_stock_per_share(plan: str) -> float:
    """
    从方案描述中解析每股送转股数

    支持格式:
    - "10转4派7元" -> 0.4
    - "10转5股派3元" -> 0.5
    - "10送2股转3股派1元" -> 0.5
    - "10送转5股" -> 0.5
    """
    if not plan:
        return 0.0

    stock = 0.0

    # 提取"送X股"或"送X"
    send_match = re.search(r'送\s*([\d.]+)\s*(?:股)?', plan)
    if send_match:
        stock += float(send_match.group(1)) / 10.0

    # 提取"转X股"或"转X"或"转增X股"
    transfer_match = re.search(r'转[增]?\s*([\d.]+)\s*(?:股)?', plan)
    if transfer_match:
        stock += float(transfer_match.group(1)) / 10.0

    return stock


def update_stock_per_share():
    """读取所有 adjust_events 记录，从 plan_desc 解析并更新 stock_per_share"""

    with get_connection() as conn:
        if conn is None:
            logger.error("无法连接数据库")
            return

        cursor = conn.cursor()

        # 读取所有记录
        cursor.execute("""
            SELECT id, plan_desc, stock_per_share 
            FROM adjust_events 
            WHERE plan_desc IS NOT NULL AND plan_desc != ''
        """)

        rows = cursor.fetchall()
        logger.info("共找到 %d 条记录需要检查", len(rows))

        updated_count = 0
        for row in rows:
            record_id, plan_desc, current_stock = row
            parsed_stock = parse_stock_per_share(plan_desc)

            # 如果解析结果与当前值不同，则更新
            if abs(parsed_stock - (current_stock or 0)) > 1e-6:
                cursor.execute("""
                    UPDATE adjust_events 
                    SET stock_per_share = %s 
                    WHERE id = %s
                """, (parsed_stock, record_id))
                updated_count += 1
                logger.info(
                    "更新 ID=%d: plan='%s', 旧值=%.4f -> 新值=%.4f",
                    record_id, plan_desc, current_stock or 0, parsed_stock
                )

        if updated_count > 0:
            conn.commit()
            logger.info("共更新 %d 条记录", updated_count)
        else:
            logger.info("无需更新")


if __name__ == '__main__':
    update_stock_per_share()
