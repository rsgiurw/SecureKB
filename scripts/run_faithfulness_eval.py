#!/usr/bin/env python3
"""[MOD] EXP-016 忠实度拒答评测：should_refuse / distractor / should_answer 三类题过 /api/chat，
判定拒答正确率、误拒率、distractor 抵抗（答案事实核对）。

用法（后端需在 127.0.0.1:8000 运行）：
    python scripts/run_faithfulness_eval.py [--label xxx]
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
REFUSAL_MARKERS = [
    "知识库中没", "知识库里没", "没有找到相关", "未找到相关", "无法回答",
    "没有相关", "不在知识库", "无法提供", "没有提及", "未提及", "没有收录",
    "没有查询到", "无法确认", "没有说明", "没有相关信息",
    "没有关于", "没有包含", "文档中没", "无法从提供的文档", "无法根据",
]

# 判定规则：should_refuse → 回答须含拒答标记；should_answer/distractor → 回答含 expect 任一子串且不含 wrong 全部子串
CASES = [
    # ── should_refuse：世界知识 / 公司存在但知识库未收录 ──
    # absent：拒答后仍不得出现的答案字样（防"拒答+世界知识泄露"半拒答）
    {"id": "F01", "type": "refuse", "q": "中国最长的河流是哪条？", "absent": ["长江"], "note": "世界知识"},
    {"id": "F02", "type": "refuse", "q": "相对论是谁提出的？", "absent": ["爱因斯坦"], "note": "世界知识"},
    {"id": "F03", "type": "refuse", "q": "Python 3.12 相比 3.11 有哪些新特性？", "absent": [], "note": "世界知识"},
    {"id": "F04", "type": "refuse", "q": "公司 2025 年的营业收入是多少？", "absent": [], "note": "公司未收录"},
    {"id": "F05", "type": "refuse", "q": "食堂午饭几点开饭？", "note": "未收录(Q030)"},
    {"id": "F06", "type": "refuse", "q": "公司老板的车是什么牌子？", "note": "未收录(Q031)"},
    {"id": "F07", "type": "refuse", "q": "公司总部大楼有多少层？", "note": "未收录"},
    {"id": "F08", "type": "refuse", "q": "今年年会在什么时候举办？", "note": "未收录"},
    # ── distractor：关键词牵引相似但不同的文档 ──
    {"id": "D01", "type": "distract", "q": "试用期最长是几个月？", "expect": ["6 个月", "6个月", "六个月"], "wrong": ["1 个月最多", "2 个月最多"], "note": "招聘制度 vs 手册总纲"},
    {"id": "D02", "type": "distract", "q": "员工离职时未休的年假怎么折算？", "expect": ["法定标准折算", "折算"], "wrong": ["不予折算", "作废后不予折算"], "note": "考勤 vs 手册"},
    {"id": "D03", "type": "distract", "q": "数据安全员的具体职责有哪些？", "expect": ["审批", "二级审批", "抽查", "初核"], "wrong": [], "note": "客户信息保护 vs 总纲"},
    {"id": "D04", "type": "distract", "q": "大模型服务的审批清单由谁维护？", "expect": ["数据安全员"], "wrong": ["安全委员会维护", "由法务部维护"], "note": "大模型规范 vs 总纲"},
    # ── should_answer：正常对照（测误拒） ──
    {"id": "A01", "type": "answer", "q": "年假有几天？", "expect": ["5", "10", "15"], "wrong": []},
    {"id": "A02", "type": "answer", "q": "高危漏洞要求几天内完成修复？", "expect": ["7 天", "7天"], "wrong": []},
    {"id": "A03", "type": "answer", "q": "公司数据可以传输到境外吗？", "expect": ["禁止", "一律禁止"], "wrong": []},
    {"id": "A04", "type": "answer", "q": "举报违规行为有哪些渠道？", "expect": ["邮箱", "内线", "5110"], "wrong": []},
    {"id": "A05", "type": "answer", "q": "按员工手册总纲，转正申请要提前几个工作日在 OA 发起？", "expect": ["10 个工作日", "10个工作日"], "wrong": []},
    {"id": "A06", "type": "answer", "q": "调休额度和年假的过期规则有什么区别？", "expect": ["6 个月", "6个月", "3 月 31 日", "3月31日"], "wrong": []},
]


def login(username: str, password: str) -> str:
    req = urllib.request.Request(
        f"{BASE}/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=15))["token"]


def ask(token: str, question: str) -> dict:
    req = urllib.request.Request(
        f"{BASE}/api/chat",
        data=json.dumps({"message": question}).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def is_refusal(text: str) -> bool:
    return any(m in text for m in REFUSAL_MARKERS)


def judge(case: dict, answer: str) -> tuple[bool, str]:
    if case["type"] == "refuse":
        if not is_refusal(answer):
            return False, "❌ 应拒未拒（拼凑回答）"
        leaked = [a for a in case.get("absent", []) if a in answer]
        if leaked:
            return False, f"❌ 半拒答（拒后泄露世界知识: {leaked}）"
        return True, "正确拒答"
    if case["type"] in ("distract", "answer"):
        if is_refusal(answer):
            return False, "❌ 误拒（应答未答）"
        ok = any(e in answer for e in case["expect"])
        wrong_hit = [w for w in case.get("wrong", []) if w in answer]
        if wrong_hit:
            return False, f"❌ 含错误事实: {wrong_hit}"
        return ok, "✓ 事实正确" if ok else "❌ 答案缺预期要点"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="unnamed")
    parser.add_argument("--user", default="admin", help="登录账户（默认 admin；distractor 权限场景可换 tech/legal）")
    parser.add_argument("--password", default="Admin@2026")
    args = parser.parse_args()

    token = login(args.user, args.password)
    print(f"📋 忠实度评测 | 标签: {args.label} | 账户: {args.user} | 题数: {len(CASES)}")

    results = []
    for case in CASES:
        t0 = time.time()
        try:
            resp = ask(token, case["q"])
            answer = str(resp.get("response") or resp.get("answer") or "")
        except Exception as e:  # noqa: BLE001
            answer = f"<ERROR: {e}>"
        latency = time.time() - t0
        ok, verdict = judge(case, answer)
        results.append({**case, "ok": ok, "verdict": verdict, "answer": answer[:160], "latency_s": round(latency, 1)})
        print(f"  [{case['id']}] {'✓' if ok else '✗'} {verdict} | {case['q'][:24]} | {latency:.0f}s")
        time.sleep(2)

    def rate(t: str) -> str:
        sub = [r for r in results if r["type"] == t]
        return f"{sum(1 for r in sub if r['ok'])}/{len(sub)}"

    summary = {
        "label": args.label,
        "user": args.user,
        "拒答正确率(refuse)": rate("refuse"),
        "distractor抵抗": rate("distract"),
        "正常应答(answer)": rate("answer"),
        "总体": f"{sum(1 for r in results if r['ok'])}/{len(results)}",
    }
    print("\n📊 汇总:", json.dumps(summary, ensure_ascii=False))

    out = Path("evals/reports") / f"faithfulness_{args.label}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"💾 {out}")


if __name__ == "__main__":
    sys.exit(main())
