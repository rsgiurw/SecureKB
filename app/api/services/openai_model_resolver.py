"""
OpenAI 호환 API 모델 리졸버

슬래시 구분 모델 문자열(예: "openrouter/google/gemini-2.5-flash")을
내부 LLMClientFactory의 provider/model 설정으로 변환합니다.

사용 예:
    parse_model("ollama/qwen2.5:3b")  → ("ollama", "qwen2.5:3b")
    parse_model("gemini")              → ("gemini", None)  # 기본 모델
"""

from __future__ import annotations

from app.lib.logger import get_logger

logger = get_logger(__name__)

# LLMClientFactory._PROVIDER_REGISTRY 키와 일치하는 유효 provider 목록
VALID_PROVIDERS = {
    "google", "gemini", "openai", "anthropic", "claude", "openrouter", "ollama",
    "zhipu", "deepseek", "qwen", "kimi",
}

# provider 별칭 정규화 (사용자 편의)
PROVIDER_ALIASES: dict[str, str] = {
    "gemini": "google",
    "claude": "anthropic",
}

# provider별 기본 모델 (sub_model 미지정 시 사용)
DEFAULT_MODELS: dict[str, str] = {
    "google": "gemini-2.0-flash",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-20250514",
    "openrouter": "google/gemini-2.5-flash",
    "ollama": "llama3.2",
    "zhipu": "glm-4-flash",
    "deepseek": "deepseek-chat",
    "qwen": "qwen-plus",
    "kimi": "moonshot-v1-8k",
}

# /v1/models 엔드포인트에 노출할 모델 목록
AVAILABLE_MODELS = [
    {"id": "gemini", "description": "Google Gemini（默认模型）"},
    {"id": "gemini/gemini-2.5-pro", "description": "Google Gemini 2.5 Pro"},
    {"id": "ollama", "description": "Ollama 本地 LLM（默认模型）"},
    {"id": "ollama/qwen2.5:3b", "description": "Ollama Qwen 2.5 3B"},
    {"id": "ollama/llama3.2", "description": "Ollama LLaMA 3.2"},
    {"id": "openrouter", "description": "OpenRouter（默认模型）"},
    {"id": "openrouter/google/gemini-2.5-flash", "description": "OpenRouter → Gemini 2.5 Flash"},
    {"id": "openrouter/anthropic/claude-sonnet-4.5", "description": "OpenRouter → Claude Sonnet 4.5"},
    {"id": "openrouter/anthropic/claude-3.5-haiku", "description": "OpenRouter → Claude 3.5 Haiku"},
    {"id": "claude", "description": "Anthropic Claude（默认模型）"},
    {"id": "claude/claude-sonnet-4-20250514", "description": "Anthropic Claude Sonnet 4"},
    {"id": "openai", "description": "OpenAI GPT（默认模型）"},
    {"id": "zhipu", "description": "智谱 GLM（默认模型，glm-4-flash 免费）"},
    {"id": "zhipu/glm-4-flash", "description": "智谱 GLM-4-Flash（免费）"},
    {"id": "zhipu/glm-4-air", "description": "智谱 GLM-4-Air"},
    {"id": "zhipu/glm-4-plus", "description": "智谱 GLM-4-Plus（旗舰）"},
    {"id": "deepseek", "description": "DeepSeek（默认模型）"},
    {"id": "deepseek/deepseek-chat", "description": "DeepSeek-V3（对话）"},
    {"id": "deepseek/deepseek-reasoner", "description": "DeepSeek-R1（推理）"},
    {"id": "qwen", "description": "通义千问（默认模型）"},
    {"id": "qwen/qwen-turbo", "description": "通义千问 Turbo（最快）"},
    {"id": "qwen/qwen-plus", "description": "通义千问 Plus（均衡）"},
    {"id": "qwen/qwen-max", "description": "通义千问 Max（旗舰）"},
    {"id": "kimi", "description": "Kimi 月之暗面（默认模型）"},
    {"id": "kimi/moonshot-v1-8k", "description": "Kimi 8K 上下文"},
    {"id": "kimi/moonshot-v1-32k", "description": "Kimi 32K 上下文"},
]


def parse_model(model: str) -> tuple[str, str | None]:
    """
    모델 문자열을 provider와 sub_model로 분리

    Args:
        model: 슬래시 구분 모델 문자열

    Returns:
        (provider, sub_model) 튜플. sub_model이 없으면 None.

    Raises:
        ValueError: 미지원 provider인 경우

    Examples:
        parse_model("gemini")                          → ("gemini", None)
        parse_model("ollama/qwen2.5:3b")               → ("ollama", "qwen2.5:3b")
        parse_model("openrouter/google/gemini-2.5-flash") → ("openrouter", "google/gemini-2.5-flash")
    """
    if "/" in model:
        provider, sub_model = model.split("/", 1)
    else:
        provider = model
        sub_model = None

    provider_lower = provider.lower()

    if provider_lower not in VALID_PROVIDERS:
        raise ValueError(
            f"지원하지 않는 provider: '{provider}'. "
            f"사용 가능: {', '.join(sorted(VALID_PROVIDERS))}"
        )

    return provider_lower, sub_model


def resolve_model_config(provider: str, sub_model: str | None) -> dict[str, str]:
    """
    provider와 sub_model을 LLMClientFactory용 설정으로 변환

    Args:
        provider: provider 이름 (parse_model의 첫 번째 반환값)
        sub_model: 세부 모델 (parse_model의 두 번째 반환값)

    Returns:
        {"provider": "...", "model": "..."} 딕셔너리
    """
    # 별칭 정규화 (gemini → google, claude → anthropic)
    canonical_provider = PROVIDER_ALIASES.get(provider, provider)

    # 모델 결정: 지정된 모델 > 기본 모델
    model = sub_model or DEFAULT_MODELS.get(canonical_provider, "")

    return {
        "provider": canonical_provider,
        "model": model,
    }


def list_available_models() -> list[dict[str, str]]:
    """
    사용 가능한 모델 목록 반환 (/v1/models 엔드포인트용)

    Returns:
        모델 정보 딕셔너리 리스트
    """
    return AVAILABLE_MODELS.copy()
