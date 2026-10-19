"""运维命令：python manage.py <命令>

  migrate-legacy-files   把旧版存在 engine/storage/file/{用户}/{文件名} 的文件搬到新存储目录，并重新入库
  reindex-all            所有文档重新入库（改了切块、分词、索引映射后使用）
  drop-legacy-indices    删除旧版"每个用户一个索引"留下的 ES 索引
"""
import argparse
import logging
import shutil
import sys
from pathlib import Path

from common import setup_logging
from core.config import settings
from db.database import session_scope
from engine.api.utils.file_utils import get_project_base_directory
from engine.rag.utils.es_conn import ESConnection
from models import Document, DocumentStatus
from services import storage
from services.tasks import enqueue_ingest

logger = logging.getLogger("manage")

LEGACY_PREFIX = "legacy:"
LEGACY_DIR = Path(get_project_base_directory()) / "storage" / "file"


def migrate_legacy_files() -> None:
    with session_scope() as db:
        docs = db.query(Document).filter(Document.storage_path.startswith(LEGACY_PREFIX)).all()
        for doc in docs:
            source = LEGACY_DIR / doc.storage_path[len(LEGACY_PREFIX):]
            if not source.is_file():
                doc.status = DocumentStatus.FAILED
                doc.error = "旧版文件不存在，请删除后重新上传"
                logger.warning("找不到旧文件 %s", source)
                continue
            relative = storage.document_path(doc.user_id, doc.id, doc.file_type)
            target = storage.absolute_path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            doc.storage_path = relative
            doc.file_size = target.stat().st_size
            doc.status = DocumentStatus.PENDING
            logger.info("已迁移 %s -> %s", source, target)
        ids = [doc.id for doc in docs if doc.status == DocumentStatus.PENDING]
    for doc_id in ids:
        enqueue_ingest(doc_id)
    logger.info("迁移完成，%d 个文档已加入入库队列", len(ids))


def reindex_all() -> None:
    with session_scope() as db:
        ids = [doc_id for (doc_id,) in db.query(Document.id)
               .filter(~Document.storage_path.startswith(LEGACY_PREFIX)).all()]
        db.query(Document).filter(Document.id.in_(ids)).update(
            {Document.status: DocumentStatus.PENDING}, synchronize_session=False)
    for doc_id in ids:
        enqueue_ingest(doc_id)
    logger.info("%d 个文档已加入入库队列", len(ids))


def drop_legacy_indices() -> None:
    es = ESConnection().es
    for name in es.indices.get_alias(index="*"):
        if name.startswith(".") or name == settings.es_index:
            continue
        if name.isdigit():
            es.indices.delete(index=name)
            logger.info("已删除旧索引 %s", name)


COMMANDS = {
    "migrate-legacy-files": migrate_legacy_files,
    "reindex-all": reindex_all,
    "drop-legacy-indices": drop_legacy_indices,
}


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=COMMANDS)
    args = parser.parse_args()
    COMMANDS[args.command]()


if __name__ == "__main__":
    sys.exit(main())
