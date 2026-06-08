---
title: HFSS 索引
tags:
  - 软件操作
  - HFSS
  - MOC
created: 2026-06-08
updated: 2026-06-08
---

# Ansys HFSS

> 3D 全波电磁仿真的"黄金标准"——天线和 RF 微波设计的首选工具。

## 🎯 HFSS 的定位

- **核心方法**：频域有限元法（FEM）
- **最擅长**：窄带/共振结构、天线、微波无源器件
- **行业地位**：天线工程师的必备工具，S 参数精度业界公认最高

## 📐 HFSS 求解器类型

| 求解器 | 做什么 | 什么时候用 |
|:---|:---|:---|
| **Modal** | S 参数模式求解 | 波导、传输线馈电 |
| **Terminal** | S 参数终端求解 | 微带、集总端口 |
| **Transient** | 时域求解（类似 CST） | 宽带、TDR |
| **Eigenmode** | 本征模 | 谐振腔 Q 值 |
| **SBR+** | 弹跳射线法 | 电大尺寸（雷达/RCS） |
| **HFSS-IE** | 矩量法 | 开放域、线天线 |

## 🛠️ HFSS 典型工作流

1. **3D 建模**（HFSS 自带 / 导入）
2. **设材料**（从库中加载或手动）
3. **设激励**（Wave Port / Lumped Port）
4. **设边界条件**（Radiation / PEC / PML）
5. **设求解参数**（频率范围 / 收敛标准）
6. **网格自动生成**（HFSS 的 Adaptive Meshing 是招牌）
7. **求解 + 后处理**（S 参数 / 场图 / 方向图）

## ⚠️ HFSS 常见坑

- **Air Box（辐射边界）太小**：辐射边界离辐射体至少要 λ/4
- **Adaptive Meshing 卡住**：Delta S 一直抖不收敛 → 手动细化关键区域
- **宽带扫描太慢**：宽带用 Interpolating Sweep，别用 Discrete
- **端口尺寸不对**：太大的波端口可能高次模激发，太小的能量截断不准
- **过约束**：边界条件之间可能冲突（比如 PEC 和 Radiation 在同一处）

## 📝 推荐学习顺序

1. 一个简单的矩形贴片天线 → 理解完整流程
2. 理解 Adaptive Meshing 和 Convergence
3. 学参数化建模 + 优化
4. 学天线阵列（Array Factor / 显式阵列）
5. 学 HFSS-IE 做电大结构
