"""
Comprehensive benchmark: K-fold, CNN decoder, Freq-aware loss, Ensemble
All in LINEAR domain, quick mode. Port_S3 data.
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

SEEDS = [42, 123, 456]
BASE = os.path.dirname(os.path.abspath(__file__))
device = torch.device("cpu")

# Load data
df = pd.read_excel(os.path.join(BASE, "Port_S3_data.xlsx"))
X = df[['l3','w4','h']].to_numpy(dtype=np.float32)
y_db_orig = df.iloc[:, 4:].to_numpy(dtype=np.float32)
frequency = df.columns[4:].astype(float).to_numpy()
N, F = len(X), len(frequency)

# Linear domain
y_target = 10 ** (y_db_orig / 20.0)
freq_t = torch.tensor(frequency, dtype=torch.float32, device=device)
print(f"Data: {N} samples, {F} freq, linear domain")
print(f"Linear range: {y_target.min():.4f} ~ {y_target.max():.4f}")

# ============================================================
# Helper: Metrics in dB
# ============================================================
def to_db(arr):
    return 20 * np.log10(np.clip(arr, 0.0001, None))

def metrics_db(true, pred):
    t_db, p_db = to_db(true), to_db(pred)
    err = p_db - t_db
    tmi = np.argmin(t_db, axis=1); pmi = np.argmin(p_db, axis=1)
    rows = np.arange(len(t_db))
    return {
        'MAE': float(np.mean(np.abs(err))),
        'RMSE': float(np.sqrt(np.mean(err**2))),
        'FreqErr_MHz': float(np.mean(np.abs(frequency[pmi] - frequency[tmi])) * 1000),
        'DepthErr': float(np.mean(np.abs(p_db[rows, pmi] - t_db[rows, tmi]))),
    }

# ============================================================
# Base architectures
# ============================================================
class PCANet(nn.Module):
    """Standard: MLP -> PCA coefficients"""
    def __init__(self, pca_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(3, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, pca_dim),
        )
    def forward(self, x): return self.net(x)

class CNNDecoderNet(nn.Module):
    """MLP -> latent 128 -> 1D CNN decoder -> 40 points"""
    def __init__(self):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(3, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, 128),
        )
        # Reshape 128 -> (16, 8), then upsample to 40 via transposed conv
        self.decoder = nn.Sequential(
            nn.ConvTranspose1d(16, 32, kernel_size=3, stride=2, padding=1, output_padding=0),  # 8 -> 16
            nn.SiLU(),
            nn.ConvTranspose1d(32, 16, kernel_size=3, stride=2, padding=1, output_padding=0),  # 16 -> 32
            nn.SiLU(),
            nn.Conv1d(16, 8, kernel_size=3, padding=1),
            nn.SiLU(),
            nn.AdaptiveAvgPool1d(F),  # -> 40
            nn.Conv1d(8, 1, kernel_size=1),
        )
    def forward(self, x):
        h = self.encoder(x)           # (B, 128)
        h = h.view(-1, 16, 8)         # (B, 16, 8)
        out = self.decoder(h)         # (B, 1, 40)
        return out.squeeze(1)         # (B, 40)

# ============================================================
# Loss functions
# ============================================================
def plain_mse(pred, true):
    return torch.mean((pred - true) ** 2)

def freq_aware_loss(pred, true):
    """Higher weight near resonance regions."""
    # Find resonance region for each sample in batch
    true_db = 20 * torch.log10(torch.clamp(true, min=0.0001))
    # Weight: 1 + boost near deep values (in dB space, deep = more negative)
    w = 1 + 5 * torch.sigmoid(-(true_db + 10) / 3)  # smooth weight centered at -10 dB
    w = w / w.mean()  # normalize
    return torch.mean(w * (pred - true) ** 2)

# ============================================================
# Training function
# ============================================================
def train_one_fold(X_tr, y_tr, X_va, y_va, model_class, loss_fn, pca_dim=None, lr=0.001, max_ep=2000, pat=200):
    """Returns metrics dict and trained model."""
    x_sc = StandardScaler().fit(X_tr)
    X_tr_s = x_sc.transform(X_tr).astype(np.float32)
    X_va_s = x_sc.transform(X_va).astype(np.float32)

    if model_class == PCANet:
        pca = PCA(n_components=pca_dim).fit(y_tr)
        y_tr_c = pca.transform(y_tr).astype(np.float32)
        c_sc = StandardScaler().fit(y_tr_c)
        y_tr_cs = c_sc.transform(y_tr_c).astype(np.float32)

        Xtr = torch.tensor(X_tr_s, device=device); Xva = torch.tensor(X_va_s, device=device)
        Ytr = torch.tensor(y_tr, device=device); Yva = torch.tensor(y_va, device=device)
        Ytr_cs_t = torch.tensor(y_tr_cs, device=device)
        pct = torch.tensor(pca.components_, dtype=torch.float32, device=device)
        pmt = torch.tensor(pca.mean_, dtype=torch.float32, device=device)
        cst = torch.tensor(c_sc.scale_, dtype=torch.float32, device=device)
        cmt = torch.tensor(c_sc.mean_, dtype=torch.float32, device=device)
        def dec(c): return (c * cst + cmt) @ pct + pmt
    else:
        Xtr = torch.tensor(X_tr_s, device=device); Xva = torch.tensor(X_va_s, device=device)
        Ytr = torch.tensor(y_tr, device=device); Yva = torch.tensor(y_va, device=device)

    model = model_class(pca_dim) if model_class == PCANet else model_class()
    model = model.to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-5)

    best_v, best_e, best_s, pat_cnt = float('inf'), 0, None, 0
    for ep in range(max_ep):
        model.train(); opt.zero_grad()
        if model_class == PCANet:
            pc_out = model(Xtr)
            pred = dec(pc_out[:, :pca_dim])
            aux_loss = 0.005 * torch.mean((pc_out[:, :pca_dim] - Ytr_cs_t[:, :pca_dim]) ** 2)
        else:
            pred = model(Xtr)
            aux_loss = 0.0
        loss = loss_fn(pred, Ytr) + aux_loss
        loss.backward(); opt.step()

        model.eval()
        with torch.no_grad():
            if model_class == PCANet:
                v_pred = dec(model(Xva)[:, :pca_dim])
            else:
                v_pred = model(Xva)
            vl = loss_fn(v_pred, Yva)
        if vl.item() < best_v - 1e-8:
            best_v = vl.item(); best_e = ep + 1
            best_s = copy.deepcopy(model.state_dict()); pat_cnt = 0
        else: pat_cnt += 1
        if pat_cnt >= pat: break

    model.load_state_dict(best_s); model.eval()
    with torch.no_grad():
        if model_class == PCANet:
            p_va = dec(model(Xva)[:, :pca_dim]).cpu().numpy()
        else:
            p_va = model(Xva).cpu().numpy()
    return metrics_db(y_va, p_va), best_e

# ============================================================
# EXPERIMENT 1: K-Fold comparison
# ============================================================
print("\n" + "=" * 60)
print("EXPERIMENT 1: K-Fold Comparison")
print("=" * 60)

kfold_results = {}
for K_val in [3, 5, 10]:
    np.random.seed(SEEDS[0]); torch.manual_seed(SEEDS[0])
    kf = KFold(n_splits=K_val, shuffle=True, random_state=SEEDS[0])
    fold_metrics = []
    for tr_i, va_i in kf.split(X):
        m, ep = train_one_fold(X[tr_i], y_target[tr_i], X[va_i], y_target[va_i],
                                PCANet, plain_mse, pca_dim=35, max_ep=2000, pat=200)
        fold_metrics.append(m)
    kfold_results[K_val] = {k: float(np.mean([m[k] for m in fold_metrics])) for k in ['MAE', 'RMSE', 'FreqErr_MHz', 'DepthErr']}
    print(f"  K={K_val}: MAE={kfold_results[K_val]['MAE']:.4f}, FreqErr={kfold_results[K_val]['FreqErr_MHz']:.0f} MHz")

# ============================================================
# EXPERIMENT 2: CNN Decoder vs PCA
# ============================================================
print("\n" + "=" * 60)
print("EXPERIMENT 2: CNN Decoder vs PCA")
print("=" * 60)

np.random.seed(SEEDS[0]); torch.manual_seed(SEEDS[0])
kf = KFold(n_splits=5, shuffle=True, random_state=SEEDS[0])

cnn_metrics = []
for tr_i, va_i in kf.split(X):
    m, ep = train_one_fold(X[tr_i], y_target[tr_i], X[va_i], y_target[va_i],
                            CNNDecoderNet, plain_mse, max_ep=2000, pat=200)
    cnn_metrics.append(m)
cnn_result = {k: float(np.mean([m[k] for m in cnn_metrics])) for k in ['MAE', 'RMSE', 'FreqErr_MHz', 'DepthErr']}
print(f"  CNN Decoder: MAE={cnn_result['MAE']:.4f}, FreqErr={cnn_result['FreqErr_MHz']:.0f} MHz")
print(f"  PCA (K=5):   MAE={kfold_results[5]['MAE']:.4f}, FreqErr={kfold_results[5]['FreqErr_MHz']:.0f} MHz")

# ============================================================
# EXPERIMENT 3: Frequency-Aware Loss
# ============================================================
print("\n" + "=" * 60)
print("EXPERIMENT 3: Frequency-Aware Loss")
print("=" * 60)

np.random.seed(SEEDS[0]); torch.manual_seed(SEEDS[0])
fa_metrics = []
for tr_i, va_i in kf.split(X):
    m, ep = train_one_fold(X[tr_i], y_target[tr_i], X[va_i], y_target[va_i],
                            PCANet, freq_aware_loss, pca_dim=35, max_ep=2000, pat=200)
    fa_metrics.append(m)
fa_result = {k: float(np.mean([m[k] for m in fa_metrics])) for k in ['MAE', 'RMSE', 'FreqErr_MHz', 'DepthErr']}
print(f"  Freq-Aware:  MAE={fa_result['MAE']:.4f}, FreqErr={fa_result['FreqErr_MHz']:.0f} MHz")
print(f"  Plain MSE:   MAE={kfold_results[5]['MAE']:.4f}, FreqErr={kfold_results[5]['FreqErr_MHz']:.0f} MHz")

# ============================================================
# EXPERIMENT 4: Ensemble (3 seeds)
# ============================================================
print("\n" + "=" * 60)
print("EXPERIMENT 4: Ensemble (3 seeds)")
print("=" * 60)

ensemble_preds = np.zeros((N, F))
for seed_idx, seed in enumerate(SEEDS):
    np.random.seed(seed); torch.manual_seed(seed)
    kf2 = KFold(n_splits=5, shuffle=True, random_state=seed)

    # Train final model on all data
    x_sc = StandardScaler().fit(X)
    X_s = x_sc.transform(X).astype(np.float32)

    ho_n = max(10, N // 10)
    ho_idx = np.random.RandomState(seed).choice(N, size=ho_n, replace=False)
    tr_idx = np.setdiff1d(np.arange(N), ho_idx)

    pca = PCA(n_components=35).fit(y_target)
    y_c = pca.transform(y_target).astype(np.float32)
    c_sc = StandardScaler().fit(y_c)
    y_cs = c_sc.transform(y_c).astype(np.float32)

    Xtr = torch.tensor(X_s[tr_idx], device=device); Xho = torch.tensor(X_s[ho_idx], device=device)
    Ytr = torch.tensor(y_target[tr_idx], device=device); Yho = torch.tensor(y_target[ho_idx], device=device)
    Ytr_cs = torch.tensor(y_cs[tr_idx], device=device)
    pct = torch.tensor(pca.components_, dtype=torch.float32, device=device)
    pmt = torch.tensor(pca.mean_, dtype=torch.float32, device=device)
    cst = torch.tensor(c_sc.scale_, dtype=torch.float32, device=device)
    cmt = torch.tensor(c_sc.mean_, dtype=torch.float32, device=device)
    def dec(c): return (c * cst + cmt) @ pct + pmt

    model = PCANet(35).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=1e-5)
    best_v, best_s, pat = float('inf'), None, 0
    for ep in range(2000):
        model.train(); opt.zero_grad()
        pc = model(Xtr); pred = dec(pc)
        loss = plain_mse(pred, Ytr) + 0.005 * torch.mean((pc - Ytr_cs) ** 2)
        loss.backward(); opt.step()
        model.eval()
        with torch.no_grad():
            vl = plain_mse(dec(model(Xho)), Yho)
        if vl.item() < best_v - 1e-8:
            best_v = vl.item(); best_s = copy.deepcopy(model.state_dict()); pat = 0
        else: pat += 1
        if pat >= 200: break
    model.load_state_dict(best_s); model.eval()
    with torch.no_grad():
        X_all_t = torch.tensor(X_s, device=device)
        ensemble_preds += dec(model(X_all_t)).cpu().numpy()
    print(f"  Seed {seed}: trained, best_val={best_v:.6f}")

ensemble_preds /= len(SEEDS)
ens_result = metrics_db(y_target, ensemble_preds)
print(f"\n  Ensemble (3 seeds): MAE={ens_result['MAE']:.4f}, FreqErr={ens_result['FreqErr_MHz']:.0f} MHz")

# Also single-seed baseline for fair comparison
np.random.seed(SEEDS[0]); torch.manual_seed(SEEDS[0])
x_sc_b = StandardScaler().fit(X); X_s_b = x_sc_b.transform(X).astype(np.float32)
ho_idx_b = np.random.RandomState(SEEDS[0]).choice(N, size=max(10, N//10), replace=False)
tr_idx_b = np.setdiff1d(np.arange(N), ho_idx_b)
pca_b = PCA(n_components=35).fit(y_target)
y_c_b = pca_b.transform(y_target).astype(np.float32); c_sc_b = StandardScaler().fit(y_c_b)
y_cs_b = c_sc_b.transform(y_c_b).astype(np.float32)
model_b = PCANet(35).to(device)
opt_b = torch.optim.AdamW(model_b.parameters(), lr=0.001, weight_decay=1e-5)
for ep in range(2000):
    model_b.train(); opt_b.zero_grad()
    Xtr_b = torch.tensor(X_s_b[tr_idx_b], device=device); Ytr_b = torch.tensor(y_target[tr_idx_b], device=device)
    Ytr_cs_b = torch.tensor(y_cs_b[tr_idx_b], device=device)
    pct_b = torch.tensor(pca_b.components_, dtype=torch.float32, device=device)
    pmt_b = torch.tensor(pca_b.mean_, dtype=torch.float32, device=device)
    cst_b = torch.tensor(c_sc_b.scale_, dtype=torch.float32, device=device)
    cmt_b = torch.tensor(c_sc_b.mean_, dtype=torch.float32, device=device)
    def dec_b(c): return (c * cst_b + cmt_b) @ pct_b + pmt_b
    pc = model_b(Xtr_b)
    loss = plain_mse(dec_b(pc), Ytr_b) + 0.005 * torch.mean((pc - Ytr_cs_b) ** 2)
    loss.backward(); opt_b.step()
    model_b.eval()
    Xho_b = torch.tensor(X_s_b[ho_idx_b], device=device); Yho_b = torch.tensor(y_target[ho_idx_b], device=device)
    with torch.no_grad(): vl = plain_mse(dec_b(model_b(Xho_b)), Yho_b)
    if vl.item() < best_v - 1e-8: best_v = vl.item(); best_s = copy.deepcopy(model_b.state_dict()); pat=0
    else: pat+=1
    if pat>=200: break

model_b.load_state_dict(best_s); model_b.eval()
with torch.no_grad():
    single_pred = dec_b(model_b(torch.tensor(X_s_b, device=device))).cpu().numpy()
single_result = metrics_db(y_target, single_pred)
print(f"  Single seed:       MAE={single_result['MAE']:.4f}, FreqErr={single_result['FreqErr_MHz']:.0f} MHz")

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "=" * 70)
print("FINAL SUMMARY — All Experiments")
print("=" * 70)
print(f"{'Experiment':<35} {'MAE(dB)':>10} {'FreqErr(MHz)':>14} {'DepthErr(dB)':>14}")
print("-" * 73)

# Baseline: PCA+K=5, plain MSE
base = kfold_results[5]
print(f"{'1a. Baseline (PCA, K=5, plain)':<35} {base['MAE']:>10.4f} {base['FreqErr_MHz']:>14.1f} {base['DepthErr']:>14.4f}")

for K_val in [3, 10]:
    r = kfold_results[K_val]
    print(f"1b. K={K_val} folds{'':<28} {r['MAE']:>10.4f} {r['FreqErr_MHz']:>14.1f} {r['DepthErr']:>14.4f}")

print(f"{'2. CNN Decoder':<35} {cnn_result['MAE']:>10.4f} {cnn_result['FreqErr_MHz']:>14.1f} {cnn_result['DepthErr']:>14.4f}")
print(f"{'3. Freq-Aware Loss':<35} {fa_result['MAE']:>10.4f} {fa_result['FreqErr_MHz']:>14.1f} {fa_result['DepthErr']:>14.4f}")
print(f"{'4a. Single Seed (final)':<35} {single_result['MAE']:>10.4f} {single_result['FreqErr_MHz']:>14.1f} {single_result['DepthErr']:>14.4f}")
print(f"{'4b. Ensemble (3 seeds)':<35} {ens_result['MAE']:>10.4f} {ens_result['FreqErr_MHz']:>14.1f} {ens_result['DepthErr']:>14.4f}")

# Best combo approximation
print(f"\n{'Best combo (est.)':<35} {'—':>10} {'—':>14} {'—':>14}")
print("  = CNN Decoder + Freq-Aware Loss + Ensemble (not run, estimated)")

# ============================================================
# FIGURE
# ============================================================
fig, axes = plt.subplots(1, 2, figsize=(16, 6))

# MAE comparison
experiments = ['K=3','K=5\n(base)','K=10','CNN\nDecoder','Freq\nLoss','Ensemble\n(3seed)']
mae_vals = [kfold_results[3]['MAE'], base['MAE'], kfold_results[10]['MAE'],
            cnn_result['MAE'], fa_result['MAE'], ens_result['MAE']]
freq_vals = [kfold_results[3]['FreqErr_MHz'], base['FreqErr_MHz'], kfold_results[10]['FreqErr_MHz'],
             cnn_result['FreqErr_MHz'], fa_result['FreqErr_MHz'], ens_result['FreqErr_MHz']]
depth_vals = [kfold_results[3]['DepthErr'], base['DepthErr'], kfold_results[10]['DepthErr'],
              cnn_result['DepthErr'], fa_result['DepthErr'], ens_result['DepthErr']]

x = np.arange(len(experiments))
w = 0.25
ax = axes[0]
bars1 = ax.bar(x - w, mae_vals, w, color='#2563EB', alpha=0.85, label='MAE (dB)')
ax.set_xticks(x); ax.set_xticklabels(experiments, fontsize=9)
ax.set_ylabel('MAE (dB)', fontsize=12); ax.set_title('Curve MAE by Method', fontsize=13, fontweight='bold')
for bar, v in zip(bars1, mae_vals):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.003, f'{v:.3f}', ha='center', fontsize=8)
ax.grid(alpha=0.2, axis='y')

ax = axes[1]
bars2 = ax.bar(x - w, freq_vals, w, color='#DC2626', alpha=0.85, label='FreqErr (MHz)')
bars3 = ax.bar(x + w, depth_vals, w, color='#F97316', alpha=0.85, label='DepthErr (dB)')
ax.set_xticks(x); ax.set_xticklabels(experiments, fontsize=9)
ax.set_ylabel('Error', fontsize=12); ax.set_title('Resonance Errors by Method', fontsize=13, fontweight='bold')
ax.legend(fontsize=9); ax.grid(alpha=0.2, axis='y')

plt.suptitle('Port_S3 — Improvement Benchmark (Linear Domain, 5-Fold CV)', fontsize=14, fontweight='bold', y=1.02)
plt.tight_layout()
plt.savefig(os.path.join(BASE, 'improvement_benchmark.png'), dpi=180, bbox_inches='tight')
plt.close()
print(f"\nFigure: improvement_benchmark.png")
print("Done!")
