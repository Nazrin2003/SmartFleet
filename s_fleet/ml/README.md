## Predictive Maintenance Artifacts

This app expects these files in `s_fleet/ml/artifacts/`:

- `maintenance_pipeline.joblib`
- `model_meta.json`

### Train artifacts from dataset

Run from project root:

```powershell
python s_fleet\ml\train_artifacts.py --dataset "C:\Users\Nazrin\Downloads\smart_fleet_dataset\logistics_dataset_with_maintenance_required.csv"
```

Optional:

```powershell
python s_fleet\ml\train_artifacts.py --dataset "<csv_path>" --out-dir "s_fleet\ml\artifacts" --threshold 0.55
```

### Notes

- Training logic is aligned with `main.ipynb` in `smart_fleet_dataset`.
- Inference uses the same engineered features and encoding shape expected by `model_meta.json`.
