import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.model_selection import train_test_split


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


class TandemInverseNet(nn.Module):
    def __init__(self, input_dim, output_dim):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.SiLU(),
            nn.Linear(64, 32),
            nn.SiLU(),
            nn.Linear(32, output_dim),
            nn.Sigmoid(),
        )

    def forward(self, x):
        return self.network(x)


def physical_features(curves, frequency):
    result = []
    for curve in curves:
        minimum_index = int(np.argmin(curve))
        below_minus_10 = np.flatnonzero(curve <= -10)
        bandwidth = (
            frequency[below_minus_10[-1]]
            - frequency[below_minus_10[0]]
            if len(below_minus_10)
            else 0.0
        )
        result.append(
            [
                frequency[minimum_index],
                curve[minimum_index],
                bandwidth,
                np.trapz(-curve, frequency),
            ]
        )
    return np.asarray(result, dtype=np.float32)


data = pd.read_excel("Backfeed_Data300.xlsx")
inverse_package = torch.load(
    "model_inverse_v1_tandem.pth",
    map_location="cpu",
    weights_only=False,
)
forward_package = torch.load(
    "model_forward_full_pca.pth",
    map_location="cpu",
    weights_only=False,
)

frequency = np.asarray(inverse_package["frequency"], dtype=np.float64)
curves_db = data.iloc[:, 16:].to_numpy(dtype=np.float32)
geometry_columns = inverse_package["variable_geometry_columns"]
true_geometry = data.loc[:, geometry_columns].to_numpy(dtype=np.float32)

pca_coefficients = (
    curves_db - inverse_package["inverse_pca_mean"]
) @ inverse_package["inverse_pca_components"].T
features = np.column_stack(
    [
        pca_coefficients,
        physical_features(curves_db, frequency),
    ]
)
scaled_features = (
    features - inverse_package["inverse_feature_mean"]
) / inverse_package["inverse_feature_scale"]

inverse_model = TandemInverseNet(
    inverse_package["input_dim"],
    inverse_package["output_dim"],
)
inverse_model.load_state_dict(inverse_package["model_state"])
inverse_model.eval()

with torch.no_grad():
    scaled_geometry = inverse_model(
        torch.tensor(scaled_features, dtype=torch.float32)
    ).numpy()

predicted_geometry = (
    scaled_geometry
    * (
        inverse_package["geometry_max"]
        - inverse_package["geometry_min"]
    )
    + inverse_package["geometry_min"]
)

forward_model = ForwardPCANet(
    forward_package["input_dim"],
    forward_package["pca_dim"],
)
forward_model.load_state_dict(forward_package["model_state"])
forward_model.eval()

forward_input = (
    predicted_geometry - forward_package["x_mean"]
) / forward_package["x_scale"]
with torch.no_grad():
    scaled_coefficients = forward_model(
        torch.tensor(forward_input, dtype=torch.float32)
    ).numpy()

coefficients = (
    scaled_coefficients * forward_package["coefficient_scale"]
    + forward_package["coefficient_mean"]
)
cycle_curves = (
    coefficients @ forward_package["pca_components"]
    + forward_package["pca_mean"]
)

all_indices = np.arange(len(data))
train_indices, validation_indices = train_test_split(
    all_indices,
    test_size=0.3,
    random_state=42,
    shuffle=True,
)
split = np.full(len(data), "train", dtype=object)
split[validation_indices] = "validation"

curve_error = cycle_curves - curves_db
curve_mae = np.mean(np.abs(curve_error), axis=1)
curve_rmse = np.sqrt(np.mean(curve_error**2, axis=1))
geometry_mae = np.mean(
    np.abs(predicted_geometry - true_geometry),
    axis=1,
)

true_min_index = np.argmin(curves_db, axis=1)
cycle_min_index = np.argmin(cycle_curves, axis=1)
rows = np.arange(len(data))
true_min_depth = curves_db[rows, true_min_index]
cycle_min_depth = cycle_curves[rows, cycle_min_index]
true_min_frequency = frequency[true_min_index]
cycle_min_frequency = frequency[cycle_min_index]

metrics = pd.DataFrame(
    {
        "data_index": all_indices,
        "excel_row": all_indices + 2,
        "split": split,
        "curve_mae_db": curve_mae,
        "curve_rmse_db": curve_rmse,
        "geometry_mean_abs_error": geometry_mae,
        "frequency_error_mhz": (
            np.abs(cycle_min_frequency - true_min_frequency) * 1000
        ),
        "depth_error_db": np.abs(cycle_min_depth - true_min_depth),
        "true_resonance_ghz": true_min_frequency,
        "pred_resonance_ghz": cycle_min_frequency,
        "true_depth_db": true_min_depth,
        "pred_depth_db": cycle_min_depth,
    }
)

for column_index, column in enumerate(geometry_columns):
    metrics[f"true_{column}"] = true_geometry[:, column_index]
    metrics[f"pred_{column}"] = predicted_geometry[:, column_index]
    metrics[f"error_{column}"] = np.abs(
        predicted_geometry[:, column_index]
        - true_geometry[:, column_index]
    )

# 按用户要求排除第一条数据。
other_metrics = metrics.loc[metrics["data_index"] != 0].copy()
other_metrics.to_csv(
    "inverse_v1_other_rows_metrics.csv",
    index=False,
)

summary = other_metrics.groupby("split").agg(
    sample_count=("curve_mae_db", "count"),
    curve_mae_mean_db=("curve_mae_db", "mean"),
    curve_mae_median_db=("curve_mae_db", "median"),
    curve_mae_90pct_db=(
        "curve_mae_db",
        lambda values: values.quantile(0.9),
    ),
    curve_mae_max_db=("curve_mae_db", "max"),
    frequency_error_mean_mhz=("frequency_error_mhz", "mean"),
    depth_error_mean_db=("depth_error_db", "mean"),
    geometry_error_mean=("geometry_mean_abs_error", "mean"),
)
summary.to_csv("inverse_v1_other_rows_summary.csv")


def representative_indices(group_indices):
    available = np.asarray(
        [index for index in group_indices if index != 0],
        dtype=int,
    )
    ordered = available[np.argsort(curve_mae[available])]
    return [
        int(ordered[0]),
        int(ordered[len(ordered) // 2]),
        int(ordered[-1]),
    ]


selected_indices = (
    representative_indices(train_indices)
    + representative_indices(validation_indices)
)

figure, axes = plt.subplots(3, 2, figsize=(14, 13), sharex=True)
for axis, sample_index in zip(axes.flat, selected_indices):
    row = metrics.loc[sample_index]
    axis.plot(
        frequency,
        curves_db[sample_index],
        color="#2563EB",
        linewidth=2.2,
        label="Target S11",
    )
    axis.plot(
        frequency,
        cycle_curves[sample_index],
        color="#DC2626",
        linewidth=1.8,
        linestyle="--",
        label="Inverse design forward check",
    )
    axis.set_title(
        f"Index {sample_index} / Excel row {sample_index + 2} / "
        f"{row['split']}\n"
        f"Curve MAE {row['curve_mae_db']:.3f} dB, "
        f"frequency error {row['frequency_error_mhz']:.1f} MHz, "
        f"depth error {row['depth_error_db']:.2f} dB"
    )
    axis.set_ylabel("S11 (dB)")
    axis.grid(alpha=0.25)
    axis.legend()

for axis in axes[-1]:
    axis.set_xlabel("Frequency (GHz)")

figure.suptitle(
    "Inverse v1 tandem: representative rows excluding the first row",
    fontsize=16,
)
figure.tight_layout(rect=[0, 0, 1, 0.97])
figure.savefig(
    "inverse_v1_other_rows_comparison.png",
    dpi=180,
    bbox_inches="tight",
)

selected_table = metrics.loc[selected_indices].copy()
selected_table.to_csv(
    "inverse_v1_representative_rows.csv",
    index=False,
)

print("SUMMARY")
print(summary.to_string())
print("\nREPRESENTATIVE ROWS")
print(
    selected_table[
        [
            "data_index",
            "excel_row",
            "split",
            "curve_mae_db",
            "frequency_error_mhz",
            "depth_error_db",
            "geometry_mean_abs_error",
        ]
    ].to_string(index=False)
)
