"""[MOD] 轻量 Web Demo：企业知识库问答可视化界面（单文件页面 + FastAPI）。

启动（项目根目录）：
    .venv/Scripts/python web_demo.py
或双击 demo.bat，然后浏览器打开 http://127.0.0.1:8000

设计说明：
  - 复用 easy_start 已验证的检索/生成链路（Qdrant Dense + BM25 RRF + 智谱 GLM）
  - 身份（角色×部门）与应答模式作为【请求参数】传入，而非进程级环境变量——
    不同浏览器标签页可以是不同身份，天然支持权限对比演示
  - 演示层身份为客户端自报（无鉴权），真实 JWT 版列入改造②完整版
"""

import json
import os
import sys
from pathlib import Path

root = Path(__file__).parent
sys.path.insert(0, str(root))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(root / ".env")
os.environ.setdefault("EASY_START_LANG", "zh")

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from easy_start.chat import generate_answer, initialize_components, search_documents  # noqa: E402

app = FastAPI(title="企业知识库问答 Demo", version="0.1.0")

DEPTS = ["全员", "技术部", "法务部", "行政部"]
MODES = {"strict": "严谨", "friendly": "通俗"}

# 进程启动时初始化一次检索组件（嵌入客户端 + Qdrant + BM25）
retriever, bm25_index, _ = initialize_components()


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    role: str = Field(default="employee", description="admin | employee")
    dept: str = Field(default="全员", description="员工部门")
    mode: str = Field(default="strict", description="strict | friendly")
    top_k: int = Field(default=3, ge=1, le=10)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(root / "web" / "demo.html")


@app.get("/api/meta")
async def meta():
    mode = "strict"
    cfg = root / "easy_start" / ".kb_config.json"
    if cfg.exists():
        try:
            mode = json.loads(cfg.read_text(encoding="utf-8")).get("answer_mode", "strict")
        except (ValueError, OSError):
            pass
    try:
        # [FIX] 复用启动时的 store client（本地模式同一进程只能开一个实例）
        doc_count = retriever.store._ensure_client().count("documents").count
    except Exception:
        doc_count = None
    return {"depts": DEPTS, "modes": MODES, "current_mode": mode, "doc_count": doc_count}


@app.post("/api/chat")
async def chat(req: ChatRequest):
    import json

    # Security Trimming：检索级部门过滤（员工可见"全员"+本部门；管理员无过滤）
    if req.role == "admin":
        filters = None
    else:
        departments = ["全员"] if req.dept == "全员" else ["全员", req.dept]
        filters = {"departments": departments}

    # 应答模式：请求级设置（演示层直接写环境变量；单用户场景无并发竞争问题）
    os.environ["KB_ANSWER_MODE"] = req.mode if req.mode in MODES else "strict"

    # [FIX] 改造②：检索级部门过滤必须按请求应用（不能走全局变量，否则身份过滤失效）
    # [MOD] 改造⑤：过滤后的 Dense 结果 + BM25 结果做 RRF 融合（复用 chat 的融合器）
    from easy_start.chat import _rrf_fuse

    search_results = await retriever.search(query=req.question, top_k=req.top_k, filters=filters)
    dense_docs = [
        {
            "content": getattr(sr, "content", ""),
            "score": getattr(sr, "score", 0.0),
            "source": getattr(sr, "id", ""),
            "metadata": getattr(sr, "metadata", {}),
        }
        for sr in search_results
    ]
    if bm25_index is not None:
        bm25_docs = [
            {"content": d.get("content", ""), "source": d.get("id", ""), "metadata": d.get("metadata", {})}
            for d in bm25_index.search(req.question, top_k=req.top_k)
        ]
        # [FIX] Security Trimming 同样覆盖 BM25 路（dense 与 bm25 每条召回路都要过滤）
        if filters and "departments" in filters:
            allowed = set(filters["departments"])
            bm25_docs = [
                d for d in bm25_docs
                if set(d["metadata"].get("departments", ["全员"])) & allowed
            ]
        docs = _rrf_fuse(dense_docs, bm25_docs, req.top_k)
    else:
        docs = dense_docs
    answer = await generate_answer(req.question, docs)

    return {
        "answer": answer,
        "mode": os.environ["KB_ANSWER_MODE"],
        "visible_as": f"{req.role}:{req.dept}",
        "citations": [
            {
                "source": d["metadata"].get("source_file", d.get("source", "")),
                "score": d["score"],
                "text": d["content"][:80],
            }
            for d in docs
        ],
    }


if __name__ == "__main__":
    import uvicorn

    print("🚀 Web Demo 启动：http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
