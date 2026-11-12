#!/usr/bin/env python3
"""[MOD] 企业制度语料入库脚本：把自建知识库文档（Markdown）加载进 Qdrant。

用法（在项目根目录，需配置 ZHIPU_API_KEY 环境变量）：
    python scripts/load_kb.py --kb-dir D:/AIProjects/rag/data/raw_docs

与 easy_start/load_data.py 的区别：语料来源是我们的企业制度文档（而非内置示例 FAQ），
嵌入走智谱 embedding-3（OpenAI 兼容端点），向量库为 Qdrant 本地嵌入式模式。
"""

import asyncio
from datetime import datetime
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


# [MOD] 改造②：部门级可见范围（Security Trimming 的数据侧）
# "全员" = 所有员工可见；其他部门标签 = 仅该部门及以上角色可见
DEPARTMENT_MAP: dict[str, list[str]] = {
    "员工考勤与请假制度": ["全员"],
    "招聘与入职管理制度": ["全员"],
    "薪酬福利制度": ["全员"],
    "会议管理规定": ["全员"],
    "差旅费报销细则": ["全员"],
    "信息安全管理制度": ["全员"],
    "大模型使用管理规范": ["全员"],
    "客户信息保护规范": ["全员"],
    "保密协议与竞业限制管理办法": ["全员"],
    "数据安全事件应急响应手册": ["全员"],
    "访客与外包人员管理办法": ["全员"],
    "AI产品研发流程规范": ["技术部"],
    "数据备份与恢复规程": ["技术部"],
    "网络安全管理规定": ["技术部"],
    "IT设备与账号管理办法": ["技术部", "行政部"],
    "合同审批流程管理办法": ["法务部"],
    "印章与证照管理办法": ["法务部", "行政部"],
}


def build_docs(kb_dir: Path) -> list[dict]:
    """读取目录下所有 .md，转成 easy_start 兼容的文档格式（附部门标签）。"""
    raw_docs = []
    for i, f in enumerate(sorted(kb_dir.glob("*.md")), 1):
        text = f.read_text(encoding="utf-8")
        if not text.strip():
            continue
        departments = DEPARTMENT_MAP.get(f.stem, ["全员"])  # 未登记的默认全员可见
        raw_docs.append({
            "id": f"kb-{i:03d}",
            "title": f.stem,
            "content": text,
            "metadata": {
                "category": "公司制度",
                "source": "company_kb",
                "departments": departments,  # [MOD] 列表字段，配合 MatchAny 过滤
            },
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
    # [FIX] prepare_documents 会重建 metadata 并丢弃自定义字段（departments 被洗掉），
    # 按 source_file（=文档标题）重新注入部门标签
    for d in docs:
        title = d["metadata"].get("source_file", "")
        d["metadata"]["departments"] = DEPARTMENT_MAP.get(title, ["全员"])
        d["metadata"]["source"] = "company_kb"

    # [MOD] 真实分块（EXP-015）：prepare_documents 是整篇一块（easy_start 无切分逻辑），
    # 此前 rag.yaml 的 chunk 配置在本链路从未生效。此处按 document_processing 段切分，
    # 每块前置文档标题保留检索上下文（跨块 BM25 命中靠标题词）。
    import yaml as _yaml
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    _cfg_path = project_root / "app" / "config" / "features" / "rag.yaml"
    _dp = _yaml.safe_load(_cfg_path.read_text(encoding="utf-8"))["document_processing"]
    _splitter = RecursiveCharacterTextSplitter(
        chunk_size=_dp["chunk_size"], chunk_overlap=_dp["chunk_overlap"]
    )
    _chunked = []
    for d in docs:
        _title = d["metadata"].get("source_file", "")
        # [FIX 用户报告] 文档管理列表/下载依赖的三类元数据此前缺失：
        # document_id（列表聚合键+下载定位键，稳定 hash 保证重入库不变）、
        # file_size（原文档字节数，列表"文件大小"列）、file_type/created_at。
        # 缺失后果：每块各算一篇文档（分块恒为 1）、文件大小恒 0、下载 404。
        import hashlib as _hashlib

        _doc_id = _hashlib.md5(_title.encode("utf-8")).hexdigest()
        _raw_text = d.get("content", "")
        _meta_common = {
            **d["metadata"],
            "document_id": _doc_id,
            "file_size": len(_raw_text.encode("utf-8")),
            "file_type": "md",
            "created_at": datetime.now().isoformat(),
        }
        _total_chunks = max(1, len(_splitter.split_text(_raw_text)))
        for _i, _piece in enumerate(_splitter.split_text(_raw_text)):
            _chunked.append(
                {
                    **d,
                    # [FIX 用户报告] 每块唯一 id：Qdrant point id = hash(顶层 id)，
                    # 同篇多块继承原文档 id 时 hash 冲突互相覆盖（22 写入剩 19、
                    # "分块都是 1"的根因）。id 形如 <document_id>-<块序>，稳定且唯一。
                    "id": f"{_doc_id}-{_i}",
                    "content": f"《{_title}》\n{_piece}",
                    "metadata": {**_meta_common, "chunk_index": _i, "chunk_total": _total_chunks},
                }
            )
    print(f"✂️ 分块：{len(docs)} 篇 → {len(_chunked)} 块（size={_dp['chunk_size']}, overlap={_dp['chunk_overlap']}）")
    docs = _chunked

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

    # [MOD] 改造⑤：构建中文 BM25 索引（jieba 分词），供 RRF 混合检索使用
    try:
        from easy_start.load_data import save_bm25_index
        from easy_start.zh_tokenizer import ChineseTokenizer

        try:
            from app.modules.core.retrieval.bm25.stopwords import StopwordFilter

            stopword_filter = StopwordFilter(use_defaults=True, enabled=True)
        except ImportError:
            stopword_filter = None

        from app.modules.core.retrieval.bm25_engine import BM25Index

        bm25_index = BM25Index(tokenizer=ChineseTokenizer(stopword_filter=stopword_filter))
        bm25_index.build(docs)
        save_bm25_index(bm25_index)
        print("✅ BM25 索引已构建（jieba 中文分词）")
    except ImportError as e:
        print(f"⚠️ BM25 跳过（缺依赖）：{e}")

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
