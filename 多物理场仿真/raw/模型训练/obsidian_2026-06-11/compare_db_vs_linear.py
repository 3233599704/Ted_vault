"""
Head-to-head: dB domain vs Linear domain training on Port_S3
Same data, same architecture, different y-domain.
"""
import copy, warnings, os
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

# Load data
df = pd.read_excel(os.path.join(BASE, "Port_S3_data.xlsx"))
X = df[['l3','w4','h']].to_numpy(dtype=np.float32)
y_db = df.iloc[:, 4:].to_numpy(dtype=np.float32)
frequency = df.columns[4:].astype(float).to_numpy()
N, F = len(X), len(frequency)
print(f"Data: {N} samples, {F} freq points, {frequency[0]:.2f}-{frequency[-1]:.2f} GHz")

# Convert to linear domain
y_linear = 10 ** (y_db / 20.0)  # S-parameter: dB -> linear magnitude

print(f"dB range:    {y_db.min():.2f} ~ {y_db.max():.2f} dB")
print(f"Linear range: {y_linear.min():.4f} ~ {y_linear.max():.4f}")
print(f"dB at resonance (sample 0): {y_db[0].min():.2f} dB -> linear = {y_linear[0].min():.5f}")
print()

# ============================================================
# Define network & training
# ============================================================
freq_t = torch.tensor(frequency, dtype=torch.float32, device=device)

class ForwardNet(nn.Module):
    def __init__(self, pca_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(3, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, pca_dim),
        )
    def forward(self, x): return self.net(x)

def train_and_eval(y_target, domain_name, max_epochs=3000, patience=300):
    """Train 5-fold CV and final model on y_target. Returns metrics dict."""
    print(f"\n{'='*50}")
    print(f"Training in {domain_name} domain")
    print(f"{'='*50}")

    kf = KFold(n_splits=5, shuffle=True, random_state=SEED)
    cv_metrics = []

    for fold, (tr_i, va_i) in enumerate(kf.split(X)):
        print(f"  Fold {fold+1}/5 ...", end=" ", flush=True)

        x_sc = StandardScaler().fit(X[tr_i])
        X_tr_s = x_sc.transform(X[tr_i]).astype(np.float32)
        X_va_s = x_sc.transform(X[va_i]).astype(np.float32)

        pdim = 35
        pca = PCA(n_components=pdim).fit(y_target[tr_i])
        y_tr_c = pca.transform(y_target[tr_i]).astype(np.float32)
        c_sc = StandardScaler().fit(y_tr_c)
        y_tr_cs = c_sc.transform(y_tr_c).astype(np.float32)

        Xtr = torch.tensor(X_tr_s, device=device); Xva = torch.tensor(X_va_s, device=device)
        Ytr = torch.tensor(y_target[tr_i], device=device); Yva = torch.tensor(y_target[va_i], device=device)
        Ytr_cs = torch.tensor(y_tr_cs, device=device)
        pct = torch.tensor(pca.components_, dtype=torch.float32, device=device)
        pmt = torch.tensor(pca.mean_, dtype=torch.float32, device=device)
        cst = torch.tensor(c_sc.scale_, dtype=torch.float32, device=device)
        cmt = torch.tensor(c_sc.mean_, dtype=torch.float32, device=device)
        def dec(c): return (c * cst + cmt) @ pct + pmt

        model = ForwardNet(pdim).to(device)
        opt = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=1e-5)
        best_v, best_e, best_s, pat = float('inf'), 0, None, 0
        for ep in range(max_epochs):
            model.train(); opt.zero_grad()
            pc = model(Xtr)
            # Simple MSE (no weighting — fair comparison)
            loss = torch.mean((dec(pc) - Ytr) ** 2) + 0.005 * torch.mean((pc - Ytr_cs) ** 2)
            loss.backward(); opt.step()
            model.eval()
            with torch.no_grad():
                v_db = dec(model(Xva)); vl = torch.mean((v_db - Yva) ** 2)
            if vl.item() < best_v - 1e-8:
                best_v = vl.item(); best_e = ep + 1
                best_s = copy.deepcopy(model.state_dict()); pat = 0
            else: pat += 1
            if pat >= patience: break

        model.load_state_dict(best_s); model.eval()
        with torch.no_grad(): p_va = dec(model(Xva)).cpu().numpy()
        cv_metrics.append({'best_epoch': best_e, 'pred': p_va, 'true': y_target[va_i]})
        # MAE in dB space for fair comparison
        if domain_name == "LINEAR":
            p_va_db = 20 * np.log10(np.clip(p_va, 0.0001, None))
            t_db = 20 * np.log10(np.clip(y_target[va_i], 0.0001, None))
            mae = np.mean(np.abs(p_va_db - t_db))
        else:
            mae = np.mean(np.abs(p_va - y_target[va_i]))
        print(f"MAE(dB)={mae:.4f}, Ep={best_e}")

    # Final model
    x_sc_f = StandardScaler().fit(X); X_s_f = x_sc_f.transform(X).astype(np.float32)
    pca_f = PCA(n_components=35).fit(y_target); y_c_f = pca_f.transform(y_target).astype(np.float32)
    c_sc_f = StandardScaler().fit(y_c_f); y_cs_f = c_sc_f.transform(y_c_f).astype(np.float32)

    ho_n = max(10, N // 10)
    ho_idx = np.random.RandomState(SEED).choice(N, size=ho_n, replace=False)
    tr_idx_f = np.setdiff1d(np.arange(N), ho_idx)

    Xtr_f = torch.tensor(X_s_f[tr_idx_f], device=device); Xho_f = torch.tensor(X_s_f[ho_idx], device=device)
    Ytr_f = torch.tensor(y_target[tr_idx_f], device=device); Yho_f = torch.tensor(y_target[ho_idx], device=device)
    Ytr_cs_f = torch.tensor(y_cs_f[tr_idx_f], device=device)
    pct_f = torch.tensor(pca_f.components_, dtype=torch.float32, device=device)
    pmt_f = torch.tensor(pca_f.mean_, dtype=torch.float32, device=device)
    cst_f = torch.tensor(c_sc_f.scale_, dtype=torch.float32, device=device)
    cmt_f = torch.tensor(c_sc_f.mean_, dtype=torch.float32, device=device)
    def dec_f(c): return (c * cst_f + cmt_f) @ pct_f + pmt_f

    model_f = ForwardNet(35).to(device)
    opt_f = torch.optim.AdamW(model_f.parameters(), lr=0.001, weight_decay=1e-5)
    best_v, best_e, best_s, pat = float('inf'), 0, None, 0
    for ep in range(max_epochs):
        model_f.train(); opt_f.zero_grad()
        pc = model_f(Xtr_f)
        loss = torch.mean((dec_f(pc) - Ytr_f) ** 2) + 0.005 * torch.mean((pc - Ytr_cs_f) ** 2)
        loss.backward(); opt_f.step()
        model_f.eval()
        with torch.no_grad():
            ho_db = dec_f(model_f(Xho_f)); vl = torch.mean((ho_db - Yho_f) ** 2)
        if vl.item() < best_v - 1e-8:
            best_v = vl.item(); best_e = ep + 1
            best_s = copy.deepcopy(model_f.state_dict()); pat = 0
        else: pat += 1
        if pat >= patience: break

    model_f.load_state_dict(best_s); model_f.eval()
    with torch.no_grad():
        X_all_t = torch.tensor(X_s_f, device=device)
        pred_all = dec_f(model_f(X_all_t)).cpu().numpy()

    # Compute metrics in dB space
    if domain_name == "LINEAR":
        pred_all_db = 20 * np.log10(np.clip(pred_all, 0.0001, None))
        true_db = 20 * np.log10(np.clip(y_target, 0.0001, None))
        all_maes_db = np.mean(np.abs(pred_all_db - true_db), axis=1)
        tmi = np.argmin(true_db, axis=1); pmi = np.argmin(pred_all_db, axis=1)
        freq_errs = np.abs(frequency[pmi] - frequency[tmi]) * 1000
        depth_errs = np.abs(pred_all_db[np.arange(N), pmi] - true_db[np.arange(N), tmi])
    else:
        all_maes_db = np.mean(np.abs(pred_all - y_target), axis=1)
        tmi = np.argmin(y_target, axis=1); pmi = np.argmin(pred_all, axis=1)
        freq_errs = np.abs(frequency[pmi] - frequency[tmi]) * 1000
        depth_errs = np.abs(pred_all[np.arange(N), pmi] - y_target[np.arange(N), tmi])

    return {
        'domain': domain_name,
        'best_epoch': best_e,
        'mae_mean': float(np.mean(all_maes_db)),
        'mae_median': float(np.median(all_maes_db)),
        'mae_max': float(np.max(all_maes_db)),
        'freq_err_mean': float(np.mean(freq_errs)),
        'freq_err_median': float(np.median(freq_errs)),
        'depth_err_mean': float(np.mean(depth_errs)),
        'depth_err_median': float(np.median(depth_errs)),
        'pred_all': pred_all,
        'pred_all_db': pred_all_db if domain_name == "LINEAR" else pred_all,
        'all_maes_db': all_maes_db,
        'freq_errs': freq_errs,
        'depth_errs': depth_errs,
    }

# ============================================================
# Train both
# ============================================================
results_db = train_and_eval(y_db, "dB")
results_linear = train_and_eval(y_linear, "LINEAR")

# ============================================================
# Comparison
# ============================================================
print("\n" + "=" * 70)
print("HEAD-TO-HEAD COMPARISON")
print("=" * 70)
print(f"{'Metric':<30} {'dB Domain':>15} {'Linear Domain':>15} {'Winner':>10}")
print("-" * 70)

for metric, key, unit in [
    ('Curve MAE (mean)', 'mae_mean', 'dB'),
    ('Curve MAE (median)', 'mae_median', 'dB'),
    ('Curve MAE (max)', 'mae_max', 'dB'),
    ('Freq Error (mean)', 'freq_err_mean', 'MHz'),
    ('Freq Error (median)', 'freq_err_median', 'MHz'),
    ('Depth Error (mean)', 'depth_err_mean', 'dB'),
    ('Depth Error (median)', 'depth_err_median', 'dB'),
]:
    v_db = results_db[key]
    v_lin = results_linear[key]
    if 'Freq' in metric or 'Hz' in metric:
        better = 'dB' if v_db < v_lin else 'Linear'
    else:
        better = 'dB' if v_db < v_lin else 'Linear'
    print(f"{metric:<30} {v_db:>12.4f} {unit} {v_lin:>12.4f} {unit} {better:>10}")

# ============================================================
# Visualization
# ============================================================
fig, axes = plt.subplots(2, 3, figsize=(20, 12))

# A: MAE histogram comparison
ax = axes[0, 0]
bins = np.linspace(0, max(results_db['mae_max'], results_linear['mae_max']), 25)
ax.hist(results_db['all_maes_db'], bins=bins, alpha=0.6, color='#2563EB', edgecolor='white', label=f"dB: mean={results_db['mae_mean']:.3f}")
ax.hist(results_linear['all_maes_db'], bins=bins, alpha=0.6, color='#DC2626', edgecolor='white', label=f"Linear: mean={results_linear['mae_mean']:.3f}")
ax.axvline(results_db['mae_mean'], color='#2563EB', linestyle='--', lw=2)
ax.axvline(results_linear['mae_mean'], color='#DC2626', linestyle='--', lw=2)
ax.set_xlabel('Curve MAE (dB)', fontsize=12); ax.set_title('MAE Distribution', fontsize=13, fontweight='bold')
ax.legend(fontsize=10); ax.grid(alpha=0.2, axis='y')

# B: Depth error histogram
ax = axes[0, 1]
bins2 = np.linspace(0, max(results_db['depth_errs'].max(), results_linear['depth_errs'].max()), 25)
ax.hist(results_db['depth_errs'], bins=bins2, alpha=0.6, color='#2563EB', edgecolor='white', label=f"dB: mean={results_db['depth_err_mean']:.3f}")
ax.hist(results_linear['depth_errs'], bins=bins2, alpha=0.6, color='#DC2626', edgecolor='white', label=f"Linear: mean={results_linear['depth_err_mean']:.3f}")
ax.axvline(results_db['depth_err_mean'], color='#2563EB', linestyle='--', lw=2)
ax.axvline(results_linear['depth_err_mean'], color='#DC2626', linestyle='--', lw=2)
ax.set_xlabel('Depth Error (dB)', fontsize=12); ax.set_title('Resonance Depth Error', fontsize=13, fontweight='bold')
ax.legend(fontsize=10); ax.grid(alpha=0.2, axis='y')

# C: Best fit — dB
ax = axes[0, 2]
bi = np.argmin(results_db['all_maes_db'])
ax.plot(frequency, y_db[bi], '-', color='#2563EB', lw=2.5, label='True')
ax.plot(frequency, results_db['pred_all_db'][bi], '--', color='#DC2626', lw=2, label='Pred (dB domain)')
ax.set_xlabel('Freq (GHz)', fontsize=10); ax.set_ylabel('S11 (dB)', fontsize=10)
ax.set_title(f'dB Domain Best | MAE={results_db["all_maes_db"][bi]:.4f}', fontsize=11, fontweight='bold')
ax.legend(fontsize=8); ax.grid(alpha=0.2)

# D: Best fit — Linear
ax = axes[1, 0]
bi_l = np.argmin(results_linear['all_maes_db'])
ax.plot(frequency, y_db[bi_l], '-', color='#2563EB', lw=2.5, label='True')
ax.plot(frequency, results_linear['pred_all_db'][bi_l], '--', color='#DC2626', lw=2, label='Pred (linear domain)')
ax.set_xlabel('Freq (GHz)', fontsize=10); ax.set_ylabel('S11 (dB)', fontsize=10)
ax.set_title(f'Linear Domain Best | MAE={results_linear["all_maes_db"][bi_l]:.4f}', fontsize=11, fontweight='bold')
ax.legend(fontsize=8); ax.grid(alpha=0.2)

# E: Worst fit — dB
ax = axes[1, 1]
wi = np.argmax(results_db['all_maes_db'])
ax.plot(frequency, y_db[wi], '-', color='#2563EB', lw=2.5, label='True')
ax.plot(frequency, results_db['pred_all_db'][wi], '--', color='#DC2626', lw=2, label='Pred (dB domain)')
ax.set_xlabel('Freq (GHz)', fontsize=10); ax.set_ylabel('S11 (dB)', fontsize=10)
ax.set_title(f'dB Domain Worst | MAE={results_db["all_maes_db"][wi]:.4f}', fontsize=11, fontweight='bold')
ax.legend(fontsize=8); ax.grid(alpha=0.2)

# F: Frequency error comparison
ax = axes[1, 2]
ax.boxplot([results_db['freq_errs'], results_linear['freq_errs']],
           tick_labels=['dB Domain', 'Linear Domain'])
ax.set_ylabel('Frequency Error (MHz)', fontsize=12)
ax.set_title('Resonance Frequency Error', fontsize=13, fontweight='bold')
ax.grid(alpha=0.2, axis='y')

plt.suptitle('dB Domain vs Linear Domain — Port_S3 (No Weighted Loss)', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig_path = os.path.join(BASE, 'db_vs_linear_comparison.png')
plt.savefig(fig_path, dpi=180, bbox_inches='tight')
plt.close()
print(f"\nFigure saved: {fig_path}")

# Print conclusion
print("\n" + "=" * 70)
print("KEY TAKEAWAY")
print("=" * 70)
if results_db['mae_mean'] < results_linear['mae_mean']:
    print(f"  dB domain wins: MAE {results_db['mae_mean']:.3f} vs {results_linear['mae_mean']:.3f} dB")
else:
    print(f"  Linear domain wins: MAE {results_linear['mae_mean']:.3f} vs {results_db['mae_mean']:.3f} dB")

if results_db['depth_err_mean'] < results_linear['depth_err_mean']:
    print(f"  dB domain better at resonance depth: {results_db['depth_err_mean']:.3f} vs {results_linear['depth_err_mean']:.3f} dB")
else:
    print(f"  Linear domain better at resonance depth: {results_linear['depth_err_mean']:.3f} vs {results_db['depth_err_mean']:.3f} dB")

if results_db['freq_err_mean'] < results_linear['freq_err_mean']:
    print(f"  dB domain better at resonance frequency: {results_db['freq_err_mean']:.1f} vs {results_linear['freq_err_mean']:.1f} MHz")
else:
    print(f"  Linear domain better at resonance frequency: {results_linear['freq_err_mean']:.1f} vs {results_db['freq_err_mean']:.1f} MHz")

print(f"\n  Why? In linear domain, all 'weight' goes to shallow regions (~1.0).")
print(f"  Deep resonance valleys (~0.02) contribute almost nothing to MSE.")
print(f"  dB domain compresses the range and treats all regions more equally.")
