<p align="center">
  <img src="assets/logo.svg" alt="SecureKB Logo" width="380"/>
</p>

<p align="center">
  <strong>安全加固的企业知识库问答系统 —— 手写可解释的检索链路 · 安全工程 · 量化评测</strong>
</p>

<p align="center">
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.12-blue.svg" alt="Python 3.12"></a>
  <img src="https://img.shields.io/badge/LLM-智谱GLM__·__embedding__3-3776ab" alt="智谱 GLM">
  <img src="https://img.shields.io/badge/向量库-Qdrant__本地模式-d36550" alt="Qdrant">
</p>

---

**SecureKB** 是基于 [OneRAG](https://github.com/notadev-iamaura/OneRAG)（MIT）深度改造的中文企业知识库问答系统：
员工用中文提问，系统在**权限范围内**检索知识库（混合检索 + 权限过滤），由大模型生成**带引用溯源**的回答，
并对输入/检索/输出全链路做**提示注入防护**。

> 本仓库在 OneRAG 基础上完成 **七项深度改造**，全部配有对比实验数据（见 [docs/experiments.md](docs/experiments.md)）。

## 界面演示

**提示注入拦截**——恶意指令被输入侧防护直接拒绝，RAG Trace 面板显示命中 `input_guard`：

![提示注入拦截](assets/demo-injection-block.png)

**检索溯源（管理员）**——回答引用知识库原文，右侧展示检索来源、相关度与 Top-K 候选：

![管理员检索溯源](assets/demo-admin-retrieval.png)

**部门权限隔离（普通员工）**——同一问题，员工无权限的文档内容不泄露，检索层服务端过滤：

![员工权限隔离](assets/demo-employee-isolation.png)

**知识库文档管理（管理员）**——多格式解析入库（PDF / Word / Excel / PPT / TXT / Markdown 等）、部门标签归类（权限语义）、检索与删除一体：

![知识库文档管理](assets/demo-doc-management.png)

**评测运行记录**（真实输出节选，完整报告见 `evals/reports/`）：

<details>
<summary>忠实度拒答评测（faithfulness_gate-final）</summary>

```json
{
  "label": "gate-final",
  "拒答正确率(refuse)": "8/8",
  "distractor抵抗": "4/4",
  "正常应答(answer)": "4/6",
  "总体": "16/18"
}
```

</details>

<details>
<summary>HyDE 查询扩展评测（负收益实证 → 生产关停）</summary>

```json
{
  "recall@1": 0.476,
  "recall@3": 0.881,
  "MRR": 0.690
}
```

</details>

## 核心数据（实测）

### 检索质量（EXP-005/006，Golden 集 31 题）

| 指标 | Dense 基线 | BM25+RRF 混合检索 | 变化 |
| --- | --- | --- | --- |
| recall@1 | 0.897 | **0.931** | +3.4pp |
| recall@3 | 0.966 | **1.000** | +3.4pp |
| MRR | 0.938 | **0.966** | +0.028 |

### 安全防护（EXP-009）

| 指标 | 数值 |
| --- | --- |
| 提示注入拦截率 | **100%**（16/16 攻击样本，两轮规则迭代） |
| 正常问题误伤率 | **0%**（12 正常样本回归） |
| 间接注入文档识别 | 2/2 |

## 七项深度改造

| # | 改造 | 说明 |
| --- | --- | --- |
| ① | 智谱 GLM 接入 | 生成（glm-4-flash）+ 嵌入（embedding-3，1024 维）切换国内直连的智谱服务；五层配置接线（generation/embeddings/环境配置/schema/校验器） |
| ② | Security Trimming | 文档 payload 打部门标签（全员/技术部/法务部），检索层按身份过滤。**关键发现：混合检索下每一条召回路都必须过滤**，只过滤 Dense 路会经 BM25 路泄露（EXP-007） |
| ③ | 应答模式可配 | 严谨/通俗两档（配置文件 + 管理脚本），严格模式自动携带条款引用；双档行为差异经 A/B 验证（EXP-004/004b） |
| ④ | 自建评测体系 | Golden 集 31 题（事实/跨文档/拒答三类）+ `run_retrieval_eval.py` 自动回归（recall@k/MRR/延迟），与上游 RAGAS 生成评测互补 |
| ⑤ | 混合检索 | BM25（**jieba 中文分词**替换上游韩语 kiwipiepy）+ Dense 双路召回 + RRF 融合（k=60），精确术语查询 rank 5→2（EXP-006） |
| ⑥ | 多轮指代消解 | 检索前查询改写：“那财务审批呢？”→“合同审批流程里财务审批需要多久？”，改写失败优雅降级（EXP-008，3/3 消解成功） |
| ⑦ | 提示注入防护 | 输入侧 13 条规则分级处置（block/句级剥离/放行）+ 间接注入文档扫描 + 输出侧泄露检测（提示词指纹/密钥形态/越狱信号） |

## 架构

```
┌─ 管理员 ─────────────────────────────────────────┐
│ 上传文档（部门标签）→ 文档管理 → 应答模式配置      │
└──────────────────┬───────────────────────────────┘
                   ↓ 解析 → jieba 分块 → 智谱 embedding-3（1024 维）
        ┌────────────────────┐
        │ Qdrant 本地模式     │  +  BM25 索引（部门标签 payload）
        │ （部门标签 payload）│
        └─────────┬──────────┘
                  ↓
┌─ 员工提问 ────────────────────────────────────────┐
│ ① 输入侧注入防护（规则分级：拦截/剥离/放行）        │
│ ② 查询改写（多轮指代消解）                         │
│ ③ Dense + BM25 双路召回 → RRF 融合                 │
│ ④ Security Trimming（部门过滤，双路都过滤）        │
│ ⑤ 间接注入文档扫描                                 │
│ ⑥ 智谱 GLM 生成（严谨/通俗模式）                   │
│ ⑦ 输出侧泄露检测                                   │
└──────────────────┬────────────────────────────────┘
                   ↓ 带引用溯源的回答 + 安全事件标注
```

## 快速开始

### 前置要求

- Python 3.11+（开发环境 3.12）
- Node.js 20+（官方前端）
- [智谱开放平台](https://open.bigmodel.cn) API Key（glm-4-flash 免费；embedding-3 约 0.5 元/百万 token）

### 启动

```bash
# 1. 后端依赖（约 300 包）
python -m venv .venv
.venv/Scripts/python -m pip install -e .          # Linux/macOS: .venv/bin/python

# 2. 配置密钥：复制 .env.example 为 .env，填入：
#    ZHIPU_API_KEY=<你的Key>
#    OPENAI_API_KEY=<同一个智谱Key>              # 生成与嵌入走 OpenAI 兼容端点
#    VECTOR_DB_PROVIDER=qdrant
#    QDRANT_PATH=<向量库数据目录>                 # 本地嵌入式模式

# 3. 语料入库（scripts/load_kb.py，--kb-dir 指向你的 Markdown 文档目录）
.venv/Scripts/python scripts/load_kb.py --kb-dir ./data/kb_docs --reset

# 4. 完整版后端（8000 端口）
.venv/Scripts/python main.py

# 5. 官方前端（另开终端）
cd frontend && npm install && npm run dev          # http://localhost:5000
```

轻量体验（无需前端构建）：`chat.bat`（CLI 问答）· `admin.bat`（管理菜单）· `demo.bat`（轻量 Web 页）。

### Docker 一键部署（推荐演示方式）

```bash
# 1. 配置 .env（同上，另含 FASTAPI_AUTH_KEY）
# 2. 首次构建前：bge 模型不入 git，从本机 HF 缓存拷入构建上下文（Windows）
#    xcopy /E /I "%USERPROFILE%\.cache\huggingface\hub\models--BAAI--bge-reranker-v2-m3\snapshots\*" models\bge-reranker-v2-m3
# 3. 构建并启动（首次约 10-20 分钟）
docker compose -f docker-compose.securekb.yml up -d --build
# 4. 打开 http://localhost:5000（admin/Admin@2026）
```

镜像特性：torch CPU 版（镜像省 ~2.5GB）· bge-reranker-v2-m3 模型直拷内置
（`HF_HUB_OFFLINE=1` 离线加载，运行时不依赖 HF Hub）· 国内源构建（apt 阿里云 /
PyPI 清华 / npm npmmirror）· nginx same-origin 反代（无跨域）。
注意：容器与本机直跑共用 `easy_start/.qdrant_data`（Qdrant 本地模式文件锁），
两者不能同时运行。

### 评测回归

```bash
.venv/Scripts/python scripts/run_retrieval_eval.py --label <配置标签>
# 输出 recall@1/3/5、MRR、检索延迟，逐题明细与报告落盘 evals/reports/
.venv/Scripts/python scripts/test_injection.py     # 注入防护回归（拦截率/误伤率）
```

## 安全工程

- **输入侧注入防护**（`app/security/input_guard.py`）：13 条正向规则覆盖指令覆盖/身份操纵/
  防护解除/提示词窃取/密钥窃取/语料窃取/间接注入；三级处置（放行 / 句级剥离 / 直接拒绝）；
  `scripts/test_injection.py` 持续回归拦截率与误伤率。
- **Security Trimming**：文档 payload 携带部门标签，Dense 与 BM25 两条召回路均按身份过滤
  （混合检索下的常见漏洞：只过滤其中一路导致权限泄露，EXP-007 有完整复盘）。
- **输出侧泄露检测**（`app/security/output_guard.py`）：系统提示词指纹比对、密钥形态、越狱信号。
- **间接注入扫描**：检索结果中的文档若携带针对 AI 的指令特征，剔除后再生成。
- **中国 PII 入口清洗**（`app/security/cn_pii.py`）：手机号/身份证/银行卡/邮箱，
  正则初筛 + 算法校验（GB11643/Luhn）双层规则，入口即掩、全链路只见掩码。
- **审计日志**（`secure_audit_service`）：SQLite append-only，security/query/admin
  三类事件，注入拦截、PII 掩码、越权访问（401/403）全量落库。
- **输出合规规则引擎**（`app/security/output_compliance.py`）：提示词泄漏 block、
  注入回声/密钥形态/PII 片段 mask，分级处置 + 审计。

## 与上游的主要差异

- 智谱 GLM 全链路接入（生成 + 嵌入，OpenAI 兼容端点），schema/校验器适配
- jieba 中文分词替换 kiwipiepy；检索评测器 `run_retrieval_eval.py` 新增
- Security Trimming、注入防护、轻量三件套启动器（chat/admin/demo.bat）
- Windows 开发环境适配（依赖版本、解释器选择、QDRANT_PATH 本地模式）

完整改造历史见 [commits](../../commits/main/)；实验全过程（含失败与复盘）见
[docs/experiments.md](docs/experiments.md)。

## Roadmap

- [x] 文档上传 Web 入口（上传 → 解析 → 打标 → 入库闭环）
- [x] PII 脱敏规则引擎（中国 PII 双层规则）
- [x] 审计日志持久化（SQLite append-only）
- [x] Docker Compose 一键部署（`docker-compose.securekb.yml`）
- [x] 前端简体中文语言包（zhHans，仅中文）
- [x] Agent 模式最小启用（ReAct + 工具白名单 + Security Trimming 六层透传）
- [ ] 反馈按钮（👍/👎）→ bad case 自动进入评测集
- [ ] 忠实度拒答联动评测（distractor / 世界知识拼凑 bad case）

## 致谢

- [OneRAG](https://github.com/notadev-iamaura/OneRAG)（MIT）——本项目的基础框架，
  检索编排、多向量库支持、生成评测（RAGAS）、WebSocket 等核心能力来自上游。
- 依赖的上游组件（LangChain 文本切分、rank-bm25、jieba 等）按各自协议使用。

## License

MIT（继承自上游 OneRAG）
