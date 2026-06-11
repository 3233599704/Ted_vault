"""
Port_S3 三个参数 (l3, w4, h) — 全流程: 转换+正向+逆向+候选搜索
576 samples, 40 freq points, 1-3 GHz
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
SRC  = os.path.join(os.path.dirname(BASE), "Port_S_data_三个参数.txt")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Device: {device}")

# ==================== 1. Convert to Excel ====================
print("=" * 60)
print("1. Convert to Excel")

with open(SRC) as f:
    header = f.readline().strip()
matches = re.findall(r'S\(1,1\),l3=([\d.]+), w4=([\d.]+), h=([\d.]+)', header)
l3_vals = sorted(set(float(m[0]) for m in matches))
w4_vals = sorted(set(float(m[1]) for m in matches))
h_vals  = sorted(set(float(m[2]) for m in matches))
print(f"  l3={l3_vals} ({len(l3_vals)} vals)")
print(f"  w4={w4_vals} ({len(w4_vals)} vals)")
print(f"  h={h_vals} ({len(h_vals)} vals)")
print(f"  Total: {len(matches)} curves")

df = pd.read_csv(SRC, sep=r'\s+', skiprows=[0,1,2,3], header=None, dtype=float, engine='python')
freq_hz = df.iloc[:, 0].values
s11_raw = df.iloc[:, 1:].values
print(f"  Freq: {freq_hz[0]/1e9:.4f}~{freq_hz[-1]/1e9:.4f} GHz, {len(freq_hz)} pts")

rows = []
for idx, (l3, w4, h) in enumerate(matches):
    row = {'l3': float(l3), 'w4': float(w4), 'h': float(h), 'Unnamed: 15': np.nan}
    for j, f in enumerate(freq_hz):
        row[f'{f/1e9:.6f}'] = s11_raw[j, idx]
    rows.append(row)

df_out = pd.DataFrame(rows)
fcols = sorted([c for c in df_out.columns if c not in ['l3','w4','h','Unnamed: 15']], key=lambda x: float(x))
df_out = df_out[['l3','w4','h','Unnamed: 15'] + fcols]

xlsx_path = os.path.join(BASE, "Port_S3_data.xlsx")
df_out.to_excel(xlsx_path, index=False, engine='openpyxl')
print(f"  Saved: {xlsx_path} ({df_out.shape[0]}x{df_out.shape[1]})")

# Prepare arrays
param_cols = ['l3', 'w4', 'h']
X = df_out[param_cols].to_numpy(dtype=np.float32)
y_db = df_out.iloc[:, 4:].to_numpy(dtype=np.float32)
frequency = df_out.columns[4:].astype(float).to_numpy()
N, F, D = len(X), len(frequency), len(param_cols)
print(f"  Samples: {N}, Freq: {F}, Input dim: {D}")

# ==================== 2. Forward Model ====================
print("\n" + "=" * 60)
print("2. Forward Model — 5-Fold CV")

freq_t = torch.tensor(frequency, dtype=torch.float32, device=device)

class ForwardNet(nn.Module):
    def __init__(self, pca_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(D, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, 128), nn.SiLU(),
            nn.Linear(128, pca_dim),
        )
    def forward(self, x): return self.net(x)

def wloss(pred, true):
    w = 1 + 25 * (torch.clamp(-true, min=0) / 40) ** 2
    return torch.mean(w * (pred - true) ** 2)

def res_loss(pred, true, alpha=0.2):
    mse = wloss(pred, true)
    pi = torch.argmin(pred, dim=1); ti = torch.argmin(true, dim=1)
    bi = torch.arange(len(pred), device=device)
    freq_err = torch.mean(((freq_t[pi] - freq_t[ti]) / (freq_t[-1] - freq_t[0])) ** 2)
    depth_err = torch.mean((pred[bi, pi] - true[bi, ti]) ** 2)
    return mse + alpha * (freq_err + depth_err)

def calc_metrics(true, pred):
    err = pred - true
    tmi = np.argmin(true, axis=1); pmi = np.argmin(pred, axis=1)
    rows = np.arange(len(true))
    return {
        'MAE': float(np.mean(np.abs(err))),
        'RMSE': float(np.sqrt(np.mean(err**2))),
        'FreqErr_MHz': float(np.mean(np.abs(frequency[pmi] - frequency[tmi])) * 1000),
        'DepthErr': float(np.mean(np.abs(pred[rows, pmi] - true[rows, tmi]))),
    }

K = 5
kf = KFold(n_splits=K, shuffle=True, random_state=SEED)
cv_fwd = []

for fold, (tr_i, va_i) in enumerate(kf.split(X)):
    print(f"  Fold {fold+1}/{K} ...", end=" ", flush=True)

    x_sc = StandardScaler().fit(X[tr_i])
    X_tr_s = x_sc.transform(X[tr_i]).astype(np.float32)
    X_va_s = x_sc.transform(X[va_i]).astype(np.float32)

    pdim = min(30, len(tr_i) - 2)
    pca = PCA(n_components=pdim).fit(y_db[tr_i])
    y_tr_c = pca.transform(y_db[tr_i]).astype(np.float32)
    c_sc = StandardScaler().fit(y_tr_c)
    y_tr_cs = c_sc.transform(y_tr_c).astype(np.float32)

    Xtr = torch.tensor(X_tr_s, device=device); Xva = torch.tensor(X_va_s, device=device)
    Ytr = torch.tensor(y_db[tr_i], device=device); Yva = torch.tensor(y_db[va_i], device=device)
    Ytr_cs = torch.tensor(y_tr_cs, device=device)
    pct = torch.tensor(pca.components_, dtype=torch.float32, device=device)
    pmt = torch.tensor(pca.mean_, dtype=torch.float32, device=device)
    cst = torch.tensor(c_sc.scale_, dtype=torch.float32, device=device)
    cmt = torch.tensor(c_sc.mean_, dtype=torch.float32, device=device)

    def dec(c): return (c * cst + cmt) @ pct + pmt

    model = ForwardNet(pdim).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=1e-5)

    best_v, best_e, best_s, pat = float('inf'), 0, None, 0
    for ep in range(5000):
        model.train(); opt.zero_grad()
        pc = model(Xtr)
        loss = res_loss(dec(pc), Ytr) + 0.005 * torch.mean((pc - Ytr_cs) ** 2)
        loss.backward(); opt.step()

        model.eval()
        with torch.no_grad():
            v_db = dec(model(Xva)); vl = wloss(v_db, Yva)
        if vl.item() < best_v - 1e-6:
            best_v = vl.item(); best_e = ep + 1
            best_s = copy.deepcopy(model.state_dict()); pat = 0
        else: pat += 1
        if pat >= 500: break

    model.load_state_dict(best_s); model.eval()
    with torch.no_grad(): p_va = dec(model(Xva)).cpu().numpy()
    cv_fwd.append(calc_metrics(y_db[va_i], p_va))
    print(f"MAE={cv_fwd[-1]['MAE']:.4f}, FreqErr={cv_fwd[-1]['FreqErr_MHz']:.0f}MHz, Ep={best_e}")

print("\n  Forward CV Summary:")
for k in ['MAE', 'RMSE', 'FreqErr_MHz', 'DepthErr']:
    vals = [m[k] for m in cv_fwd]
    print(f"    {k}: {np.mean(vals):.4f} ± {np.std(vals):.4f}")

# Final forward
print("\n  Training final forward model...")
x_sc_f = StandardScaler().fit(X); X_s_f = x_sc_f.transform(X).astype(np.float32)
pca_f = PCA(n_components=35).fit(y_db)
y_c_f = pca_f.transform(y_db).astype(np.float32)
c_sc_f = StandardScaler().fit(y_c_f); y_cs_f = c_sc_f.transform(y_c_f).astype(np.float32)

ho_n = max(10, N // 10)
ho_idx = np.random.RandomState(SEED).choice(N, size=ho_n, replace=False)
tr_idx_f = np.setdiff1d(np.arange(N), ho_idx)

Xtr_f = torch.tensor(X_s_f[tr_idx_f], device=device); Xho_f = torch.tensor(X_s_f[ho_idx], device=device)
Ytr_f = torch.tensor(y_db[tr_idx_f], device=device); Yho_f = torch.tensor(y_db[ho_idx], device=device)
Ytr_cs_f = torch.tensor(y_cs_f[tr_idx_f], device=device)
pct_f = torch.tensor(pca_f.components_, dtype=torch.float32, device=device)
pmt_f = torch.tensor(pca_f.mean_, dtype=torch.float32, device=device)
cst_f = torch.tensor(c_sc_f.scale_, dtype=torch.float32, device=device)
cmt_f = torch.tensor(c_sc_f.mean_, dtype=torch.float32, device=device)

def dec_f(c): return (c * cst_f + cmt_f) @ pct_f + pmt_f

model_f = ForwardNet(35).to(device)
opt_f = torch.optim.AdamW(model_f.parameters(), lr=0.001, weight_decay=1e-5)
best_v, best_e, best_s, pat = float('inf'), 0, None, 0
for ep in range(5000):
    model_f.train(); opt_f.zero_grad()
    pc = model_f(Xtr_f)
    loss = res_loss(dec_f(pc), Ytr_f) + 0.005 * torch.mean((pc - Ytr_cs_f) ** 2)
    loss.backward(); opt_f.step()
    model_f.eval()
    with torch.no_grad():
        ho_db = dec_f(model_f(Xho_f)); vl = wloss(ho_db, Yho_f)
    if vl.item() < best_v - 1e-8:
        best_v = vl.item(); best_e = ep + 1
        best_s = copy.deepcopy(model_f.state_dict()); pat = 0
    else: pat += 1
    if pat >= 500: break

model_f.load_state_dict(best_s); model_f.eval()
with torch.no_grad():
    X_all_t = torch.tensor(X_s_f, device=device)
    pred_all = dec_f(model_f(X_all_t)).cpu().numpy()

all_maes = np.mean(np.abs(pred_all - y_db), axis=1)
tmi_all = np.argmin(y_db, axis=1); pmi_all = np.argmin(pred_all, axis=1)
freq_errs = np.abs(frequency[pmi_all] - frequency[tmi_all]) * 1000
depth_errs = np.abs(pred_all[np.arange(N), pmi_all] - y_db[np.arange(N), tmi_all])

print(f"  PCA var: {pca_f.explained_variance_ratio_.sum():.4%}, Best ep: {best_e}")
print(f"  MAE: mean={all_maes.mean():.4f}, median={np.median(all_maes):.4f}, max={all_maes.max():.4f}")
print(f"  FreqErr: mean={freq_errs.mean():.1f}, median={np.median(freq_errs):.1f} MHz")
print(f"  DepthErr: mean={depth_errs.mean():.4f}, median={np.median(depth_errs):.4f} dB")

torch.save({
    "model_state": model_f.state_dict(), "param_cols": param_cols,
    "x_mean": x_sc_f.mean_, "x_scale": x_sc_f.scale_,
    "coeff_mean": c_sc_f.mean_, "coeff_scale": c_sc_f.scale_,
    "pca_components": pca_f.components_, "pca_mean": pca_f.mean_,
    "frequency": frequency, "input_dim": D, "pca_dim": 35,
}, os.path.join(BASE, "model_forward.pth"))
print("  Forward model saved.")

# ==================== 3. Inverse Model ====================
print("\n" + "=" * 60)
print("3. Inverse Model — Direct Regression + BGD Refinement")
print("(Tandem skipped — batch gradient descent through frozen forward is the primary solver)")

def extract_features(curves):
    feats = []
    for c in curves:
        fi = np.argmin(c); f_res = frequency[fi]; s11_min = c[fi]
        bw = {}
        for th in [-3, -5, -10]:
            mask = c <= th
            bw[th] = frequency[mask][-1] - frequency[mask][0] if mask.sum() > 1 else 0.0
        integral = np.trapezoid(c, frequency)
        if fi >= 4:
            ls = (c[fi] - c[fi-4]) / (frequency[fi] - frequency[fi-4])
        else:
            ls = 0.0
        if fi <= len(c) - 5:
            rs = (c[fi+4] - c[fi]) / (frequency[fi+4] - frequency[fi])
        else:
            rs = 0.0
        feats.append([f_res, s11_min, bw[-3], bw[-5], bw[-10], integral, np.mean(c), np.std(c), ls, rs])
    return np.array(feats, dtype=np.float32)

hand_feats = extract_features(y_db)
feat_scaler = StandardScaler().fit(hand_feats)
hand_feats_s = feat_scaler.transform(hand_feats).astype(np.float32)

pca_inv = PCA(n_components=35).fit(y_db)
y_pca_inv = pca_inv.transform(y_db).astype(np.float32)

X_inv_raw = np.concatenate([y_pca_inv, hand_feats_s], axis=1)
inv_scaler = StandardScaler().fit(X_inv_raw)
X_inv_s = inv_scaler.transform(X_inv_raw).astype(np.float32)
inp_dim = X_inv_raw.shape[1]
print(f"  Inverse input dim: {inp_dim}")

y_params = X.copy()
y_min, y_max = y_params.min(axis=0), y_params.max(axis=0)
y_params_norm = (y_params - y_min) / (y_max - y_min)

class InverseNet(nn.Module):
    def __init__(self, in_dim, out_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, 256), nn.SiLU(), nn.Dropout(0.1),
            nn.Linear(256, 256), nn.SiLU(), nn.Dropout(0.1),
            nn.Linear(256, 128), nn.SiLU(),
            nn.Linear(128, out_dim), nn.Sigmoid(),
        )
    def forward(self, x): return self.net(x)

# Direct param loss only (no tandem)
cv_inv_param = []
for fold, (tr_i, va_i) in enumerate(kf.split(X)):
    print(f"  Fold {fold+1}/{K} ...", end=" ", flush=True)
    Xi_tr = torch.tensor(X_inv_s[tr_i], device=device); Xi_va = torch.tensor(X_inv_s[va_i], device=device)
    Yp_tr = torch.tensor(y_params_norm[tr_i], dtype=torch.float32, device=device)
    Yp_va = torch.tensor(y_params_norm[va_i], dtype=torch.float32, device=device)

    inv_m = InverseNet(inp_dim, D).to(device)
    opt_i = torch.optim.AdamW(inv_m.parameters(), lr=0.001, weight_decay=1e-5)

    best_v, best_e, best_s, pat = float('inf'), 0, copy.deepcopy(inv_m.state_dict()), 0
    for ep in range(5000):
        inv_m.train(); opt_i.zero_grad()
        pp = inv_m(Xi_tr)
        loss = torch.mean((pp - Yp_tr) ** 2)
        loss.backward(); opt_i.step()

        inv_m.eval()
        with torch.no_grad():
            pp_va = inv_m(Xi_va); vl = torch.mean((pp_va - Yp_va) ** 2)
        if vl.item() < best_v - 1e-10:
            best_v = vl.item(); best_e = ep + 1
            best_s = copy.deepcopy(inv_m.state_dict()); pat = 0
        else: pat += 1
        if pat >= 500: break

    inv_m.load_state_dict(best_s); inv_m.eval()
    with torch.no_grad(): pva_n = inv_m(Xi_va).cpu().numpy()
    pva_d = pva_n * (y_max - y_min) + y_min
    cv_inv_param.append(np.mean(np.abs(pva_d - y_params[va_i])))
    print(f"ParamMAE={cv_inv_param[-1]:.4f}, Ep={best_e}")

print(f"\n  Inverse Direct Regression CV: Param MAE={np.mean(cv_inv_param):.4f}±{np.std(cv_inv_param):.4f}")

# Final inverse model
print("\n  Training final inverse (direct regression)...")
tr_n = int(N * 0.85)
tr_idx_i = np.random.RandomState(SEED).choice(N, size=tr_n, replace=False)
ho_idx_i = np.setdiff1d(np.arange(N), tr_idx_i)

Xi_tr_f2 = torch.tensor(X_inv_s[tr_idx_i], device=device); Xi_ho_f2 = torch.tensor(X_inv_s[ho_idx_i], device=device)
Yp_tr_f2 = torch.tensor(y_params_norm[tr_idx_i], dtype=torch.float32, device=device)
Yp_ho_f2 = torch.tensor(y_params_norm[ho_idx_i], dtype=torch.float32, device=device)

inv_final = InverseNet(inp_dim, D).to(device)
opt_if = torch.optim.AdamW(inv_final.parameters(), lr=0.001, weight_decay=1e-5)
best_v, best_e, best_s, pat = float('inf'), 0, copy.deepcopy(inv_final.state_dict()), 0
for ep in range(5000):
    inv_final.train(); opt_if.zero_grad()
    pp = inv_final(Xi_tr_f2); loss = torch.mean((pp - Yp_tr_f2) ** 2)
    loss.backward(); opt_if.step()
    inv_final.eval()
    with torch.no_grad():
        pho = inv_final(Xi_ho_f2); vl = torch.mean((pho - Yp_ho_f2) ** 2)
    if vl.item() < best_v - 1e-10:
        best_v = vl.item(); best_e = ep + 1
        best_s = copy.deepcopy(inv_final.state_dict()); pat = 0
    else: pat += 1
    if pat >= 500: break

inv_final.load_state_dict(best_s); inv_final.eval()
with torch.no_grad(): pall_n = inv_final(torch.tensor(X_inv_s, device=device)).cpu().numpy()
pall_d = pall_n * (y_max - y_min) + y_min
param_mae = np.mean(np.abs(pall_d - y_params), axis=0)

# Reconstruction via forward model
pall_s = x_sc_f.transform(pall_d.astype(np.float32))
with torch.no_grad(): rec_all = dec_f(model_f(torch.tensor(pall_s, device=device))).cpu().numpy()
rec_maes = np.mean(np.abs(rec_all - y_db), axis=1)

print(f"  Best ep: {best_e}")
for i, name in enumerate(param_cols):
    print(f"  {name} MAE: {param_mae[i]:.4f}")
print(f"  Rec MAE (via forward): mean={rec_maes.mean():.4f}, median={np.median(rec_maes):.4f}")

torch.save({
    "model_state": inv_final.state_dict(), "pca_inv": pca_inv,
    "feat_scaler": feat_scaler, "inv_scaler": inv_scaler,
    "y_min": y_min, "y_max": y_max, "pca_dim_inv": 35, "inp_dim": inp_dim,
    "frequency": frequency,
}, os.path.join(BASE, "model_inverse.pth"))
print("  Inverse model saved.")

# ==================== 4. Multi-Candidate Search ====================
print("\n" + "=" * 60)
print("4. Inverse Multi-Candidate Search (Batch GD)")

xm_f_t = torch.tensor(x_sc_f.mean_, dtype=torch.float32, device=device)
xs_f_t = torch.tensor(x_sc_f.scale_, dtype=torch.float32, device=device)

@torch.no_grad()
def forward_predict(params):
    ps = (params - xm_f_t) / xs_f_t
    return (model_f(ps) * cst_f + cmt_f) @ pct_f + pmt_f

def batch_search(target_curve, n_starts=200, n_iters=400, lr=0.1, top_k=10, dedup=0.3):
    target_t = torch.tensor(target_curve, dtype=torch.float32, device=device)
    ti = int(torch.argmin(target_t))
    target_depth = target_t[ti]; target_freq = freq_t[ti]

    rng = np.random.RandomState(SEED)
    starts = np.stack([
        rng.uniform(y_min[i], y_max[i], n_starts) for i in range(D)
    ], axis=1)

    params = torch.tensor(starts, dtype=torch.float32, device=device, requires_grad=True)
    opt = torch.optim.Adam([params], lr=lr)

    for _ in range(n_iters):
        opt.zero_grad()
        pred = forward_predict(params)
        w = 1 + 25 * (torch.clamp(-target_t.unsqueeze(0), min=0) / 40) ** 2
        mse = torch.mean(w * (pred - target_t.unsqueeze(0)) ** 2, dim=1)
        pi = torch.argmin(pred, dim=1)
        freq_err = ((freq_t[pi] - target_freq) / (freq_t[-1] - freq_t[0])) ** 2
        depth_err = (pred[torch.arange(n_starts), pi] - target_depth) ** 2
        bp = sum(torch.clamp(y_min[i] - params[:, i], min=0) ** 2 +
                 torch.clamp(params[:, i] - y_max[i], min=0) ** 2 for i in range(D))
        loss = torch.mean(mse + 0.3 * (freq_err + depth_err) + 10.0 * bp)
        loss.backward(); opt.step()
        with torch.no_grad():
            for i in range(D):
                params[:, i].clamp_(y_min[i] - 0.2*(y_max[i]-y_min[i]), y_max[i] + 0.2*(y_max[i]-y_min[i]))

    with torch.no_grad():
        params.clamp_(torch.tensor(y_min, device=device), torch.tensor(y_max, device=device))
        final_preds = forward_predict(params).cpu().numpy()
    final_params = params.detach().cpu().numpy()

    candidates = []
    for i in range(n_starts):
        p = final_params[i]
        pred = final_preds[i]
        mae = np.mean(np.abs(pred - target_curve))
        pi2 = np.argmin(pred)
        ferr = np.abs(frequency[pi2] - target_freq) * 1000
        derr = np.abs(pred[pi2] - target_depth)
        candidates.append({name: float(p[j]) for j, name in enumerate(param_cols)}
                          | {'curve_mae': float(mae), 'freq_err_MHz': float(ferr), 'depth_err_dB': float(derr)})

    candidates.sort(key=lambda x: x['curve_mae'])
    unique = []
    for c in candidates:
        dup = any(np.sqrt(sum((c[n] - u[n])**2 for n in param_cols)) < dedup for u in unique)
        if not dup: unique.append(c)
        if len(unique) >= top_k: break
    return unique

print(f"  Running batch search for {min(20, N)} representative samples...")
sample_indices = sorted(np.concatenate([
    np.argsort(all_maes)[:5], np.argsort(all_maes)[N//4:N//4+5],
    np.argsort(all_maes)[N//2:N//2+5], np.argsort(all_maes)[-5:]
]))

search_results = {}
for idx in sample_indices:
    cands = batch_search(y_db[idx], n_starts=200, n_iters=400, top_k=10, dedup=0.3)
    search_results[idx] = cands
    if len(search_results) % 5 == 0:
        print(f"    {len(search_results)}/{len(sample_indices)} done...", flush=True)

# Save search CSV
csv_rows = []
for idx, cands in search_results.items():
    for rank, c in enumerate(cands):
        csv_rows.append({'sample_idx': idx, 'true_l3': float(X[idx,0]), 'true_w4': float(X[idx,1]), 'true_h': float(X[idx,2]),
                         'rank': rank+1} | {n: c[n] for n in param_cols}
                        | {'curve_mae_dB': c['curve_mae'], 'freq_err_MHz': c['freq_err_MHz'], 'depth_err_dB': c['depth_err_dB']})
pd.DataFrame(csv_rows).to_csv(os.path.join(BASE, 'inverse_candidates.csv'), index=False)
print(f"  Saved: inverse_candidates.csv")

# ==================== 5. Visualizations ====================
print("\n" + "=" * 60)
print("5. Visualizations")

# Fig 1: Clean forward curves (3x3 grid)
fig, axes = plt.subplots(3, 3, figsize=(18, 14))
sorted_mae = np.argsort(all_maes)
show_idx = list(sorted_mae[:3]) + list(sorted_mae[N//3:N//3+3]) + list(sorted_mae[-3:])

for i, idx in enumerate(show_idx):
    ax = axes[i//3, i%3]
    ax.plot(frequency, y_db[idx], '-', color='#2563EB', linewidth=2.2, label='True')
    ax.plot(frequency, pred_all[idx], '--', color='#DC2626', linewidth=2.0, label='Pred')
    ax.fill_between(frequency, y_db[idx], pred_all[idx], alpha=0.08, color='gray')
    ti = np.argmin(y_db[idx]); pi = np.argmin(pred_all[idx])
    ax.scatter(frequency[ti], y_db[idx, ti], color='#2563EB', s=60, zorder=5)
    ax.scatter(frequency[pi], pred_all[idx, pi], color='#DC2626', s=60, marker='D', zorder=5)
    params_str = ', '.join(f'{n}={X[idx,j]:.1f}' for j, n in enumerate(param_cols))
    ax.set_title(f'{params_str}  |  MAE={all_maes[idx]:.4f} dB', fontsize=10, fontweight='bold')
    ax.set_xlabel('Freq (GHz)', fontsize=9); ax.set_ylabel('S11 (dB)', fontsize=9)
    ax.legend(fontsize=8, loc='lower right'); ax.grid(alpha=0.2)

plt.suptitle(f'Forward Model — {N} samples, 3 params (l3,w4,h)', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 'forward_curves_grid.png'), dpi=180, bbox_inches='tight')
plt.close()
print("  forward_curves_grid.png")

# Fig 2: Error analysis
fig, axes = plt.subplots(1, 2, figsize=(18, 7))
errors = pred_all - y_db
mean_err = np.mean(errors, axis=0); std_err = np.std(errors, axis=0)
ax = axes[0]
ax.fill_between(frequency, mean_err - std_err, mean_err + std_err, alpha=0.2, color='#2563EB')
ax.plot(frequency, mean_err, '-', color='#2563EB', linewidth=2.0)
ax.axhline(0, color='black', linewidth=0.8)
ax.set_xlabel('Freq (GHz)', fontsize=12); ax.set_ylabel('Error (dB)', fontsize=12)
ax.set_title(f'Mean Error ±1σ | MAE={np.mean(np.abs(errors)):.4f} dB', fontsize=13, fontweight='bold')
ax.grid(alpha=0.2)

ax = axes[1]
ax.hist(all_maes, bins=20, color='steelblue', edgecolor='white', alpha=0.85)
ax.axvline(np.mean(all_maes), color='red', linestyle='--', lw=2, label=f'Mean={np.mean(all_maes):.4f}')
ax.axvline(np.median(all_maes), color='darkorange', linestyle='--', lw=2, label=f'Median={np.median(all_maes):.4f}')
ax.set_xlabel('Curve MAE (dB)', fontsize=12); ax.set_title('MAE Distribution', fontsize=13, fontweight='bold')
ax.legend(fontsize=10); ax.grid(alpha=0.2, axis='y')

plt.suptitle('Forward Model — Error Analysis', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 'forward_error_analysis.png'), dpi=180, bbox_inches='tight')
plt.close()
print("  forward_error_analysis.png")

# Fig 3: Inverse candidates
fig, axes = plt.subplots(2, 3, figsize=(18, 11))
show_search = list(search_results.keys())[:6]
colors = ['#DC2626', '#2563EB', '#F97316', '#8B5CF6', '#059669']

for i, idx in enumerate(show_search[:6]):
    ax = axes[i//3, i%3]
    ax.plot(frequency, y_db[idx], '-', color='black', linewidth=2.5, label=f'Target', zorder=10)
    cands = search_results[idx]
    for j, c in enumerate(cands[:5]):
        pt = torch.tensor([[c[n] for n in param_cols]], dtype=torch.float32, device=device)
        pred = forward_predict(pt).cpu().numpy()[0]
        ax.plot(frequency, pred, '--', color=colors[j], linewidth=1.5,
                label=f'#{j+1}: {",".join(f"{n}={c[n]:.2f}" for n in param_cols)}')
    ax.set_xlabel('Freq (GHz)', fontsize=9); ax.set_ylabel('S11 (dB)', fontsize=9)
    ax.set_title(f'Sample {idx} | Top1 MAE={cands[0]["curve_mae"]:.4f} dB', fontsize=10, fontweight='bold')
    ax.legend(fontsize=6, loc='lower right'); ax.grid(alpha=0.2)

plt.suptitle('Inverse Multi-Candidate Search', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 'inverse_candidates.png'), dpi=180, bbox_inches='tight')
plt.close()
print("  inverse_candidates.png")

# Fig 4: Best & Worst
fig, axes = plt.subplots(2, 2, figsize=(16, 12))
for j, (label, sidx) in enumerate([('Best', sorted_mae[0]), ('2nd Best', sorted_mae[1]),
                                    ('Worst', sorted_mae[-1]), ('2nd Worst', sorted_mae[-2])]):
    ax = axes[j//2, j%2]
    idx = sidx
    ax.plot(frequency, y_db[idx], '-', color='#2563EB', linewidth=2.8, label='True')
    ax.plot(frequency, pred_all[idx], '--', color='#DC2626', linewidth=2.5, label='Predicted')
    ax.fill_between(frequency, y_db[idx], pred_all[idx], alpha=0.10, color='gray')
    ti = np.argmin(y_db[idx]); pi = np.argmin(pred_all[idx])
    ax.scatter(frequency[ti], y_db[idx, ti], color='#2563EB', s=120, zorder=5)
    ax.scatter(frequency[pi], pred_all[idx, pi], color='#DC2626', s=120, marker='D', zorder=5)
    params_str = ', '.join(f'{n}={X[idx,j]:.1f}' for j, n in enumerate(param_cols))
    ax.set_title(f'{label} Fit | {params_str} | MAE={all_maes[idx]:.4f} dB', fontsize=13, fontweight='bold')
    ax.set_xlabel('Freq (GHz)', fontsize=11); ax.set_ylabel('S11 (dB)', fontsize=11)
    ax.legend(fontsize=9, loc='lower right'); ax.grid(alpha=0.2)

plt.suptitle('Forward Model — Best & Worst Fits', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 'forward_best_worst.png'), dpi=180, bbox_inches='tight')
plt.close()
print("  forward_best_worst.png")

# ==================== 6. Summary ====================
summary = {
    "data": {"file": "Port_S_data_三个参数.txt", "samples": N, "freq_range": f"{frequency[0]:.4f}-{frequency[-1]:.4f} GHz",
             "freq_points": F, "params": param_cols,
             "l3": [float(l3_vals[0]), float(l3_vals[-1])],
             "w4": [float(w4_vals[0]), float(w4_vals[-1])],
             "h": [float(h_vals[0]), float(h_vals[-1])]},
    "forward": {
        "cv_mae_mean": float(np.mean([m['MAE'] for m in cv_fwd])),
        "cv_mae_std": float(np.std([m['MAE'] for m in cv_fwd])),
        "cv_freq_err_mean_MHz": float(np.mean([m['FreqErr_MHz'] for m in cv_fwd])),
        "cv_depth_err_mean_dB": float(np.mean([m['DepthErr'] for m in cv_fwd])),
        "final_mae_mean": float(np.mean(all_maes)),
        "final_mae_median": float(np.median(all_maes)),
        "final_freq_err_mean_MHz": float(np.mean(freq_errs)),
        "final_freq_err_median_MHz": float(np.median(freq_errs)),
        "pca_dim": 35, "pca_var": float(pca_f.explained_variance_ratio_.sum()),
        "best_epoch": best_e,
    },
    "inverse": {
        "cv_param_mae_mean": float(np.mean(cv_inv_param)),
        "cv_param_mae_std": float(np.std(cv_inv_param)),
        "cv_curve_mae_mean_dB": float(np.mean(cv_inv_param)),  # direct regression, no curve loss in CV
        "final_param_mae": {n: float(param_mae[i]) for i, n in enumerate(param_cols)},
        "final_rec_mae_mean_dB": float(np.mean(rec_maes)),
        "final_rec_mae_median_dB": float(np.median(rec_maes)),
        "best_epoch": best_e,
    },
}
with open(os.path.join(BASE, 'results_summary.json'), 'w') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)

print("\n" + "=" * 60)
print("ALL DONE!")
print(f"\nOutput: {BASE}/")
for fn in sorted(os.listdir(BASE)):
    sz = os.path.getsize(os.path.join(BASE, fn))
    print(f"  {fn} ({sz/1024:.0f} KB)")
