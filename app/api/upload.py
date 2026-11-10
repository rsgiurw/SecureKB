"""
Upload API endpoints
파일 업로드 및 문서 처리 API 엔드포인트
"""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..lib.auth import (
    AUTH_NOT_CONFIGURED_DETAIL,
    get_api_key,
    get_api_key_auth,
    verify_upload_access_token,
)
from ..lib.logger import get_logger
from ..modules.core.privacy.masker import DEFAULT_WHITELIST, PrivacyMasker
from ..security.users import verify_token  # [MOD] SecureKB 登录令牌校验（改造②）
from .audit_event_store import AuditEventStore, SQLiteAuditEventStore
from .original_file_storage import (
    GCS_ORIGINAL_STORAGE_BACKEND,
    OriginalFileNotFoundError,
    OriginalFileStorageError,
    delete_original_reference,
    materialize_original_to_path,
    original_file_chunks,
    original_storage_metadata,
    resolve_original_reference,
    store_original_file,
)
from .upload_job_store import (
    JsonUploadJobStore,
    PostgresUploadJobStore,
    SQLiteUploadJobStore,
    UploadJobStore,
)

logger = get_logger(__name__)

# PII 마스킹을 위한 인스턴스 (privacy.enabled 체크 후 초기화)
# DEFAULT_WHITELIST 사용 (오탐 방지: 이모님, 헬퍼님, 담당 등)
# Note: DI Container 외부에서 사용하므로 기본 화이트리스트 직접 지정
_privacy_masker: PrivacyMasker | None = PrivacyMasker(whitelist=list(DEFAULT_WHITELIST))


# 자격(principal) 식별 문자열 규약
# - API_KEY_PRINCIPAL: 서버 API 키(X-API-Key)로 인증된 운영자. 모든 업로드 API 사용 가능.
# - LOGIN_ADMIN_PRINCIPAL: [MOD] SecureKB 로그인 토큰(Bearer, role=admin)으로 인증된
#   관리자. API 키와 동등한 자격 — Web 관리 화면이 운영 설정에 키를 붙여넣지 않아도
#   로그인만으로 문서 관리를 쓸 수 있게 한다(改造②: 로그인이 관리자 신원의 원천).
# - "upload-token:{session_id}": 创建会话 시 발급된 단기 토큰을 가진 브라우저 사용자.
#   업로드/조회만 가능하고, 공유 코퍼스를 파괴하는 삭제는 불가. 취소/재시도는 자기 세션이
#   만든 작업에만 가능하다.
API_KEY_PRINCIPAL = "api-key"
LOGIN_ADMIN_PRINCIPAL = "login-admin"
UPLOAD_TOKEN_PRINCIPAL_PREFIX = "upload-token:"


def get_upload_access(request: Request) -> str:
    """업로드 API 접근 인증 (로그인 관리자 토큰 OR 서버 API 키 OR 단기 업로드 토큰).

    [MOD] 1순위: SecureKB 로그인 토큰(Bearer, role=admin). users.py가 서버에서
    서명/검증하므로 클라이언트가 역할을 위조할 수 없다. 만료 토큰은 verify_token이
    None을 돌려주므로 아래 기존 경로(X-API-Key/업로드 토큰)로 떨어진다.
    role=admin이 아닌 직원 토큰은 403을 돌려준다 — 401로 처리하면 프런트 로그아웃
    인터셉터가 채팅 세션까지 로그아웃시키므로, 권한 부족은 403으로 명확히 구분한다.

    기존 동작(X-API-Key 헤더 검증, get_api_key)을 우선 시도하고, 실패 시
    브라우저용 단기 업로드 토큰(X-OneRAG-Upload-Token + 세션 식별자)을 검증한다.
    이 경로 덕분에 브라우저 업로드 UI가 서버 API 키(FASTAPI_AUTH_KEY)를 직접
    보유하지 않고도 단기 토큰만으로 업로드할 수 있다(#22).

    세션 식별자는 X-OneRAG-Session-Id 헤더 또는 ?session_id 쿼리에서 읽는다.

    서버 API 키가 설정되지 않았으면 환경과 무관하게 401을 돌려준다(fail-closed).
    예전에는 개발 환경이면 인증을 건너뛰었는데, 환경 감지만 속이면 업로드/삭제 API가
    그대로 열리는 구조였다. (로그인 관리자 토큰은 이 fail-closed와 무관하게 독립
    검증된 자격이므로 먼저 통과시킨다.)

    Returns:
        자격 식별 문자열(LOGIN_ADMIN_PRINCIPAL / API_KEY_PRINCIPAL 또는 "upload-token:{session_id}").

    Raises:
        HTTPException: 키 미구성이거나, 어느 자격도 유효하지 않을 때.
    """
    auth = get_api_key_auth()

    # [MOD] 1순위: SecureKB 로그인 토큰(Bearer, role=admin)
    bearer = request.headers.get("Authorization", "")
    if bearer.startswith("Bearer "):
        user = verify_token(bearer[len("Bearer "):])
        if user:
            if user.get("role") == "admin":
                return LOGIN_ADMIN_PRINCIPAL
            logger.warning(
                "직원 로그인 토큰으로 업로드 API 접근 거부",
                extra={"path": request.url.path, "username": user.get("username")},
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error_code": "UPLOAD-403",
                    "error": "权限不足",
                    "message": "文档管理需要管理员登录",
                    "suggestion": "请使用管理员账户（admin）登录后重试",
                },
            )

    if not auth.api_key:
        logger.warning(
            "未设置 FASTAPI_AUTH_KEY，上传 API 请求被拒绝（fail-closed）",
            extra={"path": request.url.path},
        )
        raise HTTPException(status_code=401, detail=dict(AUTH_NOT_CONFIGURED_DETAIL))

    try:
        get_api_key(request)
        return API_KEY_PRINCIPAL
    except HTTPException as api_key_error:
        # X-API-Key가 없거나 틀린 경우, 브라우저용 단기 업로드 토큰을 대체 자격으로 허용한다.
        upload_token = request.headers.get("X-OneRAG-Upload-Token") or request.query_params.get(
            "upload_token"
        )
        session_id = request.headers.get("X-OneRAG-Session-Id") or request.query_params.get(
            "session_id"
        )
        if (
            upload_token
            and session_id
            and verify_upload_access_token(session_id, upload_token, auth.api_key)
        ):
            return f"{UPLOAD_TOKEN_PRINCIPAL_PREFIX}{session_id}"
        raise api_key_error


def _principal_session_id(principal: Any) -> str | None:
    """업로드 토큰 자격이면 바인딩된 세션 ID를, API 키 자격이면 None을 돌려준다.

    핸들러를 FastAPI 밖에서 직접 호출하면(스모크 테스트 등) principal에 Depends 객체가
    그대로 들어오므로, 문자열이 아닌 값은 운영자 자격(None)으로 취급한다.
    """
    if isinstance(principal, str) and principal.startswith(UPLOAD_TOKEN_PRINCIPAL_PREFIX):
        return principal[len(UPLOAD_TOKEN_PRINCIPAL_PREFIX) :]
    return None


def require_upload_admin_access(principal: str = Depends(get_upload_access)) -> str:
    """삭제처럼 공유 코퍼스를 바꾸는 작업은 서버 API 키 자격만 허용한다.

    업로드 토큰은 "创建会话"만으로 누구나 받을 수 있는 자격이므로, 이 토큰으로 전체
    문서를 삭제할 수 있게 두면 인증 없는 삭제와 같다. 토큰 자격이면 403을 돌려준다.
    """
    if _principal_session_id(principal) is not None:
        logger.warning(
            "以上传令牌资格拒绝管理员专用上传 API 请求",
            extra={"principal": principal},
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "UPLOAD-403",
                "error": "权限不足",
                "message": "此操作需要服务器 API 密钥（X-API-Key）认证",
                "suggestion": "请在运营者设置中输入管理员 API 密钥后重试",
            },
        )
    return principal


def _assert_job_owner(job: dict[str, Any], principal: str, job_id: str) -> None:
    """취소/재시도처럼 작업 상태를 바꾸는 요청은 작업을 만든 세션만 허용한다.

    API 키 자격은 모든 작업을 다룰 수 있다. 업로드 토큰 자격은 작업 레코드의
    owner_session_id가 토큰의 세션과 같을 때만 통과한다. (owner가 기록되지 않은 과거
    작업은 운영자 전용으로 취급한다.)
    """
    session_id = _principal_session_id(principal)
    if session_id is None:
        return
    owner = job.get("owner_session_id")
    if owner and owner == session_id:
        return
    logger.warning(
        "已拒绝操作其他会话的上传任务",
        extra={"job_id": job_id, "principal": principal, "owner_session_id": owner},
    )
    raise HTTPException(
        status_code=403,
        detail={
            "error_code": "UPLOAD-403",
            "error": "权限不足",
            "message": "此上传任务由其他会话创建",
            "job_id": job_id,
        },
    )


# ✅ H4 보안 패치: Upload API 인증 추가
# 파일 업로드/삭제는 시스템 변경이므로 인증 필요.
# get_upload_access는 X-API-Key 또는 단기 업로드 토큰(브라우저용, #22)을 허용한다.
# 삭제 계열은 require_upload_admin_access로 API 키 자격만 추가 허용한다.
router = APIRouter(tags=["Upload"], dependencies=[Depends(get_upload_access)])
modules: dict[str, Any] = {}
config: dict[str, Any] = {}


def set_dependencies(app_modules: dict[str, Any], app_config: dict[str, Any]):
    """依赖注入"""
    global modules, config, _privacy_masker
    global _audit_event_store, _audit_event_store_signature
    modules = app_modules
    config = app_config

    # config가 바뀌면 감사 스토어 경로도 달라질 수 있으므로 캐시를 무효화한다.
    _audit_event_store = None
    _audit_event_store_signature = None

    # privacy.enabled: false → PII 마스킹 비활성화
    privacy_config = config.get("privacy", {})
    if not privacy_config.get("enabled", True):
        _privacy_masker = None
        logger.info("🔓 Upload API：PII 掩码已禁用（privacy.enabled: false）")


JOBS_FILE = Path("/app/uploads/jobs.json")
JOBS_DB_FILE = Path("./uploads/upload_jobs.sqlite3")
AUDIT_EVENTS_DB_FILE = Path("./uploads/audit_events.sqlite3")
DEFAULT_UPLOAD_JOB_RETENTION_SECONDS = 24 * 60 * 60
TERMINAL_UPLOAD_JOB_STATUSES = {"completed", "failed", "cancelled"}
POSTGRES_UPLOAD_JOB_STORE_TYPES = {"postgres", "postgresql", "cloudsql", "cloud_sql"}

# 분할(chunked) 업로드 상수 (#11)
UPLOAD_STREAM_CHUNK_SIZE = 1024 * 1024  # 1MB: 서버 측 스트리밍 read 단위
CHUNKED_UPLOAD_RECOMMENDED_CHUNK_SIZE = 8 * 1024 * 1024  # 8MB: 클라이언트 권장 조각 크기

# 협조적 취소 상수 (#30)
UPLOAD_CANCELLED_MESSAGE = "文档处理已取消"
UPLOAD_CANCEL_REQUESTED_MESSAGE = "已请求取消文档处理"
UPLOAD_CANCELLABLE_STATUSES = {"receiving", "pending", "processing", "cancelling"}

# 스토어 인스턴스 캐시: 설정 시그니처가 바뀌면 재생성한다.
_upload_job_store: UploadJobStore | None = None
_upload_job_store_signature: tuple[str, str] | None = None

# 감사 이벤트 스토어 캐시 (#36)
_audit_event_store: AuditEventStore | None = None
_audit_event_store_signature: tuple[str, str] | None = None


def _resolve_upload_job_store() -> UploadJobStore:
    """설정된 업로드 잡 스토어를 해석한다.

    기본값은 SQLite로, 프로세스 재시작에도 잡 상태가 보존된다(의존성 0).
    JSON은 명시적 로컬 개발용 폴백, Postgres는 멀티워커/오토스케일 공유용이다.
    """
    global _upload_job_store, _upload_job_store_signature
    upload_config = config.get("uploads", {}) if isinstance(config, dict) else {}
    store_config = upload_config.get("job_store", {}) if isinstance(upload_config, dict) else {}
    if not isinstance(store_config, dict):
        store_config = {}
    store_type = _upload_job_store_type(store_config=store_config)
    if store_type in {"json", "file"}:
        path_value = (
            os.getenv("ONERAG_UPLOAD_JOBS_FILE")
            or store_config.get("path")
            or store_config.get("file_path")
            or str(JOBS_FILE)
        )
        path = Path(str(path_value)).expanduser().resolve()
        signature = ("json", str(path))
        if _upload_job_store is None or _upload_job_store_signature != signature:
            _upload_job_store = JsonUploadJobStore(path)
            _upload_job_store_signature = signature
        return _upload_job_store
    if store_type in POSTGRES_UPLOAD_JOB_STORE_TYPES:
        database_url = (
            os.getenv("ONERAG_UPLOAD_JOB_DATABASE_URL")
            or store_config.get("database_url")
            or store_config.get("url")
            or os.getenv("DATABASE_URL")
        )
        signature = ("postgres", str(bool(database_url)))
        if _upload_job_store is None or _upload_job_store_signature != signature:
            _upload_job_store = PostgresUploadJobStore(str(database_url) if database_url else None)
            _upload_job_store_signature = signature
        return _upload_job_store
    if store_type not in {"sqlite", "sqlite3"}:
        logger.warning(f"Unknown upload job store type '{store_type}'; falling back to sqlite")
    upload_dir = Path(str(upload_config.get("directory", "./uploads"))).expanduser().resolve()
    path_value = (
        os.getenv("ONERAG_UPLOAD_JOB_DB_PATH")
        or store_config.get("path")
        or store_config.get("database_path")
        or str(upload_dir / JOBS_DB_FILE.name)
    )
    path = Path(str(path_value)).expanduser().resolve()
    signature = ("sqlite", str(path))
    if _upload_job_store is None or _upload_job_store_signature != signature:
        _upload_job_store = SQLiteUploadJobStore(path)
        _upload_job_store_signature = signature
    return _upload_job_store


def _upload_job_store_type(*, store_config: dict[str, Any] | None = None) -> str:
    """解析上传任务存储类型（env > config > 默认 sqlite）。"""
    if store_config is None:
        upload_config = config.get("uploads", {}) if isinstance(config, dict) else {}
        store_config = upload_config.get("job_store", {}) if isinstance(upload_config, dict) else {}
        if not isinstance(store_config, dict):
            store_config = {}
    return str(
        os.getenv("ONERAG_UPLOAD_JOB_STORE")
        or store_config.get("type")
        or "sqlite"
    ).strip().lower()


def _is_shared_upload_job_store() -> bool:
    """是否为多实例共享（Postgres）存储。"""
    return _upload_job_store_type() in POSTGRES_UPLOAD_JOB_STORE_TYPES


def _resolve_audit_event_store() -> AuditEventStore | None:
    """설정된 운영 감사 이벤트 스토어를 해석한다 (#36).

    기본 백엔드는 의존성 0의 SQLite이며, type을 disabled/none/off로 두면
    감사 기록을 비활성화한다. upload_job_store와 동일한 config/env 컨벤션을 따른다.

    우선순위: 환경변수(ONERAG_AUDIT_*) > config(uploads.audit_event_store) > 기본 sqlite.
    """
    global _audit_event_store, _audit_event_store_signature
    upload_config = config.get("uploads", {}) if isinstance(config, dict) else {}
    if not isinstance(upload_config, dict):
        upload_config = {}
    audit_config = upload_config.get("audit_event_store", {})
    if not isinstance(audit_config, dict):
        audit_config = {}
    store_type = str(
        os.getenv("ONERAG_AUDIT_EVENT_STORE")
        or audit_config.get("type")
        or "sqlite"
    ).strip().lower()
    if store_type in {"disabled", "none", "off"}:
        return None
    if store_type not in {"sqlite", "sqlite3"}:
        logger.warning(f"Unknown audit event store type '{store_type}'; falling back to sqlite")
    upload_dir = Path(str(upload_config.get("directory", "./uploads"))).expanduser().resolve()
    path_value = (
        os.getenv("ONERAG_AUDIT_EVENT_DB_PATH")
        or audit_config.get("path")
        or audit_config.get("database_path")
        or str(upload_dir / AUDIT_EVENTS_DB_FILE.name)
    )
    path = Path(str(path_value)).expanduser().resolve()
    signature = ("sqlite", str(path))
    if _audit_event_store is None or _audit_event_store_signature != signature:
        _audit_event_store = SQLiteAuditEventStore(path)
        _audit_event_store_signature = signature
    return _audit_event_store


def _record_operational_audit_event(
    event: dict[str, Any],
    *,
    failure_context: str,
) -> None:
    """파괴적 운영 작업에 대한 감사 이벤트를 non-blocking으로 기록한다 (#36).

    감사 기록 실패가 본래의 운영 작업(삭제 등)을 막아서는 안 되므로, 예외는
    경고 로그만 남기고 삼킨다.
    """
    try:
        store = _resolve_audit_event_store()
        if store is not None:
            store.record_event(event)
    except Exception as error:
        logger.warning("Failed to record %s audit event: %s", failure_context, error)


def _upload_job_store_fail_closed() -> bool:
    """공유 스토어 장애 시 예외를 전파(fail-closed)할지 여부.

    공유 스토어에서 조용히 빈 결과로 폴백하면 다른 인스턴스의 잡이
    유실된 것처럼 보이므로, 테스트 환경이 아니면 기본 fail-closed.
    """
    if not _is_shared_upload_job_store():
        return False
    environment = str(os.getenv("ENVIRONMENT") or config.get("environment") or "").lower()
    if environment == "test":
        return False
    return str(os.getenv("ONERAG_UPLOAD_JOB_STORE_FAIL_OPEN") or "").lower() not in {
        "1",
        "true",
        "yes",
    }


def _upload_job_retention_seconds() -> int:
    """解析已结束任务的保留时间（秒）。"""
    upload_config = config.get("uploads", {}) if isinstance(config, dict) else {}
    store_config = upload_config.get("job_store", {}) if isinstance(upload_config, dict) else {}
    if not isinstance(store_config, dict):
        store_config = {}
    raw_value = (
        os.getenv("ONERAG_UPLOAD_JOB_RETENTION_SECONDS")
        or store_config.get("retention_seconds")
        or DEFAULT_UPLOAD_JOB_RETENTION_SECONDS
    )
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid upload job retention value '{raw_value}'; using default "
            f"{DEFAULT_UPLOAD_JOB_RETENTION_SECONDS}s"
        )
        return DEFAULT_UPLOAD_JOB_RETENTION_SECONDS


def _coerce_job_timestamp(value: Any) -> float | None:
    """将任务时间戳（epoch 数字/ISO 字符串）规范化为 epoch 浮点数。"""
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return None
    return None


def _terminal_job_timestamp(job: dict[str, Any]) -> float | None:
    """估算已结束任务的结束时间（用于保留期判定）。"""
    for key in ("completed_at", "failed_at", "cancelled_at", "updated_at"):
        timestamp = _coerce_job_timestamp(job.get(key))
        if timestamp is not None:
            return timestamp
    start_time = _coerce_job_timestamp(job.get("start_time"))
    processing_time = _coerce_job_timestamp(job.get("processing_time"))
    if start_time is not None and processing_time is not None:
        return start_time + processing_time
    return start_time


def cleanup_upload_jobs(
    jobs: dict[str, dict[str, Any]],
    *,
    now: float | None = None,
    retention_seconds: int | None = None,
) -> int:
    """从内存映射中移除超过保留期的已结束任务。"""
    retention = _upload_job_retention_seconds() if retention_seconds is None else retention_seconds
    if retention <= 0:
        return 0
    now_timestamp = datetime.now().timestamp() if now is None else now
    expired_job_ids: list[str] = []
    for job_id, job in jobs.items():
        if job.get("status") not in TERMINAL_UPLOAD_JOB_STATUSES:
            continue
        terminal_timestamp = _terminal_job_timestamp(job)
        if terminal_timestamp is None:
            continue
        if now_timestamp - terminal_timestamp >= retention:
            expired_job_ids.append(job_id)
    for job_id in expired_job_ids:
        del jobs[job_id]
    if expired_job_ids:
        logger.info(f"Cleaned up {len(expired_job_ids)} expired upload jobs")
    return len(expired_job_ids)


def load_upload_jobs() -> dict[str, dict[str, Any]]:
    """从持久存储加载上传任务状态"""
    try:
        store = _resolve_upload_job_store()
        if _is_shared_upload_job_store():
            cleanup_expired = getattr(store, "cleanup_expired", None)
            if callable(cleanup_expired):
                cleanup_expired(retention_seconds=_upload_job_retention_seconds())
        jobs = store.load_all()
        if cleanup_upload_jobs(jobs) and not _is_shared_upload_job_store():
            store.save_all(jobs)
        return jobs
    except Exception as e:
        logger.warning(f"Failed to load upload jobs: {e}")
        if _upload_job_store_fail_closed():
            raise
    return {}


def save_upload_jobs(jobs: dict[str, dict[str, Any]]):
    """将上传任务状态保存到持久存储"""
    try:
        if _is_shared_upload_job_store():
            # 오토스케일 환경에서 인스턴스 로컬 맵 전체 저장은 다른 인스턴스의
            # 잡을 덮어쓸 수 있으므로 공유 스토어에서는 per-job 저장만 허용한다.
            raise RuntimeError("Full-map upload job saves are disabled for shared job stores")
        cleanup_upload_jobs(jobs)
        _resolve_upload_job_store().save_all(jobs)
    except Exception as e:
        logger.error(f"Failed to save upload jobs: {e}")
        if _upload_job_store_fail_closed():
            raise


def save_upload_job(job_id: str) -> None:
    """仅保存单个任务（避免覆盖共享存储中的无关行）。"""
    job = upload_jobs.get(job_id)
    if not isinstance(job, dict):
        return
    if _is_shared_upload_job_store():
        try:
            _resolve_upload_job_store().save_all({job_id: job})
        except Exception as e:
            logger.error(f"Failed to save upload job {job_id}: {e}")
            if _upload_job_store_fail_closed():
                raise
        return
    save_upload_jobs(upload_jobs)


def delete_upload_job(job_id: str) -> None:
    """从存储中删除单条任务（共享存储按行删除）。"""
    if _is_shared_upload_job_store():
        try:
            store = _resolve_upload_job_store()
            delete = getattr(store, "delete", None)
            if callable(delete):
                delete(job_id)
        except Exception as e:
            logger.error(f"Failed to delete upload job {job_id}: {e}")
            if _upload_job_store_fail_closed():
                raise
        return
    save_upload_jobs(upload_jobs)


def _reload_upload_jobs_if_needed(job_id: str | None = None) -> None:
    """使用共享存储或缓存未命中时，更新进程本地任务缓存。"""
    global upload_jobs
    if _is_shared_upload_job_store() or (job_id is not None and job_id not in upload_jobs):
        loaded_jobs = load_upload_jobs()
        if _is_shared_upload_job_store() or job_id is None or job_id in loaded_jobs:
            upload_jobs = loaded_jobs


def _shared_upload_worker_lease_seconds() -> int:
    """解析共享存储工作器的任务租约时间（秒）。"""
    raw_value = os.getenv("ONERAG_UPLOAD_JOB_WORKER_LEASE_SECONDS") or "7200"
    try:
        return max(300, int(raw_value))
    except (TypeError, ValueError):
        logger.warning(
            "Invalid ONERAG_UPLOAD_JOB_WORKER_LEASE_SECONDS value '%s'; using 7200s",
            raw_value,
        )
        return 7200


def _initial_upload_jobs() -> dict[str, dict[str, Any]]:
    """모듈 import 시점의 초기 잡 맵을 로드한다.

    공유(Postgres) 스토어는 import 시점에 DB 연결을 만들지 않도록 건너뛴다.
    """
    if _is_shared_upload_job_store():
        logger.info("Skipping import-time shared upload job load")
        return {}
    return load_upload_jobs()


upload_jobs: dict[str, dict[str, Any]] = _initial_upload_jobs()


class DocumentInfo(BaseModel):
    """文档信息模型"""

    id: str
    filename: str
    file_type: str
    file_size: int
    upload_date: str
    status: str
    chunk_count: int | None = None
    processing_time: float | None = None
    error_message: str | None = None


class UploadResponse(BaseModel):
    """上传响应模型"""

    job_id: str
    message: str
    filename: str
    file_size: int
    estimated_processing_time: float
    timestamp: str


class JobStatusResponse(BaseModel):
    """작업 상태 응답 모델

    처리 프로비넌스(GAP #9): loader_type/splitter_type/storage_locations/
    extraction_summary는 백엔드가 '实际处理' 값을 권위 노출한다. 프론트엔드가
    파일 확장자·사용자 설정으로 추론하던 표기를 백엔드 실제값으로 대체한다.
    """

    job_id: str
    status: str
    progress: float
    message: str
    filename: str
    chunk_count: int | None = None
    processing_time: float | None = None
    error_message: str | None = None
    # 처리 프로비넌스(완료 후에만 채워짐; 미완료/미산출 시 None)
    loader_type: str | None = None
    splitter_type: str | None = None
    storage_locations: list[str] | None = None
    extraction_summary: dict[str, Any] | None = None
    timestamp: str


class DocumentListResponse(BaseModel):
    """文档列表响应模型"""

    documents: list[DocumentInfo]
    total_count: int
    page: int
    page_size: int
    has_next: bool


class BulkDeleteRequest(BaseModel):
    """批量删除请求模型"""

    ids: list[str] = Field(..., description="待删除文档 ID 列表")


class BulkDeleteResponse(BaseModel):
    """批量删除响应模型"""

    deleted_count: int
    failed_count: int
    failed_ids: list[str] = []
    message: str
    timestamp: str


class UploadCancelResponse(BaseModel):
    """上传任务取消响应模型（#30）"""

    job_id: str
    status: str
    progress: float
    message: str
    processing_time: float | None = None
    timestamp: str


class UploadRetryResponse(BaseModel):
    """上传任务重试响应模型（#10）"""

    job_id: str
    status: str
    message: str
    retry_count: int
    timestamp: str


class ChunkedUploadStartRequest(BaseModel):
    """大文件分块上传开始请求模型（#11）"""

    filename: str = Field(..., min_length=1, description="原始文件名")
    content_type: str | None = Field(None, description="文件 MIME 类型")
    file_size: int = Field(..., gt=0, description="文件总大小（字节）")
    metadata: dict[str, Any] | None = Field(None, description="文档元数据")


class ChunkedUploadStartResponse(BaseModel):
    """大文件分块上传开始响应模型（#11）"""

    job_id: str
    message: str
    filename: str
    file_size: int
    chunk_size: int
    timestamp: str


class ChunkedUploadChunkResponse(BaseModel):
    """大文件分块上传分片响应模型（#11）"""

    job_id: str
    status: str
    received_size: int
    file_size: int
    progress: float
    message: str
    timestamp: str


class ChunkedUploadCompleteRequest(BaseModel):
    """大文件分块上传完成请求模型（#11）"""

    metadata: dict[str, Any] | None = Field(None, description="文档元数据（可选）")


def get_upload_directory() -> Path:
    """返回上传目录"""
    upload_path = config.get("uploads", {}).get("directory", "./uploads")
    upload_dir = Path(upload_path).resolve()
    try:
        upload_dir.mkdir(exist_ok=True, parents=True)
        temp_dir = upload_dir / "temp"
        temp_dir.mkdir(exist_ok=True)
    except PermissionError:
        upload_dir = Path("/app/uploads")
        upload_dir.mkdir(exist_ok=True, parents=True)
        temp_dir = upload_dir / "temp"
        temp_dir.mkdir(exist_ok=True)
    return upload_dir


def estimate_processing_time(file_size: int, file_type: str) -> float:
    """根据文件大小和类型预测处理时间"""
    base_time = 20.0
    size_mb = file_size / (1024 * 1024)
    processing_rates = {
        "pdf": 15.0,
        "docx": 10.0,
        "pptx": 12.0,
        "xlsx": 20.0,
        "txt": 3.0,
        "md": 3.0,
        "html": 8.0,
        "csv": 12.0,
        "json": 5.0,
    }
    ext = file_type.lower()
    rate = processing_rates.get(ext, 10.0)
    estimated_time = base_time + size_mb * rate
    if size_mb > 10:
        extra_penalty = (size_mb - 10) * 3
        estimated_time += extra_penalty
    return max(30.0, min(estimated_time, 1800.0))


def _document_processing_timeout_seconds() -> float:
    """백그라운드 문서 처리의 최대 허용 시간(초)을 반환한다.

    BackgroundTasks 처리에는 본래 상한이 없어, 처리가 지연되면
    프론트 polling 한도(약 30분)를 넘겨 좀비 작업으로 남는다. 이 값을 프론트
    polling보다 짧게(기본 25분) 두어 백엔드가 먼저 명시적으로 실패시킨다.

    우선순위: uploads.processing_timeout_seconds(config) >
    ONERAG_UPLOAD_PROCESSING_TIMEOUT_SECONDS(env) > 기본 1500초.
    """
    upload_config = config.get("uploads", {}) if isinstance(config, dict) else {}
    raw: Any = upload_config.get("processing_timeout_seconds")
    if raw is None:
        raw = os.getenv("ONERAG_UPLOAD_PROCESSING_TIMEOUT_SECONDS")
    try:
        value = float(raw) if raw is not None else 1500.0
    except (TypeError, ValueError):
        logger.warning(
            "Invalid upload processing timeout %r; falling back to 1500s", raw
        )
        value = 1500.0
    # 최소 60초 보장(과도하게 짧은 설정으로 정상 처리가 끊기는 것 방지).
    return max(60.0, value)


def validate_file(file: UploadFile) -> dict[str, Any]:
    """文件校验"""
    supported_types = {
        "application/pdf": "pdf",
        "text/plain": "txt",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "text/csv": "csv",
        "text/html": "html",
        "text/markdown": "md",
        "application/json": "json",
    }
    if file.content_type not in supported_types:
        ext = Path(file.filename or "unknown").suffix.lower()[1:]
        if ext not in supported_types.values():
            return {
                "valid": False,
                "error": {
                    "error": "不支持的文件格式",
                    "message": f"'{file.content_type}' 不支持该格式",
                    "suggestion": "支持格式：PDF、DOCX、PPTX、TXT、MD、CSV、XLSX、HTML、JSON",
                    "file_name": file.filename,
                    "file_type": file.content_type,
                    "supported_extensions": [
                        ".pdf",
                        ".docx",
                        ".pptx",
                        ".txt",
                        ".md",
                        ".csv",
                        ".xlsx",
                        ".html",
                        ".json",
                    ],
                },
            }
        file_type = ext
    else:
        file_type = supported_types[file.content_type]
    max_size = config.get("uploads", {}).get("max_file_size", 50 * 1024 * 1024)
    if file.size and file.size > max_size:
        max_size_mb = max_size / (1024 * 1024)
        file_size_mb = file.size / (1024 * 1024)
        return {
            "valid": False,
            "error": {
                "error": "文件大小超限",
                "message": f"文件大小（{file_size_mb:.1f}MB）超过最大允许大小（{max_size_mb:.0f}MB）",
                "suggestion": "请压缩文件或拆分为多个文件后上传",
                "file_name": file.filename,
                "file_size_mb": round(file_size_mb, 1),
                "max_size_mb": int(max_size_mb),
            },
        }
    return {"valid": True, "file_type": file_type}


def _persist_original_file(
    content: bytes,
    *,
    upload_dir: Path,
    job_id: str,
    safe_filename: str,
    content_type: str | None,
) -> dict[str, Any]:
    """업로드 원본을 보관하고 잡 페이로드에 병합할 메타데이터를 반환한다 (#10).

    보관 실패는 업로드 자체를 막지 않는다(원본 미보관 시 재처리/원본 다운로드만
    비활성). 반환 메타에는 original_file_path/source_uri/original_storage_backend 등이
    포함된다.
    """
    try:
        stored = store_original_file(
            content,
            app_config=config,
            upload_dir=upload_dir,
            job_id=job_id,
            safe_filename=safe_filename,
            content_type=content_type,
        )
    except OriginalFileStorageError as error:
        logger.warning("Failed to store original file for %s: %s", job_id, error)
        return {}
    payload: dict[str, Any] = dict(stored.metadata)
    if stored.local_path is not None:
        payload["original_file_path"] = str(stored.local_path)
    if stored.source_uri:
        payload.setdefault("source_uri", stored.source_uri)
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _delete_original_for_document(document_id: str) -> None:
    """文档删除 시 보관 원본을 best-effort로 정리한다 (#10).

    문서 id는 업로드 job_id와 동일하므로 잡 메타데이터에서 원본 참조를 해석한다.
    원본이 없거나 해석 불가하면 조용히 통과한다(삭제 자체를 막지 않음).
    """
    _reload_upload_jobs_if_needed(document_id)
    job = upload_jobs.get(document_id)
    if not isinstance(job, dict):
        return
    metadata = original_storage_metadata(job)
    if not metadata and not job.get("original_file_path"):
        return
    upload_dir = get_upload_directory()
    try:
        reference = resolve_original_reference(
            metadata=job,
            app_config=config,
            upload_dir=upload_dir,
        )
        delete_original_reference(reference, app_config=config)
    except OriginalFileNotFoundError:
        return
    except OriginalFileStorageError as error:
        logger.warning("Failed to delete original for %s: %s", document_id, error)


def _is_upload_cancel_requested(job_id: str) -> bool:
    """협조적 취소 요청 여부를 확인한다 (#30).

    공유 스토어에서는 다른 인스턴스의 취소 요청을 반영하기 위해 재로딩한다.
    """
    if _is_shared_upload_job_store():
        _reload_upload_jobs_if_needed(job_id)
    job = upload_jobs.get(job_id)
    if not isinstance(job, dict):
        return False
    return job.get("cancel_requested") is True or job.get("status") in {
        "cancelling",
        "cancelled",
    }


def _is_current_processing_attempt(
    *,
    job_id: str,
    processing_attempt_id: str | None,
) -> bool:
    """현재 처리 attempt가 잡에 기록된 최신 attempt인지 확인한다 (#30).

    processing_attempt_id가 None이면(레거시/단일 경로) 항상 True. 이전 attempt가
    뒤늦게 상태를 덮어쓰는 것을 막기 위한 stale 가드다.
    """
    if processing_attempt_id is None:
        return True
    if _is_shared_upload_job_store():
        _reload_upload_jobs_if_needed(job_id)
    job = upload_jobs.get(job_id)
    if not isinstance(job, dict):
        return False
    return job.get("processing_attempt_id") == processing_attempt_id


def _upload_job_processing_time(job: dict[str, Any]) -> float | None:
    """估算任务累计处理时间（秒）；无则按 start_time 计算。"""
    existing = _coerce_job_timestamp(job.get("processing_time"))
    if existing is not None:
        return existing
    start_time = _coerce_job_timestamp(job.get("start_time"))
    if start_time is None:
        return None
    return max(0.0, datetime.now().timestamp() - start_time)


def _safe_unlink_child_file(path_value: Any, parent: Path) -> None:
    """仅安全删除 parent 子目录下的文件（防止路径穿越）。"""
    if not path_value:
        return
    candidate = Path(str(path_value)).expanduser()
    if not candidate.is_absolute():
        candidate = parent / candidate
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return
    try:
        if candidate.is_file():
            candidate.unlink()
    except OSError as error:
        logger.warning("Failed to clean up cancelled upload file: %s", type(error).__name__)


def _cleanup_cancelled_upload_files(
    job: dict[str, Any],
    *,
    temp_file_path: Path | str | None = None,
    original_file_path: Path | str | None = None,
) -> None:
    """취소된 잡의 임시 파일과 보관 원본을 정리한다.

    GCS 백엔드 원본은 객체 스토리지에서 삭제하고, 로컬 원본/임시 파일은
    각각의 루트 하위인 경우에만 unlink한다.
    """
    upload_dir = get_upload_directory()
    temp_dir = upload_dir / "temp"
    originals_dir = upload_dir / "originals"
    _safe_unlink_child_file(temp_file_path or job.get("temp_file_path"), temp_dir)
    if str(job.get("original_storage_backend") or "").lower() == "gcs":
        try:
            reference = resolve_original_reference(
                metadata=job,
                app_config=config,
                upload_dir=upload_dir,
            )
            delete_original_reference(reference, app_config=config)
        except OriginalFileStorageError as error:
            logger.warning(
                "Failed to clean up cancelled object-store original: %s",
                type(error).__name__,
            )
        return
    _safe_unlink_child_file(original_file_path or job.get("original_file_path"), originals_dir)


def _finalize_upload_job_cancelled(
    *,
    job_id: str,
    temp_file_path: Path | str | None = None,
    original_file_path: Path | str | None = None,
    finalized_by: str = "background",
) -> bool:
    """잡을 cancelled로 마무리하고 부분 산출물을 정리한다 (#30).

    Returns:
        취소 마무리가 수행/확인되면 True, 종결 상태라 불가하면 False.
    """
    job = upload_jobs.get(job_id)
    if not isinstance(job, dict):
        return False
    if job.get("status") == "cancelled":
        _cleanup_cancelled_upload_files(
            job, temp_file_path=temp_file_path, original_file_path=original_file_path
        )
        return True
    if job.get("status") in {"completed", "failed"}:
        return False

    now = datetime.now().isoformat()
    processing_time = _upload_job_processing_time(job)
    job.update(
        {
            "status": "cancelled",
            "progress": 0,
            "cancel_requested": True,
            "cancelled_at": job.get("cancelled_at") or now,
            "message": UPLOAD_CANCELLED_MESSAGE,
            "error_message": None,
        }
    )
    if processing_time is not None:
        job["processing_time"] = processing_time
    # 부분 적재 벡터 정리(best-effort)
    try:
        retrieval_module = modules.get("retrieval")
        if asyncio.iscoroutine(retrieval_module) or isinstance(retrieval_module, asyncio.Future):
            retrieval_module = None  # 동기 정리 경로에서는 미해소 Future를 건드리지 않는다.
        if retrieval_module is not None and hasattr(retrieval_module, "delete_document"):
            result = retrieval_module.delete_document(job_id)
            if asyncio.iscoroutine(result):
                # 동기 컨텍스트에서 호출될 수 있으므로 코루틴은 닫아 경고를 막는다.
                result.close()
    except Exception as error:
        logger.warning("Cancelled upload vector cleanup failed for %s: %s", job_id, error)
    _cleanup_cancelled_upload_files(
        job, temp_file_path=temp_file_path, original_file_path=original_file_path
    )
    save_upload_job(job_id)
    _record_operational_audit_event(
        {
            "action": "document.upload.cancelled",
            "target_type": "upload_job",
            "target_id": job_id,
            "document_id": job_id,
            "metadata": {"finalized_by": finalized_by},
        },
        failure_context="upload cancel",
    )
    return True


def _finalize_if_upload_cancel_requested(
    *,
    job_id: str,
    temp_file_path: Path | str | None = None,
    original_file_path: Path | str | None = None,
) -> bool:
    """若收到取消请求，将任务置为 cancelled（检查点用途，#30）。"""
    if not _is_upload_cancel_requested(job_id):
        return False
    return _finalize_upload_job_cancelled(
        job_id=job_id,
        temp_file_path=temp_file_path,
        original_file_path=original_file_path,
    )


# ============================================================
# 처리 프로비넌스 헬퍼 (GAP #9)
# ============================================================
# 백엔드가 '实际处理' loader/splitter/storage/extraction 값을 산출한다.
# 일본어/도메인/멀티테넌시/Document AI OCR 하드코딩은 제외해 범용성을 유지한다.

# 파일타입 → 사람이 읽기 좋은 로더 라벨
LOADER_TYPE_LABELS = {
    "pdf": "PDF",
    "txt": "Text",
    "docx": "DOCX",
    "doc": "DOCX",
    "pptx": "PPTX",
    "xlsx": "XLSX",
    "xls": "XLSX",
    "csv": "CSV",
    "html": "HTML",
    "htm": "HTML",
    "md": "Markdown",
    "markdown": "Markdown",
    "json": "JSON",
}

# splitter_type 식별자 → 사람이 읽기 좋은 라벨
SPLITTER_TYPE_LABELS = {
    "recursive": "Recursive",
    "semantic": "Semantic",
    "table_row": "Table Row",
}

# extraction_summary로 집계하는 추출 메타 필드(loader가 문서 metadata에 기록).
EXTRACTION_SUMMARY_FIELDS = {
    "page_count",
    "scanned_page_count",
    "table_count",
    "extraction_warnings",
}


def _coerce_optional_int(value: Any) -> int | None:
    """将值转换为整数；None/空字符串/布尔值按 None 处理。"""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _doc_metadata(document: Any) -> dict[str, Any] | None:
    """从文档对象中安全提取 dict 类型的 metadata（无则返回 None）。"""
    metadata = getattr(document, "metadata", None)
    return metadata if isinstance(metadata, dict) else None


def _first_metadata_value(documents: list[Any], keys: tuple[str, ...]) -> str | None:
    """返回文档 metadata 中 keys 里第一个非空字符串。"""
    for document in documents:
        metadata = _doc_metadata(document)
        if metadata is None:
            continue
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _upload_loader_type(file_type: str, documents: list[Any]) -> str:
    """실제 사용된 로더 라벨을 산출한다(GAP #9).

    PDF는 loader가 기록한 extraction_method(pypdf 등)를 라벨에 반영해 '如何'
    추출됐는지 노출한다. 그 외 파일타입은 LOADER_TYPE_LABELS로 매핑한다.

    Args:
        file_type: 업로드 파일타입(확장자, 예: "pdf"/"xlsx").
        documents: load_document 결과 문서 리스트(metadata.extraction_method 등 보유).

    Returns:
        사람이 읽기 좋은 로더 라벨(예: "PDF (pypdf)", "XLSX").
    """
    normalized = str(file_type or "").strip().lower()
    if normalized == "pdf":
        extraction_method = _first_metadata_value(
            documents, ("extraction_method", "ocr_backend", "layout_backend")
        )
        if extraction_method:
            return f"PDF ({extraction_method})"
    return LOADER_TYPE_LABELS.get(normalized, normalized.upper() or "Document")


def _upload_splitter_type(chunks: list[Any], document_processor: Any) -> str | None:
    """실제 사용된 스플리터 라벨을 산출한다(GAP #9).

    청크 metadata의 splitter_type을 우선 사용한다(표 자동청킹 시 "table_row"가
    섞이므로 ' + '로 병기). 청크에 정보가 없으면 processor의 설정값으로 폴백한다.

    Args:
        chunks: split_documents 결과 청크 리스트(metadata.splitter_type 보유).
        document_processor: splitter_type 속성을 가진 처리기(폴백용).

    Returns:
        스플리터 라벨(예: "Recursive", "Recursive + Table Row") 또는 산출 불가 시 None.
    """
    splitter_types: list[str] = []
    for chunk in chunks:
        metadata = _doc_metadata(chunk)
        if metadata is None:
            continue
        splitter_type = metadata.get("splitter_type")
        if isinstance(splitter_type, str) and splitter_type.strip():
            normalized = splitter_type.strip()
            if normalized not in splitter_types:
                splitter_types.append(normalized)
    if not splitter_types:
        configured = getattr(document_processor, "splitter_type", None)
        if isinstance(configured, str) and configured.strip():
            splitter_types.append(configured.strip())
    if not splitter_types:
        return None
    return " + ".join(
        SPLITTER_TYPE_LABELS.get(splitter_type, splitter_type)
        for splitter_type in splitter_types
    )


def _upload_storage_locations(job: dict[str, Any]) -> list[str]:
    """실제 저장된 위치 목록을 산출한다(GAP #9).

    항상 Vector Database를 포함하고, 원본 보관(local/GCS)이 있으면 Original File
    Storage를 추가한다(이미 차용된 원본보관·source_uri 인프라에 직접 얹는다).

    Args:
        job: 업로드 작업 dict(original_storage_backend/original_file_path/source_uri).

    Returns:
        저장 위치 라벨 리스트.
    """
    locations = ["Vector Database"]
    original_backend = str(job.get("original_storage_backend") or "").strip().lower()
    if original_backend == GCS_ORIGINAL_STORAGE_BACKEND:
        locations.append("Original File Storage (GCS)")
    elif job.get("original_file_path") or job.get("source_uri"):
        locations.append("Original File Storage")
    return locations


def _extract_extraction_summary(documents: list[Any]) -> dict[str, Any] | None:
    """추출 진단 요약을 집계한다(GAP #9, PDF 스캔/표/경고).

    loader가 문서 metadata에 기록한 page_number/scanned_page/table_count/
    extraction_warnings를 집계한다. 추출 메타가 전혀 없으면 None을 반환한다
    (예: xlsx/txt는 요약 없음).

    Args:
        documents: load_document 결과 문서 리스트.

    Returns:
        {"page_count","scanned_page_count","table_count","extraction_warnings"}
        부분 집합 또는 추출 메타 부재 시 None.
    """
    summary: dict[str, Any] = {}
    warnings: list[Any] = []
    saw_extraction_meta = False
    page_numbers: set[int] = set()
    scanned_page_count = 0
    saw_scanned_page = False
    derived_table_count = 0
    saw_table_count = False

    for document in documents:
        metadata = _doc_metadata(document)
        if metadata is None:
            continue
        # 추출 메타 마커: loader가 기록하는 extraction_method가 있으면 추출 문서로 본다.
        if "extraction_method" not in metadata:
            continue
        saw_extraction_meta = True

        page_number = _coerce_optional_int(metadata.get("page_number"))
        if page_number is not None:
            page_numbers.add(page_number)
        else:
            page_index = _coerce_optional_int(metadata.get("page_index"))
            if page_index is not None:
                page_numbers.add(page_index + 1)

        if isinstance(metadata.get("scanned_page"), bool):
            saw_scanned_page = True
            if metadata["scanned_page"]:
                scanned_page_count += 1

        page_table_count = _coerce_optional_int(metadata.get("table_count"))
        if page_table_count is not None:
            saw_table_count = True
            derived_table_count += page_table_count

        metadata_warnings = metadata.get("extraction_warnings")
        if isinstance(metadata_warnings, list):
            warnings.extend(metadata_warnings)
        elif metadata_warnings:
            warnings.append(metadata_warnings)

    if not saw_extraction_meta:
        return None
    if page_numbers:
        summary["page_count"] = len(page_numbers)
    if saw_scanned_page:
        summary["scanned_page_count"] = scanned_page_count
    if saw_table_count:
        summary["table_count"] = derived_table_count
    if warnings:
        seen: set[str] = set()
        summary["extraction_warnings"] = [
            warning
            for warning in warnings
            if not (str(warning) in seen or seen.add(str(warning)))
        ]
    return summary or None


async def process_document_background(
    job_id: str,
    file_path: Path,
    filename: str,
    file_type: str,
    processing_attempt_id: str | None = None,
) -> None:
    """백그라운드 문서 처리.

    각 단계 경계마다 (1) 취소 요청 협조 확인(#30), (2) stale attempt 가드(#30)를
    수행해, 취소 요청 시 즉시 중단하고 이전 attempt의 늦은 쓰기를 차단한다.
    인덱싱(add_documents) 시작 이후는 취소 불가하다(원본 구현과 동일 한계 수용).
    """
    indexing_started = False
    try:
        if not _is_current_processing_attempt(
            job_id=job_id, processing_attempt_id=processing_attempt_id
        ):
            return
        if _finalize_if_upload_cancel_requested(job_id=job_id, temp_file_path=file_path):
            return
        upload_jobs[job_id].update(
            {"status": "processing", "progress": 10, "message": "文档处理开始..."}
        )
        save_upload_job(job_id)
        document_processor = modules.get("document_processor")
        retrieval_module = modules.get("retrieval")
        if not document_processor or not retrieval_module:
            raise Exception("Required modules not available")
        # 일부 DI 구성(예: chroma)에서 retrieval 모듈이 코루틴/Future로 지연 제공되므로,
        # add_documents 호출 전에 실제 모듈로 해소한다('_asyncio.Future' has no attribute 방지).
        if asyncio.iscoroutine(retrieval_module) or isinstance(retrieval_module, asyncio.Future):
            retrieval_module = await retrieval_module
        logger.info(f"Loading document: {filename}")
        upload_jobs[job_id].update({"progress": 30, "message": "文档加载中..."})
        save_upload_job(job_id)
        file_size = file_path.stat().st_size

        # PII 마스킹: 파일명에서 개인정보 마스킹 (활성화 시에만)
        # 예: "홍길동 고객님.txt" → "고객_고객님.txt"
        if _privacy_masker:
            masked_filename = _privacy_masker.mask_filename(filename)
            if masked_filename != filename:
                logger.info(f"文件名 PII 掩码已应用：{filename} → {masked_filename}")
        else:
            masked_filename = filename  # PII 마스킹 비활성화 시 원본 사용

        original_file_path = upload_jobs.get(job_id, {}).get("original_file_path")
        docs = await document_processor.load_document(
            str(file_path),
            {
                "document_id": job_id,
                "source_file": masked_filename,
                "file_type": file_type,
                "original_file_size": file_size,
            },
        )
        # 체크포인트(로드 후): 취소 요청/이전 attempt 확인 (#30)
        if _finalize_if_upload_cancel_requested(
            job_id=job_id, temp_file_path=file_path, original_file_path=original_file_path
        ):
            return
        if not _is_current_processing_attempt(
            job_id=job_id, processing_attempt_id=processing_attempt_id
        ):
            return
        logger.info(f"Splitting document into chunks: {len(docs)} documents")
        upload_jobs[job_id].update({"progress": 50, "message": "文档分块中..."})
        save_upload_job(job_id)
        chunks = await document_processor.split_documents(docs)
        # 체크포인트(분할 후): 취소 요청/이전 attempt 확인 (#30)
        if _finalize_if_upload_cancel_requested(
            job_id=job_id, temp_file_path=file_path, original_file_path=original_file_path
        ):
            return
        if not _is_current_processing_attempt(
            job_id=job_id, processing_attempt_id=processing_attempt_id
        ):
            return
        logger.info(f"Document split into {len(chunks)} chunks")
        upload_jobs[job_id].update(
            {"progress": 70, "message": f"正在生成嵌入...（{len(chunks)} 个分块）"}
        )
        save_upload_job(job_id)
        # 병렬 임베딩 사용: 청크를 워커별로 분할해 동시 임베딩한다.
        # 대용량 PDF(수백~수천 청크)에서 직렬 embed_chunks 대비 처리 시간을 단축해
        # 프론트엔드 polling 한도 내 완료 가능성을 높인다 (순서 보장됨).
        embedded_chunks = await document_processor.embed_chunks_parallel(chunks)
        # 체크포인트(임베딩 후, 인덱싱 직전): 취소 요청/이전 attempt 확인 (#30)
        # add_documents 시작 이후는 취소 불가하므로 마지막 협조 지점이다.
        if _finalize_if_upload_cancel_requested(
            job_id=job_id, temp_file_path=file_path, original_file_path=original_file_path
        ):
            return
        if not _is_current_processing_attempt(
            job_id=job_id, processing_attempt_id=processing_attempt_id
        ):
            return
        upload_jobs[job_id].update(
            {"progress": 90, "message": f"正在写入向量数据库...（{len(embedded_chunks)} 个嵌入）"}
        )
        save_upload_job(job_id)
        indexing_started = True
        upload_jobs[job_id]["indexing_started"] = True
        index_result = await retrieval_module.add_documents(embedded_chunks)
        if isinstance(index_result, dict):
            success_count = int(index_result.get("success_count", 0))
            error_count = int(index_result.get("error_count", 0))
            total_count = int(index_result.get("total_count", len(embedded_chunks)))
            if error_count > 0 or success_count != total_count:
                if success_count > 0 and hasattr(retrieval_module, "delete_document"):
                    try:
                        await retrieval_module.delete_document(job_id)
                    except Exception as cleanup_error:
                        logger.warning(
                            f"Partial upload cleanup failed for job_id={job_id}: {cleanup_error}"
                        )
                errors = index_result.get("errors") or ["unknown error"]
                first_error = errors[0] if isinstance(errors, list) else str(errors)
                raise RuntimeError(
                    "写入向量数据库失败： "
                    f"成功 {success_count}/{total_count}，失败 {error_count}。 "
                    f"首个错误：{first_error}"
                )
        elif isinstance(index_result, int) and index_result != len(embedded_chunks):
            raise RuntimeError(
                "写入向量数据库失败： "
                f"成功 {index_result}/{len(embedded_chunks)}，失败 {len(embedded_chunks) - index_result}"
            )
        try:
            os.unlink(file_path)
        except Exception as e:
            logger.warning(f"Failed to delete temp file: {e}")
        processing_time = datetime.now().timestamp() - upload_jobs[job_id]["start_time"]
        # 처리 프로비넌스 산출(GAP #9): 实际处理 loader/splitter/storage/extraction
        # 값을 백엔드 권위로 기록한다. 산출 실패는 비치명적(필드만 누락).
        provenance_update: dict[str, Any] = {
            "loader_type": _upload_loader_type(file_type, docs),
            "splitter_type": _upload_splitter_type(chunks, document_processor),
            "storage_locations": _upload_storage_locations(upload_jobs[job_id]),
        }
        extraction_summary = _extract_extraction_summary(docs)
        if extraction_summary:
            provenance_update["extraction_summary"] = extraction_summary
        upload_jobs[job_id].update(
            {
                "status": "completed",
                "progress": 100,
                "message": "文档处理完成",
                "chunk_count": len(chunks),
                "processing_time": processing_time,
                **provenance_update,
            }
        )
        save_upload_job(job_id)
        logger.info(
            f"Document processing completed: {filename}, {len(chunks)} chunks, {processing_time:.2f}s"
        )
    except Exception as error:
        logger.error(f"Document processing failed: {error}")
        # retry_safe: 보관 원본이 있으면 /retry로 재처리 가능함을 표시한다 (#10/#30).
        # 인덱싱이 시작된 후의 실패도 원본만 있으면 멱등 재처리가 안전하다.
        original_file_path = upload_jobs.get(job_id, {}).get("original_file_path")
        upload_jobs[job_id].update(
            {
                "status": "failed",
                "progress": 0,
                "message": "文档处理失败",
                "error_message": str(error),
                "retry_safe": bool(original_file_path),
                "indexing_started": indexing_started,
                "failed_at": datetime.now().isoformat(),
            }
        )
        save_upload_job(job_id)
        # 처리용 임시 파일만 삭제한다. 보관 원본(originals/)은 재처리를 위해 보존한다 (#10).
        try:
            if file_path.exists():
                os.unlink(file_path)
        except Exception:
            pass


async def _finalize_upload_job_timed_out(
    *,
    job_id: str,
    file_path: Path,
    timeout_seconds: float,
) -> None:
    """처리 타임아웃 시 job을 failed로 표시하고 임시파일/부분 벡터를 정리한다.

    wait_for가 처리를 CancelledError로 중단하면 process_document_background의
    정상 정리 경로가 실행되지 않으므로, 여기서 명시적으로 마무리한다.
    """
    logger.error(
        "Document processing timed out after %.0fs: job_id=%s", timeout_seconds, job_id
    )
    # 부분 적재된 벡터 정리(best-effort): 타임아웃 시점에 일부 청크만 적재됐을 수 있다.
    try:
        retrieval_module = modules.get("retrieval")
        # 일부 DI 구성(예: chroma)에서 retrieval 모듈이 코루틴/Future로 지연 제공됨
        if asyncio.iscoroutine(retrieval_module) or isinstance(retrieval_module, asyncio.Future):
            retrieval_module = await retrieval_module
        if retrieval_module is not None and hasattr(retrieval_module, "delete_document"):
            await retrieval_module.delete_document(job_id)
    except Exception as cleanup_error:
        logger.warning(
            "Timed-out upload vector cleanup failed for job_id=%s: %s",
            job_id,
            cleanup_error,
        )
    job = upload_jobs.get(job_id)
    if isinstance(job, dict):
        job.update(
            {
                "status": "failed",
                "progress": 0,
                "message": "文档处理超时",
                "error_message": (
                    "文档处理超出时限，请将文件切分得更小 "
                    "请重试。"
                ),
                "internal_error_message": (
                    f"processing timed out after {timeout_seconds:.0f}s"
                ),
                "failed_at": datetime.now().isoformat(),
                # 보관 원본이 있어야 /retry로 재처리 가능 (#10)
                "retry_safe": bool(job.get("original_file_path")),
            }
        )
        save_upload_job(job_id)
    try:
        if file_path.exists():
            os.unlink(file_path)
    except Exception:
        pass


async def process_document_background_guarded(
    job_id: str,
    file_path: Path,
    filename: str,
    file_type: str,
    processing_attempt_id: str | None = None,
) -> None:
    """타임아웃 가드를 적용해 백그라운드 문서 처리를 실행한다.

    전체 처리를 wait_for로 감싸 제한 시간을 초과하면 처리를 중단하고 job을
    명시적으로 failed 처리한다(좀비 작업 방지). 정상 처리는 그대로 통과한다.
    processing_attempt_id는 stale attempt 가드를 위해 그대로 전달된다 (#30).
    """
    timeout_seconds = _document_processing_timeout_seconds()
    try:
        await asyncio.wait_for(
            process_document_background(
                job_id, file_path, filename, file_type, processing_attempt_id
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        await _finalize_upload_job_timed_out(
            job_id=job_id,
            file_path=file_path,
            timeout_seconds=timeout_seconds,
        )


async def process_queued_upload_job_once(worker_id: str = "local-worker") -> dict[str, Any]:
    """대기 중인 업로드 잡 1건을 처리한다 (워커 진입점).

    FastAPI는 보통 BackgroundTasks로 process_document_background를 실행하지만,
    공유(Postgres) 스토어 멀티워커 구성·로컬 smoke 테스트에서는 이 진입점이
    같은 단일 잡 워커 역할을 한다(업로드 API 계약 불변).
    """
    global upload_jobs
    if _is_shared_upload_job_store():
        store = _resolve_upload_job_store()
        lease_seconds = _shared_upload_worker_lease_seconds()
        # 만료된 lease의 잡을 큐로 복구한 뒤, 원자적으로 다음 잡을 확보한다.
        recover_stale = getattr(store, "recover_stale", None)
        if callable(recover_stale):
            recover_stale()
        claim_next = getattr(store, "claim_next", None)
        if not callable(claim_next):
            return {"processed": False, "job_id": None, "status": "claim_unavailable"}
        job = claim_next(worker_id=worker_id, lease_seconds=lease_seconds)
        if not isinstance(job, dict):
            return {"processed": False, "job_id": None, "status": "empty"}
        job_id = str(job["job_id"])
        processing_attempt_id = str(job.get("processing_attempt_id") or uuid4())
        update_claimed_job = getattr(store, "update_claimed_job", None)
        if callable(update_claimed_job):
            updated_job = update_claimed_job(
                job_id=job_id,
                worker_id=worker_id,
                updates={"processing_attempt_id": processing_attempt_id},
                lease_seconds=lease_seconds,
            )
            if isinstance(updated_job, dict):
                job = updated_job
        job["processing_attempt_id"] = processing_attempt_id
        upload_jobs[job_id] = job
        await process_document_background(
            job_id,
            Path(str(job["temp_file_path"])),
            str(job.get("filename") or "unknown"),
            str(job["file_type"]),
            processing_attempt_id,
        )
        _reload_upload_jobs_if_needed(job_id)
        return {
            "processed": True,
            "job_id": job_id,
            "status": upload_jobs.get(job_id, {}).get("status"),
        }

    # 로컬(SQLite/JSON) 스토어: 메모리 맵에서 가장 오래된 대기 잡을 처리한다.
    pending_jobs = sorted(
        (
            (job_id, job)
            for job_id, job in upload_jobs.items()
            if job.get("status") in {"pending", "queued", "retrying"}
        ),
        key=lambda item: (item[1].get("start_time") or 0, item[0]),
    )
    if not pending_jobs:
        return {"processed": False, "job_id": None, "status": "empty"}

    job_id, job = pending_jobs[0]
    processing_attempt_id = str(job.get("processing_attempt_id") or uuid4())
    job["processing_attempt_id"] = processing_attempt_id
    job["claimed_by"] = worker_id
    save_upload_job(job_id)

    await process_document_background(
        job_id,
        Path(str(job["temp_file_path"])),
        str(job.get("filename") or "unknown"),
        str(job["file_type"]),
        processing_attempt_id,
    )
    return {
        "processed": True,
        "job_id": job_id,
        "status": upload_jobs.get(job_id, {}).get("status"),
    }


@router.post("/upload", response_model=UploadResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    metadata: str | None = Form(None),
    principal: str = Depends(get_upload_access),
):
    """文档上传"""
    try:
        validation = validate_file(file)
        if not validation["valid"]:
            raise HTTPException(status_code=400, detail=validation["error"])
        file_type = validation["file_type"]
        job_id = str(uuid4())
        upload_dir = get_upload_directory()
        temp_dir = upload_dir / "temp"
        safe_filename = Path(file.filename or "unknown").name
        if not safe_filename or safe_filename.startswith("."):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "文件名无效",
                    "message": "文件名无效",
                    "suggestion": "请使用正确的文件名重新上传（不支持隐藏文件）",
                    "file_name": file.filename,
                },
            )
        file_path = temp_dir / f"{job_id}_{safe_filename}"
        try:
            resolved_path = file_path.resolve()
            resolved_temp_dir = temp_dir.resolve()
            if not str(resolved_path).startswith(str(resolved_temp_dir)):
                logger.error(f"已拦截路径穿越尝试：{file.filename}")
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": "安全校验失败",
                        "message": "检测到文件路径存在安全威胁",
                        "suggestion": "请确认文件名不含特殊字符或路径字符（..、/）",
                        "file_name": file.filename,
                    },
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"文件路径校验失败：{e}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "文件路径校验失败",
                    "message": "校验文件路径时发生错误",
                    "suggestion": "请确认文件名不含特殊字符后重试",
                    "file_name": file.filename,
                    "technical_error": str(e),
                },
            ) from e
        content = await file.read()

        # 동기 디스크 쓰기를 to_thread로 오프로딩한다.
        # (대용량 파일을 이벤트 루프에서 직접 쓰면 진행 중인 모든 스트리밍이 정지함)
        def _write_file() -> None:
            with open(file_path, "wb") as buffer:
                buffer.write(content)

        await asyncio.to_thread(_write_file)
        file_size = len(content)
        filename = file.filename or "unknown"
        # 원본 파일 보관(#10): 처리 후 재처리/원본 다운로드를 위해 originals/에 보존한다.
        # 보관 실패는 업로드 자체를 막지 않도록 best-effort(원본 미보관 시 retry 비활성).
        original_payload = _persist_original_file(
            content,
            upload_dir=upload_dir,
            job_id=job_id,
            safe_filename=safe_filename,
            content_type=file.content_type,
        )
        processing_attempt_id = str(uuid4())
        upload_jobs[job_id] = {
            "job_id": job_id,
            "filename": filename,
            "file_type": file_type,
            "file_size": file_size,
            "status": "pending",
            "progress": 0,
            "message": "上传完成，等待处理中...",
            "start_time": datetime.now().timestamp(),
            "chunk_count": None,
            "processing_time": None,
            "error_message": None,
            # 워커 진입점(process_queued_upload_job_once)이 재처리할 수 있도록 기록
            "temp_file_path": str(file_path),
            "processing_attempt_id": processing_attempt_id,
            # 업로드 토큰 자격이면 작업 소유 세션을 기록한다(취소/재시도 권한 판단용).
            "owner_session_id": _principal_session_id(principal),
            **original_payload,
        }
        # 동기 SQLite 트랜잭션을 to_thread로 오프로딩 (이벤트 루프 블로킹 방지)
        await asyncio.to_thread(save_upload_job, job_id)
        background_tasks.add_task(
            process_document_background_guarded,
            job_id,
            file_path,
            filename,
            file_type,
            processing_attempt_id,
        )
        estimated_time = estimate_processing_time(file_size, file_type)
        logger.info(f"Document upload initiated: {file.filename}, job_id: {job_id}")
        size_mb = file_size / (1024 * 1024)
        if estimated_time > 60:
            time_msg = f"约 {estimated_time / 60:.1f} 分钟"
        else:
            time_msg = f"约 {estimated_time:.0f} 秒"
        if size_mb > 10:
            warning_msg = (
                " ⚠️ 大文件处理可能耗时较长，请勿关闭浏览器。"
            )
        else:
            warning_msg = ""
        user_message = f"文件上传完成！文档处理中。预计时间：{time_msg}（文件大小：{size_mb:.1f}MB）{warning_msg}"
        return UploadResponse(
            job_id=job_id,
            message=user_message,
            filename=filename,
            file_size=file_size,
            estimated_processing_time=estimated_time,
            timestamp=datetime.now().isoformat(),
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.error(f"Upload error: {error}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "上传失败",
                "message": "上传文件时发生错误",
                "suggestion": "请检查网络连接后重试。若问题持续，请联系管理员",
                "file_name": file.filename if file and hasattr(file, "filename") else None,
                "retry_after": 30,
                "technical_error": str(error),
            },
        ) from error


@router.get("/upload/status/{job_id}", response_model=JobStatusResponse)
async def get_upload_status(job_id: str):
    """上传任务状态查询"""
    # 공유 스토어 사용 시 다른 인스턴스의 갱신을 반영하고, 캐시 미스 시 재로드한다.
    _reload_upload_jobs_if_needed(job_id)
    if job_id not in upload_jobs:
        logger.warning(f"Job {job_id} not found even after reload")
        raise HTTPException(
            status_code=404,
            detail={
                "error": "未找到任务",
                "message": "未找到您的上传任务",
                "suggestion": "服务器可能已重启，请重新上传文件",
                "job_id": job_id,
                "retry_upload": True,
            },
        )
    job = upload_jobs[job_id]
    current_processing_time = None
    if job["status"] == "processing":
        current_processing_time = datetime.now().timestamp() - job["start_time"]
    logger.info(f"Job {job_id} status: {job['status']}, progress: {job['progress']}%")
    return JobStatusResponse(
        job_id=job_id,
        status=job["status"],
        progress=job["progress"],
        message=job["message"],
        filename=job["filename"],
        chunk_count=job["chunk_count"],
        processing_time=job["processing_time"] or current_processing_time,
        error_message=job["error_message"],
        # 처리 프로비넌스(GAP #9): 완료된 작업에만 기록됨. 미완료/미산출 시 None.
        loader_type=job.get("loader_type"),
        splitter_type=job.get("splitter_type"),
        storage_locations=job.get("storage_locations"),
        extraction_summary=job.get("extraction_summary"),
        timestamp=datetime.now().isoformat(),
    )


def _coerce_document_upload_date(value: Any) -> str:
    """文档上传 일시를 ISO 문자열로 정규화한다.

    - bool은 int의 하위 타입이라 epoch(1초)로 오인될 수 있으므로 먼저 배제한다.
    - 숫자 epoch은 UTC 기준으로 변환해 항상 시간대 정보를 부여한다(naive/aware 혼합 방지).
    - 파싱 불가능한 문자열은 원본을 그대로 두고, 후속 단계에서 안전하게 처리한다.
    """
    if isinstance(value, bool):
        return datetime.now(UTC).isoformat()
    if isinstance(value, int | float) and value > 0:
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    if isinstance(value, str) and value.strip():
        normalized = value.strip()
        try:
            return datetime.fromisoformat(normalized.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return normalized
    return datetime.now(UTC).isoformat()


@router.get("/upload/documents", response_model=DocumentListResponse)
async def list_documents(page: int = 1, page_size: int = 20):
    """文档列表查询"""
    try:
        retrieval_module = modules.get("retrieval")
        if not retrieval_module:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "系统模块不可用",
                    "message": "文档检索模块不可用",
                    "suggestion": "请检查服务器状态并联系管理员",
                    "module_name": "retrieval",
                    "retry_after": 60,
                },
            )
        logger.info(f"Listing documents: page={page}, page_size={page_size}")
        documents_data = await retrieval_module.list_documents(page=page, page_size=page_size)
        logger.info(f"Retrieved documents_data: {documents_data}")
        documents = []
        for doc_data in documents_data.get("documents", []):
            # upload_date가 누락/None/0 등 falsy면 created_at으로 폴백한다.
            # (dict.get의 기본값은 '키 부재'에만 적용되므로, 값이 None/0인 경우를 별도 처리)
            raw_upload_date = doc_data.get("upload_date")
            if not raw_upload_date:
                raw_upload_date = doc_data.get("created_at", 0)
            upload_date = _coerce_document_upload_date(raw_upload_date)
            documents.append(
                DocumentInfo(
                    id=doc_data.get("id", "unknown"),
                    filename=doc_data.get("filename", "unknown"),
                    file_type=doc_data.get("file_type", "unknown"),
                    file_size=doc_data.get("file_size", 0),
                    upload_date=upload_date,
                    status="completed",
                    chunk_count=doc_data.get("chunk_count", 0),
                )
            )
        total_count = documents_data.get("total_count", len(documents))
        response = DocumentListResponse(
            documents=documents,
            total_count=total_count,
            page=page,
            page_size=page_size,
            has_next=page * page_size < total_count,
        )
        logger.info(f"Returning response: {len(documents)} documents, total={total_count}")
        return response
    except HTTPException:
        raise
    except Exception as error:
        logger.error(f"List documents error: {error}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "文档列表查询失败",
                "message": "加载文档列表时发生错误",
                "suggestion": "请稍后重试或联系管理员",
                "page": page,
                "page_size": page_size,
                "retry_after": 30,
                "technical_error": str(error),
            },
        ) from error


@router.delete(
    "/upload/documents/{document_id}",
    dependencies=[Depends(require_upload_admin_access)],
)
async def delete_document(document_id: str):
    """文档删除"""
    try:
        retrieval_module = modules.get("retrieval")
        if not retrieval_module:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "系统模块不可用",
                    "message": "文档检索模块不可用",
                    "suggestion": "请检查服务器状态并联系管理员",
                    "module_name": "retrieval",
                    "retry_after": 60,
                },
            )
        await retrieval_module.delete_document(document_id)
        # 보관 원본이 있으면 함께 정리하고 운영 감사 이벤트를 남긴다 (#10/#36).
        _delete_original_for_document(document_id)
        logger.info(f"Document deleted: {document_id}")
        _record_operational_audit_event(
            {
                "action": "document.delete.succeeded",
                "target_type": "document",
                "target_id": document_id,
                "document_id": document_id,
            },
            failure_context="document delete",
        )
        return {
            "message": "Document deleted successfully",
            "document_id": document_id,
            "timestamp": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as error:
        logger.error(f"Delete document error: {error}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "文档删除失败",
                "message": "删除文档时发生错误",
                "suggestion": "文档可能已被删除或无访问权限。请重试或联系管理员",
                "document_id": document_id,
                "retry_after": 30,
                "technical_error": str(error),
            },
        ) from error


@router.post(
    "/upload/documents/bulk-delete",
    response_model=BulkDeleteResponse,
    dependencies=[Depends(require_upload_admin_access)],
)
async def bulk_delete_documents(request: BulkDeleteRequest):
    """文档批量删除"""
    try:
        retrieval_module = modules.get("retrieval")
        if not retrieval_module:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "系统模块不可用",
                    "message": "文档检索模块不可用",
                    "suggestion": "请检查服务器状态并联系管理员",
                    "module_name": "retrieval",
                    "retry_after": 60,
                },
            )
        deleted_count = 0
        failed_count = 0
        failed_ids = []
        logger.info(f"Bulk delete requested for {len(request.ids)} documents: {request.ids}")
        for document_id in request.ids:
            try:
                if not document_id or document_id.strip() == "":
                    logger.warning(f"Skipping invalid document ID: {document_id}")
                    failed_count += 1
                    failed_ids.append(document_id)
                    continue
                await retrieval_module.delete_document(document_id)
                _delete_original_for_document(document_id)
                deleted_count += 1
                logger.info(f"Successfully deleted document: {document_id}")
                _record_operational_audit_event(
                    {
                        "action": "document.delete.succeeded",
                        "target_type": "document",
                        "target_id": document_id,
                        "document_id": document_id,
                        "metadata": {"bulk": True},
                    },
                    failure_context="bulk document delete",
                )
            except Exception as delete_error:
                logger.error(f"Failed to delete document {document_id}: {delete_error}")
                failed_count += 1
                failed_ids.append(document_id)
        message = f"Bulk delete completed: {deleted_count} deleted, {failed_count} failed"
        logger.info(message)
        return BulkDeleteResponse(
            deleted_count=deleted_count,
            failed_count=failed_count,
            failed_ids=failed_ids,
            message=message,
            timestamp=datetime.now().isoformat(),
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.error(f"Bulk delete error: {error}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "批量删除失败",
                "message": "批量删除文档时发生错误",
                "suggestion": "请检查网络连接后重试，或联系管理员",
                "requested_count": len(request.ids) if request and hasattr(request, "ids") else 0,
                "retry_after": 30,
                "technical_error": str(error),
            },
        ) from error


@router.get("/upload/documents/{document_id}/download")
async def download_document(document_id: str):
    """문서 다운로드 (벡터 DB에 저장된 청크 데이터를 텍스트로 재결합)

    원본 파일은 업로드 처리 후 삭제되므로,
    벡터 DB에 저장된 청크 내용을 결합하여 텍스트 파일로 제공합니다.
    """
    import io

    try:
        retrieval_module = modules.get("retrieval")
        if not retrieval_module:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "系统模块不可用",
                    "message": "文档检索模块不可用",
                    "suggestion": "请检查服务器状态并联系管理员",
                },
            )

        # 문서의 모든 청크를 검색
        chunks = await retrieval_module.get_document_chunks(document_id)

        if not chunks:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "未找到文档",
                    "message": "未找到您请求的文档",
                    "suggestion": "文档可能已被删除或 ID 不正确",
                    "document_id": document_id,
                },
            )

        # 청크 내용을 페이지/순서별로 정렬 후 결합
        sorted_chunks = sorted(
            chunks,
            key=lambda c: (
                c.get("metadata", {}).get("page", 0),
                c.get("metadata", {}).get("chunk_index", 0),
            ),
        )
        content = "\n\n".join(chunk.get("content", "") for chunk in sorted_chunks)

        # 파일명 추출 (메타데이터에서)
        first_chunk_meta = sorted_chunks[0].get("metadata", {})
        filename = first_chunk_meta.get("source_file", first_chunk_meta.get("filename", "document"))

        # 확장자가 없으면 .txt 추가
        if "." not in Path(filename).name:
            filename = f"{filename}.txt"
        # 원본이 바이너리 형식이면 .txt로 변환
        ext = Path(filename).suffix.lower()
        if ext in (".pdf", ".docx", ".pptx", ".xlsx"):
            filename = Path(filename).stem + ".txt"

        logger.info(f"Document download: {document_id}, {len(sorted_chunks)} chunks, filename={filename}")

        buffer = io.BytesIO(content.encode("utf-8"))
        return StreamingResponse(
            buffer,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(content.encode("utf-8"))),
            },
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.error(f"Download document error: {error}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "文档下载失败",
                "message": "下载文档时发生错误",
                "suggestion": "请稍后重试或联系管理员",
                "document_id": document_id,
                "technical_error": str(error),
            },
        ) from error


@router.get("/upload/supported-types")
async def get_supported_types():
    """支持的文件类型列表"""
    return {
        "supported_types": {
            "pdf": {
                "mime_type": "application/pdf",
                "description": "PDF documents",
                "max_size_mb": 10,
            },
            "docx": {
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "description": "Microsoft Word documents",
                "max_size_mb": 10,
            },
            "pptx": {
                "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "description": "Microsoft PowerPoint presentations",
                "max_size_mb": 20,
            },
            "xlsx": {
                "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "description": "Microsoft Excel spreadsheets",
                "max_size_mb": 10,
            },
            "txt": {
                "mime_type": "text/plain",
                "description": "Plain text files",
                "max_size_mb": 10,
            },
            "csv": {
                "mime_type": "text/csv",
                "description": "Comma-separated values",
                "max_size_mb": 10,
            },
            "html": {"mime_type": "text/html", "description": "HTML documents", "max_size_mb": 10},
            "md": {
                "mime_type": "text/markdown",
                "description": "Markdown documents",
                "max_size_mb": 10,
            },
            "json": {
                "mime_type": "application/json",
                "description": "JSON documents",
                "max_size_mb": 10,
            },
        },
        "max_file_size": config.get("uploads", {}).get("max_file_size", 50 * 1024 * 1024),
        "max_files_per_request": 1,
    }


# ============================================================================
# 원본 파일 다운로드 (#10)
# ============================================================================


def _guess_original_media_type(filename: str) -> str:
    """按文件扩展名推断 MIME 类型（未知时为 octet-stream）。"""
    ext = Path(filename).suffix.lower().lstrip(".")
    media_types = {
        "pdf": "application/pdf",
        "txt": "text/plain; charset=utf-8",
        "md": "text/markdown; charset=utf-8",
        "csv": "text/csv; charset=utf-8",
        "html": "text/html; charset=utf-8",
        "json": "application/json",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    return media_types.get(ext, "application/octet-stream")


_SOURCE_DETAIL_PRIVATE_METADATA_KEYS = {
    "embedding",
    "embeddings",
    "vector",
    "vectors",
    "file_path",
    "original_file_path",
    "storage_backend",
    "original_storage_backend",
}


def _chunk_metadata(chunk: Any) -> dict[str, Any]:
    if isinstance(chunk, dict):
        metadata = chunk.get("metadata")
    else:
        metadata = getattr(chunk, "metadata", None)
    return dict(metadata) if isinstance(metadata, dict) else {}


def _chunk_content(chunk: Any) -> str:
    if isinstance(chunk, dict):
        value = (
            chunk.get("content")
            or chunk.get("page_content")
            or chunk.get("text")
            or chunk.get("full_content")
        )
    else:
        value = getattr(chunk, "page_content", None) or getattr(chunk, "content", None)
    return str(value or "")


def _chunk_id(chunk: Any) -> str | None:
    if isinstance(chunk, dict):
        value = chunk.get("id") or chunk.get("_id") or chunk.get("chunk_id")
    else:
        value = getattr(chunk, "id", None) or getattr(chunk, "_id", None)
    return str(value) if value not in (None, "") else None


def _coerce_optional_int(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_source_detail_value(metadata: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = metadata.get(key)
        if value not in (None, ""):
            return value
    return None


def _public_source_detail_uri(value: Any) -> str | None:
    if value in (None, ""):
        return None
    uri = str(value)
    if uri.startswith(("file://", "gs://", "/")):
        return None
    if "://" not in uri and "/" in uri:
        return None
    return uri


def _public_source_detail_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key not in _SOURCE_DETAIL_PRIVATE_METADATA_KEYS
    }


def _source_detail_candidate_ids(
    *,
    document_id: str,
    chunk: Any,
    metadata: dict[str, Any],
    fallback_order: int,
) -> set[str]:
    candidates: set[str] = set()
    for key in ("source_id", "citation_id", "id", "_id"):
        value = metadata.get(key)
        if value not in (None, ""):
            candidates.add(str(value))
    chunk_id = _chunk_id(chunk)
    if chunk_id:
        candidates.add(chunk_id)

    page = _coerce_optional_int(_first_source_detail_value(metadata, "page", "page_number"))
    chunk_index = _coerce_optional_int(_first_source_detail_value(metadata, "chunk_index", "chunk"))
    stable_page = page if page is not None else "na"
    stable_chunk = chunk_index if chunk_index is not None else fallback_order
    candidates.add(f"rag:{document_id}:{stable_page}:{stable_chunk}")
    candidates.add(f"rag:{document_id}:na:{stable_chunk}")
    return candidates


def _matches_source_detail_filters(
    *,
    metadata: dict[str, Any],
    page: int | None,
    chunk: int | None,
) -> bool:
    if page is not None:
        metadata_page = _coerce_optional_int(
            _first_source_detail_value(metadata, "page", "page_number")
        )
        if metadata_page != page:
            return False
    if chunk is not None:
        metadata_chunk = _coerce_optional_int(
            _first_source_detail_value(metadata, "chunk_index", "chunk")
        )
        if metadata_chunk != chunk:
            return False
    return True


async def _get_document_chunks_for_detail(document_id: str) -> list[Any]:
    retrieval_module = modules.get("retrieval")
    if not retrieval_module:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "系统模块不可用",
                "message": "文档检索模块不可用",
                "document_id": document_id,
            },
        )

    get_document_chunks = getattr(retrieval_module, "get_document_chunks", None)
    if not callable(get_document_chunks):
        orchestrator = getattr(retrieval_module, "orchestrator", None)
        get_document_chunks = getattr(orchestrator, "get_document_chunks", None)
    if not callable(get_document_chunks):
        raise HTTPException(
            status_code=501,
            detail={
                "error": "不支持分块详情查询",
                "message": "当前检索模块不支持文档分块详情查询",
                "document_id": document_id,
            },
        )

    try:
        chunks = await get_document_chunks(document_id)
    except NotImplementedError as error:
        raise HTTPException(
            status_code=501,
            detail={
                "error": "不支持分块详情查询",
                "message": "当前检索模块不支持文档分块详情查询",
                "document_id": document_id,
            },
        ) from error
    if not isinstance(chunks, list):
        return []
    return chunks


@router.get("/upload/documents/{document_id}/sources/{source_id}")
async def get_document_source_detail(
    document_id: str,
    source_id: str,
    page: int | None = Query(default=None),
    chunk: int | None = Query(default=None),
) -> dict[str, Any]:
    """按 source_id 查询分块（引用来源）的完整内容。"""
    chunks = await _get_document_chunks_for_detail(document_id)
    for index, item in enumerate(chunks):
        metadata = _chunk_metadata(item)
        if source_id not in _source_detail_candidate_ids(
            document_id=document_id,
            chunk=item,
            metadata=metadata,
            fallback_order=index,
        ):
            continue
        if not _matches_source_detail_filters(metadata=metadata, page=page, chunk=chunk):
            continue

        content = _chunk_content(item)
        page_number = _coerce_optional_int(
            _first_source_detail_value(metadata, "page", "page_number")
        )
        chunk_index = _coerce_optional_int(
            _first_source_detail_value(metadata, "chunk_index", "chunk")
        )
        document_name = (
            _first_source_detail_value(
                metadata,
                "document_name",
                "source_file",
                "filename",
                "title",
            )
        )
        return {
            "source_id": source_id,
            "document_id": document_id,
            "document_name": str(document_name) if document_name else None,
            "document": str(document_name) if document_name else None,
            "page": page_number,
            "chunk": chunk_index,
            "section": metadata.get("section") or metadata.get("heading"),
            "content": content,
            "full_content": content,
            "source_uri": _public_source_detail_uri(
                _first_source_detail_value(
                    metadata,
                    "source_uri",
                    "uri",
                    "url",
                    "citation",
                )
            ),
            "metadata": _public_source_detail_metadata(metadata),
        }

    raise HTTPException(
        status_code=404,
        detail={
            "error": "未找到来源分块",
            "message": "未找到您请求的来源分块",
            "document_id": document_id,
            "source_id": source_id,
        },
    )


@router.get("/upload/documents/{document_id}/original")
async def download_original_document(document_id: str):
    """보관된 원본 파일을 그대로 스트리밍 다운로드한다 (#10).

    업로드 시 originals/에 보관된 원본 바이너리(PDF/DOCX 등)를 변환 없이 반환한다.
    보관 원본이 없으면 404(STORAGE-001).
    """
    _reload_upload_jobs_if_needed(document_id)
    job = upload_jobs.get(document_id)
    if not isinstance(job, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "UPLOAD-020",
                "error": "未找到任务",
                "message": "未找到您的上传任务",
                "document_id": document_id,
            },
        )
    upload_dir = get_upload_directory()
    try:
        reference = resolve_original_reference(
            metadata=job,
            app_config=config,
            upload_dir=upload_dir,
        )
    except OriginalFileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "STORAGE-001",
                "error": "原始文件缺失",
                "message": "未找到归档的原始文件",
                "suggestion": "可能是归档功能上线前上传的文档，或原始文件已被清理",
                "document_id": document_id,
            },
        ) from error

    filename = str(job.get("filename") or f"{document_id}.bin")
    media_type = str(job.get("content_type") or "") or _guess_original_media_type(filename)
    try:
        chunks = original_file_chunks(reference, app_config=config)
    except OriginalFileStorageError as error:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "STORAGE-003",
                "error": "原始文件下载失败",
                "message": "读取原始文件时发生错误",
                "document_id": document_id,
            },
        ) from error
    return StreamingResponse(
        chunks,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ============================================================================
# 업로드 작업 취소 (#30)
# ============================================================================


def _upload_cancel_response(job_id: str, job: dict[str, Any]) -> UploadCancelResponse:
    """从任务状态构造取消响应模型。"""
    return UploadCancelResponse(
        job_id=job_id,
        status=str(job.get("status") or "unknown"),
        progress=float(job.get("progress") or 0),
        message=str(job.get("message") or ""),
        processing_time=_upload_job_processing_time(job),
        timestamp=datetime.now().isoformat(),
    )


@router.post("/upload/status/{job_id}/cancel", response_model=UploadCancelResponse)
async def cancel_upload_job(
    job_id: str,
    principal: str = Depends(get_upload_access),
) -> UploadCancelResponse:
    """업로드 작업을 취소한다 (#30).

    - pending: 즉시 취소(부분 산출물 정리)
    - processing: 협조적 취소 마킹(cancel_requested) → 처리 루프가 체크포인트에서 중단
    - completed/failed: 409 (종결 작업은 취소 불가)
    - cancelled/cancelling: 현재 상태 그대로 응답(멱등)
    """
    _reload_upload_jobs_if_needed(job_id)
    job = upload_jobs.get(job_id)
    if not isinstance(job, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "UPLOAD-020",
                "error": "未找到任务",
                "message": "未找到您的上传任务",
                "job_id": job_id,
            },
        )

    _assert_job_owner(job, principal, job_id)
    status = str(job.get("status") or "")
    if status in {"cancelled", "cancelling"}:
        return _upload_cancel_response(job_id, job)
    if status in {"completed", "failed"}:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "UPLOAD-021",
                "error": "无法取消任务",
                "message": "已完成或已失败的上传任务无法取消",
                "job_id": job_id,
                "status": status,
            },
        )
    if status not in UPLOAD_CANCELLABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "UPLOAD-021",
                "error": "无法取消任务",
                "message": "当前状态无法取消上传任务",
                "job_id": job_id,
                "status": status,
            },
        )

    if status in {"receiving", "pending"}:
        # 아직 처리가 시작되지 않았으므로 즉시 취소(부분 산출물 정리 포함).
        _finalize_upload_job_cancelled(job_id=job_id, finalized_by="endpoint")
        return _upload_cancel_response(job_id, upload_jobs[job_id])

    # processing: 협조적 취소 요청만 마킹하고 처리 루프가 체크포인트에서 중단하게 한다.
    now = datetime.now().isoformat()
    job.update(
        {
            "status": "cancelling",
            "cancel_requested": True,
            "cancel_requested_at": job.get("cancel_requested_at") or now,
            "message": UPLOAD_CANCEL_REQUESTED_MESSAGE,
            "error_message": None,
        }
    )
    save_upload_job(job_id)
    _record_operational_audit_event(
        {
            "action": "document.upload.cancel_requested",
            "target_type": "upload_job",
            "target_id": job_id,
            "document_id": job_id,
        },
        failure_context="upload cancel request",
    )
    return _upload_cancel_response(job_id, job)


# ============================================================================
# 업로드 작업 재시도 (#10)
# ============================================================================


@router.post("/upload/status/{job_id}/retry", response_model=UploadRetryResponse)
async def retry_upload_job(
    background_tasks: BackgroundTasks,
    job_id: str,
    principal: str = Depends(get_upload_access),
) -> UploadRetryResponse:
    """실패한 업로드를 보관 원본으로부터 재처리한다 (#10).

    failed 상태이고 보관 원본(retry_safe)이 있을 때만 허용한다. 원본을 새 임시
    파일로 복원해 새 processing_attempt_id로 재처리를 합류시킨다.
    """
    _reload_upload_jobs_if_needed(job_id)
    job = upload_jobs.get(job_id)
    if not isinstance(job, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "UPLOAD-020",
                "error": "未找到任务",
                "message": "未找到您的上传任务",
                "job_id": job_id,
            },
        )
    _assert_job_owner(job, principal, job_id)
    status = str(job.get("status") or "")
    if status != "failed" or job.get("retry_safe") is not True:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "UPLOAD-022",
                "error": "无法重试任务",
                "message": "当前状态无法重试上传任务",
                "job_id": job_id,
                "status": status,
            },
        )

    upload_dir = get_upload_directory()
    try:
        reference = resolve_original_reference(
            metadata=job,
            app_config=config,
            upload_dir=upload_dir,
        )
    except OriginalFileNotFoundError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "UPLOAD-023",
                "error": "无法重试任务",
                "message": "重试所需的原始文件不可用",
                "job_id": job_id,
            },
        ) from error

    retry_count = int(job.get("retry_count") or 0) + 1
    temp_dir = upload_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = Path(str(job.get("filename") or "original")).name or "original"
    retry_temp_path = temp_dir / f"{job_id}_retry{retry_count}_{safe_filename}"
    try:
        materialize_original_to_path(reference, retry_temp_path, app_config=config)
    except (OSError, OriginalFileStorageError) as error:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "UPLOAD-023",
                "error": "无法重试任务",
                "message": "重试所需的原始文件准备失败",
                "job_id": job_id,
            },
        ) from error

    processing_attempt_id = str(uuid4())
    now = datetime.now()
    filename = str(job.get("filename") or safe_filename)
    file_type = str(job.get("file_type") or Path(safe_filename).suffix.lstrip(".") or "unknown")
    job.update(
        {
            "status": "pending",
            "progress": 0,
            "message": "等待上传重试中...",
            "start_time": now.timestamp(),
            "chunk_count": None,
            "processing_time": None,
            "error_message": None,
            "internal_error_message": None,
            "failed_at": None,
            "retry_safe": False,
            "indexing_started": False,
            "retry_count": retry_count,
            "retried_at": now.isoformat(),
            "processing_attempt_id": processing_attempt_id,
            "temp_file_path": str(retry_temp_path),
        }
    )
    save_upload_job(job_id)
    background_tasks.add_task(
        process_document_background_guarded,
        job_id,
        retry_temp_path,
        filename,
        file_type,
        processing_attempt_id,
    )
    _record_operational_audit_event(
        {
            "action": "document.upload.retried",
            "target_type": "upload_job",
            "target_id": job_id,
            "document_id": job_id,
            "metadata": {"retry_count": retry_count},
        },
        failure_context="upload retry",
    )
    return UploadRetryResponse(
        job_id=job_id,
        status="pending",
        message="开始重新处理",
        retry_count=retry_count,
        timestamp=now.isoformat(),
    )


# ============================================================================
# 대용량 분할(chunked) 업로드 (#11)
# ============================================================================


def _validate_descriptor(filename: str, content_type: str | None, file_size: int) -> dict[str, Any]:
    """分块上传开始时校验文件名/类型/大小（与单文件上传规则一致）。"""
    supported_types = {
        "application/pdf": "pdf",
        "text/plain": "txt",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "text/csv": "csv",
        "text/html": "html",
        "text/markdown": "md",
        "application/json": "json",
    }
    ext = Path(filename or "unknown").suffix.lower()[1:]
    if content_type in supported_types:
        file_type = supported_types[content_type]
    elif ext in supported_types.values():
        file_type = ext
    else:
        return {
            "valid": False,
            "error": {
                "error_code": "UPLOAD-001",
                "error": "不支持的文件格式",
                "message": f"'{content_type}' 不支持该格式",
                "file_name": filename,
            },
        }
    max_size = config.get("uploads", {}).get("max_file_size", 50 * 1024 * 1024)
    if file_size > max_size:
        return {
            "valid": False,
            "error": {
                "error_code": "UPLOAD-002",
                "error": "文件大小超限",
                "message": "文件大小超过最大允许值",
                "file_name": filename,
                "max_size_mb": int(max_size / (1024 * 1024)),
            },
        }
    return {"valid": True, "file_type": file_type}


def _build_chunked_temp_path(upload_dir: Path, *, job_id: str, raw_filename: str) -> tuple[str, Path]:
    """创建分块上传的临时文件路径（含路径穿越防护）。"""
    temp_dir = upload_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = Path(raw_filename or "unknown").name
    if not safe_filename or safe_filename.startswith("."):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "UPLOAD-005",
                "error": "文件名无效",
                "message": "文件名无效",
                "file_name": raw_filename,
            },
        )
    file_path = temp_dir / f"{job_id}_{safe_filename}"
    if not str(file_path.resolve()).startswith(str(temp_dir.resolve())):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "UPLOAD-006",
                "error": "安全校验失败",
                "message": "检测到文件路径存在安全威胁",
                "file_name": raw_filename,
            },
        )
    return safe_filename, file_path


def _get_chunked_receiving_job(job_id: str) -> dict[str, Any]:
    """获取 receiving 状态的分块上传任务（状态/类型校验）。"""
    _reload_upload_jobs_if_needed(job_id)
    job = upload_jobs.get(job_id)
    if not isinstance(job, dict):
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "UPLOAD-020",
                "error": "未找到任务",
                "message": "未找到您的分块上传任务",
                "job_id": job_id,
            },
        )
    if str(job.get("status") or "") != "receiving" or job.get("chunked_upload") is not True:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "UPLOAD-019",
                "error": "分块上传状态错误",
                "message": "当前状态无法接收文件分片",
                "job_id": job_id,
                "status": job.get("status"),
            },
        )
    return job


@router.post("/upload/chunked/start", response_model=ChunkedUploadStartResponse)
async def start_chunked_upload(
    payload: ChunkedUploadStartRequest,
    principal: str = Depends(get_upload_access),
) -> ChunkedUploadStartResponse:
    """대용량 문서 분할 업로드를 시작한다 (#11).

    리버스 프록시 바디 한도(nginx client_max_body_size, Cloud Run 32MB 등)를
    우회하기 위해, 전체 파일을 여러 조각으로 나눠 받는다. 시작 시 job_id를 발급하고
    선언된 크기만큼의 빈 temp 파일을 준비한다.
    """
    validation = _validate_descriptor(payload.filename, payload.content_type, payload.file_size)
    if not validation["valid"]:
        raise HTTPException(status_code=400, detail=validation["error"])

    job_id = str(uuid4())
    upload_dir = get_upload_directory()
    safe_filename, file_path = _build_chunked_temp_path(
        upload_dir, job_id=job_id, raw_filename=payload.filename
    )
    file_path.touch(exist_ok=False)
    upload_jobs[job_id] = {
        "job_id": job_id,
        "filename": payload.filename,
        "file_type": validation["file_type"],
        "file_size": payload.file_size,
        "status": "receiving",
        "progress": 0,
        "message": "正在接收文件分片...",
        "start_time": datetime.now().timestamp(),
        "chunk_count": None,
        "processing_time": None,
        "error_message": None,
        "temp_file_path": str(file_path),
        "safe_filename": safe_filename,
        "content_type": payload.content_type,
        "parsed_metadata": payload.metadata,
        "chunked_upload": True,
        "chunk_received_size": 0,
        "chunk_recommended_size": CHUNKED_UPLOAD_RECOMMENDED_CHUNK_SIZE,
        "owner_session_id": _principal_session_id(principal),
    }
    save_upload_job(job_id)
    return ChunkedUploadStartResponse(
        job_id=job_id,
        message="分块上传任务已创建",
        filename=payload.filename,
        file_size=payload.file_size,
        chunk_size=CHUNKED_UPLOAD_RECOMMENDED_CHUNK_SIZE,
        timestamp=datetime.now().isoformat(),
    )


@router.post("/upload/chunked/{job_id}/chunk", response_model=ChunkedUploadChunkResponse)
async def upload_chunked_chunk(
    job_id: str,
    chunk: UploadFile = File(...),
    offset: int = Form(...),
    principal: str = Depends(get_upload_access),
) -> ChunkedUploadChunkResponse:
    """파일 조각을 offset 위치에 기록한다 (#11).

    offset 순차성(409)과 선언 크기 초과(400)를 3중 검증의 일부로 검사한다.
    """
    if offset < 0:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "UPLOAD-016",
                "error": "上传位置无效",
                "message": "offset 必须大于等于 0",
                "job_id": job_id,
            },
        )
    job = _get_chunked_receiving_job(job_id)
    # 조각 전송/완료도 작업을 만든 세션만 할 수 있다(다른 세션의 임시 파일 변조 방지).
    _assert_job_owner(job, principal, job_id)
    received_size = int(job.get("chunk_received_size") or 0)
    expected_size = int(job.get("file_size") or 0)
    if offset != received_size:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "UPLOAD-016",
                "error": "上传位置不匹配",
                "message": "请求的 offset 与服务器预期不一致",
                "job_id": job_id,
                "expected_offset": received_size,
                "received_offset": offset,
            },
        )

    file_path = Path(str(job["temp_file_path"]))
    _safe_unlink_guard = get_upload_directory() / "temp"
    try:
        file_path.resolve().relative_to(_safe_unlink_guard.resolve())
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "UPLOAD-006",
                "error": "安全校验失败",
                "message": "检测到文件路径存在安全威胁",
                "job_id": job_id,
            },
        ) from error

    bytes_written = 0
    try:
        with file_path.open("r+b") as buffer:
            buffer.seek(offset)
            while part := await chunk.read(UPLOAD_STREAM_CHUNK_SIZE):
                bytes_written += len(part)
                if offset + bytes_written > expected_size:
                    buffer.truncate(offset)
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error_code": "UPLOAD-017",
                            "error": "文件大小超限",
                            "message": "收到的文件分片超过声明的总大小",
                            "job_id": job_id,
                            "file_size": expected_size,
                        },
                    )
                buffer.write(part)
    except HTTPException:
        raise
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "UPLOAD-008",
                "error": "分块上传失败",
                "message": "保存文件分片时发生错误",
                "job_id": job_id,
            },
        ) from error

    received_size = offset + bytes_written
    progress = round(min(99.0, (received_size / expected_size) * 100), 2) if expected_size else 0.0
    job.update(
        {
            "chunk_received_size": received_size,
            "progress": progress,
            "message": f"正在接收文件分片...（{received_size}/{expected_size} 字节）",
            "updated_at": datetime.now().isoformat(),
        }
    )
    save_upload_job(job_id)
    return ChunkedUploadChunkResponse(
        job_id=job_id,
        status="receiving",
        received_size=received_size,
        file_size=expected_size,
        progress=progress,
        message="文件分片已保存",
        timestamp=datetime.now().isoformat(),
    )


@router.post("/upload/chunked/{job_id}/complete", response_model=UploadResponse)
async def complete_chunked_upload(
    background_tasks: BackgroundTasks,
    job_id: str,
    payload: ChunkedUploadCompleteRequest,
    principal: str = Depends(get_upload_access),
) -> UploadResponse:
    """분할 업로드를 완료하고 문서 처리를 시작한다 (#11).

    선언 크기 == 수신 크기 == 실측 파일 크기(3중 검증)를 확인한 뒤, 원본을
    보관하고 단일 업로드와 동일한 처리 경로(process_document_background_guarded)로
    합류한다.
    """
    try:
        job = _get_chunked_receiving_job(job_id)
        # 조각 전송/완료도 작업을 만든 세션만 할 수 있다(다른 세션의 임시 파일 변조 방지).
        _assert_job_owner(job, principal, job_id)
        expected_size = int(job.get("file_size") or 0)
        received_size = int(job.get("chunk_received_size") or 0)
        file_path = Path(str(job["temp_file_path"]))
        temp_dir = get_upload_directory() / "temp"
        try:
            file_path.resolve().relative_to(temp_dir.resolve())
        except ValueError as error:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "UPLOAD-006",
                    "error": "安全校验失败",
                    "message": "检测到文件路径存在安全威胁",
                    "job_id": job_id,
                },
            ) from error
        actual_size = file_path.stat().st_size if file_path.exists() else -1
        if received_size != expected_size or actual_size != expected_size:
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "UPLOAD-018",
                    "error": "分块上传未完成",
                    "message": "收到的分片未达到文件总大小",
                    "job_id": job_id,
                    "expected_size": expected_size,
                    "received_size": received_size,
                    "actual_size": actual_size,
                },
            )

        upload_dir = get_upload_directory()
        safe_filename = str(job.get("safe_filename") or Path(str(job["filename"])).name)
        # 원본 보관(#10): 완성된 temp 파일 내용을 originals/에 보존한다.
        original_payload = _persist_original_file(
            file_path.read_bytes(),
            upload_dir=upload_dir,
            job_id=job_id,
            safe_filename=safe_filename,
            content_type=str(job.get("content_type") or "") or None,
        )
        processing_attempt_id = str(uuid4())
        filename = str(job["filename"])
        file_type = str(job["file_type"])
        job.update(
            {
                "status": "pending",
                "progress": 0,
                "message": "上传完成，等待处理中...",
                "start_time": datetime.now().timestamp(),
                "processing_attempt_id": processing_attempt_id,
                **original_payload,
            }
        )
        save_upload_job(job_id)
        background_tasks.add_task(
            process_document_background_guarded,
            job_id,
            file_path,
            filename,
            file_type,
            processing_attempt_id,
        )
        estimated_time = estimate_processing_time(expected_size, file_type)
        return UploadResponse(
            job_id=job_id,
            message="文件上传完成！文档处理中。",
            filename=filename,
            file_size=expected_size,
            estimated_processing_time=estimated_time,
            timestamp=datetime.now().isoformat(),
        )
    except HTTPException:
        raise
    except Exception as error:
        logger.error(f"Chunked upload completion error: {error}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": "UPLOAD-008",
                "error": "分块上传完成失败",
                "message": "完成分块上传时发生错误",
                "job_id": job_id,
                "retry_after": 30,
            },
        ) from error
