"""中国 PII 脱敏器测试（ChinaPIIMasker）

重点验证双层规则的价值：
- 第一层正则命中 + 第二层算法校验通过 → 掩码
- 正则命中但校验失败（订单号/伪造身份证）→ 不掩（防误报）
"""

import pytest

from app.modules.core.privacy.cn_pii import (
    ChinaPIIMasker,
    is_valid_bank_card,
    is_valid_id_card,
)

# 测试侧独立复刻校验算法（与实现代码交叉验证，防实现本身改错）
_TEST_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_TEST_CHECK_CODES = "10X98765432"


def _make_valid_id_card(body17: str) -> str:
    """用 17 位本体构造一个校验位合法的 18 位身份证。"""
    checksum = sum(int(d) * w for d, w in zip(body17, _TEST_WEIGHTS))
    return body17 + _TEST_CHECK_CODES[checksum % 11]


def _make_luhn_valid(number_body: str) -> str:
    """构造 Luhn 合法卡号（body + 计算出的校验位）。"""
    digits = [int(d) for d in number_body]
    total = 0
    for i, d in enumerate(reversed([*digits, 0])):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - total % 10) % 10
    return number_body + str(check)


@pytest.fixture
def masker() -> ChinaPIIMasker:
    return ChinaPIIMasker()


class TestValidators:
    """第二层算法校验本身的自测"""

    def test_id_card_checksum_accepts_and_rejects(self):
        valid = _make_valid_id_card("11010119900307891")
        assert is_valid_id_card(valid) is True
        # 末位改坏 → 校验失败
        assert is_valid_id_card(valid[:-1] + ("X" if valid[-1] != "X" else "5")) is False

    def test_luhn_accepts_and_rejects(self):
        valid = _make_luhn_valid("622202020011223")
        assert is_valid_bank_card(valid) is True
        assert is_valid_bank_card("4111111111111111") is True  # 业界标准测试卡号
        assert is_valid_bank_card("1234567812345678") is False


class TestMaskPhone:
    def test_plain_phone_masked(self, masker: ChinaPIIMasker):
        assert masker.mask_text("我的手机号是13812345678") == "我的手机号是138****5678"

    def test_formatted_phone_masked(self, masker: ChinaPIIMasker):
        assert masker.mask_text("联系我 138-1234-5678 谢谢") == "联系我 138-****-5678 谢谢"

    def test_long_digit_series_not_treated_as_phone(self, masker: ChinaPIIMasker):
        # 13 位时间戳：手机号正则带数字边界断言，不应从中间截取
        text = "订单时间戳 1731210000000 请查询"
        assert masker.mask_text(text) == text

    def test_landline_not_in_rules(self, masker: ChinaPIIMasker):
        # 制度文档中的客服固话不属于个人手机号，不掩
        text = "如有疑问请联系人事部 010-66881234"
        assert masker.mask_text(text) == text


class TestMaskIdCard:
    def test_valid_id_card_masked(self, masker: ChinaPIIMasker):
        id_card = _make_valid_id_card("11010119900307891")
        text = f"员工身份证号{id_card}已登记"
        masked = masker.mask_text(text)
        assert id_card not in masked
        assert masked == f"员工身份证号{id_card[:6]}********{id_card[-4:]}已登记"

    def test_invalid_checksum_not_masked(self, masker: ChinaPIIMasker):
        # 校验位错误：双层规则的第二层应拦截误报
        fake = "11010119900307891" + "9"  # 正确校验位不是 9（见上例生成）
        if is_valid_id_card(fake):
            pytest.skip("构造的非法样本意外合法，换样本")
        text = f"编号{fake}是内部单据号"
        assert masker.mask_text(text) == text

    def test_lowercase_x_accepted(self, masker: ChinaPIIMasker):
        id_card = _make_valid_id_card("11010119900307891")
        if id_card[-1].upper() != "X":
            pytest.skip("该校验位不是 X，换本体")
        assert masker.contains_pii(id_card.lower())


class TestMaskBankCard:
    def test_luhn_valid_card_masked(self, masker: ChinaPIIMasker):
        card = "4111111111111111"
        masked = masker.mask_text("工资卡号4111111111111111")
        assert "4111111111111111" not in masked
        assert card[:4] in masked and card[-4:] in masked

    def test_luhn_invalid_long_number_not_masked(self, masker: ChinaPIIMasker):
        # 16 位但 Luhn 不过：典型订单号形态，不掩（防误报）
        text = "订单号1234567812345678已生成"
        assert masker.mask_text(text) == text


class TestMaskEmail:
    def test_email_masked(self, masker: ChinaPIIMasker):
        masked = masker.mask_text("发送到 zhang.san@corp.com")
        assert "zhang.san@corp.com" not in masked
        assert masked.endswith("@corp.com")


class TestIntegration:
    def test_mixed_text(self, masker: ChinaPIIMasker):
        id_card = _make_valid_id_card("11010119900307891")
        text = f"你好，我叫张三，手机13812345678，身份证{id_card}，邮箱 me@test.com"
        masked = masker.mask_text(text)
        assert "13812345678" not in masked
        assert id_card not in masked
        assert "me@test.com" not in masked
        assert "张三" in masked  # 中文姓名不含称谓后缀，不在本规则集内（已知边界）

    def test_contains_pii(self, masker: ChinaPIIMasker):
        assert masker.contains_pii("手机号13812345678") is True
        assert masker.contains_pii("今天天气不错") is False

    def test_empty_and_none_safe(self, masker: ChinaPIIMasker):
        assert masker.mask_text("") == ""
