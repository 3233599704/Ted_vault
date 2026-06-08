---
title: "WavEDA EM-Multiphysics V2.0 — 摘要"
tags:
  - inbox
  - EDA
  - 多物理场仿真
  - 软件
source: "[[raw/EDA/WavEDA EM-Multiphysics-V2.0_20260319.pdf]]"
created: 2026-06-08
status: draft
---

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
- 支持 TET 和 HEX 网格，DDM 大幅降低内存占用

| 基准测试 | WavEDA | Ref-C | 加速比 | 误差 |
|:---|:---|:---|:---|:---|
| MEMS 力学 | 19 s | 1 min 4 s | **3.37×** | 1.47e-6 |
| JEDEC 热-力耦合 | 2 h 2 min | 7 h 26 min | **3.65×** | 0.2% |
| 声波测井 DDM | 1 h 7 min | 12 h 38 min | **11.3×** | 3.8% / 12% |

### 🔗 WavEDA Multi-Physics（多物理场耦合）

- 原生耦合：EM + Thermal + Mechanics

| 基准测试 | WavEDA | Ref-C (分步耦合) | 加速比 | 误差 |
|:---|:---|:---|:---|:---|
| PCB 电-热耦合 | 1 min 27 s | 10 min 59 s | **6.57×** | — |
| Flip chip EM-TH-Mech | 24 min 55 s | 1 h 2 min 56 s | **1.53×** | — |
| Wire bonding 耦合 | 3 min 41 s | 14 min 48 s | **3.02×** | 0.52%~11% |

### 📐 WavEDA Layout（版图环境）

- 支持格式：ODB++ / DXF / GDSII / Gerber
- 自动识别：Pad / Via / Solder ball / bump / Wirebonds / Footprint / RLC Component
- 3D 模型自动生成 + 端口自动创建
- 针对 Chiplet、2.5D/3D IC、RFIC、PCB 场景

### 🔌 WavEDA Circuit（电路仿真）

- 文中提及但细节较少，支持与 Layout + EM 的联合仿真流程

## ❓ 待理解的问题

- [ ] MFEM-fast 和 MFETD-fast 的"fast"具体指什么技术？
- [ ] DDM 在电磁模块中是否也有使用？
- [ ] Ref-A / Ref-B / Ref-C 具体是哪个软件的哪个版本？
- [ ] Circuit 模块是否支持 SPICE 级别的瞬态仿真？
- [ ] License 模式和价格？学术版/企业版？
- [ ] 国内市场占有率如何？

## 📁 后续动作

- [ ] 精炼为 wiki 页面：[[wiki/软件操作/WavEDA/WavEDA 索引]]
- [ ] 更新 [[wiki/软件操作/索引]]，把 WavEDA 加入软件清单
- [ ] 关联已有页面：[[wiki/EDA/索引]]、[[wiki/电磁仿真/有限元法 FEM]]、[[wiki/热仿真/热传导基础]]
- [ ] DDM 和 MFEM-fast 可以写专门的 wiki 页
