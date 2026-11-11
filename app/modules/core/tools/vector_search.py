# app/modules/core/tools/vector_search.py
"""
벡터 검색 도구

벡터 DB(Weaviate 등)에서 정보를 검색하는 도구들.
기존 WeaviateRetriever를 활용합니다.

도구 목록:
- search_weaviate: 하이브리드 검색
- get_document_by_id: UUID로 문서 조회
"""
from typing import Any

from ....lib.logger import get_logger

logger = get_logger(__name__)


async def search_weaviate(
    arguments: dict[str, Any],
    global_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Weaviate 벡터 DB에서 정보를 하이브리드 검색합니다.

    Dense 벡터 검색과 BM25 키워드 검색을 결합하여
    정확도 높은 검색 결과를 제공합니다.

    Args:
        arguments: 도구 인자
            - query (str): 검색 쿼리 (필수)
            - top_k (int): 반환할 결과 수 (기본값: 설정에 따름)
            - alpha (float): Dense:BM25 비율 (기본값: 0.6)
        global_config: 전역 설정 (retriever 접근용)

    Returns:
        list[dict]: 검색 결과 목록
            - content: 문서 내용
            - metadata: 메타데이터
            - score: 유사도 점수 (있는 경우)

    Raises:
        ValueError: 쿼리가 비어있거나 retriever가 설정되지 않은 경우
    """
    query = arguments.get("query", "")

    # 빈 쿼리 검증
    if not query or not query.strip():
        raise ValueError("query는 필수입니다")

    # Retriever 확인
    retriever = global_config.get("retriever")
    if retriever is None:
        raise ValueError("retriever가 설정되지 않았습니다")

    # 설정에서 파라미터 가져오기
    mcp_config = global_config.get("mcp", {})
    tool_config = mcp_config.get("tools", {}).get("search_weaviate", {})
    params = tool_config.get("parameters", {})

    default_top_k = params.get("default_top_k", 10)
    default_alpha = params.get("alpha", 0.6)

    top_k = arguments.get("top_k", default_top_k)
    alpha = arguments.get("alpha", default_alpha)

    # [MOD] Security Trimming（SecureKB）：服务端注入的部门过滤——经
    # global_config 副本传入（MCPServer.execute_tool），不在 LLM 生成的
    # arguments 通道内，无法被伪造或覆盖。员工仅检索"全员"+本部门文档。
    retrieval_filters = global_config.get("retrieval_filters")

    logger.info(f"🔍 search_weaviate: query='{query}', top_k={top_k}, alpha={alpha}")

    try:
        # 기존 WeaviateRetriever 사용
        search_results = await retriever.search(
            query=query,
            top_k=top_k,
            alpha=alpha,
            filters=retrieval_filters,
        )

        # 응답 형식으로 변환
        results = []
        for doc in search_results:
            # [MOD] 字段兼容：QdrantRetriever 返回 SearchResult（.content），
            # 旧 weaviate 链路返回 LangChain Document（.page_content）
            content = getattr(doc, "content", None) or getattr(doc, "page_content", "")
            result = {
                "content": content,
                "metadata": getattr(doc, "metadata", {}) or {},
            }

            # score가 있는 경우 포함
            if hasattr(doc, "score"):
                result["score"] = doc.score

            results.append(result)

        logger.info(f"✅ search_weaviate: {len(results)}개 결과")

        return results

    except Exception as e:
        logger.error(f"❌ search_weaviate 실패: {e}")
        raise


async def get_document_by_id(
    arguments: dict[str, Any],
    global_config: dict[str, Any],
) -> dict[str, Any] | None:
    """
    문서 ID(UUID)로 Weaviate에서 직접 조회합니다.

    정확한 문서 참조가 필요한 경우 사용합니다.

    Args:
        arguments: 도구 인자
            - document_id (str): Weaviate 문서 UUID (필수)
        global_config: 전역 설정

    Returns:
        dict | None: 문서 정보 또는 None
            - content: 문서 내용
            - metadata: 메타데이터

    Raises:
        ValueError: document_id가 없거나 retriever가 설정되지 않은 경우
    """
    document_id = arguments.get("document_id", "")

    if not document_id:
        raise ValueError("document_id는 필수입니다")

    retriever = global_config.get("retriever")
    if retriever is None:
        raise ValueError("retriever가 설정되지 않았습니다")

    logger.info(f"📄 get_document_by_id: id={document_id}")

    try:
        # get_by_id 메서드 호출
        if not hasattr(retriever, "get_by_id"):
            raise ValueError("retriever가 get_by_id를 지원하지 않습니다")

        doc = await retriever.get_by_id(document_id)

        if doc is None:
            logger.warning(f"문서 없음: {document_id}")
            return None

        result = {
            "content": doc.page_content,
            "metadata": doc.metadata,
        }

        logger.info("✅ get_document_by_id: 조회 성공")

        return result

    except Exception as e:
        logger.error(f"❌ get_document_by_id 실패: {e}")
        raise
