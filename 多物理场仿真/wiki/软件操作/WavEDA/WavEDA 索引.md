---
title: WavEDA 索引
tags:
  - 软件操作
  - WavEDA
  - EDA
  - MOC
created: 2026-06-08
updated: 2026-06-09
status: growing
---

# WavEDA EM-Multiphysics

> 芯和半导体自研的多物理场仿真平台。我的实习公司产品。

## 🎯 定位

国产 EDA 厂商，全自研求解器（电磁/热/力学），原生多物理场耦合。产品线覆盖 **EM → Thermal → Mechanics → Multi-Physics → Layout → Circuit**，从 DC 到 THz（光学）一个平台全覆盖。

| 项目 | 内容 |
|:---|:---|
| 公司 | 芯和半导体 |
| 官网 | <http://www.waveda.com> |
| 当前版本 | V2.0（2026） |
| 核心方法 | FEM（MFEM / MFETD）、DDM |
| 最大亮点 | 光学 51× 加速、DDM 大规模力学、原生多物理场 |

## 💭 学习记录

> *2026-06-08* — 通读 V2.0 产品介绍，补上对全貌的理解。

### 让我印象深的

**全自研，频段跨度大。** 电磁（MFEM/MFETD）、热、力学三套求解器都是自己写的。同一个平台从 DC 到 THz 全覆盖——静电到光学不换工具。

**光学模块数据很强。** 超表面仿真 @155THz，5 分钟 vs 竞品 4 小时 20 分，51 倍加速，误差 1.93%。这是目前看到的最亮眼的性能指标。

**DDM 是力学模块的核心武器。** 声波测井案例：不到 10 万单元 vs 竞品 375 万，1 小时 vs 12.5 小时。做大规模问题优势明显。

**Layout 自动化做得用心。** 自动识别 Pad/Via/Bump/Wirebond，一键 3D + 端口。IC 封装仿真里建端口是最繁琐的环节，这个设计背后有真需求。

### 值得记住的性能数据

| 场景 | WavEDA | 竞品（推测） | 加速比 | 误差 |
|:---|:---|:---|:---|:---|
| 超表面光学 @155THz | 5 min | 4 h 20 min (COMSOL) | **51×** | 1.93% |
| RCS 散射 | 2.18 h | 40.5 h (HFSS) | **18×** | — |
| 声波测井 DDM | 1 h 7 min | 12 h 38 min (COMSOL) | **11×** | 3.8~12% |
| PCB 电热耦合 | 1.5 min | 11 min (COMSOL) | **6.6×** | — |
| IC-SI | 1.24 h | 4.44 h (HFSS) | **3.6×** | — |

### 还想深入理解的

- **MFEM-fast 的技术细节** — 自适应阶数？混合网格？搞懂了以后讲技术优势会更有说服力
- **Circuit 模块为什么篇幅少** — 后续版本的重点方向里有没有它？
- **Benchmark 能不能用客户真实项目** — 让数据更有说服力，也帮内部找到优先改进的方向

## 📐 模块体系

| 模块 | 做什么 | 特点 |
|:---|:---|:---|
| **WavEDA EM** | 电磁仿真（DC→THz） | MFEM-fast / MFETD-fast，100+ 端口 |
| **WavEDA Thermal** | 热仿真 | PCB 级 + 3D，1D 简化选项 |
| **WavEDA Mechanics** | 力学仿真 | FEM + DDM，静态/瞬态/频域 |
| **WavEDA Multi-Physics** | 多物理场耦合 | EM + Thermal + Mechanics 原生耦合 |
| **WavEDA Layout** | 版图环境 | ODB++/DXF/GDSII/Gerber，自动 3D + 端口 |
| **WavEDA Circuit** | 电路仿真 | 与 Layout + EM 联合仿真 |

## 🔗 相关页面

- [[../索引|软件操作总索引]] — 所有工具入口
- [[../COMSOL/COMSOL 索引]] — 类比参考
- [[../ANSYS/ANSYS 索引]] — 类比参考
- [[../../EDA/索引|EDA 索引]]
- [[../../电磁仿真/有限元法 FEM]] — 被代理的求解器
- [[../../模型训练/代理模型与降阶模型]] — 和代理模型的结合点

## 📚 原始资料

- [[raw/EDA/WavEDA EM-Multiphysics-V2.0_20260319.pdf|产品介绍 PDF]]
- [[inbox/EDA/WavEDA EM-Multiphysics V2.0 - 摘要|阅读摘要]]
