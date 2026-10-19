"""启动前执行数据库迁移：python -m db.migrate"""
import logging
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from common import setup_logging
from db.database import engine

BASELINE_REVISION = "980b32f130df"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"

logger = logging.getLogger("db.migrate")


def wait_for_database(retries: int = 30, delay: float = 2.0) -> None:
    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            logger.info("等待数据库 (%d/%d)", attempt, retries)
            time.sleep(delay)
    raise RuntimeError("数据库连接失败")


def main() -> None:
    setup_logging()
    wait_for_database()
    config = Config(str(ALEMBIC_INI))
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" not in tables and "users" in tables:
        # 旧部署由 init.sql 建表，没有版本记录，标记为基线后再升级
        logger.info("检测到由 init.sql 创建的旧库，标记基线 %s", BASELINE_REVISION)
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")
    logger.info("数据库迁移完成")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("数据库迁移失败")
        sys.exit(1)
