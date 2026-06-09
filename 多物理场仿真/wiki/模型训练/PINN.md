---
title: Physics-Informed Neural Networks (PINNs)
tags:
  - 模型训练
  - 数值方法
  - 核心概念
aliases:
  - PINN
  - 物理信息神经网络
created: 2026-06-08
updated: 2026-06-08
---

# Physics-Informed Neural Networks (PINNs)

> 把 PDE 残差塞进损失函数——让神经网络"天生懂物理"。

## 📖 是什么

PINN 是一类将物理定律（PDE）直接嵌入神经网络训练的框架。传统的神经网络只拟合数据；PINN 同时拟合数据**和**物理方程。

### 损失函数结构

$$
\mathcal{L} = \mathcal{L}_{\text{data}} + \mathcal{L}_{\text{PDE}} + \mathcal{L}_{\text{BC}} + \mathcal{L}_{\text{IC}}
$$

| 项 | 含义 | 什么时候有 |
|:---|:---|:---|
| $\mathcal{L}_{\text{data}}$ | 数据拟合误差 | 有仿真/实验数据时 |
| $\mathcal{L}_{\text{PDE}}$ | PDE 残差（在配点上） | 总是有 |
| $\mathcal{L}_{\text{BC}}$ | 边界条件约束 | 总是有 |
| $\mathcal{L}_{\text{IC}}$ | 初始条件约束 | 瞬态问题 |

## 🧠 为什么重要

- **少数据甚至零数据**：纯物理驱动的 PINN 可以在没有仿真数据的情况下求解 PDE
- **物理一致性**：天然满足方程约束，不会做出"MSE 低但物理错"的预测
- **逆问题天然适配**：直接从观测数据反推参数/边界条件
- **适合多物理场**：多组 PDE 残差可以一起训

## 📐 工作流程

```
物理问题 → PDE 方程
              ↓
        NN(x, t) → û(x, t)  ← 神经网络预测
              ↓
        计算 PDE 残差 + BC 残差
              ↓
        Loss = w1·MSE_data + w2·残差_PDE + w3·残差_BC
              ↓
        反向传播 → 更新权重
```

## 🔧 在仿真中怎么用

| 场景 | PINN 的优势 |
|:---|:---|
| 参数化扫描 | 一次训练，任意参数点推理 |
| 逆问题 | 直接从测量场反推材料参数 |
| 多物理场耦合 | 多组 PDE 联合约束 |
| 实验数据融合 | 数据 + 物理双重约束 |

## ⚠️ 常见坑

- **多尺度问题训练难**：高频/陡梯度区域 PINN 学习很慢（spectral bias）
- **损失项权重难调**：$\mathcal{L}_{\text{PDE}}$ 太大 → 过拟合物理、$\mathcal{L}_{\text{data}}$ 太大 → 物理不对
- **配点策略不能随便**：随机撒点对高频问题效果差，需自适应配点
- **训练慢**：二阶导数（PDE 残差需要）计算量是普通网络的 2-3 倍
- **边界条件硬编码优于软约束**：强制满足 BC 比放在 loss 里更稳定

## 🔗 相关页面

- [[代理模型与降阶模型]]
- [[数据生成策略]]
- [[损失函数设计]]
- [[../热仿真/热传导基础]] — PINN 入门的经典案例

## 📚 参考

- Raissi et al., 2019 — PINN 开创性论文
- Lu et al., *DeepXDE: A deep learning library for solving differential equations*
- NVIDIA Modulus 文档
