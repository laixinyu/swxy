#!/usr/bin/env python3
"""
检索评测：对测试集里的每个问题跑一遍和线上相同的检索流程，看标准答案所在的块排第几。

测试集是一个 JSON 数组，每条包含：
  question  问题
  doc       答案所在文档名的一部分，例如 "润本股份"
  answer    答案块里一定会出现的一小段原文，例如 "59.0%"
某个块同时满足"来自 doc"和"原文包含 answer"，就算找到了答案。

在后端容器里运行：
  docker cp backend/scripts/. swxy_api:/tmp/scripts
  docker exec -w /app swxy_api python /tmp/scripts/eval_retrieval.py /tmp/scripts/eval_set.json --user 1
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR) if APP_DIR.exists() else os.getcwd())
logging.disable(logging.CRITICAL)

import numpy as np  # noqa: E402
from core.config import settings  # noqa: E402
from engine.rag.settings import PAGERANK_FLD  # noqa: E402
from services.retrieval import dealer  # noqa: E402

# 与线上检索使用同一份配置
TOP_K = settings.retrieval_top_k
CANDIDATES = settings.retrieval_candidates
VECTOR_WEIGHT = settings.vector_similarity_weight
THRESHOLD = settings.similarity_threshold


def rank_of_answer(question, kb_id, case):
    req = {"kb_ids": [kb_id], "doc_ids": None, "size": CANDIDATES, "question": question,
           "vector": True, "topk": 1024, "similarity": THRESHOLD, "available_int": 1}
    rank_feature = {PAGERANK_FLD: 10}
    sres = dealer.search(req, [settings.es_index], [kb_id], None, False, rank_feature=rank_feature)
    if not sres.ids:
        return None, None, 0

    sim, _, _ = dealer.rerank_by_model(sres, question, 1 - VECTOR_WEIGHT, VECTOR_WEIGHT,
                                       rank_feature=rank_feature)

    def is_answer(chunk_id):
        field = sres.field[chunk_id]
        return case["doc"] in field.get("docnm_kwd", "") and case["answer"] in field.get("content_with_weight", "")

    recall_rank = next((i + 1 for i, cid in enumerate(sres.ids) if is_answer(cid)), None)
    order = [i for i in np.argsort(-sim) if sim[i] >= THRESHOLD]
    final_rank = next((r + 1 for r, i in enumerate(order) if is_answer(sres.ids[i])), None)
    return recall_rank, final_rank, len(sres.ids)


def main():
    parser = argparse.ArgumentParser(description="检索评测")
    parser.add_argument("eval_set", help="测试集 JSON 文件路径")
    parser.add_argument("--user", default="1", help="用户 ID（检索时按它过滤知识库）")
    args = parser.parse_args()

    cases = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))
    hit1 = hit5 = recalled = 0
    reciprocal_ranks = []
    by_type = {}

    print(f"{'结果':<10}{'粗筛名次':>8}{'最终名次':>8}  问题")
    for case in cases:
        recall_rank, final_rank, total = rank_of_answer(case["question"], args.user, case)

        if final_rank and final_rank <= TOP_K:
            verdict = "命中"
        elif recall_rank:
            verdict = "重排没排上"
        else:
            verdict = "粗筛没找到"

        hit1 += final_rank == 1
        hit5 += bool(final_rank and final_rank <= TOP_K)
        recalled += bool(recall_rank)
        reciprocal_ranks.append(1 / final_rank if final_rank and final_rank <= TOP_K else 0)
        group = by_type.setdefault(case.get("type", "全部"), [0, 0, 0])
        group[0] += 1
        group[1] += final_rank == 1
        group[2] += bool(final_rank and final_rank <= TOP_K)

        print(f"{verdict:<10}{recall_rank or '-':>8}{final_rank or '-':>8}  {case['question']}"
              f"   (候选 {total} 条)")

    n = len(cases)
    print()
    print(f"粗筛召回率（答案在 {CANDIDATES} 条候选里）: {recalled}/{n} = {recalled / n:.0%}")
    print(f"前 1 命中率: {hit1}/{n} = {hit1 / n:.0%}")
    print(f"前 {TOP_K} 命中率: {hit5}/{n} = {hit5 / n:.0%}")
    print(f"MRR（答案平均排得多靠前，1 为满分）: {sum(reciprocal_ranks) / n:.2f}")
    if len(by_type) > 1:
        print()
        for name, (count, top1, top5) in by_type.items():
            print(f"[{name}] {count} 题，前 1 命中 {top1 / count:.0%}，前 {TOP_K} 命中 {top5 / count:.0%}")


if __name__ == "__main__":
    main()
