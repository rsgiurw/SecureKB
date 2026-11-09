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
    from easy_start.load_data import COLLECTION_NAME, prepare_documents

    raw_docs = build_docs(kb_dir)
    if not raw_docs:
        print(f"❌ {kb_dir} 下没有找到 .md 文档")
        sys.exit(1)
    print(f"📄 读取 {len(raw_docs)} 篇制度文档")

    docs = prepare_documents(raw_docs)

    # [FIX] 先物理清空存储（廉价且确定），再调嵌入 API——避免 API 失败时残留旧数据
    import shutil

    persist_dir = project_root / "easy_start" / ".qdrant_data"
    if reset and persist_dir.exists():
        print("🗑️ 物理删除向量存储目录...")
        shutil.rmtree(persist_dir, ignore_errors=True)

    api_key = os.getenv("ZHIPU_API_KEY")
    if not api_key:
        print("❌ 缺少 ZHIPU_API_KEY 环境变量")
        sys.exit(1)
    from app.modules.core.embedding.openai_embedder import OpenAIEmbedder
    from qdrant_client.models import Distance, VectorParams

    from app.infrastructure.storage.vector.qdrant_store import QdrantVectorStore

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

    # [FIX] qdrant 本地模式的 delete_collection 存在旧点复活问题，
    # 改为物理删除整个存储目录（上面已执行），这里直接重建集合。
    store = QdrantVectorStore(
        path=str(persist_dir),
        collection_name=COLLECTION_NAME,
    )
    client = store._ensure_client()
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(
            size=len(embeddings[0]) if embeddings else 1024,
            distance=Distance.COSINE,
        ),
    )

    qdrant_docs = [
        {
            "id": d["id"],
            "embedding": v,
            "content": d["content"],
            "metadata": d["metadata"],
        }
        for d, v in zip(docs, embeddings, strict=True)
    ]
    count = await store.add_documents(collection=COLLECTION_NAME, documents=qdrant_docs)

    total = client.count(COLLECTION_NAME).count
    print(f"✅ 入库完成：本次 {count} 条，集合共 {total} 条（应为 {len(docs)}）")


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
