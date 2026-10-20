"""Reference distribution of every gene in the training data, for drift detection.

Saved next to the model as reference_stats.json and shipped inside the serving
image. The drift job (app/drift.py) bins live inputs with the SAME bin edges and
compares the proportions with the Population Stability Index (PSI).

Values are compared on a log1p scale because read counts are heavily skewed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

N_BINS = 10


def gene_reference(values: np.ndarray, n_bins: int = N_BINS) -> dict:
    x = np.log1p(np.asarray(values, dtype=float))
    # Inner edges at the deciles; ties (many identical counts) collapse into fewer bins
    edges = np.unique(np.quantile(x, np.linspace(0, 1, n_bins + 1)[1:-1]))
    counts = np.bincount(np.searchsorted(edges, x, side="right"), minlength=len(edges) + 1)
    return {
        "edges": [round(float(e), 6) for e in edges],
        "expected": [round(float(c) / len(x), 6) for c in counts],
        "mean_log1p": round(float(x.mean()), 6),
    }


def build_reference(X: pd.DataFrame, n_bins: int = N_BINS) -> dict:
    return {
        "scale": "log1p(read_count)",
        "n_bins": n_bins,
        "n_samples": int(len(X)),
        "genes": {gene: gene_reference(X[gene].to_numpy(), n_bins) for gene in X.columns},
    }
