"""Data validation: fail the pipeline early if the input data looks wrong.

Inspired by the Data-Quality-Importance project: corrupted or missing values
silently degrade a model, so CI checks the data before training on it.

Run:  python -m liquid_biopsy.validate
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import pandas as pd

from .config import load_params, resolve
from .data import TARGET, load_dataset

ENSEMBL_ID = re.compile(r"^ENSG\d{11}$")


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_dataset(df: pd.DataFrame, min_samples: int = 100, min_class_count: int = 20) -> ValidationReport:
    report = ValidationReport()
    features = df.drop(columns=[TARGET], errors="ignore")

    if TARGET not in df.columns:
        report.errors.append(f"missing target column '{TARGET}'")
        return report

    if len(df) < min_samples:
        report.errors.append(f"only {len(df)} samples (need >= {min_samples})")

    bad_ids = [c for c in features.columns if not ENSEMBL_ID.match(str(c))]
    if bad_ids:
        report.errors.append(f"{len(bad_ids)} columns are not Ensembl gene IDs, e.g. {bad_ids[:3]}")

    if features.columns.duplicated().any():
        report.errors.append("duplicate gene columns")

    n_missing = int(features.isna().sum().sum())
    if n_missing:
        report.errors.append(f"{n_missing} missing or non-numeric values")

    if (features < 0).any().any():
        report.errors.append("negative read counts found (counts must be >= 0)")

    constant = features.columns[features.nunique() <= 1].tolist()
    if constant:
        report.warnings.append(f"{len(constant)} constant gene columns carry no signal")

    counts = df[TARGET].value_counts()
    if set(counts.index) != {0, 1}:
        report.errors.append(f"expected labels {{0, 1}}, got {sorted(counts.index.tolist())}")
    elif counts.min() < min_class_count:
        report.errors.append(f"minority class has only {counts.min()} samples")
    else:
        ratio = counts.max() / counts.min()
        if ratio > 3:
            report.warnings.append(f"class imbalance {ratio:.1f}:1 (handled with SMOTE during training)")

    return report


def main() -> int:
    params = load_params()
    df = load_dataset(
        resolve(params["data"]["path"]),
        params["data"]["healthy_prefixes"],
        params["data"].get("exclude_prefixes"),
    )
    report = validate_dataset(df)
    counts = df[TARGET].value_counts().to_dict()
    print(
        f"Samples: {len(df)}  Genes: {df.shape[1] - 1}  Classes: healthy={counts.get(0, 0)} cancer={counts.get(1, 0)}"
    )
    for w in report.warnings:
        print(f"WARNING: {w}")
    for e in report.errors:
        print(f"ERROR: {e}")
    print("Data validation", "PASSED" if report.ok else "FAILED")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
