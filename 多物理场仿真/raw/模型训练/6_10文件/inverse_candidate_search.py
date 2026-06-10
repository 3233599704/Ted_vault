"""
逆向多候选搜索：给定目标 S11 曲线，找到所有可能的 (l3, w4) 候选尺寸。
使用梯度下降 + 多起点优化，基于冻结的正向模型。
"""
import os, warnings, json
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
SEED = 42

# ========== Load forward model ==========
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

xm_t = torch.tensor(pkg['x_mean'], dtype=torch.float32, device=device)
xs_t = torch.tensor(pkg['x_scale'], dtype=torch.float32, device=device)
pc_t = torch.tensor(pkg['pca_components'], dtype=torch.float32, device=device)
pm_t = torch.tensor(pkg['pca_mean'], dtype=torch.float32, device=device)
cs_t = torch.tensor(pkg['coeff_scale'], dtype=torch.float32, device=device)
cm_t = torch.tensor(pkg['coeff_mean'], dtype=torch.float32, device=device)
frequency = pkg['frequency']

def forward_predict_torch(params_t):
    """params_t: (n, 2) tensor -> (n, F) tensor"""
    ps = (params_t - xm_t) / xs_t
    with torch.no_grad():
        return (model(ps) * cs_t + cm_t) @ pc_t + pm_t

# ========== Load data ==========
df = pd.read_excel(os.path.join(BASE, "Port_S_data_2_1-8GHz.xlsx"))
X_all = df[['l3', 'w4']].to_numpy(dtype=np.float32)
y_all = df.iloc[:, 3:].to_numpy(dtype=np.float32)
l3_min, l3_max = X_all[:, 0].min(), X_all[:, 0].max()
w4_min, w4_max = X_all[:, 1].min(), X_all[:, 1].max()

def calc_metrics(target, pred):
    """Calculate key metrics for candidate ranking."""
    curve_mae = np.mean(np.abs(pred - target))
    ti = np.argmin(target); pi = np.argmin(pred)
    freq_err = np.abs(frequency[pi] - frequency[ti]) * 1000  # MHz
    depth_err = np.abs(pred[pi] - target[ti])
    return curve_mae, freq_err, depth_err

def search_candidates(target_curve, n_starts=200, n_iters=500, lr=0.05,
                      top_k=10, dedup_threshold=0.3):
    """
    多起点梯度下降搜索逆向候选尺寸。

    Parameters:
    - target_curve: (F,) numpy array, target S11 curve in dB
    - n_starts: number of random starting points
    - n_iters: optimization iterations per start
    - lr: learning rate for Adam
    - top_k: number of top candidates to return
    - dedup_threshold: minimum Euclidean distance between candidates in (l3,w4) space
    """
    target_t = torch.tensor(target_curve, dtype=torch.float32, device=device)

    # Generate random starts across the valid range
    rng = np.random.RandomState(SEED)
    starts_l3 = rng.uniform(l3_min, l3_max, n_starts)
    starts_w4 = rng.uniform(w4_min, w4_max, n_starts)
    starts = np.stack([starts_l3, starts_w4], axis=1)

    all_candidates = []

    for i in range(n_starts):
        # Initialize optimizable parameter
        params = torch.tensor(starts[i:i+1], dtype=torch.float32, device=device, requires_grad=True)
        opt = torch.optim.Adam([params], lr=lr)

        best_loss = float('inf')
        best_params = None

        for _ in range(n_iters):
            opt.zero_grad()
            pred = forward_predict_torch(params)

            # Multi-objective loss: weighted MSE + resonance penalty
            w = 1 + 25 * (torch.clamp(-target_t, min=0) / 40) ** 2
            mse = torch.mean(w * (pred - target_t) ** 2)

            # Resonance-aware penalty
            pi = torch.argmin(pred, dim=1)
            ti = torch.argmin(target_t)
            freq_penalty = ((frequency[pi] - frequency[ti]) / (frequency[-1] - frequency[0])) ** 2
            depth_penalty = (pred[0, pi] - target_t[ti]) ** 2

            # Boundary penalty (soft constraint)
            bound_penalty = (torch.clamp(params[0, 0] - l3_max, min=0) ** 2 +
                             torch.clamp(l3_min - params[0, 0], min=0) ** 2 +
                             torch.clamp(params[0, 1] - w4_max, min=0) ** 2 +
                             torch.clamp(w4_min - params[0, 1], min=0) ** 2)

            loss = mse + 0.3 * (freq_penalty + depth_penalty) + 10.0 * bound_penalty
            loss.backward()
            opt.step()

            # Clamp to valid range
            with torch.no_grad():
                params[0, 0].clamp_(l3_min - 0.5, l3_max + 0.5)
                params[0, 1].clamp_(w4_min - 0.2, w4_max + 0.2)

            if loss.item() < best_loss:
                best_loss = loss.item()
                best_params = params.detach().clone()

        if best_params is not None:
            l3, w4 = best_params[0].cpu().numpy()
            # Clamp to valid range
            l3 = np.clip(l3, l3_min, l3_max)
            w4 = np.clip(w4, w4_min, w4_max)
            pred_curve = forward_predict_torch(torch.tensor([[l3, w4]], dtype=torch.float32, device=device)).cpu().numpy()[0]
            mae, ferr, derr = calc_metrics(target_curve, pred_curve)
            all_candidates.append({
                'l3': l3, 'w4': w4,
                'curve_mae': mae, 'freq_err_MHz': ferr, 'depth_err_dB': derr,
                'pred_curve': pred_curve,
            })

    # Sort by curve MAE
    all_candidates.sort(key=lambda x: x['curve_mae'])

    # Deduplicate: remove candidates too close to better ones
    unique = []
    for c in all_candidates:
        is_dup = False
        for u in unique:
            dist = np.sqrt((c['l3'] - u['l3'])**2 + (c['w4'] - u['w4'])**2)
            if dist < dedup_threshold:
                is_dup = True
                break
        if not is_dup:
            unique.append(c)
        if len(unique) >= top_k:
            break

    return unique

# ========== Test: search candidates for all 72 curves ==========
print("Running inverse candidate search for all 72 samples...")
print(f"  Search space: l3=[{l3_min:.0f},{l3_max:.0f}], w4=[{w4_min:.1f},{w4_max:.1f}]")
print(f"  Starts: 200, Iters: 500, Top-K: 10\n")

all_results = []
for idx in range(len(X_all)):
    true_l3, true_w4 = X_all[idx]
    target = y_all[idx]
    candidates = search_candidates(target, n_starts=200, n_iters=500, top_k=10, dedup_threshold=0.3)

    # Find if true params are in candidates (or close)
    true_in_candidates = any(
        np.sqrt((c['l3']-true_l3)**2 + (c['w4']-true_w4)**2) < 0.5
        for c in candidates
    )

    all_results.append({
        'idx': idx,
        'true_l3': float(true_l3), 'true_w4': float(true_w4),
        'true_in_top10': true_in_candidates,
        'top1_l3': candidates[0]['l3'], 'top1_w4': candidates[0]['w4'],
        'top1_mae': candidates[0]['curve_mae'],
        'top1_freq_err': candidates[0]['freq_err_MHz'],
        'top1_depth_err': candidates[0]['depth_err_dB'],
        'n_unique': len(candidates),
        'candidates': [{k: v for k, v in c.items() if k != 'pred_curve'}
                       for c in candidates],
    })

    if (idx + 1) % 10 == 0:
        print(f"  [{idx+1:3d}/{len(X_all)}] done...")

# ========== Summary ==========
true_recall = sum(1 for r in all_results if r['true_in_top10']) / len(all_results)
top1_maes = [r['top1_mae'] for r in all_results]
top1_freq_errs = [r['top1_freq_err'] for r in all_results]
n_candidates = [r['n_unique'] for r in all_results]

print(f"\n{'='*60}")
print(f"RESULTS SUMMARY")
print(f"{'='*60}")
print(f"  True params in Top-10: {true_recall:.1%}")
print(f"  Top-1 Curve MAE: mean={np.mean(top1_maes):.4f}, median={np.median(top1_maes):.4f}, max={np.max(top1_maes):.4f} dB")
print(f"  Top-1 Freq Error: mean={np.mean(top1_freq_errs):.1f}, median={np.median(top1_freq_errs):.1f} MHz")
print(f"  Avg unique candidates: {np.mean(n_candidates):.1f}")

# ========== Save all candidates to CSV ==========
csv_rows = []
for r in all_results:
    for rank, c in enumerate(r['candidates']):
        csv_rows.append({
            'sample_idx': r['idx'],
            'true_l3': r['true_l3'],
            'true_w4': r['true_w4'],
            'rank': rank + 1,
            'l3': c['l3'],
            'w4': c['w4'],
            'curve_mae_dB': c['curve_mae'],
            'freq_err_MHz': c['freq_err_MHz'],
            'depth_err_dB': c['depth_err_dB'],
        })

csv_df = pd.DataFrame(csv_rows)
csv_path = os.path.join(BASE, 'inverse_all_candidates.csv')
csv_df.to_csv(csv_path, index=False)
print(f"\nSaved: {csv_path} ({len(csv_df)} rows)")

# ========== Visualization ==========
print("\nGenerating figures...")

# Fig 1: Top candidate reconstruction for 9 representative samples
sorted_by_mae = sorted(all_results, key=lambda r: r['top1_mae'])
selected = [sorted_by_mae[0], sorted_by_mae[len(sorted_by_mae)//4],
            sorted_by_mae[len(sorted_by_mae)//2],
            sorted_by_mae[3*len(sorted_by_mae)//4], sorted_by_mae[-1]]

fig, axes = plt.subplots(2, 3, figsize=(18, 11))

# Row 1: 3 samples — each shows target + top-3 candidate reconstructions
for i, result in enumerate(selected[:3]):
    ax = axes[0, i]
    idx = result['idx']
    ax.plot(frequency, y_all[idx], '-', color='black', linewidth=2.5, label=f'Target (l3={X_all[idx,0]:.0f}, w4={X_all[idx,1]:.1f})')

    colors = ['#DC2626', '#2563EB', '#F97316']
    cands = search_candidates(y_all[idx], n_starts=200, n_iters=500, top_k=5, dedup_threshold=0.3)
    for j, c in enumerate(cands[:3]):
        ax.plot(frequency, forward_predict_torch(
            torch.tensor([[c['l3'], c['w4']]], dtype=torch.float32, device=device)
        ).cpu().numpy()[0], '--', color=colors[j], linewidth=1.8,
                label=f'#{j+1}: l3={c["l3"]:.2f}, w4={c["w4"]:.2f} (MAE={c["curve_mae"]:.3f})')

    ax.set_xlabel('Freq (GHz)', fontsize=10); ax.set_ylabel('S11 (dB)', fontsize=10)
    ax.set_title(f'Sample {idx} — {len(cands)} unique candidates', fontsize=12, fontweight='bold')
    ax.legend(fontsize=7, loc='lower right'); ax.grid(alpha=0.25)

# Row 2: 2 more samples + scatter of all candidates vs truth
for i, result in enumerate(selected[3:]):
    ax = axes[1, i]
    idx = result['idx']
    ax.plot(frequency, y_all[idx], '-', color='black', linewidth=2.5, label=f'Target')
    cands = search_candidates(y_all[idx], n_starts=200, n_iters=500, top_k=5, dedup_threshold=0.3)
    for j, c in enumerate(cands[:3]):
        ax.plot(frequency, forward_predict_torch(
            torch.tensor([[c['l3'], c['w4']]], dtype=torch.float32, device=device)
        ).cpu().numpy()[0], '--', color=colors[j], linewidth=1.8,
                label=f'#{j+1}: l3={c["l3"]:.2f}, w4={c["w4"]:.2f}')
    ax.set_xlabel('Freq (GHz)', fontsize=10); ax.set_ylabel('S11 (dB)', fontsize=10)
    ax.set_title(f'Sample {idx} — MAE={result["top1_mae"]:.3f} dB', fontsize=12, fontweight='bold')
    ax.legend(fontsize=7, loc='lower right'); ax.grid(alpha=0.25)

# Panel 6: Scatter of Top-1 predictions vs True
ax = axes[1, 2]
top1_l3s = [r['top1_l3'] for r in all_results]
top1_w4s = [r['top1_w4'] for r in all_results]
sc = ax.scatter(X_all[:, 0], X_all[:, 1], c=top1_maes, cmap='RdYlGn_r', s=40, edgecolors='gray', lw=0.3, label='True')
ax.scatter(top1_l3s, top1_w4s, c=top1_maes, cmap='RdYlGn_r', s=40, marker='x', lw=1.0, label='Top-1 Pred')
for i in range(len(X_all)):
    ax.plot([X_all[i, 0], top1_l3s[i]], [X_all[i, 1], top1_w4s[i]],
            '-', color='gray', alpha=0.15, lw=0.5)
plt.colorbar(sc, ax=ax, label='Curve MAE (dB)')
ax.set_xlabel('l3'); ax.set_ylabel('w4')
ax.set_title(f'Top-1 Inverse: True vs Pred\nRecall@Top10={true_recall:.1%}', fontsize=11, fontweight='bold')
ax.legend(fontsize=8)

plt.suptitle('Inverse Candidate Search — Multi-Start Gradient Descent', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 'inverse_candidates.png'), dpi=180, bbox_inches='tight')
plt.close()
print("Figure saved: inverse_candidates.png")

# Fig 2: Detailed view of 2 samples with ALL unique candidates
fig, axes = plt.subplots(1, 2, figsize=(16, 7))

for panel, sample_idx in enumerate([0, len(X_all)//2]):
    ax = axes[panel]
    idx = sample_idx
    target = y_all[idx]

    # Get lots of candidates
    cands = search_candidates(target, n_starts=500, n_iters=800, top_k=15, dedup_threshold=0.25)

    # Plot target
    ax.plot(frequency, target, '-', color='black', linewidth=3.0, label=f'Target (l3={X_all[idx,0]:.0f}, w4={X_all[idx,1]:.1f})', zorder=10)

    # Plot all candidate reconstructions with alpha
    cmap = plt.cm.viridis
    for j, c in enumerate(cands):
        alpha = 0.3 + 0.7 * (1 - j / len(cands))
        pred = forward_predict_torch(
            torch.tensor([[c['l3'], c['w4']]], dtype=torch.float32, device=device)
        ).cpu().numpy()[0]
        ax.plot(frequency, pred, '--', color=cmap(j / len(cands)), linewidth=1.2, alpha=alpha,
                label=f'#{j+1}: l3={c["l3"]:.2f}, w4={c["w4"]:.2f}' if j < 5 else '')

    ax.set_xlabel('Frequency (GHz)', fontsize=12)
    ax.set_ylabel('S(1,1) (dB)', fontsize=12)
    ax.set_title(f'Sample {idx}: {len(cands)} Unique Candidates', fontsize=13, fontweight='bold')
    ax.legend(fontsize=7.5, loc='lower right', ncol=1)
    ax.grid(alpha=0.2)

plt.suptitle('Inverse Multi-Candidate Search — Detailed View', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 'inverse_candidates_detailed.png'), dpi=180, bbox_inches='tight')
plt.close()
print("Figure saved: inverse_candidates_detailed.png")

print("\n=== DONE ===")
print(f"Outputs in: {BASE}/")
print(f"  inverse_all_candidates.csv — all candidate sizes for all 72 samples")
print(f"  inverse_candidates.png — overview figure")
print(f"  inverse_candidates_detailed.png — detailed view")
