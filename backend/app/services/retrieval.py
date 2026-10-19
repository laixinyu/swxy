import logging

from core.config import settings
from engine.rag.nlp.search_v2 import Dealer
from engine.rag.utils.es_conn import ESConnection

logger = logging.getLogger(__name__)

es_connection = ESConnection()
dealer = Dealer(dataStore=es_connection)


def retrieve_content(kb_id: str, question: str, doc_ids: list[str] | None = None) -> list[dict]:
    """在共享索引里按 kb_id 隔离检索，返回前 top_k 个块。

    doc_ids 非空时只在这些文档里检索。调用方负责保证这些文档属于该用户。
    """
    if not es_connection.es.indices.exists(index=settings.es_index):
        return []
    results = dealer.retrieval(
        question=question,
        embd_mdl=None,
        tenant_ids=settings.es_index,
        kb_ids=[kb_id],
        doc_ids=doc_ids or None,
        page=1,
        page_size=settings.retrieval_top_k,
        similarity_threshold=settings.similarity_threshold,
        vector_similarity_weight=settings.vector_similarity_weight,
    )
    references = []
    for chunk in results["chunks"]:
        references.append({
            "document_id": chunk.get("doc_id", ""),
            "document_name": chunk.get("docnm_kwd", "").split("/")[-1],
            "content_with_weight": chunk.get("content_with_weight", ""),
            "positions": chunk.get("positions", []),
        })
    logger.info("检索 kb=%s 范围=%s 命中 %d 块", kb_id, len(doc_ids) if doc_ids else "全部", len(references))
    return references
