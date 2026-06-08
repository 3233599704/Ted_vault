---
title: CST 索引
tags:
  - 软件操作
  - CST
  - MOC
created: 2026-06-08
updated: 2026-06-08
---

# CST Studio Suite

> 高频电磁仿真的"时间域之王"——时域求解器独步天下，宽带仿真首选。

## 🎯 CST 的定位

- **核心优势**：时域求解器（Time Domain Solver）在宽带仿真中极快
- **与 HFSS 对比**：HFSS 用频域 FEM，CST 用时域 FIT（Finite Integration Technique）
- **适用场景**：天线、微波器件、EMC/EMI、SI/PI

## 📐 CST 的求解器体系

| 求解器 | 方法 | 适合 |
|:---|:---|:---|
| **Time Domain** | 时域有限积分法（FIT） | 宽带、天线、EMC |
| **Frequency Domain** | 频域 FEM | 窄带、高 Q 谐振器 |
| **Integral Equation** | MoM / MLFMM | 电大尺寸天线/RCS |
| **Asymptotic** | PO / SBR | 超大电尺寸（雷达安装） |
| **Eigenmode** | 本征模 | 谐振腔/加速器 |
| **Thermal** | 热求解器 | 电磁-热耦合 |

## 🛠️ CST vs HFSS

| | CST | HFSS |
|:---|:---|:---|
| 核心方法 | 时域 FIT | 频域 FEM |
| 宽带天线 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| 窄带谐振器 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 网格 | 六面体为主 | 四面体为主 |
| 上手难度 | 中等 | 中等 |
| 用户群 | 欧洲/亚洲 | 全球（尤其美国） |

## ⚠️ CST 常见坑

- **时域求解器的高 Q 问题**：Q 值高的结构能量振荡衰减慢 → 仿真时间长，选 Frequency Domain 更好
- **网格对齐**：六面体网格对斜结构/曲面不如四面体友好
- **端口设置**：波导端口的大小和模式数容易被忽视
- **GPU 加速**：CST 支持 GPU 加速，但需要特定 License + 兼容硬件

## 📝 推荐学习顺序

1. 从 Time Domain Solver 开始——一个简单天线案例
2. 理解多种求解器的适用场景（什么时候换频域？）
3. 学参数化建模 + 优化
4. 学 EMC/EMI 分析的完整流程
