"""
Step 1: Convert Port_S_data_2 parameters (1-8GHz) to Excel + Forward Model Training + 5-Fold CV
"""
import copy, warnings, os, re, json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.decomposition import PCA
from sklearn.model_selection import train_test_split, KFold
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

SEED = 42
np.random.seed(SEED)
torch.manual_seed(SEED)
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

print("=" * 60)
print("STEP 1: Data Conversion + Forward Model")
print("=" * 60)

# ==================== 1a. Convert to Excel ====================
print("\n--- 1a. Converting to Excel ---")
txt_path = os.path.join(os.path.dirname(OUT_DIR), "Port_S_data_2参数_1-8ghz.txt")

with open(txt_path) as f:
    header_line = f.readline().strip()

pattern = r'S\(1,1\),l3=([\d.]+), w4=([\d.]+)'
matches = re.findall(pattern, header_line)
print(f"Parsed {len(matches)} curves: l3={sorted(set(float(m[0]) for m in matches))}")
print(f"w4={sorted(set(float(m[1]) for m in matches))}")

df = pd.read_csv(txt_path, sep=r'\s+', skiprows=[0,1,2,3], header=None, dtype=float, engine='python')
freq_hz = df.iloc[:, 0].values
s11_data = df.iloc[:, 1:].values

rows = []
for idx, (l3, w4) in enumerate(matches):
    row = {'l3': float(l3), 'w4': float(w4), 'Unnamed: 15': np.nan}
    for j, f in enumerate(freq_hz):
        row[f'{f/1e9:.6f}'] = s11_data[j, idx]
    rows.append(row)

df_out = pd.DataFrame(rows)
freq_cols = sorted([c for c in df_out.columns if c not in ['l3','w4','Unnamed: 15']], key=lambda x: float(x))
df_out = df_out[['l3','w4','Unnamed: 15'] + freq_cols]

xlsx_path = os.path.join(OUT_DIR, "Port_S_data_2_1-8GHz.xlsx")
df_out.to_excel(xlsx_path, index=False, engine='openpyxl')
print(f"Excel saved: {xlsx_path}")
print(f"Shape: {df_out.shape[0]} rows x {df_out.shape[1]} cols")
print(f"Frequency: {float(freq_cols[0]):.4f} ~ {float(freq_cols[-1]):.4f} GHz, {len(freq_cols)} pts")

# ==================== 1b. Load data for training ====================
print("\n--- 1b. Preparing training data ---")
X = df_out[['l3', 'w4']].to_numpy(dtype=np.float32)
y_db = df_out.iloc[:, 3:].to_numpy(dtype=np.float32)
frequency = df_out.columns[3:].astype(float).to_numpy()

print(f"Samples: {len(X)}, S11 points: {y_db.shape[1]}")

# ==================== 1c. 5-Fold Cross Validation ====================
print("\n--- 1c. 5-Fold Cross Validation ---")
K = 5
kf = KFold(n_splits=K, shuffle=True, random_state=SEED)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {device}")

class PCANet(nn.Module):
    def __init__(self, in_dim, pca_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, pca_dim),
        )
    def forward(self, x):
        return self.net(x)

def weighted_loss(pred, true):
    w = 1 + 20 * (torch.clamp(-true, min=0) / 40) ** 2
    return torch.mean(w * (pred - true) ** 2)

def decode_fn(cs, cm, cc, pm):
    return lambda coeff_scaled: (coeff_scaled * cs + cm) @ cc + pm

def train_one_fold(X_tr, y_tr, X_va, y_va, fold_name=""):
    # Standardize
    x_sc = StandardScaler().fit(X_tr)
    X_tr_s = x_sc.transform(X_tr).astype(np.float32)
    X_va_s = x_sc.transform(X_va).astype(np.float32)

    # PCA
    pca_dim = min(20, len(X_tr) - 2)
    pca = PCA(n_components=pca_dim).fit(y_tr)
    y_tr_c = pca.transform(y_tr).astype(np.float32)
    y_va_c = pca.transform(y_va).astype(np.float32)
    c_sc = StandardScaler().fit(y_tr_c)
    y_tr_cs = c_sc.transform(y_tr_c).astype(np.float32)

    # Tensors
    X_tr_t = torch.tensor(X_tr_s, device=device)
    X_va_t = torch.tensor(X_va_s, device=device)
    y_tr_db_t = torch.tensor(y_tr, device=device)
    y_va_db_t = torch.tensor(y_va, device=device)
    y_tr_cs_t = torch.tensor(y_tr_cs, device=device)
    c_comp_t = torch.tensor(pca.components_, dtype=torch.float32, device=device)
    c_mean_t = torch.tensor(pca.mean_, dtype=torch.float32, device=device)
    cs_t = torch.tensor(c_sc.scale_, dtype=torch.float32, device=device)
    cm_t = torch.tensor(c_sc.mean_, dtype=torch.float32, device=device)
    decode = decode_fn(cs_t, cm_t, c_comp_t, c_mean_t)

    model = PCANet(2, pca_dim).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=1e-5)

    best_val, best_epoch, best_state, patience = float('inf'), 0, None, 0
    for epoch in range(5000):
        model.train()
        opt.zero_grad()
        pc = model(X_tr_t)
        pc_db = decode(pc)
        loss = weighted_loss(pc_db, y_tr_db_t) + 0.01 * torch.mean((pc - y_tr_cs_t) ** 2)
        loss.backward()
        opt.step()

        model.eval()
        with torch.no_grad():
            va_db = decode(model(X_va_t))
            va_loss = weighted_loss(va_db, y_va_db_t)

        if va_loss.item() < best_val - 1e-6:
            best_val = va_loss.item()
            best_epoch = epoch + 1
            best_state = copy.deepcopy(model.state_dict())
            patience = 0
        else:
            patience += 1
        if patience >= 500:
            break

    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        pred_tr = decode(model(X_tr_t)).cpu().numpy()
        pred_va = decode(model(X_va_t)).cpu().numpy()

    # Metrics
    def calc_m(true, pred):
        err = pred - true
        tmi = np.argmin(true, axis=1)
        pmi = np.argmin(pred, axis=1)
        rows = np.arange(len(true))
        return {
            'MAE': np.mean(np.abs(err)),
            'RMSE': np.sqrt(np.mean(err**2)),
            'Freq_MAE_MHz': np.mean(np.abs(frequency[pmi] - frequency[tmi])) * 1000,
            'Depth_MAE': np.mean(np.abs(pred[rows, pmi] - true[rows, tmi])),
            'pca_dim': pca_dim,
            'best_epoch': best_epoch,
        }

    return calc_m(y_tr, pred_tr), calc_m(y_va, pred_va), model, x_sc, pca, c_sc

# Run K-Fold
cv_train_metrics = []
cv_val_metrics = []
all_fold_models = []

for fold, (tr_idx, va_idx) in enumerate(kf.split(X)):
    print(f"  Fold {fold+1}/{K} ...", end=" ", flush=True)
    tr_m, va_m, model, x_sc, pca, c_sc = train_one_fold(
        X[tr_idx], y_db[tr_idx], X[va_idx], y_db[va_idx], f"fold{fold+1}"
    )
    cv_train_metrics.append(tr_m)
    cv_val_metrics.append(va_m)
    all_fold_models.append((model, x_sc, pca, c_sc))
    print(f"Train MAE={tr_m['MAE']:.4f}, Val MAE={va_m['MAE']:.4f}, Val FreqErr={va_m['Freq_MAE_MHz']:.1f} MHz")

# CV Summary
print("\n--- Cross Validation Summary ---")
for metric in ['MAE', 'RMSE', 'Freq_MAE_MHz', 'Depth_MAE']:
    vals = [m[metric] for m in cv_val_metrics]
    print(f"  Val {metric}: mean={np.mean(vals):.4f}, std={np.std(vals):.4f}, all={[f'{v:.4f}' for v in vals]}")

# ==================== 1d. Final model on all data ====================
print("\n--- 1d. Training final model on ALL data ---")
X_scaler_all = StandardScaler().fit(X)
X_scaled_all = X_scaler_all.transform(X).astype(np.float32)

pca_dim_final = 25
pca_all = PCA(n_components=pca_dim_final).fit(y_db)
y_coeff_all = pca_all.transform(y_db).astype(np.float32)
coeff_scaler_all = StandardScaler().fit(y_coeff_all)
y_coeff_scaled_all = coeff_scaler_all.transform(y_coeff_all).astype(np.float32)

X_all_t = torch.tensor(X_scaled_all, device=device)
y_db_all_t = torch.tensor(y_db, device=device)
y_coeff_all_t = torch.tensor(y_coeff_scaled_all, device=device)
cc_t = torch.tensor(pca_all.components_, dtype=torch.float32, device=device)
cm_t = torch.tensor(pca_all.mean_, dtype=torch.float32, device=device)
cs_t = torch.tensor(coeff_scaler_all.scale_, dtype=torch.float32, device=device)
cmean_t = torch.tensor(coeff_scaler_all.mean_, dtype=torch.float32, device=device)
decode_all = decode_fn(cs_t, cmean_t, cc_t, cm_t)

# Train with early stopping on a small holdout
holdout_idx = np.random.RandomState(SEED).choice(len(X), size=max(5, len(X)//10), replace=False)
train_idx = np.setdiff1d(np.arange(len(X)), holdout_idx)

X_tr_t = torch.tensor(X_scaled_all[train_idx], device=device)
X_ho_t = torch.tensor(X_scaled_all[holdout_idx], device=device)
y_tr_db_t2 = torch.tensor(y_db[train_idx], device=device)
y_ho_db_t = torch.tensor(y_db[holdout_idx], device=device)
y_tr_cs_t2 = torch.tensor(y_coeff_scaled_all[train_idx], device=device)

model_final = PCANet(2, pca_dim_final).to(device)
opt_final = torch.optim.AdamW(model_final.parameters(), lr=0.001, weight_decay=1e-5)

best_val_f, best_epoch_f, best_state_f, patience_f = float('inf'), 0, None, 0
for epoch in range(5000):
    model_final.train()
    opt_final.zero_grad()
    pc = model_final(X_tr_t)
    pc_db = decode_all(pc)
    loss = weighted_loss(pc_db, y_tr_db_t2) + 0.01 * torch.mean((pc - y_tr_cs_t2) ** 2)
    loss.backward()
    opt_final.step()

    model_final.eval()
    with torch.no_grad():
        ho_db = decode_all(model_final(X_ho_t))
        ho_loss = weighted_loss(ho_db, y_ho_db_t)
    if ho_loss.item() < best_val_f - 1e-6:
        best_val_f = ho_loss.item(); best_epoch_f = epoch + 1
        best_state_f = copy.deepcopy(model_final.state_dict()); patience_f = 0
    else:
        patience_f += 1
    if patience_f >= 500:
        break

model_final.load_state_dict(best_state_f)
print(f"Final model best epoch: {best_epoch_f}")
print(f"PCA dim: {pca_dim_final}, explained variance: {pca_all.explained_variance_ratio_.sum():.4%}")

# Predict all
model_final.eval()
with torch.no_grad():
    pred_all = decode_all(model_final(X_all_t)).cpu().numpy()

val_maes = np.mean(np.abs(pred_all - y_db), axis=1)
print(f"All-data curve MAE: mean={val_maes.mean():.4f}, median={np.median(val_maes):.4f}, max={val_maes.max():.4f}")

# ==================== 1e. Visualizations ====================
print("\n--- 1e. Generating figures ---")

fig, axes = plt.subplots(2, 3, figsize=(20, 12))

# A: CV MAE boxplot
ax = axes[0, 0]
cv_maes_by_fold = []
for fold, (tr_idx, va_idx) in enumerate(kf.split(X)):
    pred = predict_with_fold(all_fold_models[fold], X[va_idx])
    maes = np.mean(np.abs(pred - y_db[va_idx]), axis=1)
    cv_maes_by_fold.append(maes)
ax.boxplot(cv_maes_by_fold, labels=[f'Fold {i+1}' for i in range(K)])
ax.set_ylabel('Curve MAE (dB)'); ax.set_title('5-Fold CV — MAE Distribution')
ax.grid(alpha=0.3, axis='y')

# B: Best & Worst fit
ax = axes[0, 1]
best_i = np.argmin(val_maes); worst_i = np.argmax(val_maes)
bl3, bw4 = X[best_i]; wl3, ww4 = X[worst_i]
ax.plot(frequency, y_db[best_i], '-', color='#2563EB', lw=2, label=f'True (l3={bl3:.0f}, w4={bw4:.1f})')
ax.plot(frequency, pred_all[best_i], '--', color='#DC2626', lw=2, label=f'Pred (MAE={val_maes[best_i]:.3f})')
ax.set_xlabel('Freq (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title(f'Best Fit | MAE={val_maes[best_i]:.4f} dB'); ax.legend(fontsize=8); ax.grid(alpha=0.3)

ax = axes[0, 2]
ax.plot(frequency, y_db[worst_i], '-', color='#2563EB', lw=2, label=f'True (l3={wl3:.0f}, w4={ww4:.1f})')
ax.plot(frequency, pred_all[worst_i], '--', color='#DC2626', lw=2, label=f'Pred (MAE={val_maes[worst_i]:.3f})')
ax.set_xlabel('Freq (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title(f'Worst Fit | MAE={val_maes[worst_i]:.4f} dB'); ax.legend(fontsize=8); ax.grid(alpha=0.3)

# C: Random validation curves
ax = axes[1, 0]
rng = np.random.RandomState(42)
sample_i = rng.choice(len(X), size=min(8, len(X)), replace=False)
colors = plt.cm.tab10(np.linspace(0, 1, len(sample_i)))
for i, idx in enumerate(sample_i):
    ax.plot(frequency, y_db[idx], '-', color=colors[i], lw=1.5, alpha=0.8)
    ax.plot(frequency, pred_all[idx], '--', color=colors[i], lw=1.2, alpha=0.8)
ax.plot([],[],'-',color='gray',lw=2,label='True'); ax.plot([],[],'--',color='gray',lw=2,label='Pred')
ax.set_xlabel('Freq (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title('Random Samples — True vs Pred'); ax.legend(fontsize=8); ax.grid(alpha=0.3)

# D: Resonance error scatter
ax = axes[1, 1]
true_depth = np.array([y_db[i, np.argmin(y_db[i])] for i in range(len(X))])
pred_depth = np.array([pred_all[i, np.argmin(pred_all[i])] for i in range(len(X))])
true_fmin = np.array([frequency[np.argmin(y_db[i])] for i in range(len(X))])
pred_fmin = np.array([frequency[np.argmin(pred_all[i])] for i in range(len(X))])
sc = ax.scatter(true_fmin, true_depth, c=val_maes, cmap='RdYlGn_r', s=60, edgecolors='gray', linewidths=0.5)
plt.colorbar(sc, ax=ax, label='Curve MAE (dB)')
ax.set_xlabel('True Resonance Freq (GHz)'); ax.set_ylabel('True Resonance Depth (dB)')
ax.set_title('Resonance Points Colored by MAE'); ax.grid(alpha=0.3)

# E: MAE histogram
ax = axes[1, 2]
ax.hist(val_maes, bins=12, color='steelblue', edgecolor='white', alpha=0.85)
ax.axvline(np.mean(val_maes), color='red', linestyle='--', lw=2, label=f'Mean={np.mean(val_maes):.3f}')
ax.axvline(np.median(val_maes), color='darkorange', linestyle='--', lw=2, label=f'Median={np.median(val_maes):.3f}')
ax.set_xlabel('Curve MAE (dB)'); ax.set_title('All-Sample MAE Distribution')
ax.legend(fontsize=9); ax.grid(alpha=0.3, axis='y')

plt.suptitle('Forward Model — Port_S2 (l3,w4 -> S11, 1-8GHz)', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig_path = os.path.join(OUT_DIR, 'forward_model_results.png')
plt.savefig(fig_path, dpi=150, bbox_inches='tight')
plt.close()
print(f"Figure saved: {fig_path}")

# ==================== 1f. Save model ====================
model_pkg = {
    "model_state": model_final.state_dict(),
    "param_cols": ['l3', 'w4'],
    "x_mean": X_scaler_all.mean_,
    "x_scale": X_scaler_all.scale_,
    "coeff_mean": coeff_scaler_all.mean_,
    "coeff_scale": coeff_scaler_all.scale_,
    "pca_components": pca_all.components_,
    "pca_mean": pca_all.mean_,
    "frequency": frequency,
    "input_dim": 2,
    "pca_dim": pca_dim_final,
}
model_path = os.path.join(OUT_DIR, 'model_forward_port_s2.pth')
torch.save(model_pkg, model_path)
print(f"Model saved: {model_path}")

# CV summary to JSON
cv_summary = {
    "K": K,
    "n_samples": len(X),
    "freq_range": f"{frequency[0]:.4f}-{frequency[-1]:.4f} GHz",
    "n_freq_points": len(frequency),
    "cv_val_metrics": cv_val_metrics,
    "final_model_best_epoch": best_epoch_f,
    "final_model_pca_dim": pca_dim_final,
    "final_model_pca_var": float(pca_all.explained_variance_ratio_.sum()),
}
with open(os.path.join(OUT_DIR, 'forward_cv_summary.json'), 'w') as f:
    json.dump(cv_summary, f, indent=2, default=str)
print("CV summary saved.")

print("\n=== STEP 1 DONE ===")

# Helper function for predict
def predict_with_fold(fold_tuple, X_new):
    model, x_sc, pca, c_sc = fold_tuple
    model.eval()
    X_s = x_sc.transform(X_new).astype(np.float32)
    X_t = torch.tensor(X_s, device=device)
    cc_t = torch.tensor(pca.components_, dtype=torch.float32, device=device)
    cm_t = torch.tensor(pca.mean_, dtype=torch.float32, device=device)
    cs_t = torch.tensor(c_sc.scale_, dtype=torch.float32, device=device)
    cmean_t = torch.tensor(c_sc.mean_, dtype=torch.float32, device=device)
    with torch.no_grad():
        return ((model(X_t) * cs_t + cmean_t) @ cc_t + cm_t).cpu().numpy()
