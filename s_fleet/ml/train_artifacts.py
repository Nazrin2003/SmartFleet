from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd
from imblearn.over_sampling import RandomOverSampler
from imblearn.under_sampling import RandomUnderSampler
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler, OrdinalEncoder


# Fixed feature order used by training and inference.
FEATURE_COLUMNS = [
    "Engine_Temperature",
    "Tire_Pressure",
    "Failure_History",
    "Battery_Status",
    "Vibration_Levels",
    "Oil_Quality",
    "Brake_Condition",
    "Delivery_Times",
    "Vehicle_Age",
    "Usage_Category",
    "Load_Percentage",
    "Since_Last_Maintenance",
    "Weather_Conditions_Rainy",
    "Weather_Conditions_Snowy",
    "Weather_Conditions_Windy",
    "Road_Conditions_Rural",
    "Road_Conditions_Urban",
]


def usage_category(hours: float) -> str:
    """Notebook-compatible category logic."""
    if hours < 2000:
        return "Low"
    if 2000 < hours < 5000:
        return "Medium"
    return "High"


def build_dataset(csv_path: Path) -> pd.DataFrame:
    """Load, clean, and transform CSV into model-ready frame."""
    df = pd.read_csv(csv_path)

    # Drop unused columns from notebook.
    df = df.drop(
        columns=[
            "Vehicle_ID",
            "Make_and_Model",
            "Vehicle_Type",
            "Maintenance_Cost",
            "Route_Info",
            "Impact_on_Efficiency",
        ],
        errors="ignore",
    )

    # Basic range cleanup from notebook.
    df = df.loc[df["Usage_Hours"] <= 30000].copy()
    df = df.loc[df["Load_Capacity"] <= 300].copy()

    # Replace zero downtime with average downtime.
    downtime = df["Downtime_Maintenance"].replace(0.0, pd.NA)
    df["Downtime_Maintenance"] = downtime.fillna(downtime.astype(float).mean())

    # Date and engineered columns.
    df["Delivery_Times"] = (df["Delivery_Times"] / 60).round(2)
    df["Last_Maintenance_Date"] = pd.to_datetime(
        df["Last_Maintenance_Date"], format="mixed", dayfirst=True, errors="coerce"
    )
    df["Vehicle_Age"] = datetime.now().year - df["Year_of_Manufacture"]
    df["Usage_Category"] = df["Usage_Hours"].apply(usage_category)
    df["Load_Percentage"] = ((df["Actual_Load"] / df["Load_Capacity"]) * 100).round()
    df["Since_Last_Maintenance"] = (pd.Timestamp.today() - df["Last_Maintenance_Date"]).dt.days

    # Keep only model columns.
    data = df[
        [
            "Engine_Temperature",
            "Tire_Pressure",
            "Failure_History",
            "Battery_Status",
            "Vibration_Levels",
            "Oil_Quality",
            "Brake_Condition",
            "Maintenance_Required",
            "Weather_Conditions",
            "Road_Conditions",
            "Delivery_Times",
            "Vehicle_Age",
            "Usage_Category",
            "Load_Percentage",
            "Since_Last_Maintenance",
        ]
    ].copy()

    # Encode two ordinal columns.
    ordinal = OrdinalEncoder(categories=[["Low", "Medium", "High"], ["Poor", "Fair", "Good"]])
    data[["Usage_Category", "Brake_Condition"]] = ordinal.fit_transform(
        data[["Usage_Category", "Brake_Condition"]]
    )

    # One-hot encode weather and road.
    data = pd.get_dummies(
        data,
        columns=["Weather_Conditions", "Road_Conditions"],
        drop_first=True,
        dtype=int,
    )

    return data


def train_model(data: pd.DataFrame):
    """Train RandomForest on balanced data."""
    x = data.drop(columns=["Maintenance_Required"])
    y = data["Maintenance_Required"]

    # Balance class counts (notebook behavior).
    over = RandomOverSampler(sampling_strategy={0: 30000}, random_state=42)
    x_over, y_over = over.fit_resample(x, y)
    under = RandomUnderSampler(sampling_strategy={1: 30000}, random_state=42)
    x_balanced, y_balanced = under.fit_resample(x_over, y_over)

    pipeline = Pipeline(
        [
            ("scaler", MinMaxScaler()),
            ("model", RandomForestClassifier(random_state=42)),
        ]
    )
    pipeline.fit(x_balanced, y_balanced)
    return pipeline, list(x.columns)


def save_artifacts(model, feature_columns: list[str], out_dir: Path, threshold: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, out_dir / "maintenance_pipeline.joblib")

    meta = {
        "model_name": "maintenance_random_forest",
        "model_version": "v1",
        "maintenance_threshold": threshold,
        "feature_columns": feature_columns,
    }
    (out_dir / "model_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Saved model: {out_dir / 'maintenance_pipeline.joblib'}")
    print(f"Saved meta : {out_dir / 'model_meta.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train predictive maintenance artifacts from CSV.")
    parser.add_argument("--dataset", required=True, help="Path to your CSV dataset")
    parser.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent / "artifacts"),
        help="Where to save maintenance_pipeline.joblib and model_meta.json",
    )
    parser.add_argument("--threshold", type=float, default=0.55, help="Decision threshold for maintenance required")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    out_dir = Path(args.out_dir)

    data = build_dataset(dataset_path)
    model, feature_columns = train_model(data)
    save_artifacts(model, feature_columns, out_dir, args.threshold)


if __name__ == "__main__":
    main()
