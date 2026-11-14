#!/usr/bin/env python3
"""[MOD] EXP-021 rerank 候选池规模对比：top_k=8 vs 6 vs 5（质量 vs 延迟）。

背景：EXP-013 证实 bge CPU rerank 是端到端延迟最大单项（8 候选 ~6.3s，
占 43.6%）。本实验在同一批检索候选上比较三种候选池规模的排序质量与耗时，
回答"rerank 候选数能否从 8 降到 5/6"。

方法：每题只做一次混合检索取 top-8 池（与生产一致），切片成 8/6/5 三个
候选子池分别 bge 重排，各算 recall@1/3/5、MRR 与平均 rerank 耗时。
切片等价于"检索时就取 top_k=N"（RRF 序确定性，前 N 名相同）。
预期：若 5 池 recall@3 与 MRR 与 8 池持平，则生产 rerank_top_k 可降到 5
（首字延迟预计 -2.5s）；若退化则回退维持 8。

用法（项目根目录；先复制 Qdrant 数据目录避开容器文件锁）：
    cp -r easy_start/.qdrant_data easy_start/.qdrant_data_eval
    QDRANT_PATH=easy_start/.qdrant_data_eval .venv/Scripts/python scripts/run_rerank_k_eval.py
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
MAX_K = 8          # 生产当前候选池
POOL_SIZES = [8, 6, 5]  # 对比档位（与 rag.yaml retrieval.top_k 对应）


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
    cases = golden_raw.get("cases") if isinstance(golden_raw, dict) else golden_raw
    cases = [c for c in cases if c.get("type") != "refusal"]  # 拒答题不评排序
    safe_print(f"评测题数（排除拒答）：{len(cases)}｜候选池档位：{POOL_SIZES}")

    retriever, bm25_index, _ = initialize_components()
    reranker = BGEReranker()

    rows = []
    for idx, case in enumerate(cases, 1):
        q = case["question"]
        expected = set(case.get("expected_docs") or [])
        docs = await search_documents(q, retriever, top_k=MAX_K, bm25_index=bm25_index)

        row = {"question": q, "expected": sorted(expected)}
        for pool in POOL_SIZES:
            sub = docs[:pool]
            srs = [
                SearchResult(id=str(i), content=d.get("content", ""), score=d.get("score", 0.0), metadata=d.get("metadata", {}))
                for i, d in enumerate(sub)
            ]
            t0 = time.perf_counter()
            reranked = await reranker.rerank(q, srs, top_n=pool)
            ms = (time.perf_counter() - t0) * 1000
            names = [x.metadata.get("source_file", "") for x in reranked]
            rank = first_hit_rank(names, expected)
            row[f"rank_{pool}"] = rank
            row[f"ms_{pool}"] = round(ms)
            row[f"order_{pool}"] = names[:3]
        rows.append(row)
        safe_print(
            f"[{idx:02d}] {q[:20]:<20} "
            + "  ".join(f"池{p}:rank={row[f'rank_{p}']}({row[f'ms_{p}']}ms)" for p in POOL_SIZES)
        )

    def metrics(key: str, cap: int | None = None) -> dict:
        ranks = [r[key] for r in rows]
        limit = cap or len(ranks)
        hits1 = sum(1 for x in ranks if x == 1)
        hits3 = sum(1 for x in ranks if x and x <= 3)
        hits5 = sum(1 for x in ranks if x and x <= 5)
        mrr = sum(mrr_at(x) for x in ranks) / limit
        return {"recall@1": round(hits1 / limit, 4), "recall@3": round(hits3 / limit, 4), "recall@5": round(hits5 / limit, 4), "mrr": round(mrr, 4)}

    summary = {}
    for pool in POOL_SIZES:
        summary[pool] = metrics(f"rank_{pool}")
        summary[pool]["avg_rerank_ms"] = round(sum(r[f"ms_{pool}"] for r in rows) / len(rows))

    report = {
        "experiment": "EXP-021 rerank 候选池规模对比（8/6/5，同一批混合检索候选切片）",
        "cases": len(rows),
        "summary": summary,
        "rows": rows,
        "created_at": datetime.now().isoformat(),
    }
    safe_print("\n===== 汇总 =====")
    for pool in POOL_SIZES:
        m = summary[pool]
        safe_print(f"池{pool}: recall@1={m['recall@1']:.3f} recall@3={m['recall@3']:.3f} MRR={m['mrr']} 平均rerank={m['avg_rerank_ms']}ms")

    base = summary[MAX_K]
    for pool in POOL_SIZES[1:]:
        m = summary[pool]
        if m["recall@3"] == base["recall@3"] and m["mrr"] >= base["mrr"] - 0.01:
            safe_print(f"[结论候选] 池{pool} 质量持平且平均 rerank 省 {base['avg_rerank_ms'] - m['avg_rerank_ms']}ms —— 可考虑切换")
        else:
            safe_print(f"[结论候选] 池{pool} 质量退化（recall@3 {base['recall@3']}→{m['recall@3']} / MRR {base['mrr']}→{m['mrr']}）—— 维持 8")

    out_dir = project_root / "evals" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"rerank_k_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    io.open(out, "w", encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    safe_print(f"报告已落盘: {out}")


if __name__ == "__main__":
    asyncio.run(main())
