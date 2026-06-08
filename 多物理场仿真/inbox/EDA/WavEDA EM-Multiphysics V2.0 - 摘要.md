---
title: 每日蒸馏 — 自动知识处理
tags:
  - inbox
  - EDA
  - 多物理场仿真
  - 软件
  - 知识库/自动化
  - 每日蒸馏
source: "[[raw/EDA/WavEDA EM-Multiphysics-V2.0_20260319.pdf]]"
created: 2026-06-08
status: draft
---

# 📋 每日蒸馏

> Codex 自动化任务每天 20:00 执行知识库流水线，输出存于此处。

## 工作机制

```
raw/ 新资料 ──→ inbox/ 摘要 ──→ 提示你审阅 ──→ wiki/ 精炼
   🤖 自动         🤖 自动         👤 你来         👤 你来
```

每天 20:00，Codex 自动执行以下动作：

1. **扫描** `raw/` 中上次处理之后新增的文件
2. **读取并消化**，生成 inbox 摘要，存入对应 `inbox/` 子目录
3. **回顾** inbox 中积压的待处理条目，标记哪些可以升级到 wiki
4. **生成日报** → `每日蒸馏/{YYYY-MM-DD}.md`，汇总今日处理内容 + 给你的审阅建议

## 文件命名

```
每日蒸馏/2026-06-08.md
每日蒸馏/2026-06-09.md
...
```

## 自动化 Prompt

完整的 Codex 自动化 prompt 见 [[../../templates/codex-每日蒸馏-prompt|codex-每日蒸馏-prompt]]
# Codex 介绍

> 更新日期：2026-06-08

## 一句话理解

Codex 是 OpenAI 面向软件开发场景的编码代理。它可以理解项目上下文，阅读和修改代码，运行本地命令，检查差异，协助测试、调试、重构、代码审查和交付。

和普通聊天式 AI 不同，Codex 更像一个可以进入仓库工作的开发搭档：它会先阅读文件，再按任务修改代码，并通过命令行、测试、浏览器或其他工具验证结果。

## Codex 能做什么

- 理解代码库结构，解释项目、模块、函数和数据流。
- 编写新功能，修复 bug，补测试，更新文档。
- 运行项目命令，例如安装依赖、启动开发服务、执行测试或格式化。
- 做代码审查，指出风险、回归点和缺失测试。
- 管理 Git 工作流，例如查看 diff、暂存、提交、推送或协助创建 PR。
- 通过截图、浏览器、文件、终端输出等上下文完成更接近真实开发的任务。

## 常见使用入口

### Codex App

Codex App 是桌面端的工作中心，适合同时管理多个项目线程。它支持 worktree、自动化、Git 操作、内置浏览器、文件预览和线程式协作，适合较长周期的任务、并行开发和交互式代码审查。

### Codex CLI

Codex CLI 是终端里的 Codex。它可以在当前目录读取、修改和运行代码，适合习惯命令行的开发者，也适合在已有终端工作流中快速发起任务。

常见启动方式：

```bash
codex
```

### IDE Extension

IDE 扩展把 Codex 放进编辑器上下文里，适合围绕当前文件、选区、诊断信息或编辑器工作区进行更贴近写代码过程的协作。

### Web 和云端任务

Web 或云端入口适合把任务交给远程环境执行，例如并行处理多个 issue、让 Codex 在隔离环境里产出变更，再回到本地查看和合并。

## 它适合哪些任务

- 快速熟悉陌生仓库：让 Codex 总结项目结构、启动方式、关键模块和潜在风险。
- 小到中等规模改动：修 bug、补边界条件、添加页面、调整接口、更新文档。
- 测试与验证：补单元测试、跑测试、定位失败原因、解释日志。
- 代码审查：在提交前请 Codex 检查行为回归、遗漏场景和安全问题。
- 重复性维护：批量更新依赖用法、统一配置、迁移接口、生成说明文档。

## 和普通 AI 编程助手的区别

普通 AI 编程助手常常停留在“给建议”或“生成片段”。Codex 的重点是“在仓库里完成任务”：它能读取真实文件、编辑文件、运行命令、观察结果，并根据反馈继续迭代。

这意味着给 Codex 的任务可以更像给同事分配工作：

```text
请修复登录页表单校验的问题，补测试，并告诉我验证结果。
```

而不是只问：

```text
怎么写一个登录页表单校验？
```

## 权限与安全边界

Codex 的本地执行能力通常受沙盒和审批策略控制。沙盒决定 Codex 能访问和修改哪些文件、能否联网、能运行哪些命令；审批策略决定它什么时候需要停下来向用户确认。

常见权限模式包括：

- `read-only`：只读项目，不能直接修改文件或运行高影响命令。
- `workspace-write`：可在当前工作区内读写文件，并运行常规本地命令。
- `danger-full-access`：无沙盒限制，适合完全信任当前任务和环境时使用。

日常开发更推荐从较保守的权限开始，让 Codex 在需要越界操作时请求确认。

## 使用建议

- 任务要具体：说明目标、约束、期望输出和验证方式。
- 让它先看代码：复杂任务可以先让 Codex 总结实现方案，再动手修改。
- 要求验证：例如“跑相关测试”“启动页面检查”“说明未能验证的原因”。
- 小步提交：把大任务拆成可审查的小变更，降低回归风险。
- 保留人工判断：涉及生产数据、密钥、权限、删除操作和大规模迁移时，仍要认真审查。

## 示例提示词

```text
帮我阅读这个仓库，说明它的技术栈、入口文件、启动方式和主要模块。
```

```text
修复这个测试失败，尽量做最小改动，并解释根因。
```

```text
请 review 当前 diff，优先找 bug、回归风险和缺失测试。
```

```text
给这个项目新增一篇 README，包含安装、运行、测试和目录结构说明。
```

## 参考资料

- [Codex 官方文档](https://developers.openai.com/codex)
- [Codex App](https://developers.openai.com/codex/app)
- [Codex CLI](https://developers.openai.com/codex/cli)
- [Codex 沙盒机制](https://developers.openai.com/codex/concepts/sandboxing)
- [Codex 使用场景](https://developers.openai.com/codex/use-cases)

# WavEDA EM-Multiphysics V2.0 — 摘要

> **原始资料**: [[raw/EDA/WavEDA EM-Multiphysics-V2.0_20260319.pdf]]
> **官网**: <http://www.waveda.com> | **支持**: tech_support@waveda.com

## 📌 核心内容

WavEDA（芯和半导体）是国内 EDA 厂商，主打**多物理场仿真平台**。V2.0 版本覆盖电磁、热、力学、多物理场耦合，以及版图级 Layout 和电路仿真。

产品线：**EM → Thermal → Mechanics → Multi-Physics → Layout → Circuit**

时间线：2022 起步 → 2023~2025 快速迭代 → 2026 V2.0 发布，称为"2035 愿景"的一部分。

## 🔑 核心模块与关键数据

### ⚡ WavEDA EM（电磁仿真）

- **频段覆盖**：DC → MHz → GHz → THz（从静电到光学全覆盖）
- **EC（电子冷却/低频）**：IR Drop 分析，3D + 1D-3D 混合
- **RF（射频/微波）**：MFEM-fast / MFETD-fast 求解器，支持 S/Y/Z 参数、TDR/TDT、2D/3D 场图，单模型 100+ 端口
- **Optics（光学）**：FEM 求解器，支持 Debye/Drude/Lorentz/Sellmeier 色散模型，误差 <1%，**速度 2.3 倍**

| 基准测试 | 网格量 | WavEDA 用时 | 对比软件用时 | 加速比 | 误差 |
|:---|:---|:---|:---|:---|:---|
| IC-SI | 1280 万 | 1.24 h | 4.44 h (Ref-A) | **3.55×** | — |
| RCS | 2.5 万 | 2.18 h | 40.49 h (Ref-A) | **18.57×** | — |
| 多层陶瓷 | 312 万 | 1.41 h | 7.18 h (Ref-A) | **4.79×** | 1.43% |
| 超表面光学 @155THz | 5.7 万 | 4 min 59 s | 4 h 20 min (Ref-C) | **51.24×** | 1.93% |
| 光学 @680THz 斜入射 | — | 33 min 17 s | 5 min 29 s (Ref-C) | — | 0.23% |

> Ref-A 推测为 HFSS，Ref-C 推测为 COMSOL，Ref-B 推测为 ANSYS/Lumerical

### 🔥 WavEDA Thermal（热仿真）

- PCB 级 + 3D 级热分析
- 1D 简化模型选项（速度优先）

| 基准测试 | WavEDA | Ref-C | 加速比 | 误差 |
|:---|:---|:---|:---|:---|
| IGBT 阵列热仿真 | 59 s | 1 min 34 s | **1.59×** | 1.25% |
| Flip chip Rjb | 4 s | 1 min 22 s | **20.5×** | 0.18% |

### 🔧 WavEDA Mechanics（力学仿真）

- FEM + **DDM（区域分解法）** 混合求解
- 覆盖：静力分析 / 瞬态动力学 / 频域分析
- **声波测井（Sonic log）** 场景有专项优化
- 支持 TET 和 HEX 网格，DDM 大幅降低内存占用（HEX-DDM 某例：96,819 vs 375 万自由度）

| 基准测试 | WavEDA | Ref-C | 加速比 | 误差 |
|:---|:---|:---|:---|:---|
| MEMS 力学 | 19 s | 1 min 4 s | **3.37×** | 1.47e-6 |
| JEDEC 热-力耦合 | 2 h 2 min | 7 h 26 min | **3.65×** | 0.2% |
| 声波测井 DDM | 1 h 7 min | 12 h 38 min | **11.3×** | 3.8% / 12% |

### 🔗 WavEDA Multi-Physics（多物理场耦合）

- 原生耦合：EM + Thermal + Mechanics
- 不自称"最全"，但强调"一体化求解"

| 基准测试 | WavEDA | Ref-C (分步耦合) | 加速比 | 误差 |
|:---|:---|:---|:---|:---|
| PCB 电-热耦合 | 1 min 27 s | 10 min 59 s | **6.57×** | — |
| Flip chip EM-TH-Mech | 24 min 55 s | 1 h 2 min 56 s | **1.53×** | — |
| Wire bonding 耦合 | 3 min 41 s | 14 min 48 s | **3.02×** | 0.52%~11% |

### 📐 WavEDA Layout（版图环境）

- 支持格式：ODB++ / DXF / GDSII / Gerber
- 自动识别：Pad / Via / Solder ball / bump / Wirebonds / Footprint / RLC Component
- 网格方式：Object / Net / Box / Conformal / Convex hull
- 3D 模型自动生成 + 端口自动创建
- 针对 Chiplet、2.5D/3D IC、RFIC、PCB 场景

### 🔌 WavEDA Circuit（电路仿真）

- 文中提及但细节较少，从上下文看支持与 Layout + EM 的联合仿真流程

## ❓ 待理解的问题

- [ ] MFEM-fast 和 MFETD-fast 的"fast"具体指什么技术？（自适应阶数？p- refinement？）
- [ ] DDM（区域分解法）在电磁模块中是否也有使用？目前只看力学提到了 DDM
- [ ] 对比的 Ref-A / Ref-B / Ref-C 具体是哪个软件的哪个版本？（A 大概率 HFSS，C 大概率 COMSOL）
- [ ] Circuit 模块是否支持 SPICE 级别的瞬态仿真，还是仅 S 参数链路分析？
- [ ] License 模式和价格？学术版/企业版？
- [ ] 国内市场占有率如何？和芯愿景/华大九天/国微集团的竞争关系？

## 📁 后续动作

- [ ] 精炼为 wiki 页面：[[wiki/软件操作/WavEDA/WavEDA 索引]]
- [ ] 更新 [[wiki/软件操作/索引]]，把 WavEDA 加入软件清单
- [ ] 提取性能对比表 → [[wiki/EDA/国产EDA工具对比]]（如果有的话）
- [ ] 关联已有页面：[[wiki/EDA/索引]]、[[wiki/电磁仿真/有限元法 FEM]]、[[wiki/热仿真/热传导基础]]
- [ ] DDM 和 MFEM-fast 如果深入，可以写专门的 wiki 页
