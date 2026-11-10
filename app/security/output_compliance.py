"""[MOD] 输出合规规则引擎（SecureKB 安全模块，2026-11-10）。

把原先分散在多处的输出侧检查（PII 出口掩码、系统提示词泄漏检测）整合成
统一的规则框架：规则注册表 → 命中检测 → 分级处置（block / mask）→ 审计数据。

处置分级：
  block  命中后整个回答不予返回，以拒答话术替代——用于不可挽回的泄露
         （系统提示词/内部指令被复述，回答已不可信）。
  mask   命中片段被替换为掩码，回答其余部分继续返回——用于可精确移除的
         泄露（PII、密钥串、注入指令回声）。

内置规则（可按 rule_id 开关）：
  pii_mask          中国 PII 出口掩码（复用 ChinaPIIMasker 双层规则）     [mask]
  prompt_leak       系统提示词/内部指令泄漏（中文+韩英关键词）              [block]
  secret_format     密钥串格式特征（sk- 开头、api_key= 赋值等）            [mask]
  injection_echo    注入指令在回答中的回声（复用 InputGuard 高权重规则）    [mask]

设计原则与 InputGuard 同构：正逆向双层（特征召回 + 精确处置），审计数据
（violations）随结果返回供审计日志记录。误伤与漏报的权衡记录于 EXP-014。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.lib.logger import get_logger
from app.modules.core.privacy.cn_pii import ChinaPIIMasker
from app.security.input_guard import INJECTION_RULES

logger = get_logger(__name__)

# block 级处置的拒答话术（与 InputGuard 拒答话术语气一致）
OUTPUT_BLOCKED_ANSWER = (
    "抱歉，系统生成的回答未通过输出合规检查，本次内容已被拦截。"
    "请调整问题后重试；如持续出现请联系管理员。"
)

# ---------------------------------------------------------------
# prompt_leak 规则：系统提示词/内部指令泄漏特征
# 中文（SecureKB 新增）+ 上游韩英关键词合并
# ---------------------------------------------------------------
_PROMPT_LEAK_PATTERNS: list[str] = [
    # 中文（LLM 被诱导复述系统提示词时的典型措辞）
    r"我的(系统)?(提示词|指令|设定|规则)(是|如下|包括)",
    r"(系统|初始|内置)提示词",
    r"(我的|内置)?(指令|规则)原文",
    r"<system_instructions>",
    r"system_instructions",
    # 韩英（沿用上游 prompt_sanitizer 的 LEAK_PATTERNS 核心项）
    r"system\s*prompt",
    r"previous\s+instructions",
    r"ignore\s+instructions",
    r"my\s+instructions\s+are",
    r"jailbreak",
    r"dan\s+mode",
    r"developer\s+mode\s+activated",
]

# ---------------------------------------------------------------
# secret_format 规则：常见密钥串格式特征
# ---------------------------------------------------------------
_SECRET_FORMAT_PATTERNS: list[str] = [
    # OpenAI 风格 sk- 开头长串
    r"\bsk-[A-Za-z0-9]{20,}\b",
    # 赋值/查询串形态的密钥（api_key=xxx、apikey: xxx、Authorization: Bearer xxx）
    r"(?i)\b(api[_\s-]?key|apikey|secret|token)\s*[=:]\s*['\"]?[A-Za-z0-9._\-]{16,}",
    # 智谱 key 形态（32 位十六进制/点分格式）
    r"(?<!\d)\b[0-9a-f]{16}\.[A-Za-z0-9]{16}\b",
]

_COMPILED_PROMPT_LEAK = [re.compile(p, re.IGNORECASE) for p in _PROMPT_LEAK_PATTERNS]
_COMPILED_SECRET_FORMAT = [re.compile(p) for p in _SECRET_FORMAT_PATTERNS]


@dataclass
class ComplianceViolation:
    """单条规则命中"""

    rule_id: str
    severity: str          # block | mask
    matched: str           # 命中片段（截断，审计用）


@dataclass
class ComplianceResult:
    """输出合规检查结果"""

    original: str
    final: str             # 处置后的文本（blocked 时为拒答话术）
    blocked: bool = False
    violations: list[ComplianceViolation] = field(default_factory=list)

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0


class OutputComplianceEngine:
    """输出合规规则引擎（block/mask 分级处置）"""

    def __init__(
        self,
        pii_masker: ChinaPIIMasker | None = None,
        enabled_rules: set[str] | None = None,
    ):
        """
        Args:
            pii_masker: PII 掩码器（None 则内部创建）
            enabled_rules: 启用的规则 id 集合；None = 全部启用
        """
        self.pii_masker = pii_masker or ChinaPIIMasker()
        self.enabled_rules = enabled_rules  # None = 全启用

    def _is_enabled(self, rule_id: str) -> bool:
        return self.enabled_rules is None or rule_id in self.enabled_rules

    # ----------------------------------------
    # 主入口
    # ----------------------------------------
    def check(self, text: str) -> ComplianceResult:
        """对 LLM 输出执行合规检查与处置。

        处置顺序：block 级检测（prompt_leak / injection_echo 任一命中即整答拦截）
        → mask 级处理（secret_format / injection_echo 片段掩码 / pii 掩码）。
        """
        if not text:
            return ComplianceResult(original=text, final=text)

        violations: list[ComplianceViolation] = []

        # 1. block 级：系统提示词泄漏（不可挽回——回答已复述内部指令，整答拦截）
        if self._is_enabled("prompt_leak"):
            for pattern in _COMPILED_PROMPT_LEAK:
                m = pattern.search(text)
                if m:
                    violations.append(ComplianceViolation(
                        rule_id="prompt_leak", severity="block", matched=m.group(0)[:40],
                    ))
                    break  # 一条命中即拦截，无需继续

        if violations:
            logger.warning(
                f"输出合规拦截: {[(v.rule_id, v.matched) for v in violations]}"
            )
            return ComplianceResult(
                original=text, final=OUTPUT_BLOCKED_ANSWER, blocked=True,
                violations=violations,
            )

        # 2. mask 级：密钥格式掩码
        final = text
        if self._is_enabled("secret_format"):
            for pattern in _COMPILED_SECRET_FORMAT:
                def secret_replace(m: re.Match[str]) -> str:
                    hit = m.group(0)
                    violations.append(ComplianceViolation(
                        rule_id="secret_format", severity="mask", matched=hit[:40],
                    ))
                    # 前 4 字符保留 + 定长掩码，保留可辨识度
                    return hit[:4] + "*" * 12

                final = pattern.sub(secret_replace, final)

        # 3. mask 级：注入指令回声（InputGuard 高权重规则）——回答中复述攻击
        #    特征句时只掩命中片段、不整答拦截：安全科普类回答（讲解攻击形态）
        #    会被误伤，片段掩码把损失限制在单个句子内（EXP-014 误伤测试覆盖）。
        if self._is_enabled("injection_echo"):
            for pattern, _category, weight in INJECTION_RULES:
                if weight < 3:
                    continue

                def echo_replace(m: re.Match[str]) -> str:
                    violations.append(ComplianceViolation(
                        rule_id="injection_echo", severity="mask", matched=m.group(0)[:40],
                    ))
                    return "[已过滤]"

                final = pattern.sub(echo_replace, final)

        # 4. mask 级：PII 掩码（复用双层规则）
        if self._is_enabled("pii_mask"):
            pii_detection = self.pii_masker.mask_text_detailed(final)
            if pii_detection.total_masked > 0:
                for count_key, rule_suffix in (
                    ("phone_count", "phone"), ("id_card_count", "id_card"),
                    ("bank_card_count", "bank_card"), ("email_count", "email"),
                ):
                    n = getattr(pii_detection, count_key)
                    if n:
                        violations.append(ComplianceViolation(
                            rule_id=f"pii_mask/{rule_suffix}", severity="mask",
                            matched=f"{n} 处",
                        ))
                final = pii_detection.masked

        return ComplianceResult(original=text, final=final, violations=violations)
