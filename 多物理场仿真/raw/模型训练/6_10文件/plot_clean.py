"""Clean S11 visualization: True vs Predicted line charts."""
import os, warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn

warnings.filterwarnings("ignore")
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

BASE = os.path.dirname(os.path.abspath(__file__))
device = torch.device("cpu")

# Load data
df = pd.read_excel(os.path.join(BASE, "Port_S_data_2_1-8GHz.xlsx"))
X = df[['l3', 'w4']].to_numpy(dtype=np.float32)
y_db = df.iloc[:, 3:].to_numpy(dtype=np.float32)
frequency = df.columns[3:].astype(float).to_numpy()
N = len(X)

# Load forward model
class ForwardNet(nn.Module):
    def __init__(self, pca_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(2, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, pca_dim),
        )
    def forward(self, x): return self.net(x)

pkg = torch.load(os.path.join(BASE, "model_forward.pth"), map_location=device, weights_only=False)
model = ForwardNet(pkg['pca_dim']).to(device)
model.load_state_dict(pkg['model_state'])
model.eval()

xm = torch.tensor(pkg['x_mean'], dtype=torch.float32, device=device)
xs = torch.tensor(pkg['x_scale'], dtype=torch.float32, device=device)
pc = torch.tensor(pkg['pca_components'], dtype=torch.float32, device=device)
pm = torch.tensor(pkg['pca_mean'], dtype=torch.float32, device=device)
cs = torch.tensor(pkg['coeff_scale'], dtype=torch.float32, device=device)
cm = torch.tensor(pkg['coeff_mean'], dtype=torch.float32, device=device)

X_s = (torch.tensor(X, device=device) - xm) / xs
with torch.no_grad():
    pred_all = ((model(X_s) * cs + cm) @ pc + pm).cpu().numpy()

all_maes = np.mean(np.abs(pred_all - y_db), axis=1)

# ==================== FIGURE 1: 代表性样本网格 ====================
# Pick 9 diverse samples: 3 best, 3 median, 3 worst
sorted_idx = np.argsort(all_maes)
n_each = 3
selected = np.concatenate([
    sorted_idx[:n_each],                          # best
    sorted_idx[len(sorted_idx)//2 - 1:len(sorted_idx)//2 + n_each - 1],  # median
    sorted_idx[-n_each:],                          # worst
])

fig, axes = plt.subplots(3, 3, figsize=(18, 14))

for i, idx in enumerate(selected):
    ax = axes[i // 3, i % 3]
    ax.plot(frequency, y_db[idx], '-', color='#2563EB', linewidth=2.2, label='True')
    ax.plot(frequency, pred_all[idx], '--', color='#DC2626', linewidth=2.0, label='Pred')
    ax.fill_between(frequency, y_db[idx], pred_all[idx], alpha=0.08, color='gray')

    # Mark resonance points
    ti = np.argmin(y_db[idx]); pi = np.argmin(pred_all[idx])
    ax.scatter(frequency[ti], y_db[idx, ti], color='#2563EB', s=70, zorder=5)
    ax.scatter(frequency[pi], pred_all[idx, pi], color='#DC2626', s=70, marker='D', zorder=5)

    l3, w4 = X[idx]
    mae = all_maes[idx]
    ax.set_title(f'l3={l3:.0f}, w4={w4:.1f}  |  MAE={mae:.4f} dB', fontsize=11, fontweight='bold')
    ax.set_xlabel('Frequency (GHz)', fontsize=9)
    ax.set_ylabel('S(1,1) (dB)', fontsize=9)
    ax.legend(fontsize=8, loc='lower right')
    ax.grid(alpha=0.25)

plt.suptitle('Forward Model — True vs Predicted S11 Curves', fontsize=16, fontweight='bold', y=1.01)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 's11_curves_grid.png'), dpi=180, bbox_inches='tight')
plt.close()
print("Fig 1: s11_curves_grid.png")

# ==================== FIGURE 2: 全样本瀑布图 ====================
fig, axes = plt.subplots(1, 2, figsize=(18, 7))

# Left: True curves
ax = axes[0]
for i in range(N):
    alpha = 0.15 + 0.55 * (1 - all_maes[i] / all_maes.max())
    ax.plot(frequency, y_db[i], color='#2563EB', linewidth=0.7, alpha=np.clip(alpha, 0.15, 0.7))
ax.set_xlabel('Frequency (GHz)', fontsize=12)
ax.set_ylabel('S(1,1) (dB)', fontsize=12)
ax.set_title(f'True S11 — {N} samples', fontsize=14, fontweight='bold')
ax.grid(alpha=0.2)

# Right: Predicted curves
ax = axes[1]
for i in range(N):
    alpha = 0.15 + 0.55 * (1 - all_maes[i] / all_maes.max())
    ax.plot(frequency, pred_all[i], color='#DC2626', linewidth=0.7, alpha=np.clip(alpha, 0.15, 0.7))
ax.set_xlabel('Frequency (GHz)', fontsize=12)
ax.set_ylabel('S(1,1) (dB)', fontsize=12)
ax.set_title(f'Predicted S11 — Mean MAE={all_maes.mean():.3f} dB', fontsize=14, fontweight='bold')
ax.grid(alpha=0.2)

plt.suptitle('Full Dataset: True vs Predicted S11', fontsize=16, fontweight='bold', y=1.02)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 's11_waterfall.png'), dpi=180, bbox_inches='tight')
plt.close()
print("Fig 2: s11_waterfall.png")

# ==================== FIGURE 3: 误差 vs 频率 ====================
fig, axes = plt.subplots(1, 2, figsize=(18, 7))

# Left: Mean ± std error band
errors = pred_all - y_db
mean_err = np.mean(errors, axis=0)
std_err = np.std(errors, axis=0)

ax = axes[0]
ax.fill_between(frequency, mean_err - std_err, mean_err + std_err, alpha=0.2, color='#2563EB')
ax.plot(frequency, mean_err, '-', color='#2563EB', linewidth=2.0)
ax.axhline(0, color='black', linestyle='-', linewidth=0.8)
ax.set_xlabel('Frequency (GHz)', fontsize=12)
ax.set_ylabel('Prediction Error (dB)', fontsize=12)
ax.set_title(f'Mean Error ± 1σ | Mean |Error| = {np.mean(np.abs(errors)):.3f} dB', fontsize=13, fontweight='bold')
ax.grid(alpha=0.2)

# Right: Per-sample error lines
ax = axes[1]
for i in range(N):
    alpha = 0.12 + 0.50 * (all_maes[i] / all_maes.max())
    ax.plot(frequency, errors[i], color='#7C3AED', linewidth=0.6, alpha=np.clip(alpha, 0.12, 0.6))
ax.axhline(0, color='black', linestyle='-', linewidth=1.0)
ax.set_xlabel('Frequency (GHz)', fontsize=12)
ax.set_ylabel('Prediction Error (dB)', fontsize=12)
ax.set_title('Per-Sample Error Curves', fontsize=13, fontweight='bold')
ax.grid(alpha=0.2)

plt.suptitle('Forward Model Error Analysis', fontsize=16, fontweight='bold', y=1.02)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 's11_error_analysis.png'), dpi=180, bbox_inches='tight')
plt.close()
print("Fig 3: s11_error_analysis.png")

# ==================== FIGURE 4: 最佳/最差对比 (大幅) ====================
fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# Top: Best 2
for j, rank in enumerate(['Best', '2nd Best']):
    idx = sorted_idx[0] if j == 0 else sorted_idx[1]
    ax = axes[0, j]
    ax.plot(frequency, y_db[idx], '-', color='#2563EB', linewidth=2.8, label='True')
    ax.plot(frequency, pred_all[idx], '--', color='#DC2626', linewidth=2.5, label='Predicted')
    ax.fill_between(frequency, y_db[idx], pred_all[idx], alpha=0.10, color='gray')
    ti = np.argmin(y_db[idx]); pi = np.argmin(pred_all[idx])
    ax.scatter(frequency[ti], y_db[idx, ti], color='#2563EB', s=120, zorder=5, label=f'True min: {y_db[idx,ti]:.2f} dB @ {frequency[ti]:.2f} GHz')
    ax.scatter(frequency[pi], pred_all[idx, pi], color='#DC2626', s=120, marker='D', zorder=5, label=f'Pred min: {pred_all[idx,pi]:.2f} dB @ {frequency[pi]:.2f} GHz')
    l3, w4 = X[idx]
    ax.set_title(f'{rank} Fit  |  l3={l3:.0f}, w4={w4:.1f}  |  MAE={all_maes[idx]:.4f} dB', fontsize=13, fontweight='bold')
    ax.set_xlabel('Frequency (GHz)', fontsize=11); ax.set_ylabel('S(1,1) (dB)', fontsize=11)
    ax.legend(fontsize=9, loc='lower right'); ax.grid(alpha=0.25)

# Bottom: Worst 2
for j, rank in enumerate(['Worst', '2nd Worst']):
    idx = sorted_idx[-1] if j == 0 else sorted_idx[-2]
    ax = axes[1, j]
    ax.plot(frequency, y_db[idx], '-', color='#2563EB', linewidth=2.8, label='True')
    ax.plot(frequency, pred_all[idx], '--', color='#DC2626', linewidth=2.5, label='Predicted')
    ax.fill_between(frequency, y_db[idx], pred_all[idx], alpha=0.10, color='gray')
    ti = np.argmin(y_db[idx]); pi = np.argmin(pred_all[idx])
    ax.scatter(frequency[ti], y_db[idx, ti], color='#2563EB', s=120, zorder=5, label=f'True min: {y_db[idx,ti]:.2f} dB @ {frequency[ti]:.2f} GHz')
    ax.scatter(frequency[pi], pred_all[idx, pi], color='#DC2626', s=120, marker='D', zorder=5, label=f'Pred min: {pred_all[idx,pi]:.2f} dB @ {frequency[pi]:.2f} GHz')
    l3, w4 = X[idx]
    ax.set_title(f'{rank} Fit  |  l3={l3:.0f}, w4={w4:.1f}  |  MAE={all_maes[idx]:.4f} dB', fontsize=13, fontweight='bold')
    ax.set_xlabel('Frequency (GHz)', fontsize=11); ax.set_ylabel('S(1,1) (dB)', fontsize=11)
    ax.legend(fontsize=9, loc='lower right'); ax.grid(alpha=0.25)

plt.suptitle('Forward Model — Best & Worst Fits', fontsize=16, fontweight='bold', y=1.01)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 's11_best_worst.png'), dpi=180, bbox_inches='tight')
plt.close()
print("Fig 4: s11_best_worst.png")

print("\nDone! Generated 4 figures in:", BASE)
for f in ['s11_curves_grid.png', 's11_waterfall.png', 's11_error_analysis.png', 's11_best_worst.png']:
    sz = os.path.getsize(os.path.join(BASE, f))
    print(f"  {f} ({sz/1024:.0f} KB)")
