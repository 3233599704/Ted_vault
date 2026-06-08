---
title: FDTD 方法
tags:
  - 光仿真
  - 电磁仿真
  - 数值方法
  - 核心概念
aliases:
  - FDTD
  - 时域有限差分
created: 2026-06-08
updated: 2026-06-08
---

# 时域有限差分法（FDTD）

> 在空间和时间上同时离散化 Maxwell 方程——一次仿真拿到整个频谱。Lumerical 的灵魂。

## 📖 是什么

FDTD（Finite-Difference Time-Domain）直接在时域中求解 Maxwell 方程。核心是 Yee 网格——电场和磁场在空间上交错排列，在时间上交替更新（蛙跳法 Leapfrog）。

## 📐 核心思想

### Yee 网格

- E 分量在网格棱边中心
- H 分量在网格面中心
- 天然满足 $\nabla \cdot \mathbf{B} = 0$ 和 $\nabla \times$ 的几何关系

### 时间步进

$$
\mathbf{H}^{n+1/2} = \mathbf{H}^{n-1/2} - \frac{\Delta t}{\mu} \nabla \times \mathbf{E}^n
$$

$$
\mathbf{E}^{n+1} = \mathbf{E}^n + \frac{\Delta t}{\varepsilon} \nabla \times \mathbf{H}^{n+1/2}
$$

### Courant 条件（稳定性）

$$
\Delta t \leq \frac{1}{c\sqrt{\frac{1}{\Delta x^2} + \frac{1}{\Delta y^2} + \frac{1}{\Delta z^2}}}
$$

> 网格越细 → $\Delta t$ 越小 → 仿真时间更长。这是 FDTD 的根本权衡。

## 🧠 FDTD vs FEM（频域）对比

| | FDTD（时域） | FEM（频域） |
|:---|:---|:---|
| 一次仿真 | 整个频段 | 一个频点 |
| 窄带高 Q | ❌ 慢（需要长时间衰减） | ✅ 快 |
| 宽带 | ✅ 优秀 | ❌ 需逐点扫 |
| 材料色散 | 天然支持（时域卷积） | 需特殊处理 |
| 网格 | 结构网格（六面体） | 非结构网格（四面体） |
| 典型工具 | Lumerical FDTD, MEEP | COMSOL, HFSS |

## 🔧 在仿真中怎么用

1. 设仿真区域大小 + 网格分辨率
2. 设材料（折射率/介电常数，可带 Lorentz/Drude 色散）
3. 设光源（平面波/高斯/偶极子）
4. 设监视器（频域场监视器、功率监视器）
5. 运行 → TFSF / 总场-散射场分离

## ⚠️ 常见坑

- **PML 反射**：入射角太大时 PML 吸收不干净
- **网格色散**：$\Delta x$ 太大会导致数值波速偏离物理波速
- **金属穿透**：长波长时金属的 penetration depth 需要额外注意网格
- **Courant 违例**：设了太小网格但没同步减小 $\Delta t$，导致数值不稳定 ❌ 实际上 Lumerical 会自动处理这个，但在 MEEP/自写代码中需要注意

## 🔗 相关页面

- [[../电磁仿真/麦克斯韦方程组]]
- [[../电磁仿真/有限元法 FEM]]
- [[射线追迹法]]
- [[光束传播法 BPM]]
