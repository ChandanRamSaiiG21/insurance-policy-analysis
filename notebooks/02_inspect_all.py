import pandas as pd
import os

RAW = r"D:\DataAnalyticsProjects\insurance-policy-analysis\data\raw"

datasets = {
    "insurance_claims":   "insurance_claims.csv",
    "insurance_dataset":  "insurance_dataset.csv",
    "data_synthetic":     "data_synthetic.csv",
}

for name, filename in datasets.items():
    path = os.path.join(RAW, filename)
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    df = pd.read_csv(path)
    print(f"  Shape:   {df.shape[0]:,} rows x {df.shape[1]} columns")
    print(f"  Columns: {list(df.columns)}")
    nulls = df.isnull().sum()
    nulls = nulls[nulls > 0]
    print(f"  Nulls:   {dict(nulls) if len(nulls) > 0 else 'None'}")
    print(f"\n  Sample:")
    print(df.head(3).to_string())