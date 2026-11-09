"""[MOD] 改造⑦ 输出侧泄露检测（Output Guard）。

生成回答后再过一道安检：拦截"输入侧漏网 + 模型自发泄露"的组合残留。
检测维度：
  1. 系统提示词泄露：回答中包含系统提示词的特征句；
  2. 密钥/凭据形态：sk- 开头、key=value、Bearer token、内部主机地址；
  3. 内部信息越权：回答声称自己切换了身份或解除了限制（模型被绕过的信号）。
命中即整体替换为安全话术（宁枉勿纵：输出侧是最后一道闸）。
"""

import re

# 系统提示词特征句（与 easy_start/prompts/system_prompt_zh.txt 对齐）
_SYSTEM_PROMPT_MARKERS = [
    "仅使用参考文档中的信息",
    "不使用资料之外的知识",
    "你是一个企业知识库助手",
    "你是OneRAG系统的AI助手",
    "检索改写器",
    "对话历史：",
    "【应答模式",
]

# 凭据/密钥形态
_CREDENTIAL_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),                      # OpenAI 风格密钥
    re.compile(r"(api[_\s]?key|apikey)\s*[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    re.compile(r"(ZHIPU_API_KEY|OPENAI_API_KEY|SECRET_KEY)\s*=", re.IGNORECASE),
    re.compile(r"(password|passwd|pwd)\s*[=:]\s*\S{6,}", re.IGNORECASE),
]

# 身份绕过信号：模型在回答中自述"已成为/扮演了"某角色（越狱成功的信号）
_JAILBREAK_SIGNAL = re.compile(
    r"(我|AI|助手)(现在)?(已经)?(是|成为了|变成)(一个)?(没有|不受).{0,8}(限制|约束)"
    r"|(限制|约束|防护).{0,6}(已|已经)?(解除|关闭|移除)"
)


def _system_prompt_text() -> str:
    from pathlib import Path

    p = Path(__file__).parent.parent / "easy_start" / "prompts" / "system_prompt_zh.txt"
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def check_output(answer: str) -> tuple[bool, list[str]]:
    """检查模型输出是否泄露敏感内容。

    Returns:
        (是否拦截, 命中的泄露类型列表)
    """
    hits: list[str] = []
    sys_prompt = _system_prompt_text()

    # 1. 系统提示词泄露：回答中含提示词特征句，或包含提示词原文中较长的连续片段
    for marker in _SYSTEM_PROMPT_MARKERS:
        if marker in answer:
            hits.append("系统提示词泄露")
            break
    if not hits and len(sys_prompt) > 20:
        # 取提示词中一个 24 字的窗口做指纹比对（模型复述提示词时难以完全改写）
        fingerprint = sys_prompt[10:34]
        if fingerprint in answer:
            hits.append("系统提示词原文复述")

    # 2. 凭据形态
    for p in _CREDENTIAL_PATTERNS:
        if p.search(answer):
            hits.append("密钥/凭据形态")
            break

    # 3. 越狱信号
    if _JAILBREAK_SIGNAL.search(answer):
        hits.append("身份绕过信号")

    return (len(hits) > 0, hits)


def safe_answer() -> str:
    """拦截后的统一安全话术。"""
    return "抱歉，该回答触发了输出安全策略，已拦截。如有疑问请联系数据安全员。"
