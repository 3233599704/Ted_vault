---
title: Lumerical 索引
tags:
  - 软件操作
  - Lumerical
  - MOC
created: 2026-06-08
updated: 2026-06-08
---

# Lumerical

> 光子学仿真的行业标准——FDTD 求解器的王者，光芯片/光通信必学。

## 🎯 Lumerical 产品线

| 产品 | 核心方法 | 典型场景 |
|:---|:---|:---|
| **FDTD** | 时域有限差分 | 微纳光学、超表面、光子晶体 |
| **MODE** | 模式求解 + 变分 FDTD | 波导设计、光纤模式 |
| **DEVICE** | 电荷输运 + 热仿真 | 光电器件（激光器/探测器） |
| **INTERCONNECT** | 电路级光子仿真 | 光通信链路、光子集成电路 |

## 📐 为什么 FDTD 适合光仿真

- FDTD 天然处理**宽带**（一次仿真得到全频谱）
- 光的波长量级网格容易控制
- 时域脉冲激发 → Fourier 变换 → 全频谱响应

## 🛠️ 典型工作流

1. **FDTD** 仿真单个光子器件（波导弯曲、光栅、微环……）
2. 提取 S 参数
3. **INTERCONNECT** 用 S 参数矩阵做链路级仿真
4. **DEVICE** 仿真有源部分（激光增益、光电转换）

## ⚠️ Lumerical 常见坑

- **PML 反射**：PML 的层数和参数对精度影响很大
- **网格色散**：网格太粗时波的传播速度会偏离实际
- **FDTD 的 Courant 条件**：时间步长受网格尺寸约束——网格越细，时间步长越小，仿真越慢
- **周期性结构的斜入射**：Bloch 边界需要 BFA 技术
- **从 FDTD 到 INTERCONNECT 的 S 参数提取**：必须做好端口定义

## 📝 推荐学习顺序

1. Lumerical FDTD：一个简单的 Mie 散射案例
2. Lumerical MODE：波导模式求解
3. FDTD + MODE 联合：波导光栅耦合器
4. INTERCONNECT：光链路
