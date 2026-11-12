#!/usr/bin/env python3
"""[MOD] EXP-017 HyDE 查询重构评测：原查询 vs 原+HyDE 假设文档双查询 RRF 融合。

与 run_retrieval_eval.py 同一条检索路径（search_documents + _rrf_fuse），
HyDE 轮每题多一次 LLM 生成（glm-4-flash）+ 一次检索 + 列表级 RRF 融合，
与生产 Multi-Query 语义一致（每条扩展查询各自混合检索后融合）。

用法（后端/容器须停止——Qdrant 本地模式锁互斥）：
    .venv/Scripts/python scripts/run_hyde_eval.py
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(project_root / ".env")
os.environ.pop("KB_DEPARTMENTS", None)

from easy_start.chat import _rrf_fuse, initialize_components, search_documents  # noqa: E402

HYDE_PROMPT = (
    "你是企业制度知识库的检索助手。请针对下面的问题，写一段假设性的答案段落"
    "（80-150 字），按企业制度的常见书面措辞组织（条款式、正式用语）。"
    "不要解释、不要铺垫，只输出答案段落本身。\n\n"
    "问题：{message}"
)


def hyde_generate(question: str) -> str | None:
    """智谱 glm-4-flash 生成假设答案段落（与生产 prompt 一致）。"""
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=os.getenv("ZHIPU_API_KEY"),
            base_url=os.getenv("ZHIPU_API_URL", "https://open.bigmodel.cn/api/paas/v4/"),
        )
        r = client.chat.completions.create(
            model="glm-4-flash",
            messages=[{"role": "user", "content": HYDE_PROMPT.format(message=question)}],
            temperature=0.0,
            max_tokens=400,
        )
        text = (r.choices[0].message.content or "").strip().strip('"“”').strip()
        return text[:400] if text and len(text) <= 400 else None
    except Exception as e:  # noqa: BLE001
        print(f"  HyDE 生成失败: {e}")
        return None


async def main() -> None:
    golden = json.loads((project_root / "evals" / "golden_set.json").read_text(encoding="utf-8"))
    cases = [c for c in golden["cases"] if c["type"] in ("fact", "cross")]
    print(f"📋 HyDE 评测 | fact/cross 题: {len(cases)}（refusal 不参与）")

    retriever, bm25_index, _merger = initialize_components()

    results = []
    for i, case in enumerate(cases):
        q = case["question"]
        t0 = time.perf_counter()
        base_docs = await search_documents(q, retriever, top_k=8, bm25_index=bm25_index)
        hyde = hyde_generate(q)
        if hyde:
            hyde_docs = await search_documents(hyde, retriever, top_k=8, bm25_index=bm25_index)
            fused = _rrf_fuse(base_docs, hyde_docs, 8)
        else:
            fused = base_docs
        latency = time.perf_counter() - t0

        retrieved = [d["metadata"].get("source_file", "") for d in fused]
        expected = case.get("expected_docs", [])
        first_rank = next((r for r, s in enumerate(retrieved, 1) if s in expected), None)
        results.append({
            "id": case["id"], "question": q, "expected": expected,
            "retrieved": retrieved[:5], "first_rank": first_rank,
            "mrr": round(1.0 / first_rank, 3) if first_rank else 0.0,
            "hit1": first_rank == 1, "hit3": first_rank is not None and first_rank <= 3,
            "latency_s": round(latency, 1), "hyde_len": len(hyde) if hyde else 0,
        })
        mark = "✓" if first_rank == 1 else ("△" if first_rank else "✗")
        print(f"  [{case['id']}] {mark} rank={first_rank} {latency:.1f}s {q[:26]}")
        await asyncio.sleep(1)

    n = len(results)
    r1 = sum(r["hit1"] for r in results) / n
    r3 = sum(r["hit3"] for r in results) / n
    mrr = sum(r["mrr"] for r in results) / n
    print(f"\n📊 HyDE 轮: recall@1={r1:.3f} recall@3={r3:.3f} MRR={mrr:.3f}")
    print("   （基线 C1400: recall@1=0.690 recall@3=0.976 MRR=0.815）")

    out = project_root / "evals" / "reports" / f"hyde_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({"summary": {"recall@1": r1, "recall@3": r3, "MRR": mrr}, "cases": results},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 {out}")


if __name__ == "__main__":
    asyncio.run(main())
