# Shell : python predict_csv.py my_samples.csv

import sys

import pandas as pd
import requests

API = "https://ca-liquid-biopsy.mangohill-328b7968.francecentral.azurecontainerapps.io"

data = pd.read_csv(sys.argv[1]).drop(columns=["Unnamed: 0"], errors="ignore").set_index("Genes")
samples = data.T  # one row per sample
results = []
for start in range(0, len(samples), 100):  # the API accepts up to 100 samples per request
    batch = samples.iloc[start : start + 100]
    r = requests.post(f"{API}/predict", json={"samples": batch.to_dict(orient="records")}, timeout=60)
    r.raise_for_status()
    for name, p in zip(batch.index, r.json()["predictions"], strict=True):
        results.append({"sample": name, "prediction": p["label_name"], "cancer_probability": p["cancer_probability"]})

out = pd.DataFrame(results)
out.to_csv("predictions.csv", index=False)
print(out.to_string(index=False))
