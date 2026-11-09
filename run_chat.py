"""中文 CLI 问答启动器：自动加载 .env（ZHIPU_API_KEY 等），无需手动设置环境变量。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe run_chat.py
或直接双击 chat.bat / 在 cmd 里运行 chat.bat
"""

import json
import os
import runpy
import sys
from pathlib import Path

root = Path(__file__).parent
sys.path.insert(0, str(root))

from dotenv import load_dotenv  # noqa: E402

# 加载根目录 .env（ZHIPU_API_KEY / ZHIPU_MODEL 等），已存在的环境变量不被覆盖
load_dotenv(root / ".env")
os.environ.setdefault("EASY_START_LANG", "zh")  # 默认中文界面与中文语料

if not os.getenv("ZHIPU_API_KEY"):
    print("❌ 未找到 ZHIPU_API_KEY：请确认根目录 .env 文件存在且已填写")
    sys.exit(1)

# ── [MOD] 改造③：读取知识库应答模式配置（kb_admin.py 管理）──
_cfg_path = root / "easy_start" / ".kb_config.json"
if _cfg_path.exists():
    try:
        _mode = json.loads(_cfg_path.read_text(encoding="utf-8")).get("answer_mode", "strict")
        os.environ.setdefault("KB_ANSWER_MODE", _mode)
    except (ValueError, OSError):
        pass
os.environ.setdefault("KB_ANSWER_MODE", "strict")

# ── [MOD] 改造②：身份选择（Security Trimming 演示）──
# 权限模型 = 角色 × 部门 两个正交维度：
#   角色（管理员/员工）决定是否过滤；部门是员工的属性，决定可见范围。
DEPTS = ["全员", "技术部", "法务部", "行政部"]

print("选择角色：")
print("  1) 管理员（可见全部文档）")
print("  2) 普通员工（仅可见'全员'及本部门文档）")
role = input("输入编号并回车 [1-2，默认 2]: ").strip() or "2"

if role == "1":
    departments = None
    os.environ.pop("KB_DEPARTMENTS", None)  # 管理员：不过滤
    role_name = "管理员"
else:
    print(f"选择你的部门：{' / '.join(f'{i+1}) {d}' for i, d in enumerate(DEPTS))}")
    dept_choice = input("输入编号并回车 [1-4，默认 1 全员]: ").strip() or "1"
    dept = DEPTS[int(dept_choice) - 1] if dept_choice.isdigit() and 1 <= int(dept_choice) <= len(DEPTS) else "全员"
    departments = ["全员"] if dept == "全员" else ["全员", dept]
    os.environ["KB_DEPARTMENTS"] = json.dumps(departments, ensure_ascii=False)
    role_name = f"普通员工（{dept}）"

print(f"✅ 当前身份：{role_name}（可见范围：{'全部文档' if departments is None else '、'.join(departments)}）\n")

# 以 __main__ 身份执行 easy_start/chat.py（其内部会自行把项目根目录加入 sys.path）
runpy.run_path(str(root / "easy_start" / "chat.py"), run_name="__main__")
