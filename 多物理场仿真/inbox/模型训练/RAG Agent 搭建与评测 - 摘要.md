---
title: "RAG Agent 搭建与评测 — 摘要"
tags:
  - inbox
  - 模型训练
  - RAG
  - LLM
source: "[[raw/模型训练/6_12/daily_2025-06-12.md]]"
created: 2026-06-12
status: draft
---

# RAG Agent 搭建与评测 — 摘要

> **原始记录**: [[raw/模型训练/6_12/daily_2025-06-12.md]]
> **主 Notebook**: `agent.ipynb`（12 个 Cell）
> **知识库**: `knowledge_base/` 目录（5 篇 AI 主题文章）

---

## 🎯 项目目标

从零搭建一个基于 DeepSeek-V3 的本地 RAG 知识库对话系统，并完成多策略检索评测对比。

---

## 🏗️ 技术栈

| 组件 | 选型 | 说明 |
|:---|:---|:---|
| LLM | DeepSeek-V3 (`deepseek-chat`) | 官方 API，兼容 OpenAI 格式 |
| 嵌入模型 | BAAI/bge-small-zh-v1.5 | 本地免费，中文优化 |
| 重排序 | BAAI/bge-reranker-v2-m3 | CrossEncoder 精排，~2.3GB |
| 向量库 | FAISS | Facebook 开源，本地运行 |
| 关键词检索 | BM25 | 稀疏检索，互补向量搜索 |
| 框架 | LangChain | 串起 LLM + RAG 全链路 |

### 核心文件

| 文件 | 作用 |
|:---|:---|
| `agent.ipynb` | 主 notebook（12 个 Cell） |
| `knowledge_base/` | 本地知识库目录（5 个 .md 文档） |
| `faiss_index/` | FAISS 向量索引（399 个 chunk，持久化） |
| `eval_retrieval_summary.csv` | 检索评测结果 |
| `eval_generation_summary.csv` | 生成质量评测结果 |

---

## 🚀 RAG 检索策略（5 种）

基于知识库中的《RAG 技术从小白到深入理解》手册（7000+ 行），实现了 5 种检索策略：

| 策略 | 管道 | 速度 | 精度 |
|:---|:---|:---|:---|
| BM25 | 关键词稀疏检索 | ⚡ 极快 | ★★ |
| 向量 (FAISS) | 单次稠密语义检索 | ⚡ 快 | ★★ |
| BM25+向量融合 | 双路 RRF 融合 | 🔶 中 | ★★★ |
| Multi-Query+RRF | LLM 改写 3 种问法 + RRF 融合 | 🐢 慢 | ★★★ |
| Multi-Q+RRF+CE | 以上 + CrossEncoder 精排 | 🐢🐢 很慢 | ★★★★ |

### 核心技术点

- **Multi-Query**：一个问题让 LLM 生成 4 种问法，并行检索
- **RRF (Reciprocal Rank Fusion)**：倒数排序融合算法，科学合并多路检索结果
- **CrossEncoder 重排序**：BGE-reranker 对候选文档逐对打分，取 top-4
- **文档预处理**：清洗飞书导出的图片链接残留
- **FAISS 持久化**：本地存读，二次启动秒加载
- **结构化 Prompt**：系统提示 + 对话历史 + 知识库上下文 + 引用规范

---

## 📊 评测体系

### 检索评测（Cell 8-9）

**5 种策略 × 8 道自动生成测试题 × 4 项指标**：

| 指标 | 含义 | 公式 |
|:---|:---|:---|
| Recall@4 | 相关文档被检出的比例 | 命中文档数 / 总相关文档数 |
| Precision@4 | 检出的文档中相关的比例 | 命中文档数 / 4 |
| MRR | 首个相关文档排名的倒数 | 1 / 第一个命中排名 |
| NDCG@4 | 考虑排序位置的增益 | DCG / IDCG |

### 生成评测（Cell 10）

**LLM-as-Judge** 打分（0-10 → 归一化到 0-1），4 个维度：

| 指标 | 含义 |
|:---|:---|
| Faithfulness | 忠实度：答案是否严格基于知识库，有无编造 |
| Relevance | 相关性：是否切题 |
| Correctness | 正确性：内容是否准确 |
| Completeness | 完整性：是否覆盖关键信息点 |

### 实际跑出的结果

**检索方面**：
- **最高召回**：Multi-Q+RRF+CE / Multi-Query+RRF / 向量(FAISS) — Recall@4 = 0.5
- **最快速**：向量(FAISS) — 22.5ms

**生成质量方面**：
- **最佳综合**：hybrid (Multi-Q+RRF) — Overall = 0.909
- 向量(基础) — Overall = 0.903
- Multi-Q+RRF+CE — Overall = 0.881

> 🔍 **反直觉发现**：CrossEncoder 重排虽然检索更精准，但生成质量反而略低于不带 CE 的 hybrid。代价是额外 5 秒延迟。检索精度不是越高越好——太"对"的文档反而可能丢失上下文多样性。

### 可视化（Cell 12）

两张三合一对比图：

![[多物理场仿真/raw/模型训练/6_12/eval_retrieval_chart.png]]

*检索指标对比 + 耗时对比 + 综合得分*

![[多物理场仿真/raw/模型训练/6_12/eval_generation_chart.png]]

*LLM-as-Judge 四维打分：Faithfulness / Relevance / Correctness / Completeness*

---

## 🔧 踩坑记录（6 个）

| # | 问题 | 原因 | 修复 |
|:---|:---|:---|:---|
| 1 | `ModuleNotFoundError: langchain.text_splitter` | LangChain 新版拆分了子包 | 改用 `langchain_text_splitters` |
| 2 | `ModuleNotFoundError: langchain.chains` | 同上 | 手动实现 RAG 链路 |
| 3 | `HuggingFaceEmbeddings` deprecated | langchain-community 弃用 | 改用 `langchain-huggingface` |
| 4 | huggingface.co 连接超时 | 国内网络限制 | 设置 `HF_ENDPOINT=https://hf-mirror.com` |
| 5 | BM25 全返回 0 | API 变更：`get_relevant_documents` → `invoke` | 全局替换为新 API |
| 6 | pandas KeyError `耗时(ms)` | 列名写错（括号 vs 无括号） | 统一为 `耗时ms` |

---

## 📝 后续方向

- [ ] 补全 Cell 4-14 的新手教学（今天讲完了 Cell 0-3）
- [ ] BM25 修复后需重新跑 Cell 8-9 验证
- [ ] 可扩展：Milvus / Chroma 向量库替代 FAISS
- [ ] 可扩展：Agent 工具调用（搜索、计算、代码执行）
- [ ] API Key 后续改环境变量，避免硬编码

## 📁 后续动作

- [ ] 精炼为 wiki 页面：[[wiki/模型训练/RAG 检索策略对比]]
- [ ] 关联 [[wiki/模型训练/损失函数设计]] — 评测指标的选择同样影响结论
