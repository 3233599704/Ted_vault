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
geometry_columns = inverse_package["variable_geometry_columns"]
curves = data.iloc[:, 16:].to_numpy(dtype=np.float32)
true_geometry = data.loc[:, geometry_columns].to_numpy(dtype=np.float32)

pca_coefficients = (
    curves - inverse_package["inverse_pca_mean"]
) @ inverse_package["inverse_pca_components"].T
features = np.column_stack(
    [pca_coefficients, physical_features(curves, frequency)]
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

forward_inputs = (
    predicted_geometry - forward_package["x_mean"]
) / forward_package["x_scale"]
with torch.no_grad():
    scaled_forward_coefficients = forward_model(
        torch.tensor(forward_inputs, dtype=torch.float32)
    ).numpy()

forward_coefficients = (
    scaled_forward_coefficients
    * forward_package["coefficient_scale"]
    + forward_package["coefficient_mean"]
)
cycle_curves = (
    forward_coefficients @ forward_package["pca_components"]
    + forward_package["pca_mean"]
)

indices = np.arange(len(data))
_, validation_indices = train_test_split(
    indices,
    test_size=0.3,
    random_state=42,
    shuffle=True,
)
curve_mae = np.mean(np.abs(cycle_curves - curves), axis=1)

# 选择验证集中较好、中等、偏差较大和最差四条数据。
ordered_validation = validation_indices[
    np.argsort(curve_mae[validation_indices])
]
selected_indices = [
    int(ordered_validation[len(ordered_validation) // 10]),
    int(ordered_validation[len(ordered_validation) // 2]),
    int(ordered_validation[int(len(ordered_validation) * 0.8)]),
    int(ordered_validation[-1]),
]

rows = []
for sample_index in selected_indices:
    row = {
        "data_index": sample_index,
        "excel_row": sample_index + 2,
        "cycle_curve_mae_db": curve_mae[sample_index],
    }
    parameter_errors = []
    for column_index, column in enumerate(geometry_columns):
        true_value = true_geometry[sample_index, column_index]
        predicted_value = predicted_geometry[sample_index, column_index]
        error = abs(predicted_value - true_value)
        row[f"true_{column}"] = true_value
        row[f"pred_{column}"] = predicted_value
        row[f"error_{column}"] = error
        parameter_errors.append(error)
    row["mean_geometry_error"] = np.mean(parameter_errors)
    rows.append(row)

results = pd.DataFrame(rows)
results.to_csv("inverse_v1_other_geometry_predictions.csv", index=False)

figure, axes = plt.subplots(
    len(selected_indices),
    2,
    figsize=(15, 4 * len(selected_indices)),
)
colors = ["#2563EB", "#DC2626"]

for row_index, sample_index in enumerate(selected_indices):
    curve_axis = axes[row_index, 0]
    geometry_axis = axes[row_index, 1]
    result = results.iloc[row_index]

    curve_axis.plot(
        frequency,
        curves[sample_index],
        color=colors[0],
        linewidth=2.2,
        label="Target S11",
    )
    curve_axis.plot(
        frequency,
        cycle_curves[sample_index],
        color=colors[1],
        linewidth=1.8,
        linestyle="--",
        label="Forward check",
    )
    curve_axis.set_title(
        f"Index {sample_index} / Excel row {sample_index + 2} | "
        f"curve MAE={curve_mae[sample_index]:.3f} dB"
    )
    curve_axis.set_ylabel("S11 (dB)")
    curve_axis.grid(alpha=0.25)
    curve_axis.legend()

    x_positions = np.arange(len(geometry_columns))
    geometry_axis.bar(
        x_positions - 0.18,
        true_geometry[sample_index],
        width=0.36,
        color=colors[0],
        label="True geometry",
    )
    geometry_axis.bar(
        x_positions + 0.18,
        predicted_geometry[sample_index],
        width=0.36,
        color=colors[1],
        label="Predicted geometry",
    )
    geometry_axis.set_xticks(x_positions, geometry_columns)
    geometry_axis.set_ylabel("Geometry value")
    geometry_axis.set_title(
        f"Mean geometry error={result['mean_geometry_error']:.3f}"
    )
    geometry_axis.grid(axis="y", alpha=0.25)
    geometry_axis.legend()

axes[-1, 0].set_xlabel("Frequency (GHz)")
figure.suptitle(
    "Inverse v1 geometry predictions on other validation rows",
    fontsize=16,
)
figure.tight_layout(rect=[0, 0, 1, 0.98])
figure.savefig(
    "inverse_v1_other_geometry_predictions.png",
    dpi=180,
    bbox_inches="tight",
)

print(results.to_string(index=False))
