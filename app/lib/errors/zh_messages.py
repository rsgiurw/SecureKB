"""[MOD] 错误消息中文覆盖层（SecureKB 仅中文化，2026-11-10）。

上游错误目录（messages.py）仅 ko/en。本模块为**用户高频可见**的错误码提供
中文覆盖：get_message_template / get_solutions_list 在 lang="zh" 时优先查
本表，缺失的错误码回退 en（回退链 zh → en → ko），保证不会因缺翻译而抛错。

覆盖范围是刻意收窄的：只翻译用户真实可见的高频错误（通用/认证/会话/流式/
限流/上传入口），内部运维类错误码（DB/CONFIG/EMBEDDING 等）保持 en 回退——
全量翻译 331 条的维护成本与收益不成比例。
"""

from __future__ import annotations

# 中文错误消息（错误码 → 消息模板，与 messages.py 的 format 占位符一致）
ZH_ERROR_MESSAGES: dict[str, str] = {
    "API-001": "服务器内部错误",
    "GENERAL-001": "发生了未知错误",
    "GENERAL-002": "输入数据校验失败：{reason}",
    "GENERAL-003": "该功能尚未实现",
    "GENERAL-004": "发生内部错误：{reason}",
    "AUTH-001": "服务器环境变量 FASTAPI_AUTH_KEY 未配置",
    "AUTH-002": "服务器认证配置错误",
    "AUTH-003": "需要 API Key",
    "AUTH-004": "提供的 API Key 无效",
    "AUTH-005": "需要 API Key",
    "AUTH-006": "提供的 API Key 无效",
    "SESSION-001": "会话处理失败",
    "SESSION-002": "会话模块错误",
    "STREAM-001": "回答生成过程中发生错误，请稍后重试。",
    "FEEDBACK-001": "反馈保存失败",
    "RATE-001": "请求过于频繁，请稍后重试。",
    "RATE-002": "聊天请求过于频繁，请稍后重试。",
    "UPLOAD-001": "不支持的文件格式：{content_type}",
    "UPLOAD-002": "文件大小超限：{size}MB（最大 {max_size}MB）",
    "UPLOAD-003": "必要模块不可用",
}

# 中文解决方案（错误码 → 建议列表）
ZH_ERROR_SOLUTIONS: dict[str, list[str]] = {
    "API-001": [
        "请查看服务器日志了解错误详情",
        "尝试重启应用程序",
        "检查数据库与外部服务连接",
        "联系系统管理员",
    ],
    "RATE-001": ["等待约 1 分钟后重试", "减少短时间内的高频操作"],
    "RATE-002": ["等待约 1 分钟后重试", "避免短时间内连续发送大量消息"],
    "STREAM-001": ["稍等片刻后重新发送问题", "若持续出现请联系管理员"],
    "SESSION-001": ["点击「开始新对话」重新开始", "刷新页面后重试"],
    "UPLOAD-001": ["使用支持的格式：PDF、TXT、Markdown、DOCX、PPTX、Excel、HTML、JSON"],
    "UPLOAD-002": ["拆分文件后分批上传", "压缩文件内容后重试"],
}
