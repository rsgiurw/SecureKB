#!/usr/bin/env python3
"""[MOD] 改造④ 检索评测运行器：Golden 集 → recall@k / MRR / 检索延迟。

与上游 scripts/run_eval.py 的分工：上游脚本评"生成质量"（faithfulness/relevance，
依赖 EvaluatorFactory），本脚本评"检索质量"（召回与排序）——两者互补，共同构成
质量门禁。检索评测不调用 LLM 生成，只消耗嵌入 API，成本低、速度快、可频繁回归。

用法（在项目根目录，需 .env 配置 ZHIPU_API_KEY）：
    .venv/Scripts/python scripts/run_retrieval_eval.py                       # 默认 evals/golden_set.json
    .venv/Scripts/python scripts/run_retrieval_eval.py --label dense-baseline

产出：
  1. 控制台逐题明细（未命中的标 ✗，方便定位 bad case）
  2. 汇总指标 recall@1/3/5、MRR、平均检索延迟
  3. 报告落盘 evals/reports/eval_<标签>_<时间戳>.json（含逐题结果，供对比实验引用）

评测口径：
  - 命中判定：expected_docs 中任意一篇出现在 top-k 即视为命中（any-match）
  - MRR：第一个相关文档排名倒数的均值
  - refusal 类型题不参与 recall/MRR，仅记录 top1 分数（供拒答阈值分析）
  - 评测以管理员视角运行（不启用部门过滤），衡量检索器本身的召回能力
"""

import argparse
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
os.environ.pop("KB_DEPARTMENTS", None)  # 评测以管理员视角，不启用部门过滤

from easy_start.chat import initialize_components, search_documents  # noqa: E402


async def eval_case(case: dict, retriever, max_k: int, bm25_index: any = None) -> dict:
    """评测单题：检索 + 命中判定 + 延迟统计。"""
    t0 = time.perf_counter()
    docs = await search_documents(case["question"], retriever, top_k=max_k, bm25_index=bm25_index)
    latency_ms = (time.perf_counter() - t0) * 1000

    retrieved = [d["metadata"].get("source_file", "") for d in docs]
    scores = [d["score"] for d in docs]
    expected = case.get("expected_docs", [])

    first_rank = None
    for rank, src in enumerate(retrieved, 1):
        if src in expected:
            first_rank = rank
            break

    return {
        "id": case["id"],
        "type": case["type"],
        "question": case["question"],
        "expected": expected,
        "retrieved": retrieved,
        "scores": scores,
        "first_rank": first_rank,
        "mrr": round(1.0 / first_rank, 3) if first_rank else 0.0,
        "latency_ms": round(latency_ms, 1),
        "top1_score": round(scores[0], 3) if scores else None,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Golden 集检索评测（recall@k / MRR）")
    parser.add_argument("--golden", default=str(project_root / "evals" / "golden_set.json"))
    parser.add_argument("--k", default="1,3,5", help="评估的截断位置，逗号分隔")
    parser.add_argument("--label", default="unnamed", help="本次评测的配置标签（写进报告）")
    args = parser.parse_args()

    k_list = sorted(int(k) for k in args.k.split(","))
    max_k = max(k_list)

    cases = json.loads(Path(args.golden).read_text(encoding="utf-8"))["cases"]
    print(f"📋 Golden 集：{len(cases)} 题 | 截断位：{k_list} | 配置标签：{args.label}\n")

    retriever, bm25_index, _ = initialize_components()

    results = []
    for case in cases:
        r = await eval_case(case, retriever, max_k, bm25_index=bm25_index)
        for k in k_list:
            r[f"hit@{k}"] = bool(r["first_rank"] and r["first_rank"] <= k)
        results.append(r)

        if case["type"] == "refusal":
            print(f"  ⚪ [{case['id']}] refusal | top1={r['top1_score']} | {case['question'][:24]}")
        elif r["first_rank"]:
            mark = "✅" if all(r[f"hit@{k}"] for k in k_list) else "⚠️ "
            print(f"  {mark} [{case['id']}] rank={r['first_rank']} | {case['question'][:24]}")
        else:
            print(f"  ✗ [{case['id']}] 未命中 | {case['question'][:24]} | 期望: {','.join(case.get('expected_docs', []))}")

    # 汇总（refusal 不参与 recall/MRR）
    evaluable = [r for r in results if r["type"] != "refusal"]
    n = len(evaluable)
    summary = {
        "label": args.label,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "golden_size": len(cases),
        "evaluable": n,
        "retriever": "QdrantRetriever dense_only alpha=1.0",
        "embedder": "zhipu embedding-3 dim=1024",
        **{f"recall@{k}": round(sum(1 for r in evaluable if r[f"hit@{k}"]) / n, 3) for k in k_list},
        "MRR": round(sum(r["mrr"] for r in evaluable) / n, 3),
        "avg_latency_ms": round(sum(r["latency_ms"] for r in evaluable) / n, 1),
        "refusal_top1_scores": [r["top1_score"] for r in results if r["type"] == "refusal"],
        "cases": results,
    }

    print("\n===== 汇总 =====")
    for k in k_list:
        print(f"  recall@{k}: {summary[f'recall@{k}']}")
    print(f"  MRR: {summary['MRR']}")
    print(f"  平均检索延迟: {summary['avg_latency_ms']} ms")

    report_dir = project_root / "evals" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"eval_{args.label}_{ts}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📄 报告已保存：{report_path}")


if __name__ == "__main__":
    asyncio.run(main())
