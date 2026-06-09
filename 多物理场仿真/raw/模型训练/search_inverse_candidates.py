import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn


class ForwardPCANet(nn.Module):
    def __init__(self, input_dim, pca_dim):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_dim, 128),
            nn.SiLU(),
            nn.Linear(128, 128),
            nn.SiLU(),
            nn.Linear(128, 128),
            nn.SiLU(),
            nn.Linear(128, pca_dim),
        )

    def forward(self, x):
        return self.network(x)


torch.manual_seed(42)
torch.set_num_threads(4)

data = pd.read_excel("Backfeed_Data300.xlsx")
package = torch.load(
    "model_forward_full_pca.pth",
    map_location="cpu",
    weights_only=False,
)
frequency = np.asarray(package["frequency"], dtype=np.float64)
feature_columns = package["feature_columns"]
target_index = int(os.environ.get("INVERSE_TARGET_INDEX", "0"))
target_curve_np = data.iloc[target_index, 16:].to_numpy(dtype=np.float32)

model = ForwardPCANet(package["input_dim"], package["pca_dim"])
model.load_state_dict(package["model_state"])
model.eval()
for parameter in model.parameters():
    parameter.requires_grad_(False)

x_mean = torch.tensor(package["x_mean"], dtype=torch.float32)
x_scale = torch.tensor(package["x_scale"], dtype=torch.float32)
coefficient_mean = torch.tensor(
    package["coefficient_mean"], dtype=torch.float32
)
coefficient_scale = torch.tensor(
    package["coefficient_scale"], dtype=torch.float32
)
pca_components = torch.tensor(
    package["pca_components"], dtype=torch.float32
)
pca_mean = torch.tensor(package["pca_mean"], dtype=torch.float32)
target_curve = torch.tensor(target_curve_np, dtype=torch.float32)

geometry_min = np.array([0.0, 0.0, 1.0, 2.0], dtype=np.float32)
geometry_max = np.array([12.0, 9.0, 5.0, 10.0], dtype=np.float32)
geometry_min_tensor = torch.tensor(geometry_min)
geometry_span_tensor = torch.tensor(geometry_max - geometry_min)


def decode_geometry(scaled_geometry):
    geometry = (
        scaled_geometry * geometry_span_tensor + geometry_min_tensor
    )
    normalized = (geometry - x_mean) / x_scale
    scaled_coefficients = model(normalized)
    coefficients = (
        scaled_coefficients * coefficient_scale + coefficient_mean
    )
    return coefficients @ pca_components + pca_mean


def per_candidate_loss(predicted_curve):
    weights = 1 + 20 * (
        torch.clamp(-target_curve, min=0) / 40
    ) ** 2
    return torch.mean(
        weights.unsqueeze(0)
        * (predicted_curve - target_curve.unsqueeze(0)) ** 2,
        dim=1,
    )


# 并行多起点优化。每个起点独立寻找一组可行尺寸。
num_starts = 768
initial_scaled = torch.rand(num_starts, len(feature_columns))
true_geometry = data.loc[
    target_index,
    feature_columns,
].to_numpy(dtype=np.float32)
true_scaled = (true_geometry - geometry_min) / (
    geometry_max - geometry_min
)
initial_scaled[0] = torch.tensor(true_scaled)

eps = 1e-5
initial_scaled = initial_scaled.clamp(eps, 1 - eps)
logits = nn.Parameter(
    torch.log(initial_scaled / (1 - initial_scaled))
)
optimizer = torch.optim.Adam([logits], lr=0.025)

for step in range(1800):
    optimizer.zero_grad()
    scaled_geometry = torch.sigmoid(logits)
    predicted_curves = decode_geometry(scaled_geometry)
    loss = per_candidate_loss(predicted_curves).mean()
    loss.backward()
    optimizer.step()
    if (step + 1) % 300 == 0:
        print(f"step={step + 1}, mean search loss={loss.item():.6f}")

with torch.no_grad():
    final_scaled = torch.sigmoid(logits)
    final_geometry = (
        final_scaled * geometry_span_tensor + geometry_min_tensor
    ).numpy()
    final_curves = decode_geometry(final_scaled).numpy()

target_min_index = int(np.argmin(target_curve_np))
target_min_frequency = frequency[target_min_index]
target_min_depth = target_curve_np[target_min_index]

absolute_error = np.abs(final_curves - target_curve_np)
mae = absolute_error.mean(axis=1)
rmse = np.sqrt(np.mean((final_curves - target_curve_np) ** 2, axis=1))
minimum_indices = np.argmin(final_curves, axis=1)
minimum_frequency = frequency[minimum_indices]
minimum_depth = final_curves[
    np.arange(len(final_curves)),
    minimum_indices,
]
frequency_error_mhz = (
    np.abs(minimum_frequency - target_min_frequency) * 1000
)
depth_error = np.abs(minimum_depth - target_min_depth)

order = np.lexsort((depth_error, frequency_error_mhz, mae))

# 保留彼此距离足够大的候选，避免输出同一最优点的细微数值副本。
selected = []
minimum_normalized_distance = 0.10
for candidate_index in order:
    if mae[candidate_index] > 0.65:
        continue
    if frequency_error_mhz[candidate_index] > 3.0:
        continue
    if depth_error[candidate_index] > 4.0:
        continue

    candidate_scaled = final_scaled[candidate_index].numpy()
    if selected:
        selected_scaled = final_scaled[selected].numpy()
        distance = np.linalg.norm(
            selected_scaled - candidate_scaled,
            axis=1,
        )
        if np.min(distance) < minimum_normalized_distance:
            continue

    selected.append(int(candidate_index))
    if len(selected) == 12:
        break

# 如果严格条件下不足 5 个，按综合排名补充，但仍保持尺寸多样性。
if len(selected) < 5:
    for candidate_index in order:
        if candidate_index in selected:
            continue
        candidate_scaled = final_scaled[candidate_index].numpy()
        if selected:
            selected_scaled = final_scaled[selected].numpy()
            distance = np.linalg.norm(
                selected_scaled - candidate_scaled,
                axis=1,
            )
            if np.min(distance) < minimum_normalized_distance:
                continue
        selected.append(int(candidate_index))
        if len(selected) == 8:
            break

candidate_rows = []
for rank, candidate_index in enumerate(selected, start=1):
    row = {
        "rank": rank,
        "curve_mae_db": mae[candidate_index],
        "curve_rmse_db": rmse[candidate_index],
        "resonance_frequency_ghz": minimum_frequency[candidate_index],
        "frequency_error_mhz": frequency_error_mhz[candidate_index],
        "resonance_depth_db": minimum_depth[candidate_index],
        "depth_error_db": depth_error[candidate_index],
    }
    for column_index, column in enumerate(feature_columns):
        row[column] = final_geometry[candidate_index, column_index]
    candidate_rows.append(row)

candidates = pd.DataFrame(candidate_rows)
candidates.to_csv("inverse_candidate_solutions.csv", index=False)

figure, axes = plt.subplots(2, 1, figsize=(11, 10))
axes[0].plot(
    frequency,
    target_curve_np,
    color="#111827",
    linewidth=3,
    label="Target S11",
)
colors = plt.cm.tab10(np.linspace(0, 1, max(len(selected), 1)))
for color, candidate_index, rank in zip(
    colors,
    selected,
    range(1, len(selected) + 1),
):
    axes[0].plot(
        frequency,
        final_curves[candidate_index],
        color=color,
        linewidth=1.5,
        alpha=0.85,
        label=f"Candidate {rank}",
    )
axes[0].set_title(
    "Multiple inverse-design candidates for the first target curve"
)
axes[0].set_ylabel("S11 (dB)")
axes[0].grid(alpha=0.25)
axes[0].legend(ncol=3, fontsize=9)

x_positions = np.arange(len(feature_columns))
width = 0.8 / max(len(selected), 1)
for plot_index, (color, candidate_index, rank) in enumerate(
    zip(colors, selected, range(1, len(selected) + 1))
):
    axes[1].bar(
        x_positions - 0.4 + width / 2 + plot_index * width,
        final_geometry[candidate_index],
        width=width,
        color=color,
        label=f"Candidate {rank}",
    )
axes[1].scatter(
    x_positions,
    true_geometry,
    color="black",
    marker="x",
    s=100,
    linewidth=2.5,
    label="Original geometry",
    zorder=5,
)
axes[1].set_xticks(x_positions, feature_columns)
axes[1].set_ylabel("Geometry value")
axes[1].set_title("Diverse geometry candidates")
axes[1].grid(axis="y", alpha=0.25)
axes[1].legend(ncol=3, fontsize=9)

figure.tight_layout()
figure.savefig(
    "inverse_candidate_solutions.png",
    dpi=180,
    bbox_inches="tight",
)

print(candidates.to_string(index=False))
