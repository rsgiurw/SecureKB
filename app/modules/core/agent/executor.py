"""
AgentExecutor - 도구 실행

ReAct 패턴의 "Acting" 담당 컴포넌트입니다.
AgentPlanner가 선택한 도구들을 MCPServer를 통해 실행합니다.

주요 기능:
- ToolCall 리스트를 받아 실제 도구 실행
- MCPServer.execute_tool() 래핑
- 동시 실행 제어 (max_concurrent_tools)
- 결과를 ToolResult로 변환

사용 예시:
    from app.modules.core.agent import AgentExecutor, AgentConfig, ToolCall

    executor = AgentExecutor(mcp_server, config)
    results = await executor.execute([
        ToolCall(tool_name="search_weaviate", arguments={"query": "검색어"})
    ])
"""

import asyncio
import time
from typing import Any

from app.lib.logger import get_logger
from app.modules.core.agent.interfaces import AgentConfig, ToolCall, ToolResult

logger = get_logger(__name__)


class AgentExecutor:
    """
    도구 실행기

    MCPServer를 통해 도구를 실행하고 결과를 수집합니다.
    병렬/순차 실행 및 동시성 제어를 담당합니다.

    Attributes:
        _mcp_server: MCP 서버 인스턴스
        _config: 에이전트 설정
        _semaphore: 동시 실행 제한용 세마포어

    Note:
        병렬 실행 시 asyncio.Semaphore를 사용하여
        max_concurrent_tools 설정에 따라 동시 실행을 제한합니다.
    """

    def __init__(
        self,
        mcp_server: Any,
        config: AgentConfig,
    ):
        """
        AgentExecutor 초기화

        Args:
            mcp_server: MCP 서버 인스턴스 (execute_tool 메서드 필요)
            config: 에이전트 설정
        """
        self._mcp_server = mcp_server
        self._config = config
        # 동시 실행 제한용 세마포어
        self._semaphore = asyncio.Semaphore(config.max_concurrent_tools)

    async def execute(
        self,
        tool_calls: list[ToolCall],
        retrieval_filters: dict[str, Any] | None = None,
    ) -> list[ToolResult]:
        """
        도구 실행

        주어진 ToolCall 리스트를 실행하고 ToolResult 리스트를 반환합니다.
        설정에 따라 병렬 또는 순차 실행됩니다.

        Args:
            tool_calls: 실행할 도구 호출 리스트

        Returns:
            ToolResult 리스트 (입력 순서 보장)

        Raises:
            asyncio.TimeoutError: 전체 작업 타임아웃 초과 시 (QA-003)
        """
        if not tool_calls:
            logger.debug("실행할 도구가 없습니다")
            return []

        logger.info(
            f"🔧 AgentExecutor: {len(tool_calls)}개 도구 실행 시작 "
            f"(parallel={self._config.parallel_execution}, "
            f"timeout={self._config.timeout_seconds}초)"
        )

        try:
            # QA-003: 전체 작업 타임아웃 적용
            if self._config.parallel_execution and len(tool_calls) > 1:
                results = await asyncio.wait_for(
                    self._execute_parallel(tool_calls, retrieval_filters=retrieval_filters),
                    timeout=self._config.timeout_seconds,
                )
            else:
                results = await asyncio.wait_for(
                    self._execute_sequential(tool_calls, retrieval_filters=retrieval_filters),
                    timeout=self._config.timeout_seconds,
                )

            # 성공/실패 통계 로깅
            success_count = sum(1 for r in results if r.success)
            logger.info(
                f"✅ AgentExecutor: {len(results)}개 완료 "
                f"(성공: {success_count}, 실패: {len(results) - success_count})"
            )

            return results

        except TimeoutError:
            logger.error(
                f"🚨 AgentExecutor 작업 타임아웃 ({self._config.timeout_seconds}초 초과)"
            )
            raise

    async def _execute_parallel(
        self,
        tool_calls: list[ToolCall],
        retrieval_filters: dict[str, Any] | None = None,
    ) -> list[ToolResult]:
        """
        도구 병렬 실행

        asyncio.gather를 사용하여 도구를 병렬로 실행합니다.
        Semaphore를 통해 동시 실행 수를 제한합니다.

        Args:
            tool_calls: 실행할 도구 호출 리스트

        Returns:
            ToolResult 리스트 (입력 순서 보장)
        """
        logger.debug(
            f"⚡ 병렬 실행: {len(tool_calls)}개 도구 "
            f"(max_concurrent={self._config.max_concurrent_tools})"
        )

        # 각 도구 호출에 대해 래핑된 태스크 생성
        tasks = [
            self._execute_with_semaphore(tc, retrieval_filters=retrieval_filters)
            for tc in tool_calls
        ]

        # 병렬 실행 (예외 발생해도 다른 태스크는 계속 실행)
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 예외를 ToolResult로 변환
        final_results: list[ToolResult] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                # gather에서 예외가 발생한 경우
                final_results.append(
                    ToolResult(
                        call_id=tool_calls[i].call_id,
                        tool_name=tool_calls[i].tool_name,
                        success=False,
                        error=str(result),
                    )
                )
            else:
                final_results.append(result)

        return final_results

    async def _execute_with_semaphore(
        self,
        tool_call: ToolCall,
        retrieval_filters: dict[str, Any] | None = None,
    ) -> ToolResult:
        """
        세마포어로 제한된 단일 도구 실행

        Args:
            tool_call: 실행할 도구 호출

        Returns:
            ToolResult
        """
        async with self._semaphore:
            return await self._execute_single(tool_call, retrieval_filters=retrieval_filters)

    async def _execute_sequential(
        self,
        tool_calls: list[ToolCall],
        retrieval_filters: dict[str, Any] | None = None,
    ) -> list[ToolResult]:
        """
        도구 순차 실행

        Args:
            tool_calls: 실행할 도구 호출 리스트

        Returns:
            ToolResult 리스트 (입력 순서 보장)
        """
        logger.debug(f"📦 순차 실행: {len(tool_calls)}개 도구")

        results = []
        for tc in tool_calls:
            result = await self._execute_single(tc, retrieval_filters=retrieval_filters)
            results.append(result)

        return results

    async def _execute_single(
        self,
        tool_call: ToolCall,
        retrieval_filters: dict[str, Any] | None = None,
    ) -> ToolResult:
        """
        단일 도구 실행

        MCPServer.execute_tool()을 호출하고
        결과를 ToolResult로 변환합니다.

        Args:
            tool_call: 실행할 도구 호출

        Returns:
            ToolResult
        """
        start_time = time.time()

        logger.debug(
            f"🔧 도구 실행: {tool_call.tool_name} "
            f"(call_id={tool_call.call_id})"
        )

        try:
            # 타임아웃 적용하여 MCP 서버 호출
            mcp_result = await asyncio.wait_for(
                self._mcp_server.execute_tool(
                    tool_name=tool_call.tool_name,
                    arguments=tool_call.arguments,
                    retrieval_filters=retrieval_filters,
                ),
                timeout=self._config.tool_timeout,
            )

            execution_time = time.time() - start_time

            # MCPToolResult → ToolResult 변환
            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                success=mcp_result.success,
                data=mcp_result.data,
                error=mcp_result.error,
                execution_time=execution_time,
            )

        except TimeoutError:
            execution_time = time.time() - start_time
            error_msg = f"타임아웃 ({self._config.tool_timeout}초 초과)"
            logger.error(f"❌ 도구 타임아웃: {tool_call.tool_name}")

            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                success=False,
                error=error_msg,
                execution_time=execution_time,
            )

        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(
                f"❌ 도구 실행 실패: {tool_call.tool_name} - {e}",
                exc_info=True,
            )

            return ToolResult(
                call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                success=False,
                error=str(e),
                execution_time=execution_time,
            )
