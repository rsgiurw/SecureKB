#!/usr/bin/env python3
"""[MOD] 企业制度语料入库脚本：把自建知识库文档（Markdown）加载进 Qdrant。

用法（在项目根目录，需配置 ZHIPU_API_KEY 环境变量）：
    python scripts/load_kb.py --kb-dir D:/AIProjects/rag/data/raw_docs

与 easy_start/load_data.py 的区别：语料来源是我们的企业制度文档（而非内置示例 FAQ），
嵌入走智谱 embedding-3（OpenAI 兼容端点），向量库为 Qdrant 本地嵌入式模式。
"""

import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def build_docs(kb_dir: Path) -> list[dict]:
    """读取目录下所有 .md，转成 easy_start 兼容的文档格式。"""
    raw_docs = []
    for i, f in enumerate(sorted(kb_dir.glob("*.md")), 1):
        text = f.read_text(encoding="utf-8")
        if not text.strip():
            continue
        raw_docs.append({
            "id": f"kb-{i:03d}",
            "title": f.stem,
            "content": text,
            "metadata": {"category": "公司制度", "source": "company_kb"},
        })
    return raw_docs


async def main(kb_dir: Path, reset: bool) -> None:
    from easy_start.load_data import (
        load_to_chroma,
        prepare_documents,
        reset_chroma_collection,
    )

    raw_docs = build_docs(kb_dir)
    if not raw_docs:
        print(f"❌ {kb_dir} 下没有找到 .md 文档")
        sys.exit(1)
    print(f"📄 读取 {len(raw_docs)} 篇制度文档")

    docs = prepare_documents(raw_docs)

    api_key = os.getenv("ZHIPU_API_KEY")
    if not api_key:
        print("❌ 缺少 ZHIPU_API_KEY 环境变量")
        sys.exit(1)
    from app.modules.core.embedding.openai_embedder import OpenAIEmbedder

    embedder = OpenAIEmbedder(
        openai_api_key=api_key,
        model_name=os.getenv("ZHIPU_EMBED_MODEL", "embedding-3"),
        output_dimensionality=1024,
        openai_base_url=os.getenv("ZHIPU_API_URL", "https://open.bigmodel.cn/api/paas/v4/"),
    )

    texts = [d["content"] for d in docs]
    print("🔢 生成嵌入（智谱 embedding-3）...")
    embeddings = embedder.embed_documents(texts)
    print(f"✅ {len(embeddings)} 个向量（维度 {len(embeddings[0])}）")

    if reset:
        print("🗑️ 清空旧集合...")
        reset_chroma_collection()
    count = await load_to_chroma(docs, embeddings)
    print(f"✅ 入库完成：{count} 条 → easy_start/.qdrant_data")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="企业制度语料入库")
    parser.add_argument(
        "--kb-dir",
        default="D:/AIProjects/rag/data/raw_docs",
        help="制度文档目录（.md）",
    )
    parser.add_argument("--reset", action="store_true", help="先清空旧集合")
    args = parser.parse_args()
    asyncio.run(main(Path(args.kb_dir), args.reset))
