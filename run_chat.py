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

# ── [MOD] 改造②：身份选择（Security Trimming 演示）──
ROLES = {
    "1": ("管理员", None),
    "2": ("全员员工", ["全员"]),
    "3": ("技术部员工", ["全员", "技术部"]),
    "4": ("法务部员工", ["全员", "法务部"]),
}

print("选择你的身份（Security Trimming 演示）：")
for key, (name, _) in ROLES.items():
    print(f"  {key}) {name}")
choice = input("输入编号并回车 [1-4，默认 1]: ").strip() or "1"
if choice not in ROLES:
    print("无效编号，按管理员处理")
    choice = "1"

role_name, departments = ROLES[choice]
if departments is None:
    os.environ.pop("KB_DEPARTMENTS", None)  # 管理员：不过滤
else:
    os.environ["KB_DEPARTMENTS"] = json.dumps(departments, ensure_ascii=False)
print(f"✅ 当前身份：{role_name}（可见范围：{'全部文档' if departments is None else '、'.join(departments)}）\n")

# 以 __main__ 身份执行 easy_start/chat.py（其内部会自行把项目根目录加入 sys.path）
runpy.run_path(str(root / "easy_start" / "chat.py"), run_name="__main__")
