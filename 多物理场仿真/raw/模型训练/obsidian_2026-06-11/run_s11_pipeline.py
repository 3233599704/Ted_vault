#!/usr/bin/env python3
"""
S11 参数化天线 — 一键训练 Pipeline
=====================================
用法: python run_s11_pipeline.py <输入文件.txt> [选项]

自动完成: 格式转换 → 正向模型 → 逆向模型 → 多候选搜索 → 可视化

选项:
  --test-size FLOAT     验证集比例 (default: 0.3)
  --k-folds INT         交叉验证折数 (default: 5)
  --pca-dim INT         PCA 维度 (default: auto, 99.9% 方差)
  --quick               快速模式 (减少 epoch 和搜索起点)
  --no-candidates       跳过逆向候选搜索
  --device STR          设备: cpu / cuda (default: auto)

示例:
  python run_s11_pipeline.py Port_S_data_三个参数.txt
  python run_s11_pipeline.py data.txt --quick --test-size 0.2
"""
import sys, os, re, json, copy, warnings, argparse, time
from pathlib import Path

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

# ============================================================
# 0. 命令行参数
# ============================================================
parser = argparse.ArgumentParser(description="S11 Pipeline — 一键训练")
parser.add_argument("input", help="输入 .txt 文件路径")
parser.add_argument("--test-size", type=float, default=0.3, help="验证集比例")
parser.add_argument("--k-folds", type=int, default=5, help="交叉验证折数")
parser.add_argument("--pca-dim", type=int, default=None, help="PCA 维度 (auto if not set)")
parser.add_argument("--quick", action="store_true", help="快速模式")
parser.add_argument("--no-candidates", action="store_true", help="跳过候选搜索")
parser.add_argument("--linear", action="store_true", help="在线性幅值域训练（默认 dB 域）")
parser.add_argument("--device", default="auto", help="cpu / cuda")
args = parser.parse_args()

device = torch.device(args.device if args.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
MAX_EPOCHS = 2000 if args.quick else 5000
PATIENCE   = 200 if args.quick else 500
N_STARTS   = 50  if args.quick else 200
N_ITERS_GD = 150 if args.quick else 400

# ============================================================
# 1. 读取 & 解析
# ============================================================
print("=" * 60)
print("S11 Pipeline — Automated Training")
print("=" * 60)
print(f"Input: {args.input}")
print(f"Device: {device} | Quick: {args.quick} | K={args.k_folds}")
print()

# --- 1a. 解析 header ---
with open(args.input) as f:
    header = f.readline().strip()

# 自动检测参数名 — 从 header 中提取所有 "param=value" 对
all_param_pairs = re.findall(r'([a-zA-Z0-9_]+)=([\d.]+)', header)
if not all_param_pairs:
    print("ERROR: Cannot parse header. Expected param=value pairs.")
    sys.exit(1)

# 按出现顺序提取唯一参数名
param_names = []
seen = set()
for pname, _ in all_param_pairs:
    if pname not in seen:
        param_names.append(pname)
        seen.add(pname)
print(f"Detected params: {param_names}")

# 提取所有参数组合
all_param_vals = {p: set() for p in param_names}
for pname, pval in all_param_pairs:
    all_param_vals[pname].add(float(pval))
for p in param_names:
    all_param_vals[p] = sorted(all_param_vals[p])

# 按参数数量分组 tokens，每个曲线 = D 个连续 token
# Extract ALL tokens from header
header_tokens = header.split()
# First token is "%", then D tokens per curve
tokens = header_tokens[1:]  # skip "%"
n_curves = len(tokens) // len(param_names)
print(f"Total curves: {n_curves}")
print(f"Grid: {' × '.join(str(len(all_param_vals[p])) for p in param_names)} = {n_curves}")
for p in param_names:
    print(f"  {p}: {all_param_vals[p]} ({len(all_param_vals[p])} values)")
print(f"Total curves: {n_curves}")
print(f"Grid: {' × '.join(str(len(all_param_vals[p])) for p in param_names)} = {n_curves}")

# --- 1b. 读取数据 ---
df = pd.read_csv(args.input, sep=r'\s+', skiprows=[0,1,2,3], header=None, dtype=float, engine='python')
freq_hz = df.iloc[:, 0].values
s11_raw = df.iloc[:, 1:].values

print(f"Frequency: {freq_hz[0]/1e9:.4f} ~ {freq_hz[-1]/1e9:.4f} GHz, {len(freq_hz)} points")
print(f"Data shape: {df.shape[0]} freq × {s11_raw.shape[1]} curves")

# --- 1c. 构建 Excel ---
rows = []
for idx in range(n_curves):
    row = {}
    for p_idx, p in enumerate(param_names):
        token = tokens[idx * len(param_names) + p_idx]
        pv = re.search(rf'{p}=([\d.]+)', token)
        row[p] = float(pv.group(1)) if pv else np.nan
    row['Unnamed: 15'] = np.nan
    for j, f in enumerate(freq_hz):
        row[f'{f/1e9:.6f}'] = s11_raw[j, idx]
    rows.append(row)

df_out = pd.DataFrame(rows)
fcols = sorted([c for c in df_out.columns if c not in param_names + ['Unnamed: 15']], key=lambda x: float(x))
df_out = df_out[param_names + ['Unnamed: 15'] + fcols]

# --- 1d. 创建输出目录 ---
input_stem = Path(args.input).stem
timestamp = time.strftime("%Y%m%d_%H%M%S")
out_dir = Path(args.input).parent / f"{input_stem}_pipeline_{timestamp}"
out_dir.mkdir(exist_ok=True)
xlsx_path = out_dir / f"{input_stem}.xlsx"
df_out.to_excel(xlsx_path, index=False, engine='openpyxl')
print(f"\nOutput dir: {out_dir}")
print(f"Excel: {xlsx_path.name}")

# --- 1e. 准备数组 ---
X = df_out[param_names].to_numpy(dtype=np.float32)
y_db_original = df_out.iloc[:, len(param_names)+1:].to_numpy(dtype=np.float32)
frequency = df_out.columns[len(param_names)+1:].astype(float).to_numpy()
N, F, D = len(X), len(frequency), len(param_names)

# Domain selection
if args.linear:
    print(f"\n*** Training in LINEAR domain (dB → magnitude) ***")
    y_db = 10 ** (y_db_original / 20.0)
    print(f"    dB range: {y_db_original.min():.2f} ~ {y_db_original.max():.2f}")
    print(f"    Linear range: {y_db.min():.4f} ~ {y_db.max():.4f}")
else:
    y_db = y_db_original
    print(f"\n*** Training in dB domain ***")

# ============================================================
# 2. 正向模型
# ============================================================
print("\n" + "=" * 60)
print("2. Forward Model (PCA + MLP)")
print("=" * 60)

np.random.seed(SEED); torch.manual_seed(SEED)
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

def curve_metrics(true, pred):
    # Convert to dB if needed
    if args.linear:
        t = 20 * np.log10(np.clip(true, 0.0001, None))
        p = 20 * np.log10(np.clip(pred, 0.0001, None))
    else:
        t, p = true, pred
    err = p - t
    tmi = np.argmin(t, axis=1); pmi = np.argmin(p, axis=1)
    rows = np.arange(len(t))
    return {
        'MAE': float(np.mean(np.abs(err))),
        'RMSE': float(np.sqrt(np.mean(err**2))),
        'FreqErr_MHz': float(np.mean(np.abs(frequency[pmi] - frequency[tmi])) * 1000),
        'DepthErr': float(np.mean(np.abs(p[rows, pmi] - t[rows, tmi]))),
    }

# 5-Fold CV
K = args.k_folds
kf = KFold(n_splits=K, shuffle=True, random_state=SEED)
cv_fwd = []

for fold, (tr_i, va_i) in enumerate(kf.split(X)):
    print(f"  Fold {fold+1}/{K} ...", end=" ", flush=True)

    x_sc = StandardScaler().fit(X[tr_i])
    X_tr_s = x_sc.transform(X[tr_i]).astype(np.float32)
    X_va_s = x_sc.transform(X[va_i]).astype(np.float32)

    pdim = args.pca_dim if args.pca_dim else min(35, len(tr_i) - 2)
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
    for ep in range(MAX_EPOCHS):
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
        if pat >= PATIENCE: break

    model.load_state_dict(best_s); model.eval()
    with torch.no_grad(): p_va = dec(model(Xva)).cpu().numpy()
    cv_fwd.append(curve_metrics(y_db[va_i], p_va))
    print(f"MAE={cv_fwd[-1]['MAE']:.4f}, FreqErr={cv_fwd[-1]['FreqErr_MHz']:.0f}MHz, Ep={best_e}")

print("\n  Forward CV Summary:")
for k in ['MAE', 'RMSE', 'FreqErr_MHz', 'DepthErr']:
    vals = [m[k] for m in cv_fwd]
    print(f"    {k}: {np.mean(vals):.4f} ± {np.std(vals):.4f}")

# Final forward
print("\n  Training final forward model...")
x_sc_f = StandardScaler().fit(X); X_s_f = x_sc_f.transform(X).astype(np.float32)
pdim_f = args.pca_dim if args.pca_dim else 35
pca_f = PCA(n_components=pdim_f).fit(y_db); y_c_f = pca_f.transform(y_db).astype(np.float32)
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

model_f = ForwardNet(pdim_f).to(device)
opt_f = torch.optim.AdamW(model_f.parameters(), lr=0.001, weight_decay=1e-5)
best_v, best_e, best_s, pat = float('inf'), 0, None, 0
for ep in range(MAX_EPOCHS):
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
    if pat >= PATIENCE: break

model_f.load_state_dict(best_s); model_f.eval()
with torch.no_grad():
    X_all_t = torch.tensor(X_s_f, device=device)
    pred_all = dec_f(model_f(X_all_t)).cpu().numpy()

# Convert back to dB for metrics if trained in linear domain
if args.linear:
    pred_all_db = 20 * np.log10(np.clip(pred_all, 0.0001, None))
    true_db = 20 * np.log10(np.clip(y_db, 0.0001, None))
else:
    pred_all_db = pred_all
    true_db = y_db

all_maes = np.mean(np.abs(pred_all_db - true_db), axis=1)
tmi_all = np.argmin(true_db, axis=1); pmi_all = np.argmin(pred_all_db, axis=1)
freq_errs = np.abs(frequency[pmi_all] - frequency[tmi_all]) * 1000
depth_errs = np.abs(pred_all_db[np.arange(N), pmi_all] - true_db[np.arange(N), tmi_all])

print(f"  PCA var: {pca_f.explained_variance_ratio_.sum():.4%}, Best ep: {best_e}")
print(f"  MAE: mean={all_maes.mean():.4f}, median={np.median(all_maes):.4f}, max={all_maes.max():.4f}")
print(f"  FreqErr: mean={freq_errs.mean():.1f}, median={np.median(freq_errs):.1f} MHz")
print(f"  DepthErr: mean={depth_errs.mean():.4f}, median={np.median(depth_errs):.4f} dB")

# Use dB versions for rest of pipeline (visualization, inverse evaluation)
y_db_disp = true_db
pred_all_disp = pred_all_db

torch.save({
    "model_state": model_f.state_dict(), "param_cols": param_names,
    "x_mean": x_sc_f.mean_, "x_scale": x_sc_f.scale_,
    "coeff_mean": c_sc_f.mean_, "coeff_scale": c_sc_f.scale_,
    "pca_components": pca_f.components_, "pca_mean": pca_f.mean_,
    "frequency": frequency, "input_dim": D, "pca_dim": pdim_f,
}, out_dir / "model_forward.pth")
print("  Saved: model_forward.pth")

# ============================================================
# 3. 逆向模型
# ============================================================
print("\n" + "=" * 60)
print("3. Inverse Model (Direct Regression)")
print("=" * 60)

def extract_features(curves):
    feats = []
    for c in curves:
        fi = np.argmin(c); f_res = frequency[fi]; s11_min = c[fi]
        bws = {}
        for th in [-3, -5, -10]:
            mask = c <= th
            bws[th] = frequency[mask][-1] - frequency[mask][0] if mask.sum() > 1 else 0.0
        integral = np.trapezoid(c, frequency)
        if fi >= 4:
            ls = (c[fi] - c[fi-4]) / (frequency[fi] - frequency[fi-4])
        else: ls = 0.0
        if fi <= len(c) - 5:
            rs = (c[fi+4] - c[fi]) / (frequency[fi+4] - frequency[fi])
        else: rs = 0.0
        feats.append([f_res, s11_min, bws[-3], bws[-5], bws[-10], integral, np.mean(c), np.std(c), ls, rs])
    return np.array(feats, dtype=np.float32)

hand_feats = extract_features(y_db)
feat_scaler = StandardScaler().fit(hand_feats)
hand_feats_s = feat_scaler.transform(hand_feats).astype(np.float32)

pca_inv = PCA(n_components=pdim_f).fit(y_db)
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

cv_inv_param = []
for fold, (tr_i, va_i) in enumerate(kf.split(X)):
    print(f"  Fold {fold+1}/{K} ...", end=" ", flush=True)
    Xi_tr = torch.tensor(X_inv_s[tr_i], device=device); Xi_va = torch.tensor(X_inv_s[va_i], device=device)
    Yp_tr = torch.tensor(y_params_norm[tr_i], dtype=torch.float32, device=device)
    Yp_va = torch.tensor(y_params_norm[va_i], dtype=torch.float32, device=device)

    inv_m = InverseNet(inp_dim, D).to(device)
    opt_i = torch.optim.AdamW(inv_m.parameters(), lr=0.001, weight_decay=1e-5)
    best_v, best_e, best_s, pat = float('inf'), 0, copy.deepcopy(inv_m.state_dict()), 0
    for ep in range(MAX_EPOCHS):
        inv_m.train(); opt_i.zero_grad()
        pp = inv_m(Xi_tr); loss = torch.mean((pp - Yp_tr) ** 2)
        loss.backward(); opt_i.step()
        inv_m.eval()
        with torch.no_grad():
            pp_va = inv_m(Xi_va); vl = torch.mean((pp_va - Yp_va) ** 2)
        if vl.item() < best_v - 1e-10:
            best_v = vl.item(); best_e = ep + 1
            best_s = copy.deepcopy(inv_m.state_dict()); pat = 0
        else: pat += 1
        if pat >= PATIENCE: break

    inv_m.load_state_dict(best_s); inv_m.eval()
    with torch.no_grad(): pva_n = inv_m(Xi_va).cpu().numpy()
    pva_d = pva_n * (y_max - y_min) + y_min
    cv_inv_param.append(np.mean(np.abs(pva_d - y_params[va_i])))
    print(f"ParamMAE={cv_inv_param[-1]:.4f}, Ep={best_e}")

print(f"\n  Inverse CV: Param MAE={np.mean(cv_inv_param):.4f}±{np.std(cv_inv_param):.4f}")

# Final inverse
print("\n  Training final inverse model...")
tr_n = int(N * 0.85)
tr_idx_i = np.random.RandomState(SEED).choice(N, size=tr_n, replace=False)
ho_idx_i = np.setdiff1d(np.arange(N), tr_idx_i)

Xi_tr_f2 = torch.tensor(X_inv_s[tr_idx_i], device=device); Xi_ho_f2 = torch.tensor(X_inv_s[ho_idx_i], device=device)
Yp_tr_f2 = torch.tensor(y_params_norm[tr_idx_i], dtype=torch.float32, device=device)
Yp_ho_f2 = torch.tensor(y_params_norm[ho_idx_i], dtype=torch.float32, device=device)

inv_final = InverseNet(inp_dim, D).to(device)
opt_if = torch.optim.AdamW(inv_final.parameters(), lr=0.001, weight_decay=1e-5)
best_v, best_e, best_s, pat = float('inf'), 0, copy.deepcopy(inv_final.state_dict()), 0
for ep in range(MAX_EPOCHS):
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
    if pat >= PATIENCE: break

inv_final.load_state_dict(best_s); inv_final.eval()
with torch.no_grad(): pall_n = inv_final(torch.tensor(X_inv_s, device=device)).cpu().numpy()
pall_d = pall_n * (y_max - y_min) + y_min
param_mae = np.mean(np.abs(pall_d - y_params), axis=0)

pall_s = x_sc_f.transform(pall_d.astype(np.float32))
with torch.no_grad(): rec_all = dec_f(model_f(torch.tensor(pall_s, device=device))).cpu().numpy()
# Measure in dB
if args.linear:
    rec_all_db = 20 * np.log10(np.clip(rec_all, 0.0001, None))
else:
    rec_all_db = rec_all
rec_maes = np.mean(np.abs(rec_all_db - y_db_disp), axis=1)

print(f"  Best ep: {best_e}")
for i, name in enumerate(param_names):
    print(f"  {name} MAE: {param_mae[i]:.4f}")
print(f"  Rec MAE (via forward): mean={rec_maes.mean():.4f}, median={np.median(rec_maes):.4f}")

torch.save({
    "model_state": inv_final.state_dict(), "pca_inv": pca_inv,
    "feat_scaler": feat_scaler, "inv_scaler": inv_scaler,
    "y_min": y_min, "y_max": y_max, "pca_dim_inv": pdim_f, "inp_dim": inp_dim,
    "frequency": frequency, "param_names": param_names,
}, out_dir / "model_inverse.pth")
print("  Saved: model_inverse.pth")

# ============================================================
# 4. 逆向多候选搜索
# ============================================================
if not args.no_candidates:
    print("\n" + "=" * 60)
    print("4. Inverse Multi-Candidate Search (BGD)")
    print("=" * 60)

    xm_f_t = torch.tensor(x_sc_f.mean_, dtype=torch.float32, device=device)
    xs_f_t = torch.tensor(x_sc_f.scale_, dtype=torch.float32, device=device)

    @torch.no_grad()
    def forward_predict(params):
        ps = (params - xm_f_t) / xs_f_t
        return (model_f(ps) * cst_f + cmt_f) @ pct_f + pmt_f

    def batch_search(target_curve, n_starts=N_STARTS, n_iters=N_ITERS_GD, lr=0.1, top_k=10, dedup=0.3):
        target_t = torch.tensor(target_curve, dtype=torch.float32, device=device)
        ti = int(torch.argmin(target_t))
        target_depth = target_t[ti]; target_freq = freq_t[ti]

        rng = np.random.RandomState(SEED)
        starts = np.stack([rng.uniform(y_min[i], y_max[i], n_starts) for i in range(D)], axis=1)
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
            final_preds_raw = forward_predict(params).cpu().numpy()
        final_params = params.detach().cpu().numpy()

        # Convert to dB for metrics display
        if args.linear:
            final_preds_db = 20 * np.log10(np.clip(final_preds_raw, 0.0001, None))
            target_db = 20 * np.log10(np.clip(target_curve, 0.0001, None))
            target_depth_db = float(20 * np.log10(max(target_depth, 0.0001)))
            target_freq_db = target_freq  # frequency unchanged
        else:
            final_preds_db = final_preds_raw
            target_db = target_curve
            target_depth_db = float(target_depth)
            target_freq_db = target_freq

        candidates = []
        for i in range(n_starts):
            p = final_params[i]; pred = final_preds_db[i]
            mae = np.mean(np.abs(pred - target_db))
            pi2 = np.argmin(pred)
            ferr = np.abs(frequency[pi2] - target_freq_db) * 1000
            derr = np.abs(pred[pi2] - target_depth_db)
            candidates.append({name: float(p[j]) for j, name in enumerate(param_names)}
                              | {'curve_mae': float(mae), 'freq_err_MHz': float(ferr), 'depth_err_dB': float(derr),
                                 'pred_curve_db': pred})
        candidates.sort(key=lambda x: x['curve_mae'])
        unique = []
        for c in candidates:
            dup = any(np.sqrt(sum((c[n] - u[n])**2 for n in param_names)) < dedup for u in unique)
            if not dup: unique.append(c)
            if len(unique) >= top_k: break
        return unique

    n_search = min(30, N)
    sample_indices = sorted(np.concatenate([
        np.argsort(all_maes)[:5], np.argsort(all_maes)[N//4:N//4+5],
        np.argsort(all_maes)[N//2:N//2+5], np.argsort(all_maes)[-5:]
    ])[:n_search])

    search_results = {}
    for idx in sample_indices:
        search_results[idx] = batch_search(y_db[idx])
        if len(search_results) % 5 == 0:
            print(f"    {len(search_results)}/{len(sample_indices)} done...", flush=True)

    csv_rows = []
    for idx, cands in search_results.items():
        for rank, c in enumerate(cands):
            row = {'sample_idx': idx, 'rank': rank+1}
            for n in param_names: row[f'true_{n}'] = float(X[idx, list(param_names).index(n)])
            row.update({n: c[n] for n in param_names})
            row.update({'curve_mae_dB': c['curve_mae'], 'freq_err_MHz': c['freq_err_MHz'], 'depth_err_dB': c['depth_err_dB']})
            # Note: pred_curve_db excluded from CSV (it's an array)
            csv_rows.append(row)
    pd.DataFrame(csv_rows).to_csv(out_dir / 'inverse_candidates.csv', index=False)
    print(f"  Saved: inverse_candidates.csv ({len(csv_rows)} rows)")

# ============================================================
# 5. 可视化
# ============================================================
print("\n" + "=" * 60)
print("5. Visualizations")
print("=" * 60)

# Fig 1: Forward curves grid
fig, axes = plt.subplots(3, 3, figsize=(18, 14))
sorted_mae = np.argsort(all_maes)
show_idx = list(sorted_mae[:3]) + list(sorted_mae[N//3:N//3+3]) + list(sorted_mae[-3:])
for i, idx in enumerate(show_idx):
    ax = axes[i//3, i%3]
    ax.plot(frequency, y_db_disp[idx], '-', color='#2563EB', linewidth=2.2, label='True')
    ax.plot(frequency, pred_all_disp[idx], '--', color='#DC2626', linewidth=2.0, label='Pred')
    ax.fill_between(frequency, y_db_disp[idx], pred_all_disp[idx], alpha=0.08, color='gray')
    ti = np.argmin(y_db_disp[idx]); pi = np.argmin(pred_all_disp[idx])
    ax.scatter(frequency[ti], y_db_disp[idx, ti], color='#2563EB', s=60, zorder=5)
    ax.scatter(frequency[pi], pred_all_disp[idx, pi], color='#DC2626', s=60, marker='D', zorder=5)
    pstr = ', '.join(f'{n}={X[idx,j]:.1f}' for j, n in enumerate(param_names))
    ax.set_title(f'{pstr}  |  MAE={all_maes[idx]:.4f} dB', fontsize=10, fontweight='bold')
    ax.set_xlabel('Freq (GHz)', fontsize=9); ax.set_ylabel('S11 (dB)', fontsize=9)
    ax.legend(fontsize=8, loc='lower right'); ax.grid(alpha=0.2)
plt.suptitle(f'Forward Model — {N} samples, {D} params ({",".join(param_names)})', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
plt.savefig(out_dir / 'forward_curves_grid.png', dpi=180, bbox_inches='tight')
plt.close()
print("  forward_curves_grid.png")

# Fig 2: Error analysis
fig, axes = plt.subplots(1, 2, figsize=(18, 7))
errors = pred_all_disp - y_db_disp
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
plt.savefig(out_dir / 'forward_error_analysis.png', dpi=180, bbox_inches='tight')
plt.close()
print("  forward_error_analysis.png")

# Fig 3: Best & Worst
fig, axes = plt.subplots(2, 2, figsize=(16, 12))
for j, (label, sidx) in enumerate([('Best', sorted_mae[0]), ('2nd Best', sorted_mae[1]),
                                    ('Worst', sorted_mae[-1]), ('2nd Worst', sorted_mae[-2])]):
    ax = axes[j//2, j%2]; idx = sidx
    ax.plot(frequency, y_db_disp[idx], '-', color='#2563EB', linewidth=2.8, label='True')
    ax.plot(frequency, pred_all_disp[idx], '--', color='#DC2626', linewidth=2.5, label='Predicted')
    ax.fill_between(frequency, y_db_disp[idx], pred_all_disp[idx], alpha=0.10, color='gray')
    ti = np.argmin(y_db_disp[idx]); pi = np.argmin(pred_all_disp[idx])
    ax.scatter(frequency[ti], y_db_disp[idx, ti], color='#2563EB', s=120, zorder=5)
    ax.scatter(frequency[pi], pred_all_disp[idx, pi], color='#DC2626', s=120, marker='D', zorder=5)
    pstr = ', '.join(f'{n}={X[idx,j]:.1f}' for j, n in enumerate(param_names))
    ax.set_title(f'{label} Fit | {pstr} | MAE={all_maes[idx]:.4f} dB', fontsize=13, fontweight='bold')
    ax.set_xlabel('Freq (GHz)', fontsize=11); ax.set_ylabel('S11 (dB)', fontsize=11)
    ax.legend(fontsize=9, loc='lower right'); ax.grid(alpha=0.2)
plt.suptitle('Forward Model — Best & Worst Fits', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
plt.savefig(out_dir / 'forward_best_worst.png', dpi=180, bbox_inches='tight')
plt.close()
print("  forward_best_worst.png")

# Fig 4: Inverse candidates (if available)
if not args.no_candidates and len(search_results) > 0:
    fig, axes = plt.subplots(2, 3, figsize=(18, 11))
    show_search = list(search_results.keys())[:6]
    colors = ['#DC2626', '#2563EB', '#F97316', '#8B5CF6', '#059669']
    for i, idx in enumerate(show_search[:6]):
        ax = axes[i//3, i%3]
        ax.plot(frequency, y_db_disp[idx], '-', color='black', linewidth=2.5, label='Target', zorder=10)
        for j, c in enumerate(search_results[idx][:5]):
            # Use pre-computed dB curve from candidate
            pred_db = c.get('pred_curve_db', None)
            if pred_db is None:
                pt = torch.tensor([[c[n] for n in param_names]], dtype=torch.float32, device=device)
                pred_raw = forward_predict(pt).cpu().numpy()[0]
                if args.linear:
                    pred_db = 20 * np.log10(np.clip(pred_raw, 0.0001, None))
                else:
                    pred_db = pred_raw
            ax.plot(frequency, pred_db, '--', color=colors[j], linewidth=1.5,
                    label=f'#{j+1}: {",".join(f"{n}={c[n]:.2f}" for n in param_names)}')
        ax.set_xlabel('Freq (GHz)', fontsize=9); ax.set_ylabel('S11 (dB)', fontsize=9)
        ax.set_title(f'Sample {idx} | Top1 MAE={search_results[idx][0]["curve_mae"]:.4f} dB', fontsize=10, fontweight='bold')
        ax.legend(fontsize=6, loc='lower right'); ax.grid(alpha=0.2)
    plt.suptitle('Inverse Multi-Candidate Search', fontsize=15, fontweight='bold', y=1.01)
    plt.tight_layout()
    plt.savefig(out_dir / 'inverse_candidates.png', dpi=180, bbox_inches='tight')
    plt.close()
    print("  inverse_candidates.png")

# ============================================================
# 6. Summary
# ============================================================
summary = {
    "input_file": args.input,
    "params": param_names,
    "n_samples": N, "n_freq_points": F,
    "freq_range": f"{frequency[0]:.4f}-{frequency[-1]:.4f} GHz",
    "forward": {
        "cv_mae_mean": float(np.mean([m['MAE'] for m in cv_fwd])),
        "cv_mae_std": float(np.std([m['MAE'] for m in cv_fwd])),
        "cv_freq_err_mean_MHz": float(np.mean([m['FreqErr_MHz'] for m in cv_fwd])),
        "final_mae_mean": float(np.mean(all_maes)),
        "final_mae_median": float(np.median(all_maes)),
        "final_freq_err_median_MHz": float(np.median(freq_errs)),
        "pca_dim": pdim_f, "pca_var": float(pca_f.explained_variance_ratio_.sum()),
    },
    "inverse": {
        "cv_param_mae_mean": float(np.mean(cv_inv_param)),
        "cv_param_mae_std": float(np.std(cv_inv_param)),
        "final_param_mae": {n: float(param_mae[i]) for i, n in enumerate(param_names)},
        "final_rec_mae_mean_dB": float(np.mean(rec_maes)),
    },
}
with open(out_dir / 'summary.json', 'w') as f:
    json.dump(summary, f, indent=2, ensure_ascii=False)

print("\n" + "=" * 60)
print("PIPELINE COMPLETE!")
print("=" * 60)
print(f"\nOutput: {out_dir}/")
for fn in sorted(os.listdir(out_dir)):
    sz = os.path.getsize(out_dir / fn)
    print(f"  {fn} ({sz/1024:.0f} KB)")
print(f"\nSummary: {out_dir / 'summary.json'}")
