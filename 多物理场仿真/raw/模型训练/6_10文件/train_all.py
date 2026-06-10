"""
Master script: Port_S2 1-8GHz — Convert + Forward + Inverse + K-Fold CV
All outputs go to the same directory as this script.
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
from sklearn.model_selection import KFold
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

SEED = 42
np.random.seed(SEED); torch.manual_seed(SEED)
BASE = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(os.path.dirname(BASE), "Port_S_data_2参数_1-8ghz.txt")

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {device}\n")

# ========== 1. Convert to Excel ==========
print("=" * 50)
print("1. Convert data to Excel")

with open(SRC) as f:
    header = f.readline().strip()
matches = re.findall(r'S\(1,1\),l3=([\d.]+), w4=([\d.]+)', header)
l3_vals = sorted(set(float(m[0]) for m in matches))
w4_vals = sorted(set(float(m[1]) for m in matches))
print(f"   l3: {l3_vals}")
print(f"   w4: {w4_vals}")
print(f"   Total curves: {len(matches)}")

df = pd.read_csv(SRC, sep=r'\s+', skiprows=[0,1,2,3], header=None, dtype=float, engine='python')
freq_hz = df.iloc[:, 0].values
s11_raw = df.iloc[:, 1:].values
print(f"   Data: {df.shape[0]} freq points x {s11_raw.shape[1]} curves")
print(f"   Frequency: {freq_hz[0]/1e9:.4f} ~ {freq_hz[-1]/1e9:.4f} GHz")

rows = []
for idx, (l3, w4) in enumerate(matches):
    row = {'l3': float(l3), 'w4': float(w4), 'Unnamed: 15': np.nan}
    for j, f in enumerate(freq_hz):
        row[f'{f/1e9:.6f}'] = s11_raw[j, idx]
    rows.append(row)

df_out = pd.DataFrame(rows)
fcols = sorted([c for c in df_out.columns if c not in ['l3','w4','Unnamed: 15']], key=lambda x: float(x))
df_out = df_out[['l3','w4','Unnamed: 15'] + fcols]

xlsx_path = os.path.join(BASE, "Port_S_data_2_1-8GHz.xlsx")
df_out.to_excel(xlsx_path, index=False, engine='openpyxl')
print(f"   Saved: {xlsx_path}\n")

# Prepare arrays
X = df_out[['l3', 'w4']].to_numpy(dtype=np.float32)
y_db = df_out.iloc[:, 3:].to_numpy(dtype=np.float32)
frequency = df_out.columns[3:].astype(float).to_numpy()
N = len(X)
print(f"   Samples: {N}, S11 dim: {y_db.shape[1]}")

# ========== 2. Forward Model + 5-Fold CV ==========
print("\n" + "=" * 50)
print("2. Forward Model — 5-Fold CV")

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

def wloss(pred, true):
    w = 1 + 20 * (torch.clamp(-true, min=0) / 40) ** 2
    return torch.mean(w * (pred - true) ** 2)

def calc_metrics(true, pred):
    err = pred - true
    tmi = np.argmin(true, axis=1); pmi = np.argmin(pred, axis=1)
    rows = np.arange(len(true))
    return {
        'MAE': float(np.mean(np.abs(err))),
        'RMSE': float(np.sqrt(np.mean(err**2))),
        'Freq_MAE_MHz': float(np.mean(np.abs(frequency[pmi] - frequency[tmi])) * 1000),
        'Depth_MAE': float(np.mean(np.abs(pred[rows, pmi] - true[rows, tmi]))),
    }

K = 5
kf = KFold(n_splits=K, shuffle=True, random_state=SEED)
cv_tr, cv_va = [], []

for fold, (tr_i, va_i) in enumerate(kf.split(X)):
    # Prepare fold data
    x_tr, x_va = X[tr_i], X[va_i]
    y_tr, y_va = y_db[tr_i], y_db[va_i]

    x_sc = StandardScaler().fit(x_tr)
    x_tr_s = x_sc.transform(x_tr).astype(np.float32)
    x_va_s = x_sc.transform(x_va).astype(np.float32)

    pdim = min(20, len(tr_i) - 2)
    pca = PCA(n_components=pdim).fit(y_tr)
    y_tr_c = pca.transform(y_tr).astype(np.float32)
    c_sc = StandardScaler().fit(y_tr_c)
    y_tr_cs = c_sc.transform(y_tr_c).astype(np.float32)

    # Tensors
    Xtr = torch.tensor(x_tr_s, device=device); Xva = torch.tensor(x_va_s, device=device)
    Ytr = torch.tensor(y_tr, device=device); Yva = torch.tensor(y_va, device=device)
    Ytr_cs = torch.tensor(y_tr_cs, device=device)
    pc_t = torch.tensor(pca.components_, dtype=torch.float32, device=device)
    pm_t = torch.tensor(pca.mean_, dtype=torch.float32, device=device)
    cs_t = torch.tensor(c_sc.scale_, dtype=torch.float32, device=device)
    cm_t = torch.tensor(c_sc.mean_, dtype=torch.float32, device=device)

    def dec(cs):
        return (cs * cs_t + cm_t) @ pc_t + pm_t

    model = ForwardNet(pdim).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=1e-5)

    best_v, best_e, best_s, pat = float('inf'), 0, None, 0
    for ep in range(5000):
        model.train(); opt.zero_grad()
        pc = model(Xtr)
        loss = wloss(dec(pc), Ytr) + 0.01 * torch.mean((pc - Ytr_cs) ** 2)
        loss.backward(); opt.step()

        model.eval()
        with torch.no_grad():
            v_db = dec(model(Xva)); vl = wloss(v_db, Yva)
        if vl.item() < best_v - 1e-6:
            best_v = vl.item(); best_e = ep + 1
            best_s = copy.deepcopy(model.state_dict()); pat = 0
        else:
            pat += 1
        if pat >= 500:
            break

    model.load_state_dict(best_s); model.eval()
    with torch.no_grad():
        p_tr = dec(model(Xtr)).cpu().numpy()
        p_va = dec(model(Xva)).cpu().numpy()

    tr_m = calc_metrics(y_tr, p_tr); va_m = calc_metrics(y_va, p_va)
    cv_tr.append(tr_m); cv_va.append(va_m)
    print(f"   Fold {fold+1}: Train MAE={tr_m['MAE']:.4f}, Val MAE={va_m['MAE']:.4f}, Val FreqErr={va_m['Freq_MAE_MHz']:.1f} MHz, PCA={pdim}, BestEp={best_e}")

# CV summary
print("\n   5-Fold CV Summary:")
for k in ['MAE', 'RMSE', 'Freq_MAE_MHz', 'Depth_MAE']:
    vals = [m[k] for m in cv_va]
    print(f"     Val {k}: {np.mean(vals):.4f} ± {np.std(vals):.4f}")

# Train final model on all data
print("\n   Training final forward model on all data...")
x_sc_final = StandardScaler().fit(X)
X_s_final = x_sc_final.transform(X).astype(np.float32)

pca_dim_final = 25
pca_final = PCA(n_components=pca_dim_final).fit(y_db)
y_c_final = pca_final.transform(y_db).astype(np.float32)
c_sc_final = StandardScaler().fit(y_c_final)
y_cs_final = c_sc_final.transform(y_c_final).astype(np.float32)

# Small holdout for early stopping
ho_n = max(5, N // 10)
ho_idx = np.random.RandomState(SEED).choice(N, size=ho_n, replace=False)
tr_idx = np.setdiff1d(np.arange(N), ho_idx)

Xtr_f = torch.tensor(X_s_final[tr_idx], device=device)
Xho_f = torch.tensor(X_s_final[ho_idx], device=device)
Ytr_f = torch.tensor(y_db[tr_idx], device=device)
Yho_f = torch.tensor(y_db[ho_idx], device=device)
Ytr_cs_f = torch.tensor(y_cs_final[tr_idx], device=device)

pc_t_f = torch.tensor(pca_final.components_, dtype=torch.float32, device=device)
pm_t_f = torch.tensor(pca_final.mean_, dtype=torch.float32, device=device)
cs_t_f = torch.tensor(c_sc_final.scale_, dtype=torch.float32, device=device)
cm_t_f = torch.tensor(c_sc_final.mean_, dtype=torch.float32, device=device)

def dec_final(cs):
    return (cs * cs_t_f + cm_t_f) @ pc_t_f + pm_t_f

model_f = ForwardNet(pca_dim_final).to(device)
opt_f = torch.optim.AdamW(model_f.parameters(), lr=0.001, weight_decay=1e-5)
best_v_f, best_e_f, best_s_f, pat_f = float('inf'), 0, None, 0

for ep in range(5000):
    model_f.train(); opt_f.zero_grad()
    pc = model_f(Xtr_f)
    loss = wloss(dec_final(pc), Ytr_f) + 0.01 * torch.mean((pc - Ytr_cs_f) ** 2)
    loss.backward(); opt_f.step()

    model_f.eval()
    with torch.no_grad():
        ho_db = dec_final(model_f(Xho_f)); vl = wloss(ho_db, Yho_f)
    if vl.item() < best_v_f - 1e-6:
        best_v_f = vl.item(); best_e_f = ep + 1
        best_s_f = copy.deepcopy(model_f.state_dict()); pat_f = 0
    else:
        pat_f += 1
    if pat_f >= 500:
        break

model_f.load_state_dict(best_s_f)
model_f.eval()
with torch.no_grad():
    X_all_t = torch.tensor(X_s_final, device=device)
    pred_all = dec_final(model_f(X_all_t)).cpu().numpy()

all_maes = np.mean(np.abs(pred_all - y_db), axis=1)
print(f"   PCA var: {pca_final.explained_variance_ratio_.sum():.4%}, Best ep: {best_e_f}")
print(f"   All-data MAE: mean={all_maes.mean():.4f}, median={np.median(all_maes):.4f}, max={all_maes.max():.4f}")

# Save forward model
torch.save({
    "model_state": model_f.state_dict(),
    "param_cols": ['l3', 'w4'],
    "x_mean": x_sc_final.mean_, "x_scale": x_sc_final.scale_,
    "coeff_mean": c_sc_final.mean_, "coeff_scale": c_sc_final.scale_,
    "pca_components": pca_final.components_, "pca_mean": pca_final.mean_,
    "frequency": frequency, "input_dim": 2, "pca_dim": pca_dim_final,
}, os.path.join(BASE, "model_forward.pth"))
print("   Forward model saved.")

# ========== 3. Inverse Model + 5-Fold CV ==========
print("\n" + "=" * 50)
print("3. Inverse Model (Tandem) — 5-Fold CV")

# Feature extraction from S11 curves
def extract_features(curves):
    """Extract rich features from S11 curves for inverse model input."""
    feats = []
    for i in range(len(curves)):
        c = curves[i]
        f_min_idx = np.argmin(c)
        f_res = frequency[f_min_idx]
        s11_min = c[f_min_idx]
        # -10 dB bandwidth
        mask_10db = c <= -10
        bw_10db = frequency[mask_10db][-1] - frequency[mask_10db][0] if mask_10db.sum() > 1 else 0.0
        # -3 dB bandwidth
        mask_3db = c <= -3
        bw_3db = frequency[mask_3db][-1] - frequency[mask_3db][0] if mask_3db.sum() > 1 else 0.0
        # Curve integral
        integral = np.trapz(c, frequency)
        # Mean & std
        f_mean = np.mean(c); f_std = np.std(c)
        feats.append([f_res, s11_min, bw_10db, bw_3db, integral, f_mean, f_std])
    return np.array(feats, dtype=np.float32)

# Prepare inverse input: PCA coefficients + handcrafted features
pca_dim_inv = 25
pca_inv = PCA(n_components=pca_dim_inv).fit(y_db)
y_pca = pca_inv.transform(y_db).astype(np.float32)
hand_feats = extract_features(y_db)
feat_scaler = StandardScaler().fit(hand_feats)
hand_feats_s = feat_scaler.transform(hand_feats).astype(np.float32)

# Inverse input = [PCA coeffs | handcrafted features]
X_inv_raw = np.concatenate([y_pca, hand_feats_s], axis=1)
inp_dim = X_inv_raw.shape[1]

# Inverse targets: l3, w4 normalized to [0, 1]
y_params = X.copy()  # (N, 2)
y_min = y_params.min(axis=0); y_max = y_params.max(axis=0)
y_params_norm = (y_params - y_min) / (y_max - y_min)

# Standardize inverse inputs
inv_scaler = StandardScaler().fit(X_inv_raw)
X_inv_s = inv_scaler.transform(X_inv_raw).astype(np.float32)

class InverseNet(nn.Module):
    def __init__(self, in_dim, out_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, 256), nn.SiLU(),
            nn.Dropout(0.1),
            nn.Linear(256, 256), nn.SiLU(),
            nn.Dropout(0.1),
            nn.Linear(256, 128), nn.SiLU(),
            nn.Linear(128, out_dim),
            nn.Sigmoid(),
        )
    def forward(self, x):
        return self.net(x)

# Tandem loss: param loss + forward reconstruction loss
# Use frozen forward model for tandem
def tandem_loss(pred_params_norm, target_params, target_curve_db):
    # Param loss
    param_loss = torch.mean((pred_params_norm - target_params) ** 2)

    # Denormalize
    y_min_t = torch.tensor(y_min, dtype=torch.float32, device=device)
    y_max_t = torch.tensor(y_max, dtype=torch.float32, device=device)
    pred_params_denorm = pred_params_norm * (y_max_t - y_min_t) + y_min_t

    # Scale inputs for forward model
    x_mean_t = torch.tensor(x_sc_final.mean_, dtype=torch.float32, device=device)
    x_scale_t = torch.tensor(x_sc_final.scale_, dtype=torch.float32, device=device)
    pred_params_scaled = (pred_params_denorm - x_mean_t) / x_scale_t

    # Forward prediction
    with torch.no_grad():
        pred_curve = dec_final(model_f(pred_params_scaled))

    curve_loss = wloss(pred_curve, target_curve_db)
    return param_loss + 0.5 * curve_loss

# 5-Fold CV for inverse
cv_inv_mae = []; cv_inv_curve_mae = []

for fold, (tr_i, va_i) in enumerate(kf.split(X)):
    print(f"   Fold {fold+1}/{K} ...", end=" ", flush=True)

    X_inv_tr = torch.tensor(X_inv_s[tr_i], device=device)
    X_inv_va = torch.tensor(X_inv_s[va_i], device=device)
    y_p_tr   = torch.tensor(y_params_norm[tr_i], dtype=torch.float32, device=device)
    y_p_va   = torch.tensor(y_params_norm[va_i], dtype=torch.float32, device=device)
    y_db_tr  = torch.tensor(y_db[tr_i], device=device)
    y_db_va  = torch.tensor(y_db[va_i], device=device)

    inv_model = InverseNet(inp_dim, 2).to(device)
    opt_inv = torch.optim.AdamW(inv_model.parameters(), lr=0.0005, weight_decay=1e-4)

    best_v, best_e, best_s, pat = float('inf'), 0, None, 0
    for ep in range(5000):
        inv_model.train(); opt_inv.zero_grad()
        pred_p = inv_model(X_inv_tr)
        loss = tandem_loss(pred_p, y_p_tr, y_db_tr)
        loss.backward(); opt_inv.step()

        inv_model.eval()
        with torch.no_grad():
            pred_p_va = inv_model(X_inv_va)
            va_loss = tandem_loss(pred_p_va, y_p_va, y_db_va)
        if va_loss.item() < best_v - 1e-6:
            best_v = va_loss.item(); best_e = ep + 1
            best_s = copy.deepcopy(inv_model.state_dict()); pat = 0
        else:
            pat += 1
        if pat >= 500:
            break

    inv_model.load_state_dict(best_s); inv_model.eval()
    with torch.no_grad():
        p_va_norm = inv_model(X_inv_va).cpu().numpy()
        p_va_denorm = p_va_norm * (y_max - y_min) + y_min

    param_mae = np.mean(np.abs(p_va_denorm - y_params[va_i]))
    cv_inv_mae.append(float(param_mae))

    # Forward reconstruction MAE
    p_va_scaled = x_sc_final.transform(p_va_denorm.astype(np.float32))
    p_va_t = torch.tensor(p_va_scaled, device=device)
    with torch.no_grad():
        rec_curve = dec_final(model_f(p_va_t)).cpu().numpy()
    curve_mae = np.mean(np.abs(rec_curve - y_db[va_i]))
    cv_inv_curve_mae.append(float(curve_mae))

    print(f"Param MAE={param_mae:.4f}, Curve MAE={curve_mae:.4f}, Ep={best_e}")

print(f"\n   Inverse 5-Fold CV Summary:")
print(f"     Param MAE: {np.mean(cv_inv_mae):.4f} ± {np.std(cv_inv_mae):.4f}")
print(f"     Curve MAE (via forward): {np.mean(cv_inv_curve_mae):.4f} ± {np.std(cv_inv_curve_mae):.4f}")

# Train final inverse model on all data
print("\n   Training final inverse model on all data...")
X_inv_all_t = torch.tensor(X_inv_s, device=device)
y_p_all_t = torch.tensor(y_params_norm, dtype=torch.float32, device=device)
y_db_all_t2 = torch.tensor(y_db, device=device)

inv_final = InverseNet(inp_dim, 2).to(device)
opt_inv_f = torch.optim.AdamW(inv_final.parameters(), lr=0.0005, weight_decay=1e-4)
best_v_i, best_e_i, best_s_i, pat_i = float('inf'), 0, None, 0

# Use holdout indices from forward model
X_inv_tr_f = torch.tensor(X_inv_s[tr_idx], device=device)
X_inv_ho_f = torch.tensor(X_inv_s[ho_idx], device=device)
y_p_tr_f   = torch.tensor(y_params_norm[tr_idx], dtype=torch.float32, device=device)
y_p_ho_f   = torch.tensor(y_params_norm[ho_idx], dtype=torch.float32, device=device)
y_db_tr_f2  = torch.tensor(y_db[tr_idx], device=device)
y_db_ho_f  = torch.tensor(y_db[ho_idx], device=device)

for ep in range(5000):
    inv_final.train(); opt_inv_f.zero_grad()
    pred_p = inv_final(X_inv_tr_f)
    loss = tandem_loss(pred_p, y_p_tr_f, y_db_tr_f2)
    loss.backward(); opt_inv_f.step()

    inv_final.eval()
    with torch.no_grad():
        p_ho = inv_final(X_inv_ho_f)
        ho_loss = tandem_loss(p_ho, y_p_ho_f, y_db_ho_f)
    if ho_loss.item() < best_v_i - 1e-6:
        best_v_i = ho_loss.item(); best_e_i = ep + 1
        best_s_i = copy.deepcopy(inv_final.state_dict()); pat_i = 0
    else:
        pat_i += 1
    if pat_i >= 500:
        break

inv_final.load_state_dict(best_s_i); inv_final.eval()
with torch.no_grad():
    pred_all_norm = inv_final(X_inv_all_t).cpu().numpy()
pred_all_denorm = pred_all_norm * (y_max - y_min) + y_min

param_mae_all = np.mean(np.abs(pred_all_denorm - y_params), axis=0)
print(f"   Best ep: {best_e_i}")
print(f"   l3 MAE: {param_mae_all[0]:.4f}, w4 MAE: {param_mae_all[1]:.4f}")

# Save inverse model
torch.save({
    "model_state": inv_final.state_dict(),
    "pca_inv": pca_inv,
    "feat_scaler": feat_scaler,
    "inv_scaler": inv_scaler,
    "y_min": y_min, "y_max": y_max,
    "pca_dim_inv": pca_dim_inv,
    "inp_dim": inp_dim,
    "frequency": frequency,
}, os.path.join(BASE, "model_inverse.pth"))
print("   Inverse model saved.")

# ========== 4. Visualizations ==========
print("\n" + "=" * 50)
print("4. Generating visualizations")

# Reconstruct curves via inverse model
pred_all_inv_scaled = x_sc_final.transform(pred_all_denorm.astype(np.float32))
pred_all_inv_t = torch.tensor(pred_all_inv_scaled, device=device)
with torch.no_grad():
    rec_curves_all = dec_final(model_f(pred_all_inv_t)).cpu().numpy()
rec_maes = np.mean(np.abs(rec_curves_all - y_db), axis=1)

fig, axes = plt.subplots(2, 3, figsize=(20, 12))

# A: Forward CV boxplot
ax = axes[0, 0]
cv_maes_list = [[np.mean(np.abs(
    dec_final(model_f(X_all_t)).cpu().numpy()[va_i] - y_db[va_i]
)) for _ in range(len(va_i))] for _, va_i in kf.split(X)]

# Better: re-run predictions per fold
cv_all_preds = np.zeros_like(y_db)
for fold, (tr_i, va_i) in enumerate(kf.split(X)):
    x_sc_cv = StandardScaler().fit(X[tr_i])
    x_va_s = x_sc_cv.transform(X[va_i]).astype(np.float32)
    pdim = min(20, len(tr_i) - 2)
    pca_cv = PCA(n_components=pdim).fit(y_db[tr_i])
    y_tr_c_cv = pca_cv.transform(y_db[tr_i]).astype(np.float32)
    c_sc_cv = StandardScaler().fit(y_tr_c_cv)
    pc_t_cv = torch.tensor(pca_cv.components_, dtype=torch.float32, device=device)
    pm_t_cv = torch.tensor(pca_cv.mean_, dtype=torch.float32, device=device)
    cs_t_cv = torch.tensor(c_sc_cv.scale_, dtype=torch.float32, device=device)
    cm_t_cv = torch.tensor(c_sc_cv.mean_, dtype=torch.float32, device=device)
    m = ForwardNet(pdim).to(device)
    # Simple training
    x_tr_s = x_sc_cv.transform(X[tr_i]).astype(np.float32)
    Xtr_cv = torch.tensor(x_tr_s, device=device)
    Ytr_cv = torch.tensor(y_db[tr_i], device=device)
    Ytr_cs_cv = torch.tensor(c_sc_cv.transform(y_tr_c_cv).astype(np.float32), device=device)
    def dec_cv(cs):
        return (cs * cs_t_cv + cm_t_cv) @ pc_t_cv + pm_t_cv
    opt_cv = torch.optim.AdamW(m.parameters(), lr=0.001, weight_decay=1e-5)
    for ep in range(1000):
        m.train(); opt_cv.zero_grad()
        pc = m(Xtr_cv)
        loss = wloss(dec_cv(pc), Ytr_cv) + 0.01 * torch.mean((pc - Ytr_cs_cv) ** 2)
        loss.backward(); opt_cv.step()
    m.eval()
    with torch.no_grad():
        Xva_cv = torch.tensor(x_va_s, device=device)
        cv_all_preds[va_i] = dec_cv(m(Xva_cv)).cpu().numpy()

cv_fold_maes = []
for fold, (_, va_i) in enumerate(kf.split(X)):
    cv_fold_maes.append(np.mean(np.abs(cv_all_preds[va_i] - y_db[va_i]), axis=1))

ax.boxplot(cv_fold_maes, labels=[f'Fold {i+1}' for i in range(K)])
ax.set_ylabel('Curve MAE (dB)'); ax.set_title('Forward 5-Fold CV — MAE per Fold')
ax.grid(alpha=0.3, axis='y')

# B: Inverse param scatter
ax = axes[0, 1]
for i in range(N):
    ax.plot([y_params[i, 0], pred_all_denorm[i, 0]],
            [y_params[i, 1], pred_all_denorm[i, 1]],
            'o-', color='gray', alpha=0.3, markersize=3)
sc2 = ax.scatter(y_params[:, 0], y_params[:, 1], c=rec_maes, cmap='RdYlGn_r', s=40, label='True')
ax.scatter(pred_all_denorm[:, 0], pred_all_denorm[:, 1], c=rec_maes, cmap='RdYlGn_r', s=40, marker='x', label='Pred')
plt.colorbar(sc2, ax=ax, label='Curve MAE (dB)')
ax.set_xlabel('l3'); ax.set_ylabel('w4')
ax.set_title('Inverse: True vs Predicted Parameters'); ax.legend(fontsize=8)

# C: Inverse curve reconstruction
ax = axes[0, 2]
best_i_i = np.argmin(rec_maes); worst_i_i = np.argmax(rec_maes)
ax.plot(frequency, y_db[best_i_i], '-', color='#2563EB', lw=2, label=f'Target (l3={y_params[best_i_i,0]:.0f}, w4={y_params[best_i_i,1]:.1f})')
ax.plot(frequency, rec_curves_all[best_i_i], '--', color='#DC2626', lw=2, label=f'Reconstructed (MAE={rec_maes[best_i_i]:.4f})')
ax.set_xlabel('Freq (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title(f'Inverse Best Reconstruction'); ax.legend(fontsize=8); ax.grid(alpha=0.3)

# D: Forward best & worst
ax = axes[1, 0]
bi = np.argmin(all_maes); wi = np.argmax(all_maes)
ax.plot(frequency, y_db[bi], '-', color='#2563EB', lw=2, label=f'True (l3={X[bi,0]:.0f}, w4={X[bi,1]:.1f})')
ax.plot(frequency, pred_all[bi], '--', color='#DC2626', lw=2, label=f'Pred (MAE={all_maes[bi]:.4f})')
ax.set_xlabel('Freq (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title(f'Forward Best Fit | MAE={all_maes[bi]:.4f} dB'); ax.legend(fontsize=8); ax.grid(alpha=0.3)

ax = axes[1, 1]
ax.plot(frequency, y_db[wi], '-', color='#2563EB', lw=2, label=f'True (l3={X[wi,0]:.0f}, w4={X[wi,1]:.1f})')
ax.plot(frequency, pred_all[wi], '--', color='#DC2626', lw=2, label=f'Pred (MAE={all_maes[wi]:.4f})')
ax.set_xlabel('Freq (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title(f'Forward Worst Fit | MAE={all_maes[wi]:.4f} dB'); ax.legend(fontsize=8); ax.grid(alpha=0.3)

# E: MAE comparison
ax = axes[1, 2]
ax.hist(all_maes, bins=12, alpha=0.6, color='steelblue', edgecolor='white', label=f'Forward (mean={np.mean(all_maes):.3f})')
ax.hist(rec_maes, bins=12, alpha=0.6, color='darkorange', edgecolor='white', label=f'Inverse (mean={np.mean(rec_maes):.3f})')
ax.axvline(np.mean(all_maes), color='steelblue', linestyle='--', lw=1.5)
ax.axvline(np.mean(rec_maes), color='darkorange', linestyle='--', lw=1.5)
ax.set_xlabel('Curve MAE (dB)'); ax.set_title('Forward vs Inverse Curve MAE Distribution')
ax.legend(fontsize=9); ax.grid(alpha=0.3, axis='y')

plt.suptitle('Port_S2 1-8GHz — Forward & Inverse Model Results', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig_path = os.path.join(BASE, 'model_results_summary.png')
plt.savefig(fig_path, dpi=150, bbox_inches='tight')
plt.close()
print(f"Figure saved: {fig_path}")

# ========== 5. Summary JSON ==========
summary = {
    "data": {
        "file": "Port_S_data_2参数_1-8ghz.txt",
        "samples": N,
        "freq_range": f"{frequency[0]:.4f}-{frequency[-1]:.4f} GHz",
        "freq_points": len(frequency),
        "l3_range": [float(l3_vals[0]), float(l3_vals[-1])],
        "w4_range": [float(w4_vals[0]), float(w4_vals[-1])],
    },
    "forward": {
        "k_fold": K,
        "cv_val_mae_mean": float(np.mean([m['MAE'] for m in cv_va])),
        "cv_val_mae_std": float(np.std([m['MAE'] for m in cv_va])),
        "cv_val_freq_err_mean_MHz": float(np.mean([m['Freq_MAE_MHz'] for m in cv_va])),
        "cv_val_depth_mae_mean": float(np.mean([m['Depth_MAE'] for m in cv_va])),
        "final_pca_dim": pca_dim_final,
        "final_pca_var": float(pca_final.explained_variance_ratio_.sum()),
        "best_epoch": best_e_f,
        "all_mae_mean": float(np.mean(all_maes)),
        "all_mae_median": float(np.median(all_maes)),
        "all_mae_max": float(np.max(all_maes)),
    },
    "inverse": {
        "k_fold": K,
        "cv_param_mae_mean": float(np.mean(cv_inv_mae)),
        "cv_param_mae_std": float(np.std(cv_inv_mae)),
        "cv_curve_mae_mean": float(np.mean(cv_inv_curve_mae)),
        "cv_curve_mae_std": float(np.std(cv_inv_curve_mae)),
        "l3_mae_all": float(param_mae_all[0]),
        "w4_mae_all": float(param_mae_all[1]),
        "best_epoch": best_e_i,
        "rec_mae_mean": float(np.mean(rec_maes)),
        "rec_mae_median": float(np.median(rec_maes)),
    },
}

with open(os.path.join(BASE, 'results_summary.json'), 'w') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)
print(f"Summary saved: {os.path.join(BASE, 'results_summary.json')}")

print("\n" + "=" * 50)
print("ALL DONE!")
print(f"Output directory: {BASE}")
print("Files:")
for f in sorted(os.listdir(BASE)):
    print(f"  {f}")
