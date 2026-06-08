---
title: "{% tp.system.prompt('摘要标题（默认用源文件名）', '') %} — 摘要"
tags:
  - inbox
  - "{% tp.system.suggester(['电磁仿真', '热仿真', '光仿真', 'EDA', '软件操作', '小白问答'], ['电磁仿真', '热仿真', '光仿真', 'EDA', '软件操作', '小白问答']) %}"
source: "{% tp.system.prompt('链接到 raw 源文件（如 [[raw/EDA/xxx.pdf]]）', '') %}"
created: "{% tp.date.now('YYYY-MM-DD') %}"
status: draft
---

# {% tp.frontmatter.title %}

> **原始资料**: {% tp.frontmatter.source %}
> **状态**: `draft` → 待审阅 → `reviewed` → 可升级到 wiki

## 📌 核心内容

<!-- AI 提取的 3-5 个要点 -->

## 🔑 关键数据 / 公式 / 结论

<!-- 数字、公式、对比表 -->

## ❓ 待理解的问题

- [ ] 
- [ ] 

## 📁 建议 wiki 去向

- [ ] [[wiki/{领域}/{页面名}]]
