"""
Chat Service - 비즈니스 로직 레이어

Phase 3.2: chat.py에서 추출한 검증된 비즈니스 로직
기존 코드 기반: app/api/chat.py의 핵심 함수들

⚠️ 주의: 이 코드는 기존 검증된 로직을 재사용합니다.

## Service Layer의 역할
- 비즈니스 로직만 담당 (HTTP 요청/응답과 분리)
- 모듈 의존성 주입을 통한 테스트 가능성 확보
- RAG 파이프라인, 세션 처리, 통계 관리 등 핵심 기능 제공
"""

import asyncio
import re
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import datetime
from typing import Any, cast

from ...lib.errors import ErrorCode, SessionError
from ...lib.langfuse_client import langfuse_context, observe
from ...lib.logger import get_logger
from ...lib.metrics import CostTracker, PerformanceMetrics
from ...lib.topic_extractor import extract_topic
from ...lib.types import RAGResultDict, SessionInfoDict, SessionResult, StatsDict
from ...modules.core.privacy.cn_pii import ChinaPIIMasker
from ...security.input_guard import scan_input
from ...security.output_compliance import OutputComplianceEngine
from .secure_audit_service import get_secure_audit_service
from .blocked_response import DEFAULT_BLOCKED_ANSWER
from .rag_pipeline import RAGPipeline

# LangSmith 트레이싱 import
try:
    from langsmith import traceable

    LANGSMITH_AVAILABLE = True
except ImportError:
    LANGSMITH_AVAILABLE = False

    def _noop_traceable(*args: Any, **kwargs: Any) -> Callable[[Any], Any]:
        def decorator(func: Any) -> Any:
            return func

        return decorator

    traceable = _noop_traceable


logger = get_logger(__name__)

# [FIX 用户报告] 流式中断兜底保存的后台任务集（强引用防 GC，完成回调自动移除）
_STREAM_PERSIST_TASKS: set[asyncio.Task] = set()


# 스트리밍(SSE/WS)에서 검색 결과가 0건일 때 사용자에게 chunk로 안내하는 informational
# 메시지의 코드 내장 기본값(한국어). 에러가 아닌 안내성 문자열이라 ErrorCode 카탈로그를
# 적용하지 않는다. 운영자는 rag.yaml generation_fallback.no_documents_message로 코드 포크
# 없이 다른 언어/도메인 문구로 오버라이드할 수 있다 → 미설정 시 아래 기본값으로 회귀 0.
STREAM_NO_DOCUMENTS_MESSAGE = (
    "관련 문서를 찾을 수 없습니다. 문서를 업로드했는지 확인하거나 "
    "질문을 바꿔 다시 시도해주세요."
)


def _resolve_user_message(configured: Any, default: str) -> str:
    """사용자 노출 메시지를 config 우선으로 해소한다(미설정/공백이면 기본값).

    rag_pipeline._resolve_generation_fallback_message와 동일한 정책을 따른다:
    문자열이면서 앞뒤 공백 제거 후 비어있지 않을 때만 유효 오버라이드로 인정하고,
    그 외(None/공백/비문자열)는 코드 내장 기본값을 사용한다(회귀 0 보장).

    Args:
        configured: rag.yaml에서 읽은 원시 값(문자열/None/기타).
        default: 코드 내장 기본 문자열(회귀 0 보장용).

    Returns:
        유효한 문자열(앞뒤 공백만 있는 값은 무효로 보아 default 사용).
    """
    if isinstance(configured, str) and configured.strip():
        return configured
    return default


class ChatService:
    """
    채팅 비즈니스 로직 서비스

    역할:
    - RAG 파이프라인 실행
    - 세션 관리
    - 통계 수집
    - 컨텍스트 처리

    기존 코드 기반: app/api/chat.py의 함수들을 클래스로 재구성
    """

    # [MOD] 寒暄前置分流（2026-11-10）：问候/致谢/身份询问不需要检索知识库，
    # 直接应答且不附来源卡片。锚定正则——仅当整条消息只是寒暄词本身时才命中
    # （"你好"✓ / "你好，年假有几天"✗ 不会误伤真实问题）。比把中文关键词塞进
    # greeting 规则更安全：规则系统是子串匹配且语言体系只有 ko/en。
    _SMALL_TALK_PATTERN = re.compile(
        r"^\s*(?:"
        r"你好|您好|哈喽|嗨|嗨嗨|hello|hi|hey|在吗|"
        r"谢谢|多谢|感谢|thanks|thank you|"
        r"再见|拜拜|bye|"
        r"你是谁|你叫什么名字|你能做什么|你会什么|介绍一下自己"
        r")[！!。.？?~～，,\s]*$",
        re.IGNORECASE,
    )
    _SMALL_TALK_ANSWER = (
        "你好！我是 SecureKB 企业知识库助手。\n\n"
        "可以向我提问公司制度相关的问题，例如：考勤与请假、差旅报销、"
        "信息安全规范、印章使用、合同审批等。"
    )

    @staticmethod
    def _is_small_talk(message: str) -> bool:
        return bool(ChatService._SMALL_TALK_PATTERN.match(message or ""))

    # [MOD EXP-018] 澄清式对话（用户三层决策模型的中间层）：
    # 清晰+内部→答（正常检索）｜明确外部→拒答（EXP-016 拒答门）｜
    # 指代不明→追问澄清（本层）。触发条件（保守，避免误伤）：
    # ①消息为"动作+无先行词指代"结构（解析/总结/查看 + 这个/那份 + 文档类名词）
    # ②且会话无历史（新会话首条消息——多轮场景由 standalone rewrite 处理先行词）
    # 响应不列具体文档名：员工澄清响应若列出无权限文档名即越权泄露，
    # 通用追问让用户自行补充主题词，权限一致性最优。
    _AMBIGUOUS_DOC_PATTERN = re.compile(
        r"^\s*(?:帮我)?\s*"
        r"(?:解析|总结|概括|查看|打开|读取|分析|介绍一下?|看看|读一下)"
        r"\s*(?:一下)?\s*(?:这|那|该)(?:个|份|篇|则)?"
        r"(?:文档|文件|资料|内容|规定|制度|手册|材料)"
        r"[的，,。.！!？?\s]*"
        r"(?:重点|要点|核心|内容|摘要)?"
        r"\s*[吧呢吗？?！!。.]*\s*$",
        re.IGNORECASE,
    )
    _CLARIFICATION_ANSWER = (
        "您提到的「文档」我不太确定具体指哪一份。请告诉我文档的名称或主题"
        "（例如：考勤与请假制度、信息安全规范、差旅报销细则），"
        "我会为您检索并总结对应的内容。"
    )

    async def _clarification_answer_if_needed(
        self, message: str, session_id: str
    ) -> str | None:
        """[MOD EXP-018] 命中模糊指代且会话无历史 → 返回澄清话术；否则 None。

        "无历史"用后端会话消息数判定（新会话首条消息 = 指代无先行词可着落）；
        多轮场景交给 standalone rewrite。查询失败按无历史处理（保守澄清）。
        """
        if not self._AMBIGUOUS_DOC_PATTERN.match(message or ""):
            return None
        try:
            session_module = self.modules.get("session")
            if session_module:
                history = await session_module.get_chat_history(session_id)
                msgs = history.get("messages", []) if isinstance(history, dict) else []
                if msgs:
                    return None  # 已有对话 → 指代可能有着落，不澄清
        except Exception:  # noqa: BLE001 - 会话查询失败按无历史处理
            pass
        return self._CLARIFICATION_ANSWER

    # [MOD] 提示注入拒绝话术（InputGuard block 级处置）
    _INJECTION_REFUSAL_ANSWER = (
        "您的请求已被安全防护拦截（检测到提示注入特征）。"
        "请调整提问内容后重试；如有疑问请联系管理员。"
    )

    @staticmethod
    def _audit(**kwargs: Any):
        """安全/行为审计记录入口（non-blocking——审计失败不阻断业务）。"""
        return get_secure_audit_service().record(**kwargs)

    @staticmethod
    def _source_display_name(source: Any) -> str:
        """来源标题：dict（SSE metadata/done）与 Source 对象（REST）双形态兼容。"""
        if isinstance(source, dict):
            return str(source.get("document") or source.get("document_name") or "").strip()
        return str(
            getattr(source, "document", None)
            or getattr(source, "document_name", None)
            or ""
        ).strip()

    @staticmethod
    def _title_in_answer(name: str, answer: str) -> bool:
        """文档名是否出现在回答中。LLM 常省略'员工/公司/企业'等通用前缀
        （'员工考勤与请假制度'→'考勤与请假制度'），生成前缀剥离变体再匹配。"""
        if not name:
            return False
        variants = {name}
        for prefix in ("员工", "公司", "企业"):
            if name.startswith(prefix) and len(name) - len(prefix) >= 4:
                variants.add(name[len(prefix):])
        return any(v in answer for v in variants)

    @classmethod
    def _filter_sources_by_answer(
        cls, sources: list[Any], answer: str
    ) -> list[Any]:
        """[MOD] 来源引用过滤（2026-11-10）：只展示回答中实际提及的文档。

        检索 top_k 返回的是"生成候选"，不是"实际引用"——年假问题的 8 篇候选里
        真正被回答使用的往往只有考勤制度一篇。轻量启发式：LLM 的提示词风格
        倾向于在回答中点名文档（"根据《员工考勤与请假制度》…"），因此文档名
        （含前缀剥离变体）出现在回答文本中即视为被引用。
        兜底（2026-11-12 收紧）：回答未点名任何文档时保留 top-1 而非前 3——
        无引用证据时"前 3 篇候选"会让用户误读为"这 3 篇都被回答使用"
        （实际案例：数据分级问题的回答只用了管理制度的四级分类，来源却展示
        3 篇——其中总纲是同内容冗余召回、应急手册是"事件分级"近似语义召回）。
        top-1 是无引用追踪下对"回答最可能基于的文档"的最诚实近似。
        """
        if not sources or not answer:
            return sources
        cited: list[Any] = []
        for source in sources:
            name = cls._source_display_name(source)
            if cls._title_in_answer(name, answer):
                cited.append(source)
        return cited if cited else sources[:1]

    def __init__(self, modules: dict[str, Any], config: dict[str, Any]):
        """
        Args:
            modules: 애플리케이션 모듈 딕셔너리 (DI)
            config: 설정 딕셔너리
        """
        self.modules = modules
        self.config = config

        # [MOD] 中国 PII 入口清洗（SecureKB 安全模块，2026-11-10）。
        # 开关读 privacy.enabled，但语义为"默认开启"：SecureKB 场景下 PII 掩码
        # 是安全基线，config 缺失/显式 false 都不关闭——只有显式传 False 才关。
        privacy_config = config.get("privacy") or {}
        self._pii_masking_enabled = privacy_config.get("enabled") is not False
        self._pii_masker = ChinaPIIMasker()
        # [MOD] 输出合规规则引擎（安全模块：整合 PII 掩码/提示词泄漏/密钥格式/
        # 注入回声四类规则，block/mask 分级处置）
        self._output_engine = OutputComplianceEngine(pii_masker=self._pii_masker)

        # 통계 정보
        self.stats = {
            "total_chats": 0,
            "total_tokens": 0,
            "average_latency": 0.0,
            "error_rate": 0.0,
            "errors": 0,
        }

        # RAGPipeline 인스턴스 생성 (의존성 주입)
        self.rag_pipeline = RAGPipeline(
            config=config,
            query_router=modules.get("query_router"),
            query_expansion=modules.get("query_expansion"),
            retrieval_module=modules.get("retrieval"),
            generation_module=modules.get("generation"),
            session_module=modules.get("session"),
            self_rag_module=modules.get("self_rag"),  # ✅ Self-RAG 모듈 주입
            extract_topic_func=self.extract_topic,
            circuit_breaker_factory=modules.get(
                "circuit_breaker_factory"
            ),  # ✅ Circuit Breaker Factory 주입
            cost_tracker=modules.get("cost_tracker") or CostTracker(),  # ✅ 비용 추적기 주입
            performance_metrics=modules.get("performance_metrics")
            or PerformanceMetrics(),  # ✅ 성능 메트릭 주입
            sql_search_service=modules.get(
                "sql_search_service"
            ),  # ✅ SQL Search Service 주입 (Phase 3)
            llm_factory=modules.get("llm_factory"),  # ✅ standalone rewrite용 LLM Factory
            # Phase 2.6: Grok answer 모드 / Agentic RAG 의존성 전달
            # (미전달 시 grok answer는 GROK_003 에러, use_agent는 일반 RAG로 강등)
            grok_answer_provider=modules.get("grok_answer_provider"),
            agent_orchestrator=modules.get("agent_orchestrator"),
        )

        # GAP #5: 서버사이드 SSE 청크 페이싱 설정.
        # 연속 LLM chunk burst가 SSE에 한꺼번에 flush되면 프론트 타이프라이터
        # 애니메이션의 평활도가 깨진다. 최소 간격(초)을 두어 소스 레벨에서 토큰
        # 방출을 평탄화한다. 기본 0.0=무동작(opt-in, 회귀 0).
        streaming_config = config.get("rag", {}).get("streaming", {})
        self.stream_chunk_min_interval_seconds = self._coerce_non_negative_float(
            streaming_config.get("chunk_min_interval_seconds"),
            default=0.0,
        )

        # 검색 0건 안내 메시지 외부화: rag.generation_fallback.no_documents_message로
        # 코드 포크 없이 다른 언어/도메인 문구로 교체할 수 있다. null/공백/미설정이면
        # 코드 내장 한국어 기본값(STREAM_NO_DOCUMENTS_MESSAGE)을 사용한다 → 회귀 0.
        rag_config = config.get("rag", {})
        generation_fallback_config = rag_config.get("generation_fallback", {})
        if not isinstance(generation_fallback_config, dict):
            generation_fallback_config = {}
        self.stream_no_documents_message: str = _resolve_user_message(
            generation_fallback_config.get("no_documents_message"),
            STREAM_NO_DOCUMENTS_MESSAGE,
        )

        logger.info("ChatService 초기화 완료 (RAGPipeline + Self-RAG + SQL Search 포함)")

    @staticmethod
    def _coerce_non_negative_float(value: Any, *, default: float) -> float:
        """설정값을 0 이상 float로 정규화한다(음수/비정상 값은 default).

        Args:
            value: YAML에서 읽은 원본 설정값(숫자/문자열/None 가능)
            default: 변환 실패 또는 None일 때 사용할 기본값

        Returns:
            0.0 이상으로 보정된 float
        """
        try:
            if value is None:
                return default
            coerced = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, coerced)

    async def _sleep_for_stream_pacing(self, delay_seconds: float) -> None:
        """스트리밍 청크 페이싱 전용 sleep 훅(테스트에서 교체 가능).

        Args:
            delay_seconds: 대기할 시간(초)
        """
        await asyncio.sleep(delay_seconds)

    async def _pace_stream_chunk(self, last_sent_at: float | None) -> float:
        """연속 chunk burst가 SSE에 한 번에 flush되지 않도록 최소 간격을 둔다(GAP #5).

        간격(chunk_min_interval_seconds)이 0 이하이거나 첫 청크(last_sent_at=None)면
        sleep 없이 현재 시각만 반환한다(무동작). 그 외에는 직전 전송 이후 경과
        시간을 빼고 남은 만큼만 sleep 해, 이미 느린 청크는 추가 지연 없이 통과시킨다.

        Args:
            last_sent_at: 직전 청크 전송 시각(time.monotonic 기준, 첫 청크는 None)

        Returns:
            이번 청크 전송 직후의 time.monotonic 값(다음 호출의 기준점)
        """
        interval = self.stream_chunk_min_interval_seconds
        if interval <= 0 or last_sent_at is None:
            return time.monotonic()

        elapsed = time.monotonic() - last_sent_at
        delay = interval - elapsed
        if delay > 0:
            await self._sleep_for_stream_pacing(delay)
        return time.monotonic()

    async def handle_session(
        self, session_id: str | None, context: dict[str, Any]
    ) -> SessionResult:
        """
        세션 처리 - 기존 세션 검증 또는 새 세션 생성

        기존 코드: chat.py의 handle_session() 함수 (L235-298)

        Args:
            session_id: 요청된 세션 ID (None이면 새로 생성)
            context: 요청 컨텍스트 (IP, User-Agent 등)

        Returns:
            세션 처리 결과 딕셔너리
        """
        try:
            session_module = self.modules.get("session")
            if not session_module:
                return {"success": False, "message": "Session module not available"}

            logger.debug(f"🔍 세션 요청 - 요청받은 session_id: {session_id}")

            if session_id:
                # 기존 세션 조회
                logger.debug(f"기존 세션 조회 시도: {session_id}")
                session_result = await session_module.get_session(session_id, context)

                if session_result.get("is_valid"):
                    logger.debug(f"✅ 세션 유효함 - session_id: {session_id}")
                    return {
                        "success": True,
                        "session_id": session_id,
                        "is_new": False,
                        "validation_result": session_result,
                    }
                else:
                    logger.warning(
                        f"세션 만료/없음: {session_id}, "
                        f"이유: {session_result.get('reason', 'unknown')}"
                    )

            # 새 세션 생성
            logger.debug(f"새 세션 생성 중... (기존 세션: {session_id})")
            new_session = await session_module.create_session(
                {"metadata": context}, session_id=session_id
            )
            new_session_id = new_session["session_id"]

            logger.debug(f"✅ 새 세션 생성 완료 - session_id: {new_session_id}")

            return {
                "success": True,
                "session_id": new_session_id,
                "is_new": True,
                "message": "새 대화 세션이 시작되었습니다.",
            }

        except KeyError as e:
            # 세션 모듈 초기화 안 됨 또는 필수 키 누락
            # RAGException 시그니처는 (error_code, **context)이므로 message=/
            # original_error= 키워드는 데드 인자(context dict에 흡수, 렌더 안 됨)였다.
            # 사용자 노출 메시지는 양언어 카탈로그(SESSION-002)가 렌더하고,
            # 디버깅 정보는 context로 전달한다(데드 한국어 문자열 제거).
            logger.error(f"Session handling error - missing key: {e}", exc_info=True)
            raise SessionError(
                ErrorCode.SESSION_MODULE_NOT_AVAILABLE,
                missing_key=str(e),
            ) from e
        except Exception as e:
            # 예상치 못한 세션 처리 에러
            # 위와 동일: 데드 message=/original_error= 제거, context는 키워드로 전달.
            logger.error(f"Session handling error: {e}", exc_info=True)
            raise SessionError(
                ErrorCode.SESSION_CREATE_FAILED,
                session_id=session_id,
                context=context,
            ) from e

    def extract_topic(self, message: str) -> str:
        """토픽 추출 (키워드 기반).

        lib의 단일 소스 함수(topic_extractor.extract_topic)에 위임한다(DRY).
        토픽 키워드는 config(routing.topic_keywords)로 외부화되며, 미설정 시
        코드 내장 한국어 기본 맵을 사용한다(회귀 0). 토픽은 세션 메타(cosmetic)
        라벨이라 검색/라우팅 동작에는 영향을 주지 않는다.
        """
        topic_keywords = self.config.get("routing", {}).get("topic_keywords")
        return str(extract_topic(message, topic_keywords))

    @traceable(
        name="RAGPipeline",
        tags=["chat", "rag", "pipeline"],
        metadata={"module": "chat_service", "version": "3.0.0"},
    )
    async def execute_rag_pipeline(
        self, message: str, session_id: str, options: dict[str, Any] | None = None
    ) -> RAGResultDict:
        """
        RAG 파이프라인 실행

        Phase 2 개선: 150줄 블랙박스 → RAGPipeline.execute() 단일 호출
        - 8개 독립 단계로 분해된 파이프라인 사용
        - 단계별 성능 추적 (PipelineTracker)
        - Circuit Breaker, Graceful Degradation 패턴 적용

        Args:
            message: 사용자 메시지
            session_id: 세션 ID
            options: 추가 옵션 (limit, min_score, top_n 등)

        Returns:
            RAG 파이프라인 실행 결과:
            {
                "answer": str,
                "sources": List[Source],
                "tokens_used": int,
                "topic": str,
                "processing_time": float,
                "search_results": int,
                "ranked_results": int,
                "model_info": Dict[str, Any],
                "routing_metadata": Optional[Dict[str, Any]],
                "performance_metrics": Dict[str, Any]  # NEW: PipelineTracker 메트릭
            }
        """
        logger.debug(
            "RAG Pipeline Starting (Phase 2 Refactored)",
            message_preview=message[:50],
            session_id=session_id,
        )

        # [MOD] PII 入口清洗（与流式路径同策略）：进入检索/存储/日志前统一掩码
        if self._pii_masking_enabled:
            pii_detection = self._pii_masker.mask_text_detailed(message)
            if pii_detection.total_masked > 0:
                logger.info(
                    "用户消息检出 PII，已入口掩码",
                    extra={"session_id": session_id, "message_preview": message[:20]},
                )
                self._audit(
                    category="security",
                    action="pii.masked",
                    session_id=session_id,
                    result="masked",
                    detail={
                        "phone": pii_detection.phone_count,
                        "id_card": pii_detection.id_card_count,
                        "bank_card": pii_detection.bank_card_count,
                        "email": pii_detection.email_count,
                        "validation_blocked": pii_detection.skipped_by_validation,
                    },
                )
                message = pii_detection.masked

        # [MOD] 寒暄前置分流：问候/致谢跳过检索，零来源直接应答（与流式路径同策略）
        if self._is_small_talk(message):
            logger.info(
                "寒暄命中，跳过检索直接应答",
                extra={"session_id": session_id, "message_preview": message[:30]},
            )
            return cast(
                RAGResultDict,
                {
                    "answer": self._SMALL_TALK_ANSWER,
                    "sources": [],
                    "tokens_used": 0,
                    "topic": "small_talk",
                    "processing_time": 0.0,
                    "search_results": 0,
                    "ranked_results": 0,
                    "model_info": {
                        "provider": "internal",
                        "model": "rule-based",
                        "streaming": False,
                    },
                    "routing_metadata": {"route": "small_talk"},
                    "performance_metrics": None,
                },
            )

        # [MOD EXP-018] 澄清式对话：指代不明（无先行词的"解析这个文档"类）→
        # 追问澄清而非盲目检索。位于寒暄之后、注入扫描之前。
        clarification_answer = await self._clarification_answer_if_needed(message, session_id)
        if clarification_answer:
            logger.info(
                "澄清命中：指代不明，返回追问",
                extra={"session_id": session_id, "message_preview": message[:30]},
            )
            return cast(
                RAGResultDict,
                {
                    "answer": clarification_answer,
                    "sources": [],
                    "tokens_used": 0,
                    "topic": "clarification",
                    "processing_time": 0.0,
                    "search_results": 0,
                    "ranked_results": 0,
                    "model_info": {
                        "provider": "internal",
                        "model": "rule-based",
                        "streaming": False,
                    },
                    "routing_metadata": {"route": "clarification"},
                    "performance_metrics": None,
                },
            )

        # [MOD] 提示注入防护接线（与流式路径同策略）：block 拒绝+审计，warn 剥离
        guard_result = scan_input(message)
        if guard_result.level == "block":
            self._audit(
                category="security",
                action="injection.blocked",
                session_id=session_id,
                result="blocked",
                detail={
                    "hits": [h.category for h in guard_result.hits],
                    "total_weight": guard_result.total_weight,
                    "matched": [h.matched for h in guard_result.hits],
                },
            )
            return cast(
                RAGResultDict,
                {
                    "answer": self._INJECTION_REFUSAL_ANSWER,
                    "sources": [],
                    "tokens_used": 0,
                    "topic": "injection_blocked",
                    "processing_time": 0.0,
                    "search_results": 0,
                    "ranked_results": 0,
                    "model_info": {"provider": "internal", "model": "input_guard"},
                    "routing_metadata": {"route": "injection_blocked"},
                    "performance_metrics": None,
                },
            )
        if guard_result.level == "warn":
            self._audit(
                category="security",
                action="injection.warn",
                session_id=session_id,
                result="masked",
                detail={
                    "hits": [h.category for h in guard_result.hits],
                    "total_weight": guard_result.total_weight,
                },
            )
            message = guard_result.cleaned

        # RAGPipeline.execute() 단일 호출 (8단계 오케스트레이션)
        result = await self.rag_pipeline.execute(
            message=message, session_id=session_id, options=options
        )
        # [MOD] 来源引用过滤：与非流式 done 事件同策略（实际提及的文档优先）
        if result.get("answer"):
            result["sources"] = self._filter_sources_by_answer(
                result.get("sources") or [], result["answer"]
            )
            # [MOD] 输出合规规则引擎（纵深防御最后一层）：PII 掩码 + 提示词泄漏
            # /密钥格式/注入回声四类规则，block/mask 分级处置；violations 入审计。
            # （流式路径 chunk 已渐进发射无法事后处置，其防线是入口清洗——已知限制）
            if self._pii_masking_enabled:
                compliance = self._output_engine.check(result["answer"])
                if compliance.violations:
                    self._audit(
                        category="security",
                        action="output.compliance",
                        session_id=session_id,
                        result="blocked" if compliance.blocked else "masked",
                        detail={
                            "violations": [
                                {"rule": v.rule_id, "severity": v.severity}
                                for v in compliance.violations
                            ],
                        },
                    )
                result["answer"] = compliance.final
        return cast(RAGResultDict, result)

    async def add_conversation_to_session(
        self, session_id: str, user_message: str, assistant_answer: str, metadata: dict[str, Any]
    ) -> None:
        """
        세션에 대화 기록 추가

        Args:
            session_id: 세션 ID
            user_message: 사용자 메시지
            assistant_answer: 어시스턴트 응답
            metadata: 추가 메타데이터
        """
        session_module = self.modules.get("session")
        if session_module:
            logger.debug(f"대화 추가: session_id={session_id}")
            await session_module.add_conversation(
                session_id, user_message, assistant_answer, metadata
            )
            # [MOD] 对话写入后持久化会话快照（会话元数据 + 内存消息）；
            # 消息全文由 chat_store（FileChatStore）在同一调用链内落库。
            persist_snapshot = getattr(session_module, "persist_snapshot", None)
            if persist_snapshot:
                persist_snapshot()

    def update_stats(self, data: dict[str, Any]) -> None:
        """
        통계 업데이트

        기존 코드: chat.py의 update_stats() 함수 (L161-179)
        """
        self.stats["total_chats"] += 1

        if data.get("success"):
            if data.get("tokens_used"):
                self.stats["total_tokens"] += data["tokens_used"]

            if data.get("latency"):
                current_avg = self.stats["average_latency"]
                chat_count = self.stats["total_chats"]
                self.stats["average_latency"] = (
                    current_avg * (chat_count - 1) + data["latency"]
                ) / chat_count
        else:
            self.stats["errors"] += 1
            self.stats["error_rate"] = (self.stats["errors"] / self.stats["total_chats"]) * 100

    def get_stats(self) -> StatsDict:
        """현재 통계 반환"""
        return cast(StatsDict, dict(self.stats))

    async def get_session_info(self, session_id: str) -> SessionInfoDict:
        """
        세션 상세 정보 조회

        Returns:
            세션 정보 딕셔너리 (message_count, tokens_used, processing_time 등)
        """
        session_module = self.modules.get("session")
        if not session_module:
            raise Exception("Session module not available")

        # 세션 존재 확인
        session_result = await session_module.get_session(session_id, {})
        if not session_result.get("is_valid"):
            raise Exception("Session not found")

        # 채팅 히스토리에서 통계 추출
        history = await session_module.get_chat_history(session_id)
        messages = history.get("messages", [])

        # 통계 계산
        message_count = len(messages)
        total_tokens = 0
        total_processing_time = 0
        latest_model_info = None

        for message in messages:
            if message.get("type") == "assistant":
                if "tokens_used" in message:
                    total_tokens += message["tokens_used"]
                if "processing_time" in message:
                    total_processing_time += message["processing_time"]
                if "model_info" in message:
                    latest_model_info = message["model_info"]

        return {
            "session_id": session_id,
            "message_count": message_count,
            "tokens_used": total_tokens,
            "processing_time": total_processing_time,
            "model_info": latest_model_info,
            "timestamp": datetime.now().isoformat(),
        }

    def _format_stream_sources(
        self,
        documents: list[Any],
        sql_search_result: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Normalize retrieved documents for stream metadata and done events.

        [MOD] 复用与非流式路径完全相同的来源格式化（rag_pipeline.format_sources
        → _format_rag_source）。此前这里手写了简化版来源，document 字段取到
        集合名（company_kb），Web 来源卡片的标题全部显示成集合名而非真实
        文档名（2026-11-10）。同源格式化保证 SSE 与 REST 的引用字段一致。
        """
        try:
            formatted = self.rag_pipeline.format_sources(documents, sql_search_result)
            return [source.model_dump(mode="json") for source in formatted.sources]
        except Exception as e:
            # 格式化失败非致命：回答照常流式输出，仅不附带来源卡片。
            logger.warning(f"스트리밍 소스 포맷팅 실패, 빈 목록으로 진행 - {e}")
            return []

    def _get_stream_model_info(self, generation_module: Any | None) -> dict[str, Any]:
        """Return best-effort model metadata for streaming completion events."""
        if generation_module is None:
            return {"provider": "none", "model": "none", "streaming": True}
        return {
            "provider": getattr(generation_module, "provider", "unknown"),
            "model": getattr(generation_module, "default_model", "unknown"),
            "streaming": True,
        }

    def _get_first_token_timeout(self, options: dict[str, Any]) -> float:
        """Resolve the first-token watchdog timeout in seconds."""
        timeout = options.get("first_token_timeout", options.get("stream_first_token_timeout"))
        if timeout is None:
            timeout = self.config.get("streaming", {}).get("first_token_timeout", 25.0)
        try:
            return max(float(timeout), 0.0)
        except (TypeError, ValueError):
            return 25.0

    @staticmethod
    def _estimate_stream_tokens(answer: str, chunk_count: int) -> int:
        """Estimate token usage when providers do not expose streaming usage."""
        if not answer:
            return 0
        return max(chunk_count * 5, len(answer) // 4)

    @staticmethod
    def _split_fallback_answer_into_chunks(text: str, target: int = 60) -> list[str]:
        """Split a non-streaming fallback answer into SSE-sized chunks."""
        if not text:
            return []

        sentence_boundaries = "。．.!?！？"
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for char in text:
            current.append(char)
            current_len += 1
            is_boundary = char in sentence_boundaries or char == "\n"
            if (current_len >= target and is_boundary) or current_len >= target * 2:
                chunks.append("".join(current))
                current = []
                current_len = 0

        if current:
            chunks.append("".join(current))
        return chunks

    def _stream_hallucination_gate_triggered(self, message: str, documents: list[Any]) -> bool:
        """비스트리밍 환각 게이트와 같은 조건(활성 + 기간 일치 요구 + 불일치)인지 판정한다."""
        pipeline = self.rag_pipeline
        if not getattr(pipeline, "hallucination_gate_enabled", False):
            return False
        if not getattr(pipeline, "hallucination_gate_require_period_match", False):
            return False
        try:
            return bool(pipeline._hallucination_gate_period_mismatch(message, documents))
        except Exception as gate_error:  # noqa: BLE001 - 판정 실패는 비치명적
            logger.debug("스트리밍 환각 게이트 판정 실패(무시)", extra={"error": str(gate_error)})
            return False

    @observe(name="RAG Pipeline (Streaming)", capture_input=False, capture_output=False)
    async def stream_rag_pipeline(
        self, message: str, session_id: str | None, options: dict[str, Any] | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        스트리밍 RAG 파이프라인 실행

        세션 처리, 컨텍스트 준비, 문서 검색, 리랭킹은 비스트리밍으로 처리하고,
        답변 생성 단계에서만 스트리밍으로 청크를 yield합니다.

        이벤트 타입:
        - metadata: 검색 결과 메타데이터 (세션 ID, 문서 수, 소스 등)
        - chunk: LLM 응답 텍스트 청크 (data, chunk_index)
        - done: 스트리밍 완료 이벤트 (session_id, total_chunks)
        - error: 에러 이벤트 (error_code, message)

        Args:
            message: 사용자 메시지
            session_id: 세션 ID (None이면 새로 생성)
            options: 추가 옵션 (temperature, max_tokens, model 등)

        Yields:
            dict: 스트리밍 이벤트 딕셔너리

        Example:
            async for event in chat_service.stream_rag_pipeline(message, session_id):
                if event["event"] == "chunk":
                    print(event["data"], end="", flush=True)
        """
        options = options or {}
        start_time = time.time()
        chunk_index = 0
        final_session_id = session_id
        message_id = str(uuid.uuid4())
        answer_chunks: list[str] = []
        # [FIX 用户报告] 持久化跟踪：正常完成/拒答分支各自保存后置 True；
        # finally 兜底仅在"未保存且已有部分内容"时保存（客户端流式中途断开场景）。
        stream_persisted = False
        # GAP #5: 직전 청크 전송 시각(time.monotonic). 첫 청크는 None이라 페이싱
        # 무동작이며, chunk_min_interval_seconds=0이면 전체 구간에서 무동작이다.
        last_chunk_sent_at: float | None = None

        # [MOD] PII 入口清洗：用户消息在进入检索/会话存储/日志前统一掩码。
        # 入口即掩 → 后续全链路（query、会话历史、审计日志）只见掩码文本
        # （纵深防御第一层；制度语料本身无真实 PII，生成侧无 PII 来源）。
        if self._pii_masking_enabled:
            pii_detection = self._pii_masker.mask_text_detailed(message)
            if pii_detection.total_masked > 0:
                logger.info(
                    "用户消息检出 PII，已入口掩码",
                    extra={"session_id": session_id, "message_preview": message[:20]},
                )
                self._audit(
                    category="security",
                    action="pii.masked",
                    session_id=session_id,
                    result="masked",
                    detail={
                        "phone": pii_detection.phone_count,
                        "id_card": pii_detection.id_card_count,
                        "bank_card": pii_detection.bank_card_count,
                        "email": pii_detection.email_count,
                        "validation_blocked": pii_detection.skipped_by_validation,
                    },
                )
                message = pii_detection.masked

        try:
            # 1. 세션 처리 (비스트리밍)
            session_module = self.modules.get("session")

            if session_module:
                if session_id:
                    # 기존 세션 검증
                    session_result = await session_module.get_session(session_id, {})
                    if not session_result.get("is_valid"):
                        # 세션이 유효하지 않으면 새로 생성
                        new_session = await session_module.create_session(
                            {"metadata": {}}, session_id=session_id
                        )
                        final_session_id = new_session["session_id"]
                        logger.debug(f"스트리밍: 새 세션 생성 - {final_session_id}")
                else:
                    # 세션 ID 없으면 새로 생성
                    new_session = await session_module.create_session({"metadata": {}})
                    final_session_id = new_session["session_id"]
                    logger.debug(f"스트리밍: 새 세션 생성 - {final_session_id}")

                # 2. 세션 컨텍스트 조회 (비스트리밍)
                # 세션 컨텍스트는 run_retrieval_phase(prepare_context)가 한 번만 읽는다(중복 I/O 제거).
                session_context = ""
            else:
                session_context = ""
                if not final_session_id:
                    final_session_id = str(uuid.uuid4())

            # [MOD] 寒暄前置分流：放在 route_query 之前，问候/致谢完全跳过检索与
            # 路由，零来源直接应答。事件序列（metadata→chunk→done）与正常路径一致。
            if self._is_small_talk(message):
                logger.info(
                    "寒暄命中（流式），跳过检索直接应答",
                    extra={"session_id": final_session_id, "message_preview": message[:30]},
                )
                answer = self._SMALL_TALK_ANSWER
                yield {
                    "event": "metadata",
                    "data": {
                        "session_id": final_session_id,
                        "search_results": 0,
                        "ranked_results": 0,
                        "reranking_applied": False,
                        "message_id": message_id,
                        "sources": [],
                        "route": "small_talk",
                        "timestamp": datetime.now().isoformat(),
                    },
                }
                yield {"event": "chunk", "data": answer, "chunk_index": 0}
                processing_time = time.time() - start_time
                if final_session_id:
                    try:
                        await self.add_conversation_to_session(
                            final_session_id,
                            message,
                            answer,
                            {
                                "tokens_used": 0,
                                "processing_time": processing_time,
                                "sources": [],
                                "topic": "small_talk",
                                "model_info": {"provider": "internal", "model": "rule-based"},
                                "message_id": message_id,
                                "route": "small_talk",
                                "can_evaluate": False,
                            },
                        )
                    except Exception as persist_error:  # noqa: BLE001 - 存储失败非致命
                        logger.warning(
                            "寒暄应答存储失败",
                            extra={"session_id": final_session_id, "error": str(persist_error)},
                        )
                yield {
                    "event": "done",
                    "data": {
                        "session_id": final_session_id,
                        "message_id": message_id,
                        "total_chunks": 1,
                        "processing_time": processing_time,
                        "tokens_used": 0,
                        "sources": [],
                        "route": "small_talk",
                        "model_info": {"provider": "internal", "model": "rule-based"},
                        "token_estimation": "small_talk_short_circuit",
                    },
                }
                return

            # [MOD EXP-018] 澄清式对话（流式路径，与 execute() 同策略）：指代不明
            # → 追问澄清而非盲目检索。事件序列（metadata→chunk→done）与正常路径一致。
            clarification_answer = await self._clarification_answer_if_needed(
                message, final_session_id or session_id
            )
            if clarification_answer:
                logger.info(
                    "澄清命中（流式）：指代不明，返回追问",
                    extra={"session_id": final_session_id, "message_preview": message[:30]},
                )
                answer = clarification_answer
                yield {
                    "event": "metadata",
                    "data": {
                        "session_id": final_session_id,
                        "search_results": 0,
                        "ranked_results": 0,
                        "reranking_applied": False,
                        "message_id": message_id,
                        "sources": [],
                        "route": "clarification",
                        "timestamp": datetime.now().isoformat(),
                    },
                }
                yield {"event": "chunk", "data": answer, "chunk_index": 0}
                processing_time = time.time() - start_time
                if final_session_id:
                    try:
                        # 澄清对话同样持久化（用户切回来能看到追问历史）
                        await self.add_conversation_to_session(
                            final_session_id,
                            message,
                            answer,
                            {
                                "tokens_used": 0,
                                "processing_time": processing_time,
                                "sources": [],
                                "topic": "clarification",
                                "model_info": {"provider": "internal", "model": "rule-based"},
                                "message_id": message_id,
                                "route": "clarification",
                                "can_evaluate": False,
                            },
                        )
                    except Exception as persist_error:  # noqa: BLE001 - 存储失败非致命
                        logger.warning(
                            "澄清应答存储失败",
                            extra={"session_id": final_session_id, "error": str(persist_error)},
                        )
                yield {
                    "event": "done",
                    "data": {
                        "session_id": final_session_id,
                        "message_id": message_id,
                        "total_chunks": 1,
                        "processing_time": processing_time,
                        "tokens_used": 0,
                        "sources": [],
                        "route": "clarification",
                        "model_info": {"provider": "internal", "model": "rule-based"},
                        "token_estimation": "clarification_short_circuit",
                    },
                }
                return

            # [MOD] 提示注入防护接线（改造⑦ Guard 上线，2026-11-10）：
            # 此前 InputGuard 仅存在于测试脚本，线上从未执行（EXP-009 的拦截率
            # 是脚本回归结果）。block 拒绝并记审计；warn 剥离可疑句后继续。
            guard_result = scan_input(message)
            if guard_result.level == "block":
                self._audit(
                    category="security",
                    action="injection.blocked",
                    session_id=final_session_id,
                    result="blocked",
                    detail={
                        "hits": [h.category for h in guard_result.hits],
                        "total_weight": guard_result.total_weight,
                        "matched": [h.matched for h in guard_result.hits],
                    },
                )
                refusal = self._INJECTION_REFUSAL_ANSWER
                yield {
                    "event": "metadata",
                    "data": {
                        "session_id": final_session_id,
                        "search_results": 0,
                        "ranked_results": 0,
                        "reranking_applied": False,
                        "message_id": message_id,
                        "sources": [],
                        "route": "injection_blocked",
                        "timestamp": datetime.now().isoformat(),
                    },
                }
                yield {"event": "chunk", "data": refusal, "chunk_index": 0}
                yield {
                    "event": "done",
                    "data": {
                        "session_id": final_session_id,
                        "message_id": message_id,
                        "total_chunks": 1,
                        "processing_time": time.time() - start_time,
                        "tokens_used": 0,
                        "sources": [],
                        "route": "injection_blocked",
                        "model_info": {"provider": "internal", "model": "input_guard"},
                    },
                }
                return
            if guard_result.level == "warn":
                self._audit(
                    category="security",
                    action="injection.warn",
                    session_id=final_session_id,
                    result="masked",
                    detail={
                        "hits": [h.category for h in guard_result.hits],
                        "total_weight": guard_result.total_weight,
                    },
                )
                message = guard_result.cleaned

            route_decision = await self.rag_pipeline.route_query(
                message, final_session_id or "", start_time
            )
            if not route_decision.should_continue:
                immediate = route_decision.immediate_response or {}
                route = route_decision.metadata.get("route") or route_decision.metadata.get(
                    "llm_route"
                )
                answer = str(
                    immediate.get("answer")
                    or (DEFAULT_BLOCKED_ANSWER if route == "blocked" else "")
                )
                yield {
                    "event": "metadata",
                    "data": {
                        "session_id": final_session_id,
                        "search_results": 0,
                        "ranked_results": 0,
                        "reranking_applied": False,
                        "message_id": message_id,
                        "sources": [],
                        "route": route,
                        "timestamp": datetime.now().isoformat(),
                    },
                }
                if answer:
                    yield {"event": "chunk", "data": answer, "chunk_index": 0}
                processing_time = time.time() - start_time
                if final_session_id and answer:
                    try:
                        await self.add_conversation_to_session(
                            final_session_id,
                            message,
                            answer,
                            {
                                "tokens_used": 0,
                                "processing_time": processing_time,
                                "sources": [],
                                "topic": immediate.get("topic") or self.extract_topic(message),
                                "model_info": immediate.get("model_info", {}),
                                "message_id": message_id,
                                "route": route,
                                "can_evaluate": False,
                            },
                        )
                    except Exception as persist_error:  # noqa: BLE001 - 스트리밍 저장 실패는 비치명적
                        logger.warning(
                            "스트리밍 단락 응답 저장 실패",
                            extra={"session_id": final_session_id, "error": str(persist_error)},
                        )
                yield {
                    "event": "done",
                    "data": {
                        "session_id": final_session_id,
                        "message_id": message_id,
                        "total_chunks": 1 if answer else 0,
                        "processing_time": processing_time,
                        "tokens_used": 0,
                        "sources": [],
                        "route": route,
                        "model_info": immediate.get("model_info", {}),
                        "token_estimation": "routing_short_circuit",
                    },
                }
                return

            # 3. 문서 검색 (비스트리밍)
            # 검색 국면은 비스트리밍 execute()와 같은 run_retrieval_phase를 공유한다.
            # (세션 컨텍스트, 멀티턴 재작성, 쿼리 확장, 정확 식별자/문서명 구출, 리랭크 융합·
            #  안정화, 앵커 부스트, 컨텍스트 확장이 스트리밍에서도 동일하게 적용된다.)
            search_results: list[Any] = []
            reranked_documents: list[Any] = []
            reranking_applied = False
            context_expanded_grew = False
            sql_search_result = None
            # 비스트리밍 execute()와 동일하게 라우팅이 정한 data_source를 검색 옵션에 반영한다.
            data_source = route_decision.metadata.get("data_source")
            if data_source is not None:
                options["data_source"] = data_source
            try:
                phase = await self.rag_pipeline.run_retrieval_phase(
                    message,
                    final_session_id or "",
                    options,
                    start_time=start_time,
                    budget=lambda: self.rag_pipeline._remaining_stream_budget(start_time),
                )
                search_results = list(phase.retrieval_results.documents)
                reranked_documents = list(phase.context_documents)
                reranking_applied = phase.rerank_results.reranked
                context_expanded_grew = len(phase.context_documents) > len(
                    phase.rerank_results.documents
                )
                sql_search_result = phase.sql_search_result
                if phase.prepared_context.session_context:
                    session_context = phase.prepared_context.session_context
                logger.debug(
                    f"스트리밍: 검색 국면 완료 - 검색 {len(search_results)}개, "
                    f"컨텍스트 {len(reranked_documents)}개 (reranked={reranking_applied})"
                )
            except Exception as e:
                # 검색 실패는 비치명적: 문서 없음 경로(canned no-answer)로 계속 진행한다.
                logger.warning(f"스트리밍: 검색 국면 실패, 빈 컨텍스트로 진행 - {e}")

            # [MOD EXP-016] 检索相关性阈值 + 拒答联动（流式路径，与 execute() 同策略）：
            # rerank 后 top1 分数低于阈值 → 跳过 LLM 生成，直接流式输出罐头拒答，
            # 不发 sources（检索无据，来源卡片无意义）。根治"世界知识拼凑"。
            refusal_gate = None
            try:
                refusal_gate = self.rag_pipeline._refusal_gate_check(
                    phase.rerank_results, options
                )
            except Exception:  # noqa: BLE001 - 门失败不阻断主链路，退回 prompt 约束
                refusal_gate = None
            if refusal_gate is not None:
                top_score, threshold = refusal_gate
                logger.info(
                    "拒答门触发（流式）：rerank top1=%.3f < 阈值 %.3f", top_score, threshold
                )
                refusal_answer = (
                    "抱歉，知识库中没有找到与您的问题相关的内容，"
                    "我无法提供有依据的回答。请尝试换个问法，"
                    "或确认该问题是否属于公司制度知识库的覆盖范围。"
                )
                yield {"event": "metadata", "data": {
                    "session_id": final_session_id,
                    "search_results": len(search_results),
                    "ranked_results": 0,
                    "reranking_applied": reranking_applied,
                    "message_id": message_id,
                    "sources": [],
                    "refusal_reason": "low_relevance_top1",
                    "timestamp": datetime.now().isoformat(),
                }}
                yield {"event": "message", "data": {"content": refusal_answer}}
                yield {"event": "done", "data": {
                    "session_id": final_session_id,
                    "message_id": message_id,
                    "tokens_used": 0,
                    "refusal_reason": "low_relevance_top1",
                }}
                # [FIX 用户报告] 拒答对话同样要持久化：此前提前 return 绕过了
                # 尾部的 add_conversation_to_session，导致拒答会话切换回来时
                # 历史为空（后端实测 history=0 条）。保存失败仅记日志（响应
                # 已发出，协议上无法再报错——与尾部保存的容错语义一致）。
                try:
                    stream_persisted = True
                    await self.add_conversation_to_session(
                        final_session_id,
                        message,
                        refusal_answer,
                        {
                            "tokens_used": 0,
                            "message_id": message_id,
                            "refusal_reason": "low_relevance_top1",
                            "can_evaluate": False,
                        },
                    )
                except Exception as save_error:  # noqa: BLE001
                    logger.warning(
                        "拒答对话保存失败（会话历史将缺失本轮）",
                        extra={"session_id": final_session_id, "error": str(save_error)},
                    )
                return
            try:
                langfuse_context.update_current_trace(session_id=final_session_id)
            except Exception:  # noqa: BLE001 - 관측 실패는 파이프라인에 비치명적
                pass

            # 5. 메타데이터 이벤트 전송
            # [MOD] 传入 sql_search_result：与 REST 同源格式化（含 SQL 元数据来源）
            stream_sources = self._format_stream_sources(reranked_documents, sql_search_result)
            metadata_event = {
                "event": "metadata",
                "data": {
                    "session_id": final_session_id,
                    "search_results": len(search_results),
                    "ranked_results": len(reranked_documents),
                    "reranking_applied": reranking_applied,
                    "message_id": message_id,
                    "sources": stream_sources,
                    "timestamp": datetime.now().isoformat(),
                },
            }
            yield metadata_event

            # 6. 스트리밍 답변 생성
            generation_module = self.modules.get("generation")
            model_info = self._get_stream_model_info(generation_module)
            stream_tokens_used = 0
            # 안내성(문서 없음) 응답은 실제 답변이 아니므로 저장/성공 집계에서 제외한다(#7/[28]).
            canned_no_answer = False

            if not reranked_documents:
                canned_no_answer = True
                # 검색 0건 안내 메시지(informational). config로 외부화되어 운영자가
                # 다른 언어/도메인으로 교체 가능하며, 미설정 시 한국어 기본값 → 회귀 0.
                no_context_answer = self.stream_no_documents_message
                # answer_chunks에 추가하지 않아 answer_text를 비워둔다 → tokens_used=0,
                # 영속화/성공 집계 스킵(안내 UX는 chunk+done으로 유지).
                yield {
                    "event": "chunk",
                    "data": no_context_answer,
                    "chunk_index": chunk_index,
                }
                chunk_index += 1
            elif generation_module and hasattr(generation_module, "stream_answer"):
                # 컨텍스트 문서 준비 (리랭킹된 문서 사용)
                context_documents = reranked_documents

                # 생성 옵션 구성
                generation_options = {
                    **options,
                    "session_context": session_context,
                }
                # 인접 청크 확장으로 문서가 늘었을 때만 프롬프트 한도를 상향(20)해
                # 실제 히트가 이웃 청크에 밀려나지 않게 한다(비스트리밍 execute와 정합)(#3).
                if context_expanded_grew:
                    generation_options.setdefault("max_context_documents", 20)
                if sql_search_result is not None and getattr(sql_search_result, "used", False):
                    generation_options["sql_context"] = sql_search_result.formatted_context

                # 스트리밍 호출
                # 첫 청크(first-chunk) 타임아웃 + 청크 간(inter-chunk) 타임아웃.
                #  - 첫 토큰이 너무 늦으면 에러 대신 검증된 비스트리밍 답변으로
                #    폴백한다(답변 보장).
                #  - 이후 청크도 generate_answer stage 예산만큼만 대기해, 생성
                #    중간에 무한정 멈추는 상황을 막는다(pipeline_timeout opt-in).
                try:
                    first_chunk_timeout = self._get_first_token_timeout(options)
                    inter_chunk_timeout = (
                        self.rag_pipeline.pipeline_stage_budgets.get("generate_answer")
                        if getattr(self.rag_pipeline, "pipeline_timeout_enabled", False)
                        else None
                    )
                    stream_gen = generation_module.stream_answer(
                        query=message,
                        context_documents=context_documents,
                        options=generation_options,
                    )
                    stream_iter = stream_gen.__aiter__()
                    # 폴백 결정 상태:
                    #  - chunk_emitted: chunk 이벤트를 1개라도 yield했는지. True면
                    #    중복 답변 방지를 위해 폴백하지 않고 기존 에러 처리를 유지.
                    #  - fallback_reason: 첫 청크 단계에서 실패해 폴백할 사유.
                    #    None이면 폴백하지 않는다(정상 종료 또는 중간 실패).
                    chunk_emitted = False
                    fallback_reason: str | None = None
                    try:
                        is_first_chunk = True
                        while True:
                            chunk_timeout = (
                                first_chunk_timeout if is_first_chunk else inter_chunk_timeout
                            )
                            try:
                                if chunk_timeout is not None and chunk_timeout > 0:
                                    text_chunk = await asyncio.wait_for(
                                        anext(stream_iter), timeout=chunk_timeout
                                    )
                                else:
                                    text_chunk = await anext(stream_iter)
                            except StopAsyncIteration:
                                break
                            except TimeoutError:
                                # 무한 대기 대신 명확한 timeout. 정리는 아래 finally의
                                # aclose로 결정적으로 수행한다.
                                if not chunk_emitted:
                                    # 첫 청크가 끝내 오지 않은 경우: 검증된 비스트리밍
                                    # 답변으로 폴백한다. 실제 폴백 호출은 stream_gen
                                    # 정리(finally) 후 수행한다.
                                    logger.warning(
                                        "스트리밍 첫 청크 타임아웃, 비스트리밍 fallback 진입",
                                        extra={
                                            "timeout": first_chunk_timeout,
                                            "session_id": final_session_id,
                                        },
                                    )
                                    fallback_reason = (
                                        f"first_token_timeout:{first_chunk_timeout}"
                                    )
                                    break
                                # 이미 청크를 보낸 뒤 중간 timeout: 폴백 시 중복 답변이
                                # 되므로 stage timeout 에러로 종료한다.
                                logger.warning(
                                    "스트리밍 청크 간 타임아웃",
                                    extra={"timeout_seconds": inter_chunk_timeout},
                                )
                                yield {
                                    "event": "error",
                                    "error_code": ErrorCode.PIPELINE_STAGE_TIMEOUT.value,
                                    "message": "답변 생성이 지연되어 중단되었습니다. 잠시 후 다시 시도해주세요.",
                                }
                                return

                            answer_chunks.append(str(text_chunk))
                            # GAP #5: burst flush 방지를 위한 서버사이드 페이싱.
                            last_chunk_sent_at = await self._pace_stream_chunk(
                                last_chunk_sent_at
                            )
                            yield {
                                "event": "chunk",
                                "data": text_chunk,
                                "chunk_index": chunk_index,
                            }
                            chunk_index += 1
                            chunk_emitted = True
                            is_first_chunk = False
                    except Exception as stream_error:
                        # 첫 청크 전(아직 chunk 미전송) 예외는 폴백 대상.
                        # 이미 청크를 보낸 뒤라면 중복 답변 방지를 위해 기존 에러 처리.
                        if not chunk_emitted:
                            logger.warning(
                                "스트리밍 첫 청크 전 예외, 비스트리밍 fallback 진입: %s",
                                stream_error,
                            )
                            fallback_reason = f"stream_error:{stream_error}"
                        else:
                            raise
                    finally:
                        # 생성 제너레이터를 닫아 하위 브리지(_async_bridge) 정리를
                        # 즉시 트리거한다. 정상 소진 시에는 이미 닫혀 no-op이다.
                        # aclose 실패는 정리 단계이므로 치명적이지 않다(로깅 후 흡수).
                        aclose = getattr(stream_gen, "aclose", None)
                        if callable(aclose):
                            try:
                                await aclose()
                            except Exception as close_exc:  # noqa: BLE001 - 정리 단계
                                logger.debug(
                                    "스트리밍 생성 제너레이터 aclose 실패(무시)",
                                    extra={"error": str(close_exc)},
                                )

                    if fallback_reason is not None:
                        try:
                            fallback_result = await generation_module.generate_answer(
                                query=message,
                                context_documents=context_documents,
                                options=generation_options,
                            )
                        except Exception as fallback_error:
                            logger.error(
                                f"스트리밍 비스트리밍 fallback 실패: {fallback_error}",
                                exc_info=True,
                            )
                            yield {
                                "event": "error",
                                "error_code": ErrorCode.GENERATION_REQUEST_FAILED.value,
                                "message": "답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                            }
                            return

                        fallback_answer = str(getattr(fallback_result, "answer", "") or "")
                        stream_tokens_used = int(getattr(fallback_result, "tokens_used", 0) or 0)
                        fallback_model_info = getattr(fallback_result, "model_info", None)
                        if isinstance(fallback_model_info, dict):
                            model_info = fallback_model_info

                        for piece in self._split_fallback_answer_into_chunks(fallback_answer):
                            answer_chunks.append(piece)
                            # GAP #5: 폴백 분할 청크도 동일하게 페이싱한다.
                            last_chunk_sent_at = await self._pace_stream_chunk(
                                last_chunk_sent_at
                            )
                            yield {
                                "event": "chunk",
                                "data": piece,
                                "chunk_index": chunk_index,
                            }
                            chunk_index += 1

                except Exception as e:
                    logger.error(f"스트리밍 답변 생성 실패: {e}", exc_info=True)
                    yield {
                        "event": "error",
                        "error_code": ErrorCode.GENERATION_REQUEST_FAILED.value,
                        "message": "답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                    }
                    return
            else:
                # 생성 모듈이 없거나 스트리밍을 미지원하면 실제 실패이므로 안내 청크를
                # 답변으로 저장/성공 집계하지 않고 error 이벤트로 종료한다(#7).
                logger.warning("스트리밍: 생성 모듈 없음 또는 스트리밍 미지원")
                self.update_stats(
                    {
                        "tokens_used": 0,
                        "latency": time.time() - start_time,
                        "success": False,
                    }
                )
                yield {
                    "event": "error",
                    "error_code": ErrorCode.GENERATION_REQUEST_FAILED.value,
                    "message": "답변을 생성할 수 없습니다. 잠시 후 다시 시도해주세요.",
                }
                return

            # 7. 완료 이벤트 전송
            processing_time = time.time() - start_time
            answer_text = "".join(answer_chunks)
            # 생성기가 컨텍스트 부재 시 반환하는 안내문구도 실제 답변이 아니므로
            # 저장/성공 집계에서 제외한다(reranked_documents가 있으나 본문이 비는 경우).
            from app.modules.core.generation.generator import NO_DOCUMENTS_MESSAGE

            if answer_text.strip() == NO_DOCUMENTS_MESSAGE.strip():
                canned_no_answer = True
            # 안내성 응답(canned_no_answer)이거나 답변이 비면 토큰을 0으로 보고한다([28]).
            if canned_no_answer or not answer_text:
                tokens_used = 0
            else:
                tokens_used = stream_tokens_used or self._estimate_stream_tokens(
                    answer_text, chunk_index
                )

            # [MOD] 来源引用过滤：done 事件只携带回答中实际提及的文档（metadata
            # 事件仍是全量候选——过滤需待回答生成完成后才能判定）
            if answer_text and not canned_no_answer:
                stream_sources = self._filter_sources_by_answer(stream_sources, answer_text)

            # 실제 답변이 있을 때만 저장하고 성공으로 집계한다(#7).
            persisted_real_answer = bool(
                final_session_id and answer_text and not canned_no_answer
            )
            if persisted_real_answer:
                stream_persisted = True
                try:
                    await self.add_conversation_to_session(
                        str(final_session_id),
                        message,
                        answer_text,
                        {
                            "tokens_used": tokens_used,
                            "processing_time": processing_time,
                            "sources": stream_sources,
                            "topic": self.extract_topic(message),
                            "model_info": model_info,
                            "message_id": message_id,
                            "can_evaluate": True,
                        },
                    )
                except Exception as persist_error:
                    logger.warning(
                        "스트리밍 대화 저장 실패",
                        extra={
                            "session_id": final_session_id,
                            "error": str(persist_error),
                        },
                        exc_info=True,
                    )

            self.update_stats(
                {
                    "tokens_used": tokens_used,
                    "latency": processing_time,
                    "success": persisted_real_answer,
                }
            )

            done_event: dict[str, Any] = {
                "event": "done",
                "data": {
                    "session_id": final_session_id,
                    "message_id": message_id,
                    "total_chunks": chunk_index,
                    "processing_time": processing_time,
                    "tokens_used": tokens_used,
                    "sources": stream_sources,
                    "model_info": model_info,
                    "token_estimation": "estimated_from_stream",
                },
            }
            # 환각 게이트(기간 불일치)는 전체 답변을 본 뒤 판정하므로 스트리밍 텍스트를 바꾸지는
            # 않고, 비스트리밍과 같은 기준의 판정 결과를 done 이벤트에 실어 클라이언트가 안내할 수
            # 있게 한다.
            if self._stream_hallucination_gate_triggered(message, reranked_documents):
                done_event["data"]["hallucination_gate"] = "period_mismatch"
            yield done_event

            logger.info(
                f"스트리밍 완료: session_id={final_session_id}, "
                f"chunks={chunk_index}, time={processing_time:.2f}s"
            )

        except Exception as e:
            # 에러 이벤트 전송
            logger.error(f"스트리밍 파이프라인 에러: {e}", exc_info=True)
            yield {
                "event": "error",
                "error_code": ErrorCode.GENERAL_004.value,
                "message": "스트리밍 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            }

        # [FIX 用户报告] 客户端流式中途断开（如等待回答时切换会话）→ FastAPI 取消
        # generator（GeneratorExit，BaseException 不被上方 except 捕获），正常尾部
        # 保存被跳过 → 切回来这一轮问答从历史里消失。
        # 兜底用独立 asyncio task 保存：generator 取消场景下 finally 里的直接
        # await 会被连带取消（CancelledError 也是 BaseException），独立任务
        # 在事件循环里继续跑完保存；后台集引用防 GC。
        finally:
            partial_text = "".join(answer_chunks)
            # 断开常发生在检索阶段（尚未产出任何 chunk）——answer_chunks 为空时
            # 问题本身也从未保存过（正常路径的保存是问+答一起），此时保存问题 +
            # 中断占位，保证"切回来问题还在"。
            if not partial_text.strip():
                partial_text = "（回答生成被中断）"
            if not stream_persisted and final_session_id:
                _bg = asyncio.create_task(
                    self._persist_partial_stream(
                        str(final_session_id), message, partial_text, chunk_index
                    )
                )
                _STREAM_PERSIST_TASKS.add(_bg)
                _bg.add_done_callback(_STREAM_PERSIST_TASKS.discard)

    async def _persist_partial_stream(
        self, session_id: str, message: str, partial_text: str, chunk_index: int
    ) -> None:
        """流式中断兜底保存：客户端断开（切会话/关页/网络）时保存已生成的部分回答。

        独立 asyncio task 执行——generator 被取消时其 finally 里的直接 await
        会被连带取消，独立任务在事件循环里继续完成保存。
        """
        try:
            await self.add_conversation_to_session(
                session_id,
                message,
                partial_text,
                {
                    "tokens_used": self._estimate_stream_tokens(partial_text, chunk_index),
                    "message_id": str(uuid.uuid4()),
                    "partial_stream": True,
                    "can_evaluate": False,
                },
            )
            logger.info(
                "流式中断兜底保存完成（客户端断开/异常场景）",
                extra={"session_id": session_id, "len": len(partial_text)},
            )
        except Exception as e:  # noqa: BLE001 - 保存失败仅日志（数据丢失优于进程崩溃）
            logger.warning(
                "流式中断兜底保存失败",
                extra={"session_id": session_id, "error": str(e)},
            )
