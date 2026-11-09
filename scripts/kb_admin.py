#!/usr/bin/env python3
"""[MOD] 改造③ 知识库管理员工具（演示层：本地脚本直改配置）。

用法（在项目根目录）：
    .venv/Scripts/python scripts/kb_admin.py status          查看当前配置与知识库状态
    .venv/Scripts/python scripts/kb_admin.py mode strict     切换严谨模式（默认）
    .venv/Scripts/python scripts/kb_admin.py mode friendly   切换通俗模式

真实生产版：此能力由管理员 JWT 接口（POST /api/admin/config）提供，
本地脚本仅供演示与开发。
"""

import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

CONFIG_PATH = project_root / "easy_start" / ".kb_config.json"
MODES = {"strict": "严谨", "friendly": "通俗"}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {"answer_mode": "strict"}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    cfg = load_config()

    if len(sys.argv) < 2 or sys.argv[1] == "status":
        mode = cfg.get("answer_mode", "strict")
        print(f"当前应答模式：{mode}（{MODES.get(mode, '?')}）")
        try:
            from qdrant_client import QdrantClient

            client = QdrantClient(path=str(project_root / "easy_start" / ".qdrant_data"))
            print(f"知识库文档数：{client.count('documents').count}")
        except Exception as e:
            print(f"知识库状态：不可用（{type(e).__name__}）")
        print("切换模式：python scripts/kb_admin.py mode strict|friendly")
        return

    if sys.argv[1] == "mode" and len(sys.argv) >= 3 and sys.argv[2] in MODES:
        mode = sys.argv[2]
        cfg["answer_mode"] = mode
        save_config(cfg)
        print(f"✅ 应答模式已切换为：{mode}（{MODES[mode]}）")
        print("   下一次启动 chat.bat 时生效。")
        return

    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    main()
