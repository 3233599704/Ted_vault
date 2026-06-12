---
title: "6_12 RAG Agent 搭建与评测 — 摘要"
tags:
  - inbox
  - AI
  - RAG
  - Agent
source: "[[raw/模型训练/6_12/daily_2025-06-12]]"
created: "2026-06-12"
status: draft
---

# 6_12 RAG Agent 搭建与评测 — 摘要

> **原始资料**: [[raw/模型训练/6_12/daily_2025-06-12|daily_2025-06-12]]

## 📌 核心内容

- 从零搭建了基于 **DeepSeek-V4 Pro** 的本地 RAG 知识库对话 Agent，完整覆盖嵌入→检索→重排→生成全链路
- 实现了 **5 种检索策略**：BM25、向量(FAISS)、BM25+向量融合(RRF)、Multi-Query+RRF、Multi-Q+RRF+CrossEncoder
- 建立了**双轨评测体系**：检索评测（Recall/Precision/MRR/NDCG）× 生成评测（LLM-as-Judge 四维打分）
- 完整记录了 **6 个踩坑修复**（LangChain 包拆分、huggingface 网络、BM25 API 变更等）
- **最佳综合生成质量**：hybrid (Multi-Q+RRF)，Overall = **0.909**（4 项指标加权）

## 🔑 关键公式 / 结论 / 数据

### 技术栈

| 组件    | 选型                                    |
| :---- | :------------------------------------ |
| LLM   | DeepSeek-V4 pro (`deepseek-chat`)     |
| 嵌入模型  | BAAI/bge-small-zh-v1.5（本地）            |
| 重排序   | BAAI/bge-reranker-v2-m3（CrossEncoder） |
| 向量库   | FAISS（399 chunk，持久化）                  |
| 关键词检索 | BM25                                  |
| 框架    | LangChain                             |

### 检索评测结果（5 策略 × 8 测试题）

| 策略 | Recall@4 | Precision@4 | MRR | NDCG@4 | 耗时 |
|:---|:---|:---|:---|:---|:---|
| Multi-Q+RRF+CE | 0.5 | 0.125 | 0.5 | 0.5 | 5231ms |
| Multi-Query+RRF | 0.5 | 0.125 | 0.406 | 0.429 | 1644ms |
| 向量(FAISS) | 0.5 | 0.125 | 0.438 | 0.533 | **22.5ms** |
| BM25 | 0.0 | 0.0 | 0.0 | 0.0 | 0ms |
| BM25+向量融合 | 0.0 | 0.0 | 0.0 | 0.0 | 0ms |

> ⚠️ BM25 两策略因 API 变更导致结果异常，待修复后重跑

### 生成质量评测（LLM-as-Judge, 0-1）

| 策略 | Faithfulness | Relevance | Correctness | Completeness | Overall |
|:---|:---|:---|:---|:---|:---|
| **hybrid** | **0.913** | **0.962** | 0.913 | 0.838 | **0.909** |
| simple | 0.912 | 0.925 | 0.925 | 0.85 | 0.903 |
| multi_query | 0.875 | 0.962 | 0.888 | 0.8 | 0.881 |

> 有趣发现：CrossEncoder 重排虽然检索更精准，但生成质量反而略低于不带 CE 的 hybrid。代价是额外 ~3.6s 延迟。

### 核心算法
- **RRF (Reciprocal Rank Fusion)**：倒数排序融合，`score(d) = Σ 1/(k + rank_i(d))`，k=60
- **Multi-Query**：LLM 生成 4 种问法，并行检索后 RRF 融合
- **LLM-as-Judge**：用 LLM 对 Faithfulness/Relevance/Correctness/Completeness 打分

## ❓ 待理解的问题

- BM25 两策略全返回 0，是否确实是 API 变更导致？修复后能否恢复到预期精度？
- 为什么 CrossEncoder 重排后生成质量反而下降？是否因为候选集从 8 缩到 4 损失了信息多样性？
- Multi-Query 的多问法生成可以在 prompt 中引导更多样化吗？（当前 4 种问法可能语义相近）
- FAISS 向量检索 22.5ms 的单路检索就达到最高 NDCG (0.533)，是否说明当前知识库规模下简单管道就已足够？

## 📁 建议 wiki 去向

- [ ] [[wiki/AI与工具/RAG 检索策略对比]]
- [ ] [[wiki/AI与工具/LangChain 踩坑记录]]
- [ ] [[wiki/AI与工具/LLM-as-Judge 评测方法]]
