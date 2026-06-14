---
title: RAG 检索策略对比
tags:
  - 模型训练
  - RAG
  - LLM
  - 核心概念
aliases:
  - RAG Retrieval Strategies
  - RAG 评测
created: 2026-06-12
updated: 2026-06-14
status: growing
---

# RAG 检索策略对比

> 基于 DeepSeek-V3 + BGE + FAISS 的本地 RAG 系统，5 种检索策略 × 8 道测试题 × 4 项指标对比。核心发现：**CrossEncoder 精排反而降低生成质量。**

---

## 🏗️ 系统架构

```
用户问题
  → 多路检索（BM25 / FAISS / Multi-Query）
  → RRF 融合
  → （可选）CrossEncoder 精排
  → Prompt 组装（系统提示 + 上下文 + 引用规范）
  → DeepSeek-V3 生成回答
```

### 技术栈

| 组件 | 选型 | 说明 |
|:---|:---|:---|
| LLM | DeepSeek-V3 | 兼容 OpenAI 格式 |
| 嵌入 | BAAI/bge-small-zh-v1.5 | 本地免费，中文优化 |
| 重排序 | BAAI/bge-reranker-v2-m3 | CrossEncoder，~2.3GB |
| 向量库 | FAISS | 本地，毫秒级检索 |
| 关键词 | BM25 | 稀疏检索，互补向量 |
| 框架 | LangChain | 全链路串联 |

---

## 🚀 五种检索策略

| 策略 | 管道 | 速度 | 精度 |
|:---|:---|:---|:---|
| BM25 | 关键词稀疏检索 | ⚡ 极快 | ★★ |
| 向量 (FAISS) | 单次稠密语义检索 | ⚡ 快 (22.5ms) | ★★ |
| BM25+向量融合 | 双路 RRF 融合 | 🔶 中 | ★★★ |
| Multi-Query+RRF | LLM 改写 3 种问法 + RRF | 🐢 慢 | ★★★ |
| Multi-Q+RRF+CE | 以上 + CrossEncoder 精排 | 🐢🐢 很慢 (+5s) | ★★★★ |

### RRF 融合

Reciprocal Rank Fusion——倒数排序融合算法。多路检索各自产出排序列表后，按 $\frac{1}{k + \text{rank}}$ 加权合并。$k=60$ 是常用平滑参数。

### Multi-Query

一个问题让 LLM 并行生成 4 种不同问法，每路独立向量检索，RRF 合并。利用 LLM 的语言能力弥补关键词和向量检索各自的盲区。

---

## 📊 评测体系

### 检索维度（4 项指标）

| 指标 | 含义 |
|:---|:---|
| Recall@4 | 相关文档被检出的比例 |
| Precision@4 | 检出文档中相关的比例 |
| MRR | 首个相关文档排名的倒数 |
| NDCG@4 | 考虑排序位置的增益 |

### 生成维度（LLM-as-Judge，4 维）

| 指标 | 含义 |
|:---|:---|
| Faithfulness | 是否严格基于知识库，有无编造 |
| Relevance | 是否切题 |
| Correctness | 内容是否准确 |
| Completeness | 是否覆盖关键信息点 |

### 计算结果

**检索**：Multi-Q+RRF+CE / Multi-Query+RRF / 向量(FAISS) 三者 Recall@4 均为 0.5。最快是向量(FAISS) — 22.5ms。

**生成质量**：

| 策略 | Overall |
|:---|:---|
| Multi-Query+RRF | **0.909 🏆** |
| 向量 (基础) | 0.903 |
| Multi-Q+RRF+CE | 0.881 |

![[多物理场仿真/raw/模型训练/6_12/eval_retrieval_chart.png]]

*检索指标对比 + 耗时对比 + 综合得分*

![[多物理场仿真/raw/模型训练/6_12/eval_generation_chart.png]]

*LLM-as-Judge 四维打分：Faithfulness / Relevance / Correctness / Completeness*

---

## 🔍 反直觉发现：CE 为什么反而降低生成质量

CrossEncoder 精排后 Recall 最高（0.5），但生成质量反而最低（0.881）。

**推测原因**：CE 过度聚焦"最相关"的文档，导致喂给 LLM 的上下文多样性降低。太"对"的检索结果可能丢失了不同角度的补充信息——检索精度和生成质量不是线性关系。

**代价**：额外 5 秒延迟 + 更差的生成效果。在这个场景下 CE 不值得。

---

## 🔧 踩坑记录

| # | 问题 | 原因 | 修复 |
|:---|:---|:---|:---|
| 1 | `ModuleNotFoundError: langchain.text_splitter` | LangChain 新版拆分子包 | 改用 `langchain_text_splitters` |
| 2 | `ModuleNotFoundError: langchain.chains` | 同上 | 手动实现 RAG 链路 |
| 3 | `HuggingFaceEmbeddings` deprecated | langchain-community 弃用 | 改用 `langchain-huggingface` |
| 4 | huggingface.co 超时 | 国内墙 | `HF_ENDPOINT=https://hf-mirror.com` |
| 5 | BM25 全返回 0 | API 改名：`get_relevant_documents` → `invoke` | 全局替换 |
| 6 | pandas KeyError | 列名括号不一致 | 统一为 `耗时ms` |

---

## 🔗 相关页面

- [[损失函数设计]] — 评测指标的选择同样影响结论，类比 Loss 选择
- [[代理模型与降阶模型]] — 检索策略的"精度不是越高越好"和代理模型的"MAE 低不代表物理对"是同一个哲学

## 📚 原始资料

- [[raw/模型训练/6_12/daily_2025-06-12.md|实验日志]]
- [[raw/模型训练/6_12/eval_retrieval_summary.csv|检索评测数据]]
- [[raw/模型训练/6_12/eval_generation_summary.csv|生成评测数据]]
