from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.model_selection import train_test_split


class FullBandPCANet(nn.Module):
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


data = pd.read_excel("Backfeed_Data300.xlsx")
package = torch.load(
    "model_forward_full_pca.pth",
    map_location="cpu",
    weights_only=False,
)

features = package["feature_columns"]
inputs = data.loc[:, features].to_numpy(dtype=np.float64)
scaled_inputs = (
    (inputs - package["x_mean"]) / package["x_scale"]
).astype(np.float32)
true_db = data.iloc[:, 16:].to_numpy(dtype=np.float64)
frequency = np.asarray(package["frequency"], dtype=np.float64)

model = FullBandPCANet(package["input_dim"], package["pca_dim"])
model.load_state_dict(package["model_state"])
model.eval()

with torch.no_grad():
    scaled_coefficients = model(torch.tensor(scaled_inputs)).numpy()

coefficients = (
    scaled_coefficients * package["coefficient_scale"]
    + package["coefficient_mean"]
)
predicted_db = (
    coefficients @ package["pca_components"] + package["pca_mean"]
)

indices = np.arange(len(data))
train_indices, val_indices = train_test_split(
    indices,
    test_size=0.3,
    random_state=42,
    shuffle=True,
)
split = np.full(len(data), "train", dtype=object)
split[val_indices] = "validation"

error = predicted_db - true_db
mae = np.mean(np.abs(error), axis=1)
rmse = np.sqrt(np.mean(error**2, axis=1))

true_min_index = np.argmin(true_db, axis=1)
predicted_min_index = np.argmin(predicted_db, axis=1)
rows = np.arange(len(data))
true_min_db = true_db[rows, true_min_index]
predicted_min_db = predicted_db[rows, predicted_min_index]
true_min_frequency = frequency[true_min_index]
predicted_min_frequency = frequency[predicted_min_index]

metrics = pd.DataFrame(
    {
        "data_index": indices,
        "excel_row": indices + 2,
        "split": split,
        "mae_db": mae,
        "rmse_db": rmse,
        "true_min_db": true_min_db,
        "pred_min_db": predicted_min_db,
        "depth_error_db": np.abs(predicted_min_db - true_min_db),
        "true_min_freq_ghz": true_min_frequency,
        "pred_min_freq_ghz": predicted_min_frequency,
        "freq_error_mhz": (
            np.abs(predicted_min_frequency - true_min_frequency) * 1000
        ),
    }
)


def representative_indices(group_indices):
    ordered = metrics.loc[group_indices].sort_values("mae_db")
    positions = [0, len(ordered) // 2, len(ordered) - 1]
    return ordered.iloc[positions]["data_index"].astype(int).tolist()


train_examples = representative_indices(train_indices)
validation_examples = representative_indices(val_indices)
selected_indices = train_examples + validation_examples

figure, axes = plt.subplots(3, 2, figsize=(14, 13), sharex=True)
for axis, row_index in zip(axes.flat, selected_indices):
    row_metrics = metrics.loc[row_index]
    axis.plot(
        frequency,
        true_db[row_index],
        color="#2563EB",
        linewidth=2.2,
        label="True",
    )
    axis.plot(
        frequency,
        predicted_db[row_index],
        color="#DC2626",
        linewidth=1.8,
        linestyle="--",
        label="Prediction",
    )
    axis.set_title(
        f"Index {row_index} / Excel row {row_index + 2} / "
        f"{row_metrics['split']}\n"
        f"MAE {row_metrics['mae_db']:.3f} dB, "
        f"resonance error {row_metrics['freq_error_mhz']:.1f} MHz, "
        f"depth error {row_metrics['depth_error_db']:.2f} dB"
    )
    axis.set_ylabel("S11 (dB)")
    axis.grid(alpha=0.25)
    axis.legend()

for axis in axes[-1]:
    axis.set_xlabel("Frequency (GHz)")

figure.suptitle(
    "Full-band model: representative best, median, and worst rows",
    fontsize=16,
)
figure.tight_layout(rect=[0, 0, 1, 0.97])
figure.savefig(
    "multiple_rows_model_comparison.png",
    dpi=170,
    bbox_inches="tight",
)

selected_metrics = metrics.loc[selected_indices].copy()
selected_metrics.to_csv("multiple_rows_metrics.csv", index=False)

summary = metrics.groupby("split").agg(
    sample_count=("mae_db", "count"),
    curve_mae_mean_db=("mae_db", "mean"),
    curve_mae_median_db=("mae_db", "median"),
    curve_mae_90pct_db=("mae_db", lambda values: values.quantile(0.9)),
    curve_mae_max_db=("mae_db", "max"),
    resonance_freq_mae_mhz=("freq_error_mhz", "mean"),
    resonance_depth_mae_db=("depth_error_db", "mean"),
)
summary.to_csv("multiple_rows_summary.csv")

print("SELECTED")
print(selected_metrics.to_string(index=False))
print("\nSUMMARY")
print(summary.to_string())
