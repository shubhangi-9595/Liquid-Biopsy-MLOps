"""Load the DESeq2-filtered platelet RNA-seq matrix and turn it into X / y.

The raw file (output of notebooks/00_deseq2_preprocessing.Rmd) is gene-major:
one row per differentially expressed gene, one column per sample. The sample
name encodes the label: names starting with "HD" or "Control" are healthy
donors, everything else is a cancer patient (GEO series GSE68086).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

TARGET = "label"
LABEL_NAMES = {0: "healthy", 1: "cancer"}


def label_from_sample_name(name: str, healthy_prefixes: list[str]) -> int:
    return 0 if str(name).startswith(tuple(healthy_prefixes)) else 1


def load_dataset(path: str | Path, healthy_prefixes: list[str]) -> pd.DataFrame:
    """Return a sample-major frame: one row per sample, gene columns + `label`."""
    raw = pd.read_csv(path)
    raw = raw.drop(columns=[c for c in raw.columns if c.startswith("Unnamed")])
    if "Genes" not in raw.columns:
        raise ValueError("Expected a 'Genes' column with Ensembl gene IDs")

    df = raw.set_index("Genes").T
    df.columns.name = None
    df.index.name = "sample"
    df = df.apply(pd.to_numeric, errors="coerce")
    df[TARGET] = [label_from_sample_name(s, healthy_prefixes) for s in df.index]
    return df


def split_features_target(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    return df.drop(columns=[TARGET]), df[TARGET]
