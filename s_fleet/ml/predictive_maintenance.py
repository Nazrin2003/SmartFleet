from __future__ import annotations

import json
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any


ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "maintenance_pipeline.joblib"
META_PATH = ARTIFACTS_DIR / "model_meta.json"

# Keep this fixed and explicit so it is easy to explain.
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

_MODEL = None
_META: dict[str, Any] | None = None


def _f(value: Any, default: float = 0.0) -> float:
    """Safe float conversion."""
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_model_and_meta():
    """Load model + metadata once."""
    global _MODEL, _META

    if _META is None:
        if META_PATH.exists():
            try:
                _META = json.loads(META_PATH.read_text(encoding="utf-8"))
            except Exception:
                _META = {}
        else:
            _META = {}

    if _MODEL is None and MODEL_PATH.exists():
        try:
            import joblib  # type: ignore

            _MODEL = joblib.load(MODEL_PATH)
        except Exception:
            _MODEL = None

    return _MODEL, _META


def _fallback_rule(features: dict[str, float]) -> tuple[float, bool]:
    """Simple backup rule when model file is missing."""
    hits = 0
    if features["Engine_Temperature"] >= 110:
        hits += 1
    if features["Oil_Quality"] < 50:
        hits += 1
    if features["Brake_Condition"] <= 0:
        hits += 1
    if features["Failure_History"] >= 2:
        hits += 1
    score = min(0.25 + 0.18 * hits, 0.95)
    return score, hits >= 2


def build_features_from_trip(trip, completion) -> dict[str, float]:
    """
    Build the exact input features from already-entered project data.
    This is intentionally explicit (easy to explain to a teacher).
    """
    vehicle = trip.vehicle
    latest_maintenance = vehicle.maintenance_history.order_by("-created_at").first()

    now_year = datetime.now().year
    vehicle_age = float(now_year - vehicle.year_of_manufacture) if vehicle.year_of_manufacture else 0.0

    usage_hours = _f(vehicle.usage_hours)
    if usage_hours < 2000:
        usage_category = 0.0
    elif usage_hours < 5000:
        usage_category = 1.0
    else:
        usage_category = 2.0

    load_capacity = _f(vehicle.load_capacity)
    actual_load = _f(trip.actual_load)
    load_percentage = round((actual_load / load_capacity) * 100.0, 0) if load_capacity > 0 else 0.0

    if latest_maintenance and latest_maintenance.created_at:
        delta_days = (datetime.now(dt_timezone.utc) - latest_maintenance.created_at).days
        since_last_maintenance = float(max(delta_days, 0))
    else:
        since_last_maintenance = 0.0

    weather = str((getattr(completion, "weather", None) or trip.weather_conditions or "")).strip().lower()
    road = str((getattr(completion, "road_condition", None) or trip.road_conditions or "")).strip().lower()

    brake_raw = getattr(completion, "brake_condition", None)
    brake_text = str(brake_raw).strip().lower()
    if brake_text in {"poor", "bad"}:
        brake_encoded = 0.0
    elif brake_text in {"fair", "moderate"}:
        brake_encoded = 1.0
    elif brake_text == "good":
        brake_encoded = 2.0
    else:
        brake_num = _f(brake_raw, default=-1.0)
        if brake_num < 0:
            brake_encoded = 1.0
        elif brake_num <= 40:
            brake_encoded = 0.0
        elif brake_num <= 70:
            brake_encoded = 1.0
        else:
            brake_encoded = 2.0

    delivery_time = _f(getattr(completion, "actual_delivery_time", 0.0))
    if delivery_time > 24:
        delivery_time = round(delivery_time / 60.0, 2)

    failure_history = float(vehicle.maintenance_history.filter(is_resolved=False).count())

    return {
        "Engine_Temperature": _f(getattr(completion, "engine_temp", 0.0)),
        "Tire_Pressure": _f(getattr(latest_maintenance, "tire_pressure", 0.0)),
        "Failure_History": failure_history,
        "Battery_Status": _f(vehicle.battery_status),
        "Vibration_Levels": _f(getattr(latest_maintenance, "vibration_levels", 0.0)),
        "Oil_Quality": _f(getattr(completion, "oil_quality", 0.0)),
        "Brake_Condition": brake_encoded,
        "Delivery_Times": round(delivery_time, 2),
        "Vehicle_Age": vehicle_age,
        "Usage_Category": usage_category,
        "Load_Percentage": load_percentage,
        "Since_Last_Maintenance": since_last_maintenance,
        "Weather_Conditions_Rainy": 1.0 if weather == "rainy" else 0.0,
        "Weather_Conditions_Snowy": 1.0 if weather == "snowy" else 0.0,
        "Weather_Conditions_Windy": 1.0 if weather == "windy" else 0.0,
        "Road_Conditions_Rural": 1.0 if road == "rural" else 0.0,
        "Road_Conditions_Urban": 1.0 if road == "urban" else 0.0,
    }


def predict_maintenance_for_trip(trip, completion) -> dict[str, Any]:
    """
    Main inference function used by Django view.
    Output keys are unchanged to keep integration stable.
    """
    model, meta = _load_model_and_meta()
    features = build_features_from_trip(trip, completion)

    feature_columns = meta.get("feature_columns") or FEATURE_COLUMNS
    threshold = _f(meta.get("maintenance_threshold"), default=0.5)
    model_name = str(meta.get("model_name") or "maintenance_model")
    model_version = str(meta.get("model_version") or "v1")

    # If model artifact is missing, use fallback rule.
    if model is None:
        score, required = _fallback_rule(features)
        return {
            "predictive_score": float(score),
            "maintenance_required": bool(required),
            "used_model": False,
            "model_name": "rule_engine",
            "model_version": "fallback_v1",
            "features": features,
        }

    # Build input row in fixed feature order.
    row = [features.get(col, 0.0) for col in feature_columns]

    try:
        import pandas as pd  # type: ignore

        x_input = pd.DataFrame([row], columns=feature_columns)
        if hasattr(model, "predict_proba"):
            score = float(model.predict_proba(x_input)[0][1])
        else:
            pred = float(model.predict(x_input)[0])
            score = 1.0 if pred >= 1.0 else 0.0
    except Exception:
        # Fallback if pipeline fails on DataFrame/array shape.
        try:
            if hasattr(model, "predict_proba"):
                score = float(model.predict_proba([row])[0][1])
            else:
                pred = float(model.predict([row])[0])
                score = 1.0 if pred >= 1.0 else 0.0
        except Exception:
            score, required = _fallback_rule(features)
            return {
                "predictive_score": float(score),
                "maintenance_required": bool(required),
                "used_model": False,
                "model_name": "rule_engine",
                "model_version": "fallback_v1",
                "features": features,
            }

    return {
        "predictive_score": float(score),
        "maintenance_required": bool(score >= threshold),
        "used_model": True,
        "model_name": model_name,
        "model_version": model_version,
        "features": features,
    }
