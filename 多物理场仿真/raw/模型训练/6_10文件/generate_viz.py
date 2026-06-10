"""Generate visualizations from saved models."""
import os, json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.model_selection import KFold

matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

SEED = 42
BASE = os.path.dirname(os.path.abspath(__file__))
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# Load data
df = pd.read_excel(os.path.join(BASE, "Port_S_data_2_1-8GHz.xlsx"))
X = df[['l3', 'w4']].to_numpy(dtype=np.float32)
y_db = df.iloc[:, 3:].to_numpy(dtype=np.float32)
frequency = df.columns[3:].astype(float).to_numpy()
N = len(X)

# Load forward model
fwd_pkg = torch.load(os.path.join(BASE, "model_forward.pth"), map_location=device, weights_only=False)

class ForwardNet(nn.Module):
    def __init__(self, pca_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(2, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, pca_dim),
        )
    def forward(self, x):
        return self.net(x)

model_f = ForwardNet(fwd_pkg['pca_dim']).to(device)
model_f.load_state_dict(fwd_pkg['model_state'])
model_f.eval()

x_mean_t = torch.tensor(fwd_pkg['x_mean'], dtype=torch.float32, device=device)
x_scale_t = torch.tensor(fwd_pkg['x_scale'], dtype=torch.float32, device=device)
pc_t = torch.tensor(fwd_pkg['pca_components'], dtype=torch.float32, device=device)
pm_t = torch.tensor(fwd_pkg['pca_mean'], dtype=torch.float32, device=device)
cs_t = torch.tensor(fwd_pkg['coeff_scale'], dtype=torch.float32, device=device)
cm_t = torch.tensor(fwd_pkg['coeff_mean'], dtype=torch.float32, device=device)

def forward_predict(params):
    """params: (n, 2) numpy array -> (n, 40) S11 curves"""
    p = (torch.tensor(params, dtype=torch.float32, device=device) - x_mean_t) / x_scale_t
    with torch.no_grad():
        c = model_f(p)
        return ((c * cs_t + cm_t) @ pc_t + pm_t).cpu().numpy()

# Load inverse model
inv_pkg = torch.load(os.path.join(BASE, "model_inverse.pth"), map_location=device, weights_only=False)

class InverseNet(nn.Module):
    def __init__(self, in_dim, out_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, 256), nn.SiLU(), nn.Dropout(0.1),
            nn.Linear(256, 256), nn.SiLU(), nn.Dropout(0.1),
            nn.Linear(256, 128), nn.SiLU(),
            nn.Linear(128, out_dim), nn.Sigmoid(),
        )
    def forward(self, x):
        return self.net(x)

inv_model = InverseNet(inv_pkg['inp_dim'], 2).to(device)
inv_model.load_state_dict(inv_pkg['model_state'])
inv_model.eval()

y_min = inv_pkg['y_min']; y_max = inv_pkg['y_max']

def inverse_predict(curves):
    """curves: (n, 40) numpy array -> (n, 2) params"""
    pca = inv_pkg['pca_inv']
    feat_scaler = inv_pkg['feat_scaler']
    inv_scaler = inv_pkg['inv_scaler']

    pca_coeff = pca.transform(curves).astype(np.float32)
    # Handcrafted features
    feats = []
    for c in curves:
        f_min_idx = np.argmin(c)
        f_res = frequency[f_min_idx]
        s11_min = c[f_min_idx]
        m10 = c <= -10; bw10 = frequency[m10][-1] - frequency[m10][0] if m10.sum() > 1 else 0.0
        m3 = c <= -3; bw3 = frequency[m3][-1] - frequency[m3][0] if m3.sum() > 1 else 0.0
        integral = np.trapz(c, frequency)
        feats.append([f_res, s11_min, bw10, bw3, integral, np.mean(c), np.std(c)])
    feats = np.array(feats, dtype=np.float32)
    feats_s = feat_scaler.transform(feats)

    inp = np.concatenate([pca_coeff, feats_s], axis=1)
    inp_s = inv_scaler.transform(inp)
    inp_t = torch.tensor(inp_s, dtype=torch.float32, device=device)
    with torch.no_grad():
        out = inv_model(inp_t).cpu().numpy()
    return out * (y_max - y_min) + y_min

# Predict all
pred_all = forward_predict(X)
all_maes = np.mean(np.abs(pred_all - y_db), axis=1)

pred_params = inverse_predict(y_db)
rec_curves = forward_predict(pred_params.astype(np.float32))
rec_maes = np.mean(np.abs(rec_curves - y_db), axis=1)

# 5-Fold CV predictions
K = 5
kf = KFold(n_splits=K, shuffle=True, random_state=SEED)
cv_preds = np.zeros_like(y_db)
for fold, (tr_i, va_i) in enumerate(kf.split(X)):
    # Train a quick forward model per fold
    from sklearn.decomposition import PCA
    from sklearn.preprocessing import StandardScaler
    import copy

    x_sc = StandardScaler().fit(X[tr_i])
    x_tr_s = x_sc.transform(X[tr_i]).astype(np.float32)
    x_va_s = x_sc.transform(X[va_i]).astype(np.float32)
    pdim = min(20, len(tr_i) - 2)
    pca = PCA(n_components=pdim).fit(y_db[tr_i])
    y_tr_c = pca.transform(y_db[tr_i]).astype(np.float32)
    c_sc = StandardScaler().fit(y_tr_c)
    y_tr_cs = c_sc.transform(y_tr_c).astype(np.float32)

    Xtr = torch.tensor(x_tr_s, device=device); Xva = torch.tensor(x_va_s, device=device)
    Ytr = torch.tensor(y_db[tr_i], device=device)
    Ytr_cs = torch.tensor(y_tr_cs, device=device)
    pct = torch.tensor(pca.components_, dtype=torch.float32, device=device)
    pmt = torch.tensor(pca.mean_, dtype=torch.float32, device=device)
    cst = torch.tensor(c_sc.scale_, dtype=torch.float32, device=device)
    cmt = torch.tensor(c_sc.mean_, dtype=torch.float32, device=device)

    def dec(c):
        return (c * cst + cmt) @ pct + pmt

    m = ForwardNet(pdim).to(device)
    opt = torch.optim.AdamW(m.parameters(), lr=0.001, weight_decay=1e-5)
    for ep in range(1000):
        m.train(); opt.zero_grad()
        pc = m(Xtr)
        w = 1 + 20 * (torch.clamp(-Ytr, min=0) / 40) ** 2
        loss = torch.mean(w * (dec(pc) - Ytr) ** 2) + 0.01 * torch.mean((pc - Ytr_cs) ** 2)
        loss.backward(); opt.step()
    m.eval()
    with torch.no_grad():
        cv_preds[va_i] = dec(m(Xva)).cpu().numpy()

cv_fold_maes = []
for _, va_i in kf.split(X):
    cv_fold_maes.append(np.mean(np.abs(cv_preds[va_i] - y_db[va_i]), axis=1))

# ===== Plot =====
fig, axes = plt.subplots(2, 3, figsize=(20, 12))

# A: CV boxplot
ax = axes[0, 0]
ax.boxplot(cv_fold_maes, labels=[f'Fold {i+1}' for i in range(K)])
ax.set_ylabel('Curve MAE (dB)'); ax.set_title('Forward 5-Fold CV — MAE per Fold')
ax.grid(alpha=0.3, axis='y')

# B: Inverse param scatter
ax = axes[0, 1]
sc = ax.scatter(X[:, 0], X[:, 1], c=rec_maes, cmap='RdYlGn_r', s=50, edgecolors='gray', linewidths=0.5, label='True')
ax.scatter(pred_params[:, 0], pred_params[:, 1], c=rec_maes, cmap='RdYlGn_r', s=50, marker='x', linewidths=1.5, label='Pred (inv)')
for i in range(N):
    ax.plot([X[i, 0], pred_params[i, 0]], [X[i, 1], pred_params[i, 1]], '-', color='gray', alpha=0.2, lw=0.8)
plt.colorbar(sc, ax=ax, label='Curve MAE (dB)')
ax.set_xlabel('l3'); ax.set_ylabel('w4')
ax.set_title('Inverse: True (circle) vs Predicted (x) Parameters'); ax.legend(fontsize=8)

# C: Inverse best reconstruction
ax = axes[0, 2]
bi = np.argmin(rec_maes)
ax.plot(frequency, y_db[bi], '-', color='#2563EB', lw=2.5, label=f'Target (l3={X[bi,0]:.0f}, w4={X[bi,1]:.1f})')
ax.plot(frequency, rec_curves[bi], '--', color='#DC2626', lw=2, label=f'Reconstructed (MAE={rec_maes[bi]:.4f} dB)')
ax.set_xlabel('Frequency (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title(f'Inverse Best Reconstruction | l3_pred={pred_params[bi,0]:.2f}, w4_pred={pred_params[bi,1]:.2f}')
ax.legend(fontsize=8); ax.grid(alpha=0.3)

# D: Forward best / worst
ax = axes[1, 0]
bi_f = np.argmin(all_maes)
ax.plot(frequency, y_db[bi_f], '-', color='#2563EB', lw=2, label=f'True (l3={X[bi_f,0]:.0f}, w4={X[bi_f,1]:.1f})')
ax.plot(frequency, pred_all[bi_f], '--', color='#DC2626', lw=2, label=f'Pred (MAE={all_maes[bi_f]:.4f} dB)')
ax.set_xlabel('Frequency (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title(f'Forward Best Fit | MAE={all_maes[bi_f]:.4f} dB'); ax.legend(fontsize=8); ax.grid(alpha=0.3)

ax = axes[1, 1]
wi_f = np.argmax(all_maes)
ax.plot(frequency, y_db[wi_f], '-', color='#2563EB', lw=2, label=f'True (l3={X[wi_f,0]:.0f}, w4={X[wi_f,1]:.1f})')
ax.plot(frequency, pred_all[wi_f], '--', color='#DC2626', lw=2, label=f'Pred (MAE={all_maes[wi_f]:.4f} dB)')
ax.set_xlabel('Frequency (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title(f'Forward Worst Fit | MAE={all_maes[wi_f]:.4f} dB'); ax.legend(fontsize=8); ax.grid(alpha=0.3)

# F: MAE histogram
ax = axes[1, 2]
bins = np.linspace(0, max(all_maes.max(), rec_maes.max()), 15)
ax.hist(all_maes, bins=bins, alpha=0.6, color='steelblue', edgecolor='white', label=f'Forward (mean={np.mean(all_maes):.3f}, med={np.median(all_maes):.3f})')
ax.hist(rec_maes, bins=bins, alpha=0.6, color='darkorange', edgecolor='white', label=f'Inverse (mean={np.mean(rec_maes):.3f}, med={np.median(rec_maes):.3f})')
ax.axvline(np.mean(all_maes), color='steelblue', linestyle='--', lw=1.5)
ax.axvline(np.mean(rec_maes), color='darkorange', linestyle='--', lw=1.5)
ax.set_xlabel('Curve MAE (dB)'); ax.set_title('Forward vs Inverse — MAE Distribution')
ax.legend(fontsize=8); ax.grid(alpha=0.3, axis='y')

plt.suptitle('Port_S2 1-8GHz — Forward & Inverse Model Results', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig_path = os.path.join(BASE, 'model_results_summary.png')
plt.savefig(fig_path, dpi=150, bbox_inches='tight')
plt.close()
print(f"Figure saved: {fig_path}")
print("Done!")
