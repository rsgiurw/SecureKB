"""
中国 PII 脱敏模块（ChinaPIIMasker）

SecureKB 安全模块：对话入口的中国个人身份信息清洗。
上游 PrivacyMasker 面向韩国标识符（주민등록번호/운전면허等），对中文场景
全部失效，故独立实现本模块（接口与 PrivacyMasker 对齐，便于替换）。

规则设计（正逆向双层，源自电信内容合规检测经验）：
- 第一层（正向初筛）：正则匹配格式特征——快、召回高；
- 第二层（逆向校验）：算法校验位二次确认——身份证 GB11643 加权校验、
  银行卡 Luhn 算法。校验失败的命中不掩码，防止把订单号/流水号等
  长数字串误掩（误报会破坏回答可读性，漏报会泄露 PII，两者权衡下
  对"格式合法的 PII"从严、对"长得像的数字串"从宽）。

掩码格式（保留部分明文以便人工核对，行业惯例）：
- 手机号   13812345678        → 138****5678
- 身份证   11010119900307891X → 110101********91X（前 6 后 4）
- 银行卡   6222020200112233456 → 6222*********3456（前 4 后 4）
- 邮箱     zhang.san@corp.com → z*******n@corp.com

已知边界：
- 固话（区号开头的客服电话）不在规则内——制度文档中的机构联系方式
  属于公开信息，掩码反而破坏回答可读性；
- 流式输出按 chunk 渐进发射，跨 chunk 的 PII 无法事后掩码；本模块
  应用于入口（用户消息进入检索/存储/日志前），入口清洗后检索上下文
  与生成结果即无 PII 来源（纵深防御第一层）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.lib.logger import get_logger

logger = get_logger(__name__)


# ========================================
# 第一层：格式正则（正向初筛）
# ========================================
# (?<!\d)/(?!\d) 数字边界断言：防止从更长数字串（订单号、时间戳）中间截取。
CHINA_PII_PATTERNS: dict[str, str] = {
    # 手机号（带分隔符变体）：138-1234-5678 / 138 1234 5678
    "phone_formatted": r"(?<!\d)1[3-9]\d[-\s]\d{4}[-\s]\d{4}(?!\d)",
    # 手机号（裸 11 位）
    "phone_plain": r"(?<!\d)1[3-9]\d{9}(?!\d)",
    # 身份证 18 位（末位可为 X）
    "id_card": r"(?<!\d)\d{17}[\dXx](?!\d)",
    # 银行卡 16-19 位（须经 Luhn 二次确认）
    "bank_card": r"(?<!\d)\d{16,19}(?!\d)",
    # 邮箱
    "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
}


# ========================================
# 第二层：算法校验（逆向确认）
# ========================================
_ID_CARD_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CARD_CHECK_CODES = "10X98765432"


def is_valid_id_card(number: str) -> bool:
    """GB 11643-1999 身份证校验位验证（18 位）。"""
    body = number[:17].upper()
    if not body.isdigit():
        return False
    checksum = sum(int(d) * w for d, w in zip(body, _ID_CARD_WEIGHTS))
    expected = _ID_CARD_CHECK_CODES[checksum % 11]
    return number[-1].upper() == expected


def is_valid_bank_card(number: str) -> bool:
    """Luhn 算法验证（银行卡/信用卡通用）。"""
    digits = [int(d) for d in number]
    if len(digits) < 2:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class ChinaPIIDetection:
    """单次掩码处理的检测结果（审计/统计用）"""

    original: str
    masked: str
    phone_count: int = 0
    id_card_count: int = 0
    bank_card_count: int = 0
    email_count: int = 0
    skipped_by_validation: int = 0  # 正则命中但校验失败（防误报）的次数
    details: list[dict[str, str]] = field(default_factory=list)

    @property
    def total_masked(self) -> int:
        return self.phone_count + self.id_card_count + self.bank_card_count + self.email_count


class ChinaPIIMasker:
    """中国个人身份信息脱敏器（正则初筛 + 算法校验双层规则）"""

    def __init__(
        self,
        mask_phone: bool = True,
        mask_id_card: bool = True,
        mask_bank_card: bool = True,
        mask_email: bool = True,
        validate_id_card: bool = True,
        validate_bank_card: bool = True,
    ):
        self.mask_phone = mask_phone
        self.mask_id_card = mask_id_card
        self.mask_bank_card = mask_bank_card
        self.mask_email = mask_email
        # 校验开关默认开启：关闭仅用于测试对照（单层规则 vs 双层规则的误报对比）
        self.validate_id_card = validate_id_card
        self.validate_bank_card = validate_bank_card

        self._patterns = {
            key: re.compile(pattern) for key, pattern in CHINA_PII_PATTERNS.items()
        }

    # ----------------------------------------
    # 主入口
    # ----------------------------------------
    def mask_text(self, text: str) -> str:
        """文本中的中国 PII 掩码（入口清洗主入口）。"""
        if not text:
            return text
        return self.mask_text_detailed(text).masked

    def mask_text_detailed(self, text: str) -> ChinaPIIDetection:
        if not text:
            return ChinaPIIDetection(original=text, masked=text)

        result = text
        detection = ChinaPIIDetection(original=text, masked=text)

        # 顺序：身份证（最敏感）→ 银行卡 → 手机号 → 邮箱
        # 注意银行卡在裸手机号前处理：两者都是纯数字，先长后短减少互扰。
        if self.mask_id_card:
            result, count, skipped = self._apply(
                result, "id_card", self._validate_id_card_hit
            )
            detection.id_card_count += count
            detection.skipped_by_validation += skipped

        if self.mask_bank_card:
            result, count, skipped = self._apply(
                result, "bank_card", self._validate_bank_card_hit
            )
            detection.bank_card_count += count
            detection.skipped_by_validation += skipped

        if self.mask_phone:
            # 带分隔符的变体先处理（否则裸号正则可能吃掉部分片段）
            result, count, skipped = self._apply(
                result, "phone_formatted", self._validate_phone_hit
            )
            detection.phone_count += count
            detection.skipped_by_validation += skipped
            result, count, skipped = self._apply(
                result, "phone_plain", self._validate_phone_hit
            )
            detection.phone_count += count
            detection.skipped_by_validation += skipped

        if self.mask_email:
            result, count, _ = self._apply(result, "email", None)
            detection.email_count += count

        detection.masked = result
        if detection.total_masked > 0:
            logger.info(
                f"中国 PII 掩码完成: 手机 {detection.phone_count}, 身份证 {detection.id_card_count}, "
                f"银行卡 {detection.bank_card_count}, 邮箱 {detection.email_count}, "
                f"校验拦截误报 {detection.skipped_by_validation}"
            )
        return detection

    def contains_pii(self, text: str) -> bool:
        """文本是否包含（格式+校验双合格的）中国 PII。"""
        if not text:
            return False
        detection = self.mask_text_detailed(text)
        return detection.total_masked > 0

    # ----------------------------------------
    # 内部：通用应用器（匹配 → 校验 → 掩码）
    # ----------------------------------------
    def _apply(
        self,
        text: str,
        pattern_key: str,
        validator: Any = None,  # Callable[[str], bool] | None；None 表示正则命中即掩码
    ) -> tuple[str, int, int]:
        """对 text 应用单个 PII 规则。返回 (掩码后文本, 掩码数, 校验拦截数)。"""
        pattern = self._patterns[pattern_key]
        masked_count = 0
        skipped_count = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal masked_count, skipped_count
            hit = match.group()
            if validator is not None and not validator(hit):
                skipped_count += 1
                return hit  # 校验失败：不掩码（防误报）
            masked_count += 1
            return self._mask_hit(pattern_key, hit)

        new_text = pattern.sub(replace, text)
        return new_text, masked_count, skipped_count

    def _validate_id_card_hit(self, hit: str) -> bool:
        return (not self.validate_id_card) or is_valid_id_card(hit)

    def _validate_bank_card_hit(self, hit: str) -> bool:
        return (not self.validate_bank_card) or is_valid_bank_card(hit)

    def _validate_phone_hit(self, hit: str) -> bool:
        # 手机号段约束（1[3-9]）已是第一层初筛，无第二层算法可做
        return True

    def _mask_hit(self, pattern_key: str, hit: str) -> str:
        char = "*"
        if pattern_key == "id_card":
            # 前 6（地区码）后 4 保留，中间 8 位掩码（18-6-4）
            return hit[:6] + char * 8 + hit[-4:]
        if pattern_key == "bank_card":
            # 前 4 后 4 保留
            return hit[:4] + char * (len(hit) - 8) + hit[-4:]
        if pattern_key in ("phone_plain", "phone_formatted"):
            # 按原格式重建：保留分隔符位置，掩中间 4 位
            if "-" in hit:
                parts = hit.split("-")
                return f"{parts[0]}-{char * 4}-{parts[2]}"
            if " " in hit:
                parts = hit.split(" ")
                return f"{parts[0]} {char * 4} {parts[2]}"
            return hit[:3] + char * 4 + hit[-4:]
        if pattern_key == "email":
            local, domain = hit.split("@")
            if len(local) <= 2:
                return f"{local[0]}{char}@{domain}"
            return f"{local[0]}{char * (len(local) - 2)}{local[-1]}@{domain}"
        return char * len(hit)
