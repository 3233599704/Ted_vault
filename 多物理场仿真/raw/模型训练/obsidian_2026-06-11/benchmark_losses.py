"""
Benchmark: Different loss functions on Port_S3 linear domain
- L1 (MAE)
- L2 (MSE)
- Huber (smooth mix)
- SmoothL1
- LogCosh
- MAPE (relative error)
"""
import copy, warnings, os, time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch, torch.nn as nn
from sklearn.decomposition import PCA
from sklearn.model_selection import KFold
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

SEED = 42
BASE = os.path.dirname(os.path.abspath(__file__))
device = torch.device("cpu")

df = pd.read_excel(os.path.join(BASE, "Port_S3_data.xlsx"))
X = df[['l3','w4','h']].to_numpy(dtype=np.float32)
y_db_orig = df.iloc[:, 4:].to_numpy(dtype=np.float32)
frequency = df.columns[4:].astype(float).to_numpy()
y_target = 10 ** (y_db_orig / 20.0)
N, F = len(X), len(frequency)
print(f"Data: {N} samples, {F} freq points, linear domain")

def to_db(arr):
    return 20 * np.log10(np.clip(arr, 0.0001, None))

def metrics_db(true, pred):
    t_db, p_db = to_db(true), to_db(pred)
    err = p_db - t_db
    tmi = np.argmin(t_db, axis=1); pmi = np.argmin(p_db, axis=1)
    rows = np.arange(len(t_db))
    return {
        'MAE': float(np.mean(np.abs(err))),
        'FreqErr_MHz': float(np.mean(np.abs(frequency[pmi] - frequency[tmi])) * 1000),
        'DepthErr': float(np.mean(np.abs(p_db[rows, pmi] - t_db[rows, tmi]))),
    }

class PCANet(nn.Module):
    def __init__(self, pca_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(3, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, pca_dim),
        )
    def forward(self, x): return self.net(x)

# Loss functions
def loss_l1(pred, true):
    return torch.mean(torch.abs(pred - true))

def loss_l2(pred, true):
    return torch.mean((pred - true) ** 2)

def loss_huber(pred, true, delta=0.01):
    """Huber: L2 near 0, L1 far away. delta tuned for linear domain range 0.02~0.98"""
    err = torch.abs(pred - true)
    return torch.mean(torch.where(err < delta, 0.5 * err**2, delta * (err - 0.5 * delta)))

def loss_smooth_l1(pred, true):
    return nn.functional.smooth_l1_loss(pred, true, beta=0.01)

def loss_logcosh(pred, true):
    """log(cosh(x)): smooth like L2 near 0, like L1 far away"""
    return torch.mean(torch.log(torch.cosh(pred - true)))

def loss_mape(pred, true):
    """Mean Absolute Percentage Error — relative error"""
    eps = 0.001
    return torch.mean(torch.abs((pred - true) / torch.clamp(torch.abs(true), min=eps)))

# Weighted L2 (our standard from dB days)
def loss_weighted_l2(pred, true):
    true_db = 20 * torch.log10(torch.clamp(true, min=0.0001))
    w = 1 + 25 * (torch.clamp(-true_db, min=0) / 40) ** 2
    return torch.mean(w * (pred - true) ** 2)

LOSSES = {
    'L1 (MAE)': loss_l1,
    'L2 (MSE)': loss_l2,
    'Huber': loss_huber,
    'SmoothL1': loss_smooth_l1,
    'LogCosh': loss_logcosh,
    'MAPE': loss_mape,
    'Weighted L2': loss_weighted_l2,
}

def train_one_fold_loss(X_tr, y_tr, X_va, y_va, loss_fn, loss_name):
    x_sc = StandardScaler().fit(X_tr)
    X_tr_s = x_sc.transform(X_tr).astype(np.float32)
    X_va_s = x_sc.transform(X_va).astype(np.float32)

    pca = PCA(n_components=35).fit(y_tr)
    y_tr_c = pca.transform(y_tr).astype(np.float32)
    c_sc = StandardScaler().fit(y_tr_c)
    y_tr_cs = c_sc.transform(y_tr_c).astype(np.float32)

    Xtr = torch.tensor(X_tr_s, device=device); Xva = torch.tensor(X_va_s, device=device)
    Ytr = torch.tensor(y_tr, device=device); Yva = torch.tensor(y_va, device=device)
    Ytr_cs = torch.tensor(y_tr_cs, device=device)
    pct = torch.tensor(pca.components_, dtype=torch.float32, device=device)
    pmt = torch.tensor(pca.mean_, dtype=torch.float32, device=device)
    cst = torch.tensor(c_sc.scale_, dtype=torch.float32, device=device)
    cmt = torch.tensor(c_sc.mean_, dtype=torch.float32, device=device)
    def dec(c): return (c * cst + cmt) @ pct + pmt

    model = PCANet(35).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=1e-5)
    best_v, best_e, best_s, pat = float('inf'), 0, None, 0
    for ep in range(2000):
        model.train(); opt.zero_grad()
        pc = model(Xtr); pred = dec(pc)
        loss = loss_fn(pred, Ytr) + 0.005 * torch.mean((pc - Ytr_cs) ** 2)
        if torch.isnan(loss) or torch.isinf(loss):
            return None, 0  # NaN guard
        loss.backward(); opt.step()

        model.eval()
        with torch.no_grad():
            v_pred = dec(model(Xva)); vl = loss_fn(v_pred, Yva)
        if vl.item() < best_v - 1e-8:
            best_v = vl.item(); best_e = ep + 1
            best_s = copy.deepcopy(model.state_dict()); pat = 0
        else: pat += 1
        if pat >= 200: break

    if best_s is None: return None, 0
    model.load_state_dict(best_s); model.eval()
    with torch.no_grad(): p_va = dec(model(Xva)).cpu().numpy()
    return metrics_db(y_va, p_va), best_e

# Run all losses
print("\n" + "=" * 70)
print("LOSS FUNCTION BENCHMARK — Port_S3 Linear Domain")
print("=" * 70)

np.random.seed(SEED); torch.manual_seed(SEED)
kf = KFold(n_splits=5, shuffle=True, random_state=SEED)

results = {}
all_fold_details = {}

for loss_name, loss_fn in LOSSES.items():
    print(f"  {loss_name:<15} ...", end=" ", flush=True)
    fold_metrics = []
    for tr_i, va_i in kf.split(X):
        m, ep = train_one_fold_loss(X[tr_i], y_target[tr_i], X[va_i], y_target[va_i], loss_fn, loss_name)
        if m is not None:
            fold_metrics.append(m)
    if fold_metrics:
        results[loss_name] = {
            'MAE': float(np.mean([m['MAE'] for m in fold_metrics])),
            'FreqErr': float(np.mean([m['FreqErr_MHz'] for m in fold_metrics])),
            'DepthErr': float(np.mean([m['DepthErr'] for m in fold_metrics])),
        }
        print(f"MAE={results[loss_name]['MAE']:.4f}, FreqErr={results[loss_name]['FreqErr']:.0f} MHz")
    else:
        results[loss_name] = {'MAE': float('nan'), 'FreqErr': float('nan'), 'DepthErr': float('nan')}
        print("FAILED (NaN)")

# ============================================================
# Summary
# ============================================================
print("\n" + "=" * 70)
print("LOSS FUNCTION COMPARISON")
print("=" * 70)
print(f"{'Loss':<15} {'MAE(dB)':>10} {'FreqErr(MHz)':>14} {'DepthErr(dB)':>14} {'vs L2':>10}")
print("-" * 63)
baseline_mae = results['L2 (MSE)']['MAE']
for name, r in results.items():
    delta = f"{(r['MAE'] - baseline_mae)/baseline_mae*100:+.1f}%" if not np.isnan(r['MAE']) else "N/A"
    print(f"{name:<15} {r['MAE']:>10.4f} {r['FreqErr']:>14.1f} {r['DepthErr']:>14.4f} {delta:>10}")

# Best
valid = {k: v for k, v in results.items() if not np.isnan(v['MAE'])}
best_name = min(valid, key=lambda k: valid[k]['MAE'])
print(f"\nBest: {best_name} (MAE={results[best_name]['MAE']:.4f} dB)")

# ============================================================
# Figure
# ============================================================
fig, axes = plt.subplots(1, 2, figsize=(16, 6))

names = list(results.keys())
mae_vals = [results[n]['MAE'] for n in names]
freq_vals = [results[n]['FreqErr'] for n in names]
depth_vals = [results[n]['DepthErr'] for n in names]
colors = plt.cm.tab10(np.linspace(0, 1, len(names)))

x = np.arange(len(names))
w = 0.25

ax = axes[0]
bars = ax.bar(x, mae_vals, color=colors, edgecolor='white', alpha=0.85)
ax.axhline(baseline_mae, color='gray', linestyle='--', lw=1.5, alpha=0.5, label=f'L2 baseline={baseline_mae:.4f}')
ax.set_xticks(x); ax.set_xticklabels(names, fontsize=9, rotation=30)
ax.set_ylabel('MAE (dB)', fontsize=12)
ax.set_title('Curve MAE by Loss Function', fontsize=13, fontweight='bold')
for bar, v in zip(bars, mae_vals):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.002, f'{v:.4f}', ha='center', fontsize=7)
ax.legend(fontsize=8); ax.grid(alpha=0.2, axis='y')

ax = axes[1]
w2 = 0.3
bars1 = ax.bar(x - w2/2, freq_vals, w2, color='#DC2626', alpha=0.8, label='FreqErr (MHz)')
bars2 = ax.bar(x + w2/2, depth_vals, w2, color='#F97316', alpha=0.8, label='DepthErr (dB)')
ax.set_xticks(x); ax.set_xticklabels(names, fontsize=9, rotation=30)
ax.set_ylabel('Error', fontsize=12)
ax.set_title('Resonance Errors by Loss Function', fontsize=13, fontweight='bold')
ax.legend(fontsize=9); ax.grid(alpha=0.2, axis='y')

plt.suptitle('Loss Function Benchmark — Port_S3 Linear Domain (5-Fold CV)', fontsize=14, fontweight='bold', y=1.02)
plt.tight_layout()
plt.savefig(os.path.join(BASE, 'loss_benchmark.png'), dpi=180, bbox_inches='tight')
plt.close()
print(f"\nFigure: loss_benchmark.png")

# ============================================================
# Explanation
# ============================================================
print("\n" + "=" * 70)
print("WHAT EACH LOSS MEANS")
print("=" * 70)
explanations = {
    'L1 (MAE)': '绝对值误差。对异常值不敏感，但梯度恒定，接近最优时不易收敛。',
    'L2 (MSE)': '平方误差。异常值惩罚极重，梯度随误差减小而减小，收敛稳定。',
    'Huber': '小误差用L2（平滑），大误差用L1（抗噪）。需要调delta阈值。',
    'SmoothL1': 'PyTorch内置版Huber。beta=0.01适合线性域的小数值范围。',
    'LogCosh': 'log(cosh(x))。处处平滑可导，类似Huber但无需调参。',
    'MAPE': '相对百分比误差。在物理意义上有优势——深谷的相对误差更重要。',
    'Weighted L2': 'dB域风格的加权L2。在线性域大概率多余（线性域已自动加权）。',
}
for name, explanation in explanations.items():
    print(f"  {name:<15}: {explanation}")

print("\nDone!")
