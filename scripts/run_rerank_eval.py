#!/usr/bin/env python3
"""[MOD] EXP-013 rerank 效果评测：同一批混合检索候选，RRF 序 vs bge 重排序。

与 run_retrieval_eval.py 的分工：那个评"检索器本身"（混合检索+RRF，不含
rerank——rerank 在完整版 pipeline 的 rerank 阶段）；本脚本评"rerank 对排序
的增量"——同一批 RRF 候选，分别按 RRF 序与 bge 重排序计算 expected_docs
的 recall@1/3、MRR，直接量化 rerank 的贡献。

用法（项目根目录）：
    HF_ENDPOINT=https://hf-mirror.com .venv/Scripts/python scripts/run_rerank_eval.py
"""

import asyncio
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(project_root / ".env")
import os  # noqa: E402

os.environ.pop("KB_DEPARTMENTS", None)  # 管理员视角

from easy_start.chat import initialize_components, search_documents  # noqa: E402
from app.modules.core.retrieval.interfaces import SearchResult  # noqa: E402
from app.modules.core.retrieval.rerankers.bge_reranker import BGEReranker  # noqa: E402

EVAL_SET = project_root / "evals" / "golden_set.json"
MAX_K = 8  # 与主链路 top_k=8 一致：候选池大小


def mrr_at(rank: int | None) -> float:
    return 0.0 if rank is None else 1.0 / rank


def first_hit_rank(order: list[str], expected: set[str]) -> int | None:
    for i, name in enumerate(order, start=1):
        if name in expected:
            return i
    return None


def safe_print(s: str) -> None:
    io.open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False).write(s + "\n")


async def main() -> None:
    golden_raw = json.loads(io.open(EVAL_SET, encoding="utf-8").read())
    # Golden 集外层为对象（description/cases 等键），题目列表在 cases 键
    cases = golden_raw.get("cases") if isinstance(golden_raw, dict) else golden_raw
    cases = [c for c in cases if c.get("type") != "refusal"]  # 拒答题不评排序
    safe_print(f"评测题数（排除拒答）：{len(cases)}")

    retriever, bm25_index, _ = initialize_components()  # 同步函数，返回 (retriever, bm25_index, merger)
    reranker = BGEReranker()

    rows = []
    for idx, case in enumerate(cases, 1):
        q = case["question"]
        expected = set(case.get("expected_docs") or [])
        docs = await search_documents(q, retriever, top_k=MAX_K, bm25_index=bm25_index)

        # 文档名提取（与评测口径一致：metadata.source_file）
        names_rrf = [d.get("metadata", {}).get("source_file", "") for d in docs]

        # bge 重排：把 dict 候选转成 SearchResult（rerank 接口要求）
        srs = [
            SearchResult(id=str(i), content=d.get("content", ""), score=d.get("score", 0.0), metadata=d.get("metadata", {}))
            for i, d in enumerate(docs)
        ]
        t0 = time.perf_counter()
        reranked = await reranker.rerank(q, srs, top_n=MAX_K)
        rerank_ms = (time.perf_counter() - t0) * 1000
        names_bge = [x.metadata.get("source_file", "") for x in reranked]

        rank_rrf = first_hit_rank(names_rrf, expected)
        rank_bge = first_hit_rank(names_bge, expected)
        rows.append({
            "question": q,
            "expected": sorted(expected),
            "rrf_order": names_rrf[:3],
            "bge_order": names_bge[:3],
            "rank_rrf": rank_rrf,
            "rank_bge": rank_bge,
            "rerank_ms": round(rerank_ms),
        })
        flag = ""
        if rank_rrf != rank_bge:
            flag = "  <-- 排名变化"
        safe_print(f"[{idx:02d}] {q[:24]:<24} RRF排名={rank_rrf} bge排名={rank_bge} rerank={rerank_ms:.0f}ms{flag}")

    # 汇总
    def metrics(key: str) -> dict:
        ranks = [r[key] for r in rows]
        hits1 = sum(1 for x in ranks if x == 1)
        hits3 = sum(1 for x in ranks if x and x <= 3)
        mrr = sum(mrr_at(x) for x in ranks) / len(ranks)
        return {"recall@1": hits1 / len(ranks), "recall@3": hits3 / len(ranks), "mrr": round(mrr, 4)}

    m_rrf, m_bge = metrics("rank_rrf"), metrics("rank_bge")
    improved = sum(1 for r in rows if (r["rank_rrf"] or 99) > (r["rank_bge"] or 99) and r["rank_bge"])
    degraded = sum(1 for r in rows if (r["rank_rrf"] or 99) < (r["rank_bge"] or 99) and r["rank_bge"])
    avg_ms = round(sum(r["rerank_ms"] for r in rows) / len(rows))

    report = {
        "experiment": "EXP-013 rerank 效果对比（同一批混合检索候选）",
        "cases": len(rows),
        "rrf_baseline": m_rrf,
        "bge_reranked": m_bge,
        "improved": improved,
        "degraded": degraded,
        "avg_rerank_ms": avg_ms,
        "rows": rows,
        "created_at": datetime.now().isoformat(),
    }
    safe_print("\n===== 汇总 =====")
    safe_print(f"RRF 基线:  recall@1={m_rrf['recall@1']:.3f} recall@3={m_rrf['recall@3']:.3f} MRR={m_rrf['mrr']}")
    safe_print(f"bge 重排:  recall@1={m_bge['recall@1']:.3f} recall@3={m_bge['recall@3']:.3f} MRR={m_bge['mrr']}")
    safe_print(f"排序改善 {improved} 题 / 退化 {degraded} 题 / 平均 rerank 耗时 {avg_ms}ms")

    out_dir = project_root / "evals" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"rerank_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    io.open(out, "w", encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    safe_print(f"报告已落盘: {out}")


if __name__ == "__main__":
    asyncio.run(main())
