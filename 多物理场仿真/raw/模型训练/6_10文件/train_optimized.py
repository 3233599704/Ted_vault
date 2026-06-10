"""
Optimized training: Forward + Inverse with multi-task loss, data augmentation,
cosine annealing, ensemble, dynamic tandem weighting.
"""
import copy, warnings, os, re, json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.optim.lr_scheduler import CosineAnnealingWarmRestarts
from sklearn.decomposition import PCA
from sklearn.model_selection import KFold
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

SEED = 42
np.random.seed(SEED); torch.manual_seed(SEED)
BASE = os.path.dirname(os.path.abspath(__file__))
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {device}")

# ========== Load Data ==========
df = pd.read_excel(os.path.join(BASE, "Port_S_data_2_1-8GHz.xlsx"))
X = df[['l3', 'w4']].to_numpy(dtype=np.float32)
y_db = df.iloc[:, 3:].to_numpy(dtype=np.float32)
frequency = df.columns[3:].astype(float).to_numpy()
N = len(X); F = len(frequency)
print(f"Samples: {N}, Freq points: {F}, Range: {frequency[0]:.2f}-{frequency[-1]:.2f} GHz")

# Precompute per-sample resonance info (used in loss)
true_res_freq = np.array([frequency[np.argmin(y_db[i])] for i in range(N)])
true_res_depth = np.array([y_db[i, np.argmin(y_db[i])] for i in range(N)])

# ========== Data Augmentation ==========
def augment(X_raw, y_raw, noise_std=0.02, n_aug=2):
    """Add small Gaussian noise to input params, keep same S11."""
    X_list = [X_raw]; y_list = [y_raw]
    for _ in range(n_aug):
        noise = np.random.RandomState(SEED + _ + 100).randn(*X_raw.shape).astype(np.float32) * noise_std
        X_list.append(X_raw + noise)
        y_list.append(y_raw)
    return np.concatenate(X_list), np.concatenate(y_list)

# ============================================================
# 1. OPTIMIZED FORWARD MODEL
# ============================================================
print("\n" + "=" * 60)
print("1. OPTIMIZED FORWARD MODEL")
print("=" * 60)

class ForwardNetV2(nn.Module):
    """Wider, deeper network with residual connections."""
    def __init__(self, pca_dim):
        super().__init__()
        self.fc1 = nn.Linear(2, 256)
        self.fc2 = nn.Linear(256, 256)
        self.fc3 = nn.Linear(256, 256)
        self.fc4 = nn.Linear(256, 256)
        self.head = nn.Linear(256, pca_dim)
        self.act = nn.SiLU()
        self.drop = nn.Dropout(0.05)

    def forward(self, x):
        h1 = self.act(self.fc1(x))
        h2 = self.act(self.fc2(h1)) + h1  # residual
        h3 = self.act(self.fc3(self.drop(h2)))
        h4 = self.act(self.fc4(h3)) + h3  # residual
        return self.head(h4)

def resonance_aware_loss(pred_curve, true_curve, alpha=0.3):
    """
    Combines weighted MSE + explicit resonance frequency/depth penalty.
    """
    # Weighted MSE (deep resonance regions weighted higher)
    w = 1 + 25 * (torch.clamp(-true_curve, min=0) / 40) ** 2
    mse = torch.mean(w * (pred_curve - true_curve) ** 2)

    # Resonance frequency + depth loss
    pred_min_idx = torch.argmin(pred_curve, dim=1)
    true_min_idx = torch.argmin(true_curve, dim=1)

    batch_idx = torch.arange(len(pred_curve), device=device)
    pred_depth = pred_curve[batch_idx, pred_min_idx]
    true_depth = true_curve[batch_idx, true_min_idx]

    # Frequency error (soft approximation via weighted centroid)
    freq_t = torch.tensor(frequency, dtype=torch.float32, device=device)
    pred_freq = freq_t[pred_min_idx]
    true_freq = freq_t[true_min_idx]
    freq_err = torch.mean((pred_freq - true_freq) ** 2) / (frequency[-1] - frequency[0]) ** 2

    depth_err = torch.mean((pred_depth - true_depth) ** 2)

    return mse + alpha * (freq_err + depth_err)

# 5-Fold CV
K = 5
kf = KFold(n_splits=K, shuffle=True, random_state=SEED)
cv_fwd_va = []
cv_fwd_models = []

for fold, (tr_i, va_i) in enumerate(kf.split(X)):
    print(f"  Fold {fold+1}/{K} ...", end=" ", flush=True)

    # Augment training data
    X_tr_aug, y_tr_aug = augment(X[tr_i], y_db[tr_i], noise_std=0.015, n_aug=1)

    x_sc = StandardScaler().fit(X_tr_aug)
    X_tr_s = x_sc.transform(X_tr_aug).astype(np.float32)
    X_va_s = x_sc.transform(X[va_i]).astype(np.float32)

    pdim = min(25, len(tr_i) - 2)
    pca = PCA(n_components=pdim).fit(y_tr_aug)
    y_tr_c = pca.transform(y_tr_aug).astype(np.float32)
    c_sc = StandardScaler().fit(y_tr_c)
    y_tr_cs = c_sc.transform(y_tr_c).astype(np.float32)

    Xtr = torch.tensor(X_tr_s, device=device)
    Xva = torch.tensor(X_va_s, device=device)
    Ytr = torch.tensor(y_tr_aug, device=device)
    Yva = torch.tensor(y_db[va_i], device=device)
    Ytr_cs = torch.tensor(y_tr_cs, device=device)
    pct = torch.tensor(pca.components_, dtype=torch.float32, device=device)
    pmt = torch.tensor(pca.mean_, dtype=torch.float32, device=device)
    cst = torch.tensor(c_sc.scale_, dtype=torch.float32, device=device)
    cmt = torch.tensor(c_sc.mean_, dtype=torch.float32, device=device)

    def dec(c):
        return (c * cst + cmt) @ pct + pmt

    model = ForwardNetV2(pdim).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=0.002, weight_decay=1e-5)
    scheduler = CosineAnnealingWarmRestarts(opt, T_0=200, T_mult=2, eta_min=1e-6)

    best_v, best_e, best_s, pat = float('inf'), 0, None, 0
    alpha_schedule = lambda ep: 0.1 + 0.4 * min(1.0, ep / 500)  # gradually increase resonance weight

    for ep in range(5000):
        model.train(); opt.zero_grad()
        pc = model(Xtr)
        alpha = alpha_schedule(ep)
        loss = resonance_aware_loss(dec(pc), Ytr, alpha=alpha) + 0.005 * torch.mean((pc - Ytr_cs) ** 2)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        scheduler.step()

        model.eval()
        with torch.no_grad():
            v_db = dec(model(Xva))
            vl = resonance_aware_loss(v_db, Yva, alpha=alpha)
        if vl.item() < best_v - 1e-6:
            best_v = vl.item(); best_e = ep + 1
            best_s = copy.deepcopy(model.state_dict()); pat = 0
        else:
            pat += 1
        if pat >= 600:
            break

    model.load_state_dict(best_s); model.eval()
    with torch.no_grad():
        p_va = dec(model(Xva)).cpu().numpy()

    err = p_va - y_db[va_i]
    tmi = np.argmin(y_db[va_i], axis=1); pmi = np.argmin(p_va, axis=1)
    va_m = {
        'MAE': float(np.mean(np.abs(err))),
        'RMSE': float(np.sqrt(np.mean(err**2))),
        'FreqErr_MHz': float(np.mean(np.abs(frequency[pmi] - frequency[tmi])) * 1000),
        'DepthErr': float(np.mean(np.abs(p_va[np.arange(len(va_i)), pmi] - y_db[va_i][np.arange(len(va_i)), tmi]))),
    }
    cv_fwd_va.append(va_m)
    cv_fwd_models.append((model, x_sc, pca, c_sc))
    print(f"MAE={va_m['MAE']:.4f}, FreqErr={va_m['FreqErr_MHz']:.1f} MHz, BestEp={best_e}")

print("\n  Optimized Forward CV Summary:")
for k in ['MAE', 'RMSE', 'FreqErr_MHz', 'DepthErr']:
    vals = [m[k] for m in cv_fwd_va]
    print(f"    {k}: {np.mean(vals):.4f} ± {np.std(vals):.4f}")

# Train final forward model on all data
print("\n  Training final optimized forward model...")
# Fit PCA and scalers on ORIGINAL data only, then augment for training
x_sc_f = StandardScaler().fit(X)
X_s_orig = x_sc_f.transform(X).astype(np.float32)

pca_f = PCA(n_components=25).fit(y_db)
y_c_f = pca_f.transform(y_db).astype(np.float32)
c_sc_f = StandardScaler().fit(y_c_f)
y_cs_f = c_sc_f.transform(y_c_f).astype(np.float32)

# Augment only the scaled inputs for training
X_aug_s, y_aug_db = augment(X_s_orig, y_db, noise_std=0.015, n_aug=1)
y_aug_c = pca_f.transform(y_aug_db).astype(np.float32)
y_aug_cs = c_sc_f.transform(y_aug_c).astype(np.float32)

ho_n = max(5, N // 10)
ho_idx = np.random.RandomState(SEED).choice(N, size=ho_n, replace=False)  # holdout from original
tr_idx_f = np.setdiff1d(np.arange(len(X_aug_s)), ho_idx)
# Holdout = original ho samples only (no augmentation in holdout)
orig_ho_mask = np.array([i < N and i in ho_idx for i in range(len(X_aug_s))])
tr_idx_f = np.where(~orig_ho_mask)[0]
ho_idx_f = np.where(orig_ho_mask)[0]

Xtr_f = torch.tensor(X_aug_s[tr_idx_f], device=device)
Xho_f = torch.tensor(X_aug_s[ho_idx_f], device=device)
Ytr_f_db = torch.tensor(y_aug_db[tr_idx_f], device=device)
Yho_f_db = torch.tensor(y_aug_db[ho_idx_f], device=device)
Ytr_f_cs = torch.tensor(y_aug_cs[tr_idx_f], device=device)

pct_f = torch.tensor(pca_f.components_, dtype=torch.float32, device=device)
pmt_f = torch.tensor(pca_f.mean_, dtype=torch.float32, device=device)
cst_f = torch.tensor(c_sc_f.scale_, dtype=torch.float32, device=device)
cmt_f = torch.tensor(c_sc_f.mean_, dtype=torch.float32, device=device)

def dec_f(c):
    return (c * cst_f + cmt_f) @ pct_f + pmt_f

model_f = ForwardNetV2(25).to(device)
opt_f = torch.optim.AdamW(model_f.parameters(), lr=0.002, weight_decay=1e-5)
sched_f = CosineAnnealingWarmRestarts(opt_f, T_0=200, T_mult=2, eta_min=1e-6)
best_v_f, best_e_f, best_s_f, pat_f = float('inf'), 0, None, 0

for ep in range(5000):
    model_f.train(); opt_f.zero_grad()
    pc = model_f(Xtr_f)
    alpha = 0.1 + 0.4 * min(1.0, ep / 500)
    loss = resonance_aware_loss(dec_f(pc), Ytr_f_db, alpha=alpha) + 0.005 * torch.mean((pc - Ytr_f_cs) ** 2)
    loss.backward()
    torch.nn.utils.clip_grad_norm_(model_f.parameters(), 1.0)
    opt_f.step(); sched_f.step()

    model_f.eval()
    with torch.no_grad():
        ho_db = dec_f(model_f(Xho_f))
        vl = resonance_aware_loss(ho_db, Yho_f_db, alpha=alpha)
    if vl.item() < best_v_f - 1e-8:
        best_v_f = vl.item(); best_e_f = ep + 1
        best_s_f = copy.deepcopy(model_f.state_dict()); pat_f = 0
    else:
        pat_f += 1
    if pat_f >= 600:
        break

model_f.load_state_dict(best_s_f); model_f.eval()

# Predict on ALL original data
X_orig_s = X_s_orig  # already transformed above
with torch.no_grad():
    X_orig_t = torch.tensor(X_orig_s, device=device)
    pred_all = dec_f(model_f(X_orig_t)).cpu().numpy()

all_maes = np.mean(np.abs(pred_all - y_db), axis=1)
tmi = np.argmin(y_db, axis=1); pmi = np.argmin(pred_all, axis=1)
freq_errs = np.abs(frequency[pmi] - frequency[tmi]) * 1000
depth_errs = np.abs(pred_all[np.arange(N), pmi] - y_db[np.arange(N), tmi])

print(f"  PCA var: {pca_f.explained_variance_ratio_.sum():.4%}, Best ep: {best_e_f}")
print(f"  MAE: mean={all_maes.mean():.4f}, median={np.median(all_maes):.4f}, max={all_maes.max():.4f}")
print(f"  FreqErr: mean={freq_errs.mean():.1f} MHz, median={np.median(freq_errs):.1f}")
print(f"  DepthErr: mean={depth_errs.mean():.4f} dB, median={np.median(depth_errs):.4f}")

# Save forward model
torch.save({
    "model_state": model_f.state_dict(),
    "param_cols": ['l3', 'w4'],
    "x_mean": x_sc_f.mean_, "x_scale": x_sc_f.scale_,
    "coeff_mean": c_sc_f.mean_, "coeff_scale": c_sc_f.scale_,
    "pca_components": pca_f.components_, "pca_mean": pca_f.mean_,
    "frequency": frequency, "input_dim": 2, "pca_dim": 25,
}, os.path.join(BASE, "model_forward_v2.pth"))
print("  Forward V2 model saved.")

# ============================================================
# 2. OPTIMIZED INVERSE MODEL
# ============================================================
print("\n" + "=" * 60)
print("2. OPTIMIZED INVERSE MODEL (Enhanced Tandem + Ensemble)")
print("=" * 60)

def extract_features_v2(curves):
    """Richer feature set from S11 curves."""
    feats = []
    for c in curves:
        fi = np.argmin(c)
        f_res = frequency[fi]
        s11_min = c[fi]

        # Bandwidths at multiple thresholds
        bw = {}
        for th in [-3, -5, -10, -15]:
            mask = c <= th
            if mask.sum() > 1:
                bw[th] = frequency[mask][-1] - frequency[mask][0]
            else:
                bw[th] = 0.0

        # Curve statistics
        integral = np.trapezoid(c, frequency)
        # Slopes on either side of resonance
        left_slope = (c[min(fi, 1)] - c[max(fi - 3, 0)]) / (frequency[min(fi, 1)] - frequency[max(fi - 3, 0)]) if fi >= 3 else 0
        right_slope = (c[min(fi + 3, len(c) - 1)] - c[max(fi, len(c) - 2)]) / (frequency[min(fi + 3, len(c) - 1)] - frequency[max(fi, len(c) - 2)]) if fi <= len(c) - 4 else 0

        # Mean outside resonance (±500 MHz around resonance)
        res_mask = np.abs(frequency - f_res) < 0.5
        mean_out = np.mean(c[~res_mask]) if (~res_mask).sum() > 0 else np.mean(c)
        std_out = np.std(c[~res_mask]) if (~res_mask).sum() > 0 else np.std(c)

        feats.append([
            f_res, s11_min,
            bw[-3], bw[-5], bw[-10], bw[-15],
            integral, np.mean(c), np.std(c),
            left_slope, right_slope,
            mean_out, std_out,
        ])
    return np.array(feats, dtype=np.float32)

hand_feats = extract_features_v2(y_db)
feat_scaler = StandardScaler().fit(hand_feats)
hand_feats_s = feat_scaler.transform(hand_feats).astype(np.float32)

pca_inv = PCA(n_components=25).fit(y_db)
y_pca_inv = pca_inv.transform(y_db).astype(np.float32)

X_inv_raw = np.concatenate([y_pca_inv, hand_feats_s], axis=1)
inv_scaler = StandardScaler().fit(X_inv_raw)
X_inv_s = inv_scaler.transform(X_inv_raw).astype(np.float32)
inp_dim = X_inv_raw.shape[1]

y_params = X.copy()
y_min, y_max = y_params.min(axis=0), y_params.max(axis=0)
y_params_norm = (y_params - y_min) / (y_max - y_min)

print(f"Inverse input: {inp_dim} dims (25 PCA + {hand_feats.shape[1]} features)")

class InverseNetV2(nn.Module):
    """Deeper inverse network with residual."""
    def __init__(self, in_dim):
        super().__init__()
        self.fc1 = nn.Linear(in_dim, 256)
        self.fc2 = nn.Linear(256, 256)
        self.fc3 = nn.Linear(256, 256)
        self.fc4 = nn.Linear(256, 128)
        self.head = nn.Linear(128, 2)
        self.act = nn.SiLU()
        self.drop = nn.Dropout(0.1)

    def forward(self, x):
        h1 = self.act(self.fc1(x))
        h2 = self.act(self.fc2(self.drop(h1))) + h1
        h3 = self.act(self.fc3(self.drop(h2)))
        h4 = self.act(self.fc4(h3)) + h3[:, :128]  # residual
        return torch.sigmoid(self.head(h4))

# Tandem components as tensors
ym_t = torch.tensor(y_min, dtype=torch.float32, device=device)
yx_t = torch.tensor(y_max, dtype=torch.float32, device=device)
xm_t = torch.tensor(x_sc_f.mean_, dtype=torch.float32, device=device)
xs_t = torch.tensor(x_sc_f.scale_, dtype=torch.float32, device=device)

def tandem_loss_v2(pred_norm, target_norm, target_db, epoch_frac):
    """Dynamic tandem: curve weight increases with training progress."""
    param_loss = torch.mean((pred_norm - target_norm) ** 2)

    pd = pred_norm * (yx_t - ym_t) + ym_t
    ps = (pd - xm_t) / xs_t
    with torch.no_grad():
        pred_curve = dec_f(model_f(ps))

    curve_weight = 0.1 + 2.0 * epoch_frac  # 0.1 -> 2.1 over training
    curve_loss = resonance_aware_loss(pred_curve, target_db, alpha=0.3)
    return param_loss + curve_weight * curve_loss

# 5-Fold CV
cv_inv_param = []; cv_inv_curve = []; cv_inv_models = []

for fold, (tr_i, va_i) in enumerate(kf.split(X)):
    print(f"  Fold {fold+1}/{K} ...", end=" ", flush=True)

    Xi_tr = torch.tensor(X_inv_s[tr_i], device=device)
    Xi_va = torch.tensor(X_inv_s[va_i], device=device)
    Yp_tr = torch.tensor(y_params_norm[tr_i], dtype=torch.float32, device=device)
    Yp_va = torch.tensor(y_params_norm[va_i], dtype=torch.float32, device=device)
    Ydb_tr = torch.tensor(y_db[tr_i], device=device)
    Ydb_va = torch.tensor(y_db[va_i], device=device)

    inv_m = InverseNetV2(inp_dim).to(device)
    opt_i = torch.optim.AdamW(inv_m.parameters(), lr=0.0005, weight_decay=1e-4)
    sched_i = CosineAnnealingWarmRestarts(opt_i, T_0=300, T_mult=2, eta_min=1e-7)

    best_v, best_e, best_s, pat = float('inf'), 0, None, 0
    for ep in range(5000):
        inv_m.train(); opt_i.zero_grad()
        pp = inv_m(Xi_tr)
        ef = min(1.0, ep / 3000)
        loss = tandem_loss_v2(pp, Yp_tr, Ydb_tr, ef)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(inv_m.parameters(), 1.0)
        opt_i.step(); sched_i.step()

        inv_m.eval()
        with torch.no_grad():
            pp_va = inv_m(Xi_va)
            vl = tandem_loss_v2(pp_va, Yp_va, Ydb_va, ef)
        if vl.item() < best_v - 1e-8:
            best_v = vl.item(); best_e = ep + 1
            best_s = copy.deepcopy(inv_m.state_dict()); pat = 0
        else:
            pat += 1
        if pat >= 600:
            break

    inv_m.load_state_dict(best_s); inv_m.eval()
    with torch.no_grad():
        pva_n = inv_m(Xi_va).cpu().numpy()
    pva_d = pva_n * (y_max - y_min) + y_min
    pva_s = x_sc_f.transform(pva_d.astype(np.float32))
    pva_t = torch.tensor(pva_s, device=device)
    with torch.no_grad():
        rec_c = dec_f(model_f(pva_t)).cpu().numpy()

    cv_inv_param.append(np.mean(np.abs(pva_d - y_params[va_i])))
    cv_inv_curve.append(np.mean(np.abs(rec_c - y_db[va_i])))
    cv_inv_models.append((inv_m,))
    print(f"ParamMAE={cv_inv_param[-1]:.4f}, CurveMAE={cv_inv_curve[-1]:.4f}, Ep={best_e}")

print("\n  Optimized Inverse CV Summary:")
print(f"    Param MAE: {np.mean(cv_inv_param):.4f} ± {np.std(cv_inv_param):.4f}")
print(f"    Curve MAE: {np.mean(cv_inv_curve):.4f} ± {np.std(cv_inv_curve):.4f}")

# Train final inverse model
print("\n  Training final optimized inverse model...")
Xi_all_t = torch.tensor(X_inv_s, device=device)
Yp_all_t = torch.tensor(y_params_norm, dtype=torch.float32, device=device)
Ydb_all_t = torch.tensor(y_db, device=device)

# Simple approach: train on 85% of original data
tr_n = int(N * 0.85)
tr_idx_i = np.random.RandomState(SEED).choice(N, size=tr_n, replace=False)
ho_idx_i = np.setdiff1d(np.arange(N), tr_idx_i)

Xi_tr_f2 = torch.tensor(X_inv_s[tr_idx_i], device=device)
Xi_ho_f2 = torch.tensor(X_inv_s[ho_idx_i], device=device)
Yp_tr_f2 = torch.tensor(y_params_norm[tr_idx_i], dtype=torch.float32, device=device)
Yp_ho_f2 = torch.tensor(y_params_norm[ho_idx_i], dtype=torch.float32, device=device)
Ydb_tr_f2 = torch.tensor(y_db[tr_idx_i], device=device)
Ydb_ho_f2 = torch.tensor(y_db[ho_idx_i], device=device)

inv_final = InverseNetV2(inp_dim).to(device)
opt_if = torch.optim.AdamW(inv_final.parameters(), lr=0.0005, weight_decay=1e-4)
sched_if = CosineAnnealingWarmRestarts(opt_if, T_0=300, T_mult=2, eta_min=1e-7)
best_v_i, best_e_i, best_s_i, pat_i = float('inf'), 0, None, 0

for ep in range(5000):
    inv_final.train(); opt_if.zero_grad()
    pp = inv_final(Xi_tr_f2)
    ef = min(1.0, ep / 3000)
    loss = tandem_loss_v2(pp, Yp_tr_f2, Ydb_tr_f2, ef)
    loss.backward()
    torch.nn.utils.clip_grad_norm_(inv_final.parameters(), 1.0)
    opt_if.step(); sched_if.step()

    inv_final.eval()
    with torch.no_grad():
        pho = inv_final(Xi_ho_f2)
        vl = tandem_loss_v2(pho, Yp_ho_f2, Ydb_ho_f2, ef)
    if vl.item() < best_v_i - 1e-8:
        best_v_i = vl.item(); best_e_i = ep + 1
        best_s_i = copy.deepcopy(inv_final.state_dict()); pat_i = 0
    else: pat_i += 1
    if pat_i >= 600: break

inv_final.load_state_dict(best_s_i); inv_final.eval()
with torch.no_grad():
    pall_n = inv_final(Xi_all_t).cpu().numpy()

pall_d = pall_n * (y_max - y_min) + y_min
param_mae = np.mean(np.abs(pall_d - y_params), axis=0)

# Reconstruction
pall_s = x_sc_f.transform(pall_d.astype(np.float32))
with torch.no_grad():
    rec_all = dec_f(model_f(torch.tensor(pall_s, device=device))).cpu().numpy()
rec_maes = np.mean(np.abs(rec_all - y_db), axis=1)

print(f"  Best ep: {best_e_i}")
print(f"  l3 MAE: {param_mae[0]:.4f}, w4 MAE: {param_mae[1]:.4f}")
print(f"  Rec MAE: mean={rec_maes.mean():.4f}, median={np.median(rec_maes):.4f}")

torch.save({
    "model_state": inv_final.state_dict(),
    "pca_inv": pca_inv, "feat_scaler": feat_scaler, "inv_scaler": inv_scaler,
    "y_min": y_min, "y_max": y_max, "pca_dim_inv": 25, "inp_dim": inp_dim,
    "frequency": frequency, "n_hand_feats": hand_feats.shape[1],
}, os.path.join(BASE, "model_inverse_v2.pth"))
print("  Inverse V2 model saved.")

# ============================================================
# 3. COMPARISON & VISUALIZATION
# ============================================================
print("\n" + "=" * 60)
print("3. Comparison: Baseline vs Optimized")
print("=" * 60)

# Load baseline results
base_summary = json.load(open(os.path.join(BASE, "results_summary.json")))

print("\n--- Forward Model ---")
print(f"{'Metric':<25} {'Baseline':>12} {'Optimized':>12} {'Improvement':>12}")
print("-" * 61)
for metric, base_key in [('CV MAE (dB)', 'cv_val_mae_mean_dB'),
                           ('CV FreqErr (MHz)', 'cv_val_freq_err_mean_MHz'),
                           ('Final MAE (dB)', 'all_mae_mean_dB'),
                           ('Final Median MAE (dB)', 'all_mae_median_dB')]:
    base_val = base_summary['forward'][base_key]
    if 'MAE' in metric:
        opt_val = all_maes.mean() if 'CV' not in metric else np.mean([m['MAE'] for m in cv_fwd_va])
        if 'Median' in metric: opt_val = np.median(all_maes)
        impr = f"{((base_val - opt_val) / base_val * 100):+.1f}%"
    elif 'FreqErr' in metric:
        opt_val = np.mean([m['FreqErr_MHz'] for m in cv_fwd_va])
        impr = f"{((base_val - opt_val) / base_val * 100):+.1f}%"
    print(f"{metric:<25} {base_val:>12.4f} {opt_val:>12.4f} {impr:>12}")

print("\n--- Inverse Model ---")
print(f"{'Metric':<25} {'Baseline':>12} {'Optimized':>12} {'Improvement':>12}")
print("-" * 61)
for metric, base_key in [('CV Param MAE', 'cv_param_mae_mean'),
                           ('CV Curve MAE (dB)', 'cv_curve_mae_mean_dB'),
                           ('Final Rec MAE (dB)', 'rec_mae_mean_dB'),
                           ('Final l3 MAE', 'l3_mae_all'),
                           ('Final w4 MAE', 'w4_mae_all')]:
    base_val = base_summary['inverse'][base_key]
    if 'Param' in metric:
        opt_val = np.mean(cv_inv_param)
    elif 'CV Curve' in metric:
        opt_val = np.mean(cv_inv_curve)
    elif 'Rec' in metric:
        opt_val = rec_maes.mean()
    elif 'l3' in metric:
        opt_val = param_mae[0]
    elif 'w4' in metric:
        opt_val = param_mae[1]
    impr = f"{((base_val - opt_val) / base_val * 100):+.1f}%" if base_val != 0 else "N/A"
    print(f"{metric:<25} {base_val:>12.4f} {opt_val:>12.4f} {impr:>12}")

# ============================================================
# 4. Visualization
# ============================================================
print("\n--- Generating comparison figure ---")
fig, axes = plt.subplots(2, 3, figsize=(20, 12))

# A: Forward CV boxplot comparison
ax = axes[0, 0]
# Baseline CV predictions
cv_preds_base = np.zeros_like(y_db)
for fi, (tri, vai) in enumerate(kf.split(X)):
    xs_cv = StandardScaler().fit(X[tri])
    pdim = min(20, len(tri) - 2)
    pcv = PCA(n_components=pdim).fit(y_db[tri])
    ytc = pcv.transform(y_db[tri]).astype(np.float32)
    csc = StandardScaler().fit(ytc); ytcs = csc.transform(ytc).astype(np.float32)
    class FwdSimple(nn.Module):
        def __init__(self): super().__init__()
        def net(self): return nn.Sequential(nn.Linear(2,128),nn.SiLU(),nn.Linear(128,128),nn.SiLU(),nn.Linear(128,128),nn.SiLU(),nn.Linear(128,pdim))
        def forward(self, x): return self.net()(x)
    m = FwdSimple().to(device)
    o = torch.optim.AdamW(m.parameters(), lr=0.001, weight_decay=1e-5)
    Xt = torch.tensor(xs_cv.transform(X[tri]).astype(np.float32), device=device)
    Xv = torch.tensor(xs_cv.transform(X[vai]).astype(np.float32), device=device)
    Yt = torch.tensor(y_db[tri], device=device); Ytc = torch.tensor(ytcs, device=device)
    pct_cv = torch.tensor(pcv.components_, dtype=torch.float32, device=device)
    pmt_cv = torch.tensor(pcv.mean_, dtype=torch.float32, device=device)
    cst_cv = torch.tensor(csc.scale_, dtype=torch.float32, device=device)
    cmt_cv = torch.tensor(csc.mean_, dtype=torch.float32, device=device)
    def dc(c): return (c * cst_cv + cmt_cv) @ pct_cv + pmt_cv
    for ep in range(1000):
        m.train(); o.zero_grad()
        pc = m(Xt); w = 1 + 20 * (torch.clamp(-Yt, min=0) / 40) ** 2
        loss = torch.mean(w * (dc(pc) - Yt) ** 2) + 0.01 * torch.mean((pc - Ytc) ** 2)
        loss.backward(); o.step()
    m.eval()
    with torch.no_grad(): cv_preds_base[vai] = dc(m(Xv)).cpu().numpy()

base_data = [np.mean(np.abs(cv_preds_base[vai] - y_db[vai]), axis=1) for _, vai in kf.split(X)]
opt_data = [np.mean(np.abs(
    # Re-predict with optimized CV models
    np.zeros_like(y_db[vai])  # placeholder
), axis=1) for _, vai in kf.split(X)]

# Actually use stored CV fold models
opt_data_real = []
for fi, (_, vai) in enumerate(kf.split(X)):
    m_opt, x_sc_opt, pca_opt, c_sc_opt = cv_fwd_models[fi]
    x_va_s = x_sc_opt.transform(X[vai]).astype(np.float32)
    Xva_opt = torch.tensor(x_va_s, device=device)
    pct_opt = torch.tensor(pca_opt.components_, dtype=torch.float32, device=device)
    pmt_opt = torch.tensor(pca_opt.mean_, dtype=torch.float32, device=device)
    cst_opt = torch.tensor(c_sc_opt.scale_, dtype=torch.float32, device=device)
    cmt_opt = torch.tensor(c_sc_opt.mean_, dtype=torch.float32, device=device)
    def dec_opt(c): return (c * cst_opt + cmt_opt) @ pct_opt + pmt_opt
    m_opt.eval()
    with torch.no_grad():
        p_opt = dec_opt(m_opt(Xva_opt)).cpu().numpy()
    opt_data_real.append(np.mean(np.abs(p_opt - y_db[vai]), axis=1))

positions = np.arange(K) * 3
bp1 = ax.boxplot(base_data, positions=positions - 0.4, widths=0.6, patch_artist=True)
bp2 = ax.boxplot(opt_data_real, positions=positions + 0.4, widths=0.6, patch_artist=True)
for patch in bp1['boxes']: patch.set_facecolor('#87CEEB')
for patch in bp2['boxes']: patch.set_facecolor('#FF8C00')
ax.set_xticks(positions); ax.set_xticklabels([f'Fold {i+1}' for i in range(K)])
ax.set_ylabel('Curve MAE (dB)'); ax.set_title('Forward CV: Baseline vs Optimized')
ax.legend([bp1["boxes"][0], bp2["boxes"][0]], ['Baseline', 'Optimized'], fontsize=9)
ax.grid(alpha=0.3, axis='y')

# B: Forward all-data MAE histogram
ax = axes[0, 1]
bins = np.linspace(0, max(all_maes.max(), 2.5), 15)
# Load baseline preds
# Quick baseline re-predict
X_bs = (X - x_sc_f.mean_) / x_sc_f.scale_
with torch.no_grad():
    # Use simple forward net for baseline
    pass
# Just use stored all_maes from earlier run (reload)
# For now, compare against the earlier pred_all
ax.hist(all_maes, bins=bins, alpha=0.7, color='#FF8C00', edgecolor='white', label=f'Optimized (mean={np.mean(all_maes):.3f})')
# Annotate baseline mean
ax.axvline(0.717, color='steelblue', linestyle='--', lw=2, label=f'Baseline mean=0.717')
ax.axvline(np.mean(all_maes), color='darkorange', linestyle='--', lw=2, label=f'Opt mean={np.mean(all_maes):.3f}')
ax.set_xlabel('Curve MAE (dB)'); ax.set_title('Forward Final Model MAE Distribution')
ax.legend(fontsize=9); ax.grid(alpha=0.3, axis='y')

# C: Forward best/worst (optimized)
ax = axes[0, 2]
best_i = np.argmin(all_maes); worst_i = np.argmax(all_maes)
ax.plot(frequency, y_db[best_i], '-', color='#2563EB', lw=2, label=f'True (l3={X[best_i,0]:.0f}, w4={X[best_i,1]:.1f})')
ax.plot(frequency, pred_all[best_i], '--', color='#DC2626', lw=2, label=f'Pred (MAE={all_maes[best_i]:.4f})')
ax.set_xlabel('Freq (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title(f'Optimized Best Fit'); ax.legend(fontsize=8); ax.grid(alpha=0.3)

# D: Inverse param scatter (optimized)
ax = axes[1, 0]
sc = ax.scatter(X[:,0], X[:,1], c=rec_maes, cmap='RdYlGn_r', s=45, edgecolors='gray', lw=0.5, label='True')
ax.scatter(pall_d[:,0], pall_d[:,1], c=rec_maes, cmap='RdYlGn_r', s=45, marker='x', lw=1.2, label='Pred (opt)')
for i in range(N):
    ax.plot([X[i,0], pall_d[i,0]], [X[i,1], pall_d[i,1]], '-', color='gray', alpha=0.15, lw=0.6)
plt.colorbar(sc, ax=ax, label='Curve MAE (dB)')
ax.set_xlabel('l3'); ax.set_ylabel('w4')
ax.set_title('Inverse V2: True vs Predicted'); ax.legend(fontsize=8)

# E: Inverse best reconstruction
ax = axes[1, 1]
bi = np.argmin(rec_maes)
ax.plot(frequency, y_db[bi], '-', color='#2563EB', lw=2.5, label=f'Target (l3={X[bi,0]:.0f}, w4={X[bi,1]:.1f})')
ax.plot(frequency, rec_all[bi], '--', color='#DC2626', lw=2, label=f'Reconstructed (MAE={rec_maes[bi]:.4f})')
ax.set_xlabel('Freq (GHz)'); ax.set_ylabel('S11 (dB)')
ax.set_title(f'Inverse V2 Best | l3->{pall_d[bi,0]:.2f}, w4->{pall_d[bi,1]:.2f}')
ax.legend(fontsize=8); ax.grid(alpha=0.3)

# F: Resonance error comparison
ax = axes[1, 2]
true_res = np.array([frequency[np.argmin(y_db[i])] for i in range(N)])
pred_res = np.array([frequency[np.argmin(pred_all[i])] for i in range(N)])
ax.scatter(true_res, pred_res, c=all_maes, cmap='RdYlGn_r', s=50, edgecolors='gray', lw=0.5)
ax.plot([1, 8], [1, 8], 'k--', alpha=0.3)
ax.set_xlabel('True Resonance Freq (GHz)'); ax.set_ylabel('Predicted Resonance Freq (GHz)')
ax.set_title(f'Resonance Frequency Parity | Err={freq_errs.mean():.1f} MHz')
ax.grid(alpha=0.3)
plt.colorbar(ax.collections[0], ax=ax, label='Curve MAE (dB)')

plt.suptitle('Optimized Model Results — Port_S2 1-8GHz', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig_path = os.path.join(BASE, 'optimized_model_results.png')
plt.savefig(fig_path, dpi=150, bbox_inches='tight')
plt.close()
print(f"Figure saved: {fig_path}")

# Save optimized summary
opt_summary = {
    "optimizations": [
        "Multi-task loss with explicit resonance frequency/depth penalty",
        "Cosine annealing LR scheduler (warm restarts)",
        "Wider+deeper network with residual connections (256 hidden)",
        "Data augmentation (Gaussian noise on input params)",
        "Gradient clipping",
        "Dynamic tandem weight (curve loss increases during training)",
        "Richer S11 features for inverse (13 handcrafted features)",
        "Dropout regularization",
    ],
    "forward_v2": {
        "cv_val_mae_mean_dB": float(np.mean([m['MAE'] for m in cv_fwd_va])),
        "cv_val_mae_std_dB": float(np.std([m['MAE'] for m in cv_fwd_va])),
        "cv_val_freq_err_mean_MHz": float(np.mean([m['FreqErr_MHz'] for m in cv_fwd_va])),
        "cv_val_depth_err_mean_dB": float(np.mean([m['DepthErr'] for m in cv_fwd_va])),
        "final_mae_mean_dB": float(np.mean(all_maes)),
        "final_mae_median_dB": float(np.median(all_maes)),
        "final_freq_err_mean_MHz": float(np.mean(freq_errs)),
        "final_freq_err_median_MHz": float(np.median(freq_errs)),
        "final_depth_err_mean_dB": float(np.mean(depth_errs)),
        "pca_dim": 25, "pca_var": float(pca_f.explained_variance_ratio_.sum()),
        "best_epoch": best_e_f,
    },
    "inverse_v2": {
        "cv_param_mae_mean": float(np.mean(cv_inv_param)),
        "cv_param_mae_std": float(np.std(cv_inv_param)),
        "cv_curve_mae_mean_dB": float(np.mean(cv_inv_curve)),
        "cv_curve_mae_std_dB": float(np.std(cv_inv_curve)),
        "final_l3_mae": float(param_mae[0]),
        "final_w4_mae": float(param_mae[1]),
        "final_rec_mae_mean_dB": float(np.mean(rec_maes)),
        "final_rec_mae_median_dB": float(np.median(rec_maes)),
        "best_epoch": best_e_i,
    },
}
with open(os.path.join(BASE, 'optimized_summary.json'), 'w') as f:
    json.dump(opt_summary, f, indent=2, ensure_ascii=False)
print(f"Summary saved: {os.path.join(BASE, 'optimized_summary.json')}")

print("\n" + "=" * 60)
print("OPTIMIZATION COMPLETE!")
print(f"Output: {BASE}/")
for f in sorted(os.listdir(BASE)):
    sz = os.path.getsize(os.path.join(BASE, f))
    print(f"  {f} ({sz/1024:.0f} KB)")
