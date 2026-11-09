"""[MOD] 改造⑦ 输入侧提示注入防护（Input Guard）。

规则体系设计沿用大模型内容合规检测的正逆向双层经验：
  正向规则（INJECTION_RULES）负责召回攻击特征；
  处置分级 + 句级剥离在拦截攻击与不误伤正常问题之间平衡；
  误报率用 Golden 集正常问题回归验证（scripts/test_injection.py）。

处置分级：
  pass   未命中任何规则，正常放行
  warn   低置信命中：剥离可疑句子后继续回答（攻击意图明显但不构成完整攻击）
  block  高置信命中：直接拒绝，不调用 LLM（省 token 且响应快）

威胁模型对齐 OWASP LLM Top 10（LLM01 提示注入）：
  直接注入（用户输入攻击指令）、身份操纵（角色扮演绕过）、
  信息窃取（套取系统提示词/密钥/语料）、防护解除。
"""

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------
# 正向规则库：(编译后正则, 类别, 权重)
# 权重 3 = 高置信攻击特征；权重 2 = 中置信；权重 1 = 弱特征（累积判级）
# ---------------------------------------------------------------
_INJECTION_RULES_RAW: list[tuple[str, str, int]] = [
    # —— 指令覆盖（LLM01 经典形态）——
    (r"(忽略|无视|越过|跳过|抛开)(你|我|之前|上述|上面|前面|先前|所有|全部|系统)?(的)?(所有)?(指令|规则|提示词?|约束|设定|要求|限制)", "指令覆盖", 3),
    (r"(ignore|disregard|forget|bypass).{0,12}(previous|above|prior|all|your).{0,12}(instructions?|rules?|prompts?|constraints?|guardrails?)", "指令覆盖(EN)", 3),
    # —— 身份/角色操纵 ——（.{0,8} 容忍"AI 助手，从现在开始"类插入语）
    (r"(你|AI|助手|机器人).{0,10}(现在)?(是|变成|扮演|假装|充当|模拟)(一个)?(没有|不受)?(任何)?(安全)?(限制|约束|道德|审查)?(的)?(管理员|开发者|运维|根用户|DAN|越狱模式)", "身份操纵", 3),
    (r"(pretend|act|roleplay).{0,8}(to be|as).{0,12}(admin|developer|root|DAN|unrestricted|jailbreak)", "身份操纵(EN)", 3),
    (r"(解除|关闭|取消|移除|卸下)(你的)?(所有)?(安全)?(限制|约束|防护|审查)", "防护解除", 3),
    (r"(developer|god|DAN)\s*mode", "越狱模式(EN)", 3),
    # —— 信息窃取 ——
    (r"(输出|打印|显示|告诉我|透露|泄露|复述|原样).{0,10}(你的)?(系统提示词?|system\s*prompt|初始提示|内置指令|规则原文|系统设定)", "提示词窃取", 3),
    (r"(输出|打印|显示|告诉我|透露).{0,14}(环境变量|api[_\s]?key|apikey|密钥|secret|密码|凭据|凭证)(的)?(值|内容)?", "密钥窃取", 3),
    (r"(输出|显示|打印|列出|给我).{0,10}(所有|全部|完整).{0,8}(文档|知识库|语料)(的)?(完整)?(内容|原文|全文)", "语料窃取", 3),
    # —— 间接注入特征（多用于扫描文档内容，权重低于直接注入）——
    (r"(AI|人工智能|助手|模型|系统|机器人)(请注意|必须遵守|新指令|指令更新|现在必须)", "间接注入标记", 2),
    (r"(在此之后|从现在起|接下来).{0,8}(你必须|请务必|你将).{0,10}(执行|服从|遵照).{0,8}(指令|要求)", "间接注入标记", 2),
]

INJECTION_RULES = [(re.compile(p, re.IGNORECASE), cat, w) for p, cat, w in _INJECTION_RULES_RAW]

# 剥离可疑句用的切句正则（中文句号/问号/叹号/换行 + 英文句点）
_SENT_SPLIT = re.compile(r"(?<=[。！？!?\n])\s*")

BLOCK_THRESHOLD = 3   # 命中权重总和 >= 3 → block
WARN_THRESHOLD = 1    # 命中权重总和 >= 1 → warn（剥离可疑句）


@dataclass
class GuardHit:
    category: str
    weight: int
    matched: str  # 命中的原文片段（截断）


@dataclass
class GuardResult:
    level: str                 # pass | warn | block
    total_weight: int
    hits: list[GuardHit] = field(default_factory=list)
    cleaned: str = ""          # warn 级剥离可疑句后的安全文本；block/pass 级为原文


def scan_input(text: str) -> GuardResult:
    """扫描用户输入：正则召回 → 权重判级 → warn 级句级剥离。"""
    hits: list[GuardHit] = []
    total = 0
    for pattern, category, weight in INJECTION_RULES:
        m = pattern.search(text)
        if m:
            hits.append(GuardHit(category=category, weight=weight, matched=m.group(0)[:40]))
            total += weight

    if total >= BLOCK_THRESHOLD:
        return GuardResult(level="block", total_weight=total, hits=hits, cleaned=text)
    if total >= WARN_THRESHOLD:
        cleaned = _strip_suspicious_sentences(text, hits)
        return GuardResult(level="warn", total_weight=total, hits=hits, cleaned=cleaned)
    return GuardResult(level="pass", total_weight=0, hits=[], cleaned=text)


def _strip_suspicious_sentences(text: str, hits: list[GuardHit]) -> str:
    """warn 级处置：按句切分，剥离包含命中特征的句子，保留其余正常内容。"""
    suspicious = [h.matched for h in hits]
    kept = []
    for sentence in _SENT_SPLIT.split(text):
        if sentence.strip() and not any(s in sentence for s in suspicious):
            kept.append(sentence)
    cleaned = "".join(kept) if kept else text  # 全句被剥时保底返回原文（由调用方决定拦截）
    return cleaned


def scan_document(content: str) -> GuardResult:
    """间接注入扫描：检查文档内容是否携带针对 AI 的指令（间接注入载体）。

    用于检索后的文档安检：命中的文档不应进入生成上下文。
    """
    return scan_input(content)
