---
title: "RAG Agent 搭建与评测 — 摘要"
tags:
  - inbox
  - 模型训练
  - RAG
source: "[[raw/模型训练/6_12/daily_2025-06-12.md]]"
created: 2026-06-12
status: draft
---

# RAG Agent 搭建与评测 — 摘要

> **原始记录**: [[raw/模型训练/6_12/daily_2025-06-12.md]]

## 💭 做了什么

从零搭了一个本地 RAG 知识库对话系统。DeepSeek-V3 做 LLM，BGE 做嵌入和重排，FAISS 做向量库，LangChain 做框架串联。吃了 5 篇 AI 知识文章后测试了 5 种检索策略。

## 🔑 技术栈

| 组件 | 选型 | 为什么 |
|:---|:---|:---|
| LLM | DeepSeek-V3 | OpenAI 兼容，便宜 |
| 嵌入 | BAAI/bge-small-zh-v1.5 | 本地免费，中文优化 |
| 重排序 | BAAI/bge-reranker-v2-m3 | CrossEncoder 精排 |
| 向量库 | FAISS | 本地，毫秒级 |
| 关键词 | BM25 | 稀疏检索，互补向量 |

## 📊 5 种检索策略对比

| 策略 | 速度 | Recall@4 | 生成质量 | 亮点 |
|:---|:---|:---|:---|:---|
| BM25 关键词 | ⚡ 极快 | — | — | 互补向量检索 |
| 向量 FAISS | ⚡ 22.5ms | 0.5 | 0.903 | 最快+最高召回 |
| BM25+向量融合 | 🔶 中 | — | — | RRF 双路 |
| Multi-Query+RRF | 🐢 慢 | 0.5 | **0.909 最佳** | LLM 改写问题 |
| Multi-Q+RRF+CE | 🐢🐢 很慢 | 0.5 | 0.881 | 精排反而稍差 |

**有意思的发现**：CrossEncoder 重排虽然检索更精准（Recall 更高），但生成质量反而略低于不带 CE 的 hybrid。精度不是越高越好——检索到的文档太"对"的时候反而可能丢失上下文多样性。

## 🔧 踩了 6 个坑

全是 LangChain 版本兼容问题——新版拆包、API 改名、huggingface 被墙等等。每个都记了原因和修复方案。

## 📁 后续动作

- [ ] 精炼为 wiki 页面：[[wiki/模型训练/RAG 检索策略对比]]
- [ ] 关联 [[wiki/模型训练/损失函数设计]] — RAG 评测和 Loss 评测底层逻辑一样：指标选对才重要
