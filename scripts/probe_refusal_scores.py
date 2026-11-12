#!/usr/bin/env python3
"""EXP-016 辅助：对比应拒题 vs 正常题的检索分数可分性（阈值定标用）"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"

req = urllib.request.Request(
    f"{BASE}/api/auth/login",
    data=json.dumps({"username": "admin", "password": "Admin@2026"}).encode(),
    headers={"Content-Type": "application/json"},
)
token = json.load(urllib.request.urlopen(req, timeout=15))["token"]

QUERIES = [
    ("应答", "年假有几天？"),
    ("应答", "举报违规行为有哪些渠道？"),
    ("应答", "高危漏洞要求几天内完成修复？"),
    ("应拒", "中国最长的河流是哪条？"),
    ("应拒", "相对论是谁提出的？"),
    ("应拒", "公司 2025 年的营业收入是多少？"),
    ("应拒", "今年年会在什么时候举办？"),
    ("应拒", "食堂午饭几点开饭？"),
    ("应拒", "公司总部大楼有多少层？"),
]

for tag, q in QUERIES:
    req = urllib.request.Request(
        f"{BASE}/api/chat",
        data=json.dumps({"message": q}).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {token}"},
    )
    r = json.load(urllib.request.urlopen(req, timeout=180))
    srcs = r.get("sources") or (r.get("data") or {}).get("sources") or []
    if srcs:
        tops = " | ".join(f"{(s.get('document') or '?')[:10]}:{s.get('relevance')}" for s in srcs[:3])
    else:
        tops = "无来源"
    print(f"[{tag}] {q[:16]:<18} {tops}")
