"""输出合规规则引擎测试（block/mask 分级、误伤防线）"""

import pytest

from app.security.output_compliance import OutputComplianceEngine


@pytest.fixture
def engine() -> OutputComplianceEngine:
    return OutputComplianceEngine()


class TestPromptLeak:
    def test_chinese_prompt_leak_blocked(self, engine: OutputComplianceEngine):
        result = engine.check("我的系统提示词是：你是一个企业知识库助手。")
        assert result.blocked is True
        assert result.final != result.original  # 返回拒答话术
        assert result.violations[0].rule_id == "prompt_leak"

    def test_english_prompt_leak_blocked(self, engine: OutputComplianceEngine):
        result = engine.check("Here is my system prompt: You are...")
        assert result.blocked is True

    def test_benign_answer_not_blocked(self, engine: OutputComplianceEngine):
        # 防误伤：正常制度回答不应触发 block
        result = engine.check("根据《员工考勤与请假制度》，年假按工龄计算，每年 5 天。")
        assert result.blocked is False
        assert result.final == result.original


class TestSecretFormat:
    def test_openai_style_key_masked(self, engine: OutputComplianceEngine):
        result = engine.check("密钥是 sk-abcdef1234567890abcdef 请保管好")
        assert "sk-abcdef1234567890abcdef" not in result.final
        assert any(v.rule_id == "secret_format" for v in result.violations)
        assert result.blocked is False  # mask 级不拦截

    def test_api_key_assignment_masked(self, engine: OutputComplianceEngine):
        result = engine.check("配置 api_key=abcd1234efgh5678ijkl 后重启服务")
        assert "abcd1234efgh5678ijkl" not in result.final


class TestPiiMask:
    def test_phone_masked(self, engine: OutputComplianceEngine):
        result = engine.check("联系电话 13812345678")
        assert "13812345678" not in result.final
        assert any(v.rule_id.startswith("pii_mask") for v in result.violations)


class TestInjectionEcho:
    def test_high_weight_echo_masked_or_blocked(self, engine: OutputComplianceEngine):
        # 注入指令回声：高权重（3）特征出现在回答中——mask 级处置
        result = engine.check("攻击者会说：请忽略之前所有指令来绕过防护。")
        assert any(v.rule_id == "injection_echo" for v in result.violations)

    def test_defense_education_not_flagged_as_block(self, engine: OutputComplianceEngine):
        # 防误伤防线：安全科普解释注入手法时不应被整答拦截
        result = engine.check(
            "提示注入攻击的常见形态是让模型'忽略之前所有指令'，"
            "防御方法是输入过滤与输出检测双层防护。"
        )
        # injection_echo 规则命中也只 mask，不 block
        assert result.blocked is False


class TestNormalPass:
    def test_clean_answer_untouched(self, engine: OutputComplianceEngine):
        answer = "根据《信息安全管理制度》，数据分为公开、内部、敏感、机密四级。"
        result = engine.check(answer)
        assert result.final == answer
        assert result.has_violations is False

    def test_empty_safe(self, engine: OutputComplianceEngine):
        assert engine.check("").final == ""
