"""
Fast inverse candidate search — batch all starts into one tensor.
"""
import os, sys, warnings, json
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
freq_t = torch.tensor(frequency, dtype=torch.float32, device=device)  # tensor version for indexing

@torch.no_grad()
def forward_predict(params):
    """params: (N, 2) tensor -> (N, F) tensor"""
    ps = (params - xm_t) / xs_t
    return (model(ps) * cs_t + cm_t) @ pc_t + pm_t

# ========== Load data ==========
df = pd.read_excel(os.path.join(BASE, "Port_S_data_2_1-8GHz.xlsx"))
X_all = df[['l3', 'w4']].to_numpy(dtype=np.float32)
y_all = df.iloc[:, 3:].to_numpy(dtype=np.float32)
l3_min, l3_max = X_all[:, 0].min(), X_all[:, 0].max()
w4_min, w4_max = X_all[:, 1].min(), X_all[:, 1].max()

def batch_search(target_curve, n_starts=100, n_iters=300, lr=0.1, top_k=10, dedup=0.3):
    """
    Batch gradient descent: all starts optimized simultaneously.
    MUCH faster than sequential loops.
    """
    target_t = torch.tensor(target_curve, dtype=torch.float32, device=device)

    # Generate starts
    rng = np.random.RandomState(SEED)
    l3_s = rng.uniform(l3_min, l3_max, n_starts)
    w4_s = rng.uniform(w4_min, w4_max, n_starts)

    params = torch.tensor(np.stack([l3_s, w4_s], axis=1),
                          dtype=torch.float32, device=device, requires_grad=True)
    opt = torch.optim.Adam([params], lr=lr)

    # Precompute target info for loss
    ti = int(torch.argmin(target_t))
    target_depth = target_t[ti]
    target_freq = frequency[ti]

    for _ in range(n_iters):
        opt.zero_grad()
        pred = forward_predict(params)  # (n_starts, F)

        # Weighted MSE (per sample)
        w = 1 + 25 * (torch.clamp(-target_t.unsqueeze(0), min=0) / 40) ** 2
        mse = torch.mean(w * (pred - target_t.unsqueeze(0)) ** 2, dim=1)  # (n_starts,)

        # Resonance penalty
        pi = torch.argmin(pred, dim=1)  # (n_starts,)
        freq_err = ((freq_t[pi] - target_freq) / (freq_t[-1] - freq_t[0])) ** 2
        depth_err = (pred[torch.arange(n_starts), pi] - target_depth) ** 2

        # Boundary penalty
        bp = (torch.clamp(params[:, 0] - l3_max, min=0) ** 2 +
              torch.clamp(l3_min - params[:, 0], min=0) ** 2 +
              torch.clamp(params[:, 1] - w4_max, min=0) ** 2 +
              torch.clamp(w4_min - params[:, 1], min=0) ** 2)

        loss = torch.mean(mse + 0.3 * (freq_err + depth_err) + 10.0 * bp)
        loss.backward()
        opt.step()

        with torch.no_grad():
            params[:, 0].clamp_(l3_min - 0.5, l3_max + 0.5)
            params[:, 1].clamp_(w4_min - 0.2, w4_max + 0.2)

    # Evaluate all converged starts
    with torch.no_grad():
        final_params = params.detach()
        final_params[:, 0].clamp_(l3_min, l3_max)
        final_params[:, 1].clamp_(w4_min, w4_max)
        final_preds = forward_predict(final_params).cpu().numpy()

    candidates = []
    for i in range(n_starts):
        l3, w4 = final_params[i].cpu().numpy()
        pred = final_preds[i]
        mae = np.mean(np.abs(pred - target_curve))
        pi2 = np.argmin(pred)
        ferr = np.abs(frequency[pi2] - target_freq) * 1000
        derr = np.abs(pred[pi2] - target_depth)
        candidates.append({
            'l3': float(l3), 'w4': float(w4),
            'curve_mae': float(mae), 'freq_err_MHz': float(ferr), 'depth_err_dB': float(derr),
        })

    candidates.sort(key=lambda x: x['curve_mae'])

    # Dedup
    unique = []
    for c in candidates:
        dup = any(np.sqrt((c['l3']-u['l3'])**2 + (c['w4']-u['w4'])**2) < dedup for u in unique)
        if not dup:
            unique.append(c)
        if len(unique) >= top_k:
            break
    return unique

# ========== Run for all samples ==========
print(f"Batch search: 100 starts x 300 iters per sample")
print(f"Space: l3=[{l3_min:.0f},{l3_max:.0f}], w4=[{w4_min:.1f},{w4_max:.1f}]")
print()

all_results = []
for idx in range(len(X_all)):
    true_l3, true_w4 = X_all[idx]
    candidates = batch_search(y_all[idx], n_starts=100, n_iters=300, top_k=10, dedup=0.3)

    true_in = any(np.sqrt((c['l3']-true_l3)**2 + (c['w4']-true_w4)**2) < 0.5 for c in candidates)
    all_results.append({
        'idx': idx,
        'true_l3': float(true_l3), 'true_w4': float(true_w4),
        'true_in_top10': true_in,
        'top1_l3': candidates[0]['l3'], 'top1_w4': candidates[0]['w4'],
        'top1_mae': candidates[0]['curve_mae'],
        'top1_freq_err': candidates[0]['freq_err_MHz'],
        'top1_depth_err': candidates[0]['depth_err_dB'],
        'n_unique': len(candidates),
        'candidates': candidates,
    })
    if (idx + 1) % 5 == 0 or idx == 0:
        print(f"  [{idx+1:3d}/{len(X_all)}] Sample {idx}: {len(candidates)} unique, Top1 MAE={candidates[0]['curve_mae']:.4f}", flush=True)

# ========== Summary ==========
true_recall = sum(1 for r in all_results if r['true_in_top10']) / len(all_results)
top1_maes = [r['top1_mae'] for r in all_results]
top1_freq_errs = [r['top1_freq_err'] for r in all_results]

print(f"\n{'='*60}")
print(f"RESULTS")
print(f"{'='*60}")
print(f"  True params in Top-10: {true_recall:.1%}")
print(f"  Top-1 MAE: mean={np.mean(top1_maes):.4f}, median={np.median(top1_maes):.4f}")
print(f"  Top-1 FreqErr: mean={np.mean(top1_freq_errs):.1f} MHz")

# ========== Save CSV ==========
csv_rows = []
for r in all_results:
    for rank, c in enumerate(r['candidates']):
        csv_rows.append({
            'sample_idx': r['idx'], 'true_l3': r['true_l3'], 'true_w4': r['true_w4'],
            'rank': rank + 1, 'l3': c['l3'], 'w4': c['w4'],
            'curve_mae_dB': c['curve_mae'], 'freq_err_MHz': c['freq_err_MHz'],
            'depth_err_dB': c['depth_err_dB'],
        })

csv_df = pd.DataFrame(csv_rows)
csv_path = os.path.join(BASE, 'inverse_all_candidates.csv')
csv_df.to_csv(csv_path, index=False)
print(f"Saved: {csv_path}")

# ========== Visualization ==========
print("Generating figures...")

fig, axes = plt.subplots(2, 3, figsize=(18, 11))

# Get diverse samples
sorted_idx = np.argsort(top1_maes)
samples_to_show = [sorted_idx[0], sorted_idx[len(sorted_idx)//4],
                   sorted_idx[len(sorted_idx)//2], sorted_idx[3*len(sorted_idx)//4],
                   sorted_idx[-1]]

colors = ['#DC2626', '#2563EB', '#F97316', '#8B5CF6', '#059669']

for panel, sample_idx in enumerate(samples_to_show[:5]):
    ax = axes[panel // 3, panel % 3]
    idx = sample_idx
    result = all_results[idx]

    # Target
    ax.plot(frequency, y_all[idx], '-', color='black', linewidth=2.8, zorder=10,
            label=f'Target (l3={X_all[idx,0]:.0f}, w4={X_all[idx,1]:.1f})')

    # Candidates
    for j, c in enumerate(result['candidates'][:5]):
        params_t = torch.tensor([[c['l3'], c['w4']]], dtype=torch.float32, device=device)
        pred = forward_predict(params_t).cpu().numpy()[0]
        ax.plot(frequency, pred, '--', color=colors[j], linewidth=1.5,
                label=f'#{j+1}: l3={c["l3"]:.2f}, w4={c["w4"]:.2f} (MAE={c["curve_mae"]:.3f})')

    ax.set_xlabel('Freq (GHz)', fontsize=10); ax.set_ylabel('S11 (dB)', fontsize=10)
    tag = 'Best' if sample_idx == sorted_idx[0] else ('Worst' if sample_idx == sorted_idx[-1] else f'Rank {np.where(sorted_idx == sample_idx)[0][0]+1}')
    ax.set_title(f'Sample {idx} ({tag}) | Top1 MAE={result["top1_mae"]:.4f} dB', fontsize=11, fontweight='bold')
    ax.legend(fontsize=6.5, loc='lower right'); ax.grid(alpha=0.2)

# Panel 6: scatter
ax = axes[1, 2]
top1_l3s = [r['top1_l3'] for r in all_results]
top1_w4s = [r['top1_w4'] for r in all_results]
sc = ax.scatter(X_all[:, 0], X_all[:, 1], c=top1_maes, cmap='RdYlGn_r', s=35, edgecolors='gray', lw=0.3)
ax.scatter(top1_l3s, top1_w4s, c=top1_maes, cmap='RdYlGn_r', s=35, marker='x', lw=0.8)
for i in range(len(X_all)):
    ax.plot([X_all[i,0], top1_l3s[i]], [X_all[i,1], top1_w4s[i]], '-', color='gray', alpha=0.12, lw=0.4)
plt.colorbar(sc, ax=ax, label='Curve MAE (dB)')
ax.set_xlabel('l3'); ax.set_ylabel('w4')
ax.set_title(f'Top-1 Inverse Predictions\nRecall@Top10={true_recall:.1%}', fontsize=11, fontweight='bold')

plt.suptitle('Inverse Multi-Candidate Search — Batch Gradient Descent', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 'inverse_candidates.png'), dpi=180, bbox_inches='tight')
plt.close()
print("Figure: inverse_candidates.png")

# Detailed view for 2 samples
fig, axes = plt.subplots(1, 2, figsize=(16, 7))
for panel, si in enumerate([sorted_idx[0], sorted_idx[2]]):
    ax = axes[panel]
    result = all_results[si]
    ax.plot(frequency, y_all[si], '-', color='black', linewidth=3.0,
            label=f'Target (l3={X_all[si,0]:.0f}, w4={X_all[si,1]:.1f})', zorder=10)
    cmap = plt.cm.viridis
    for j, c in enumerate(result['candidates']):
        params_t = torch.tensor([[c['l3'], c['w4']]], dtype=torch.float32, device=device)
        pred = forward_predict(params_t).cpu().numpy()[0]
        alpha = 0.3 + 0.7 * (1 - j / len(result['candidates']))
        ax.plot(frequency, pred, '--', color=cmap(j / len(result['candidates'])), linewidth=1.3,
                alpha=alpha, label=f'#{j+1}: l3={c["l3"]:.2f}, w4={c["w4"]:.2f}' if j < 5 else '')
    ax.set_xlabel('Freq (GHz)', fontsize=12); ax.set_ylabel('S11 (dB)', fontsize=12)
    ax.set_title(f'Sample {si}: {len(result["candidates"])} Candidates | Top1 MAE={result["top1_mae"]:.4f}', fontsize=13, fontweight='bold')
    ax.legend(fontsize=7, loc='lower right'); ax.grid(alpha=0.2)
plt.suptitle('Inverse Candidate Search — Detailed View', fontsize=15, fontweight='bold', y=1.01)
plt.tight_layout()
fig.savefig(os.path.join(BASE, 'inverse_candidates_detailed.png'), dpi=180, bbox_inches='tight')
plt.close()
print("Figure: inverse_candidates_detailed.png")

print("\n=== DONE ===")
