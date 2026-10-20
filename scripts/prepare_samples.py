"""Prepare new samples for the liquid biopsy model, the same way the training data was prepared.

Shell:
python prepare_samples.py GSE68086_TEP_data_matrix.txt
python prepare_samples.py GSE107868_counts.txt --sep space
python prepare_samples.py raw.txt --out my_samples.csv --drop-unknown
"""

import argparse
import re
import sys

import numpy as np
import pandas as pd
import requests

API = "https://ca-liquid-biopsy.mangohill-328b7968.francecentral.azurecontainerapps.io"
SEPARATORS = {"tab": "\t", "comma": ",", "space": r"\s+"}


def detect_separator(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        header = fh.readline()
    return "tab" if "\t" in header else "comma" if "," in header else "space"


def ask_separator(path: str) -> str:
    detected = detect_separator(path)
    with open(path, encoding="utf-8") as fh:
        print("First line of the file:\n  " + fh.readline()[:150].rstrip())
    while True:
        answer = input(f"How are the values separated? Type tab, comma or space [Enter = {detected}]: ").strip().lower()
        if not answer:
            return detected
        if answer in SEPARATORS:
            return answer
        print("Please type tab, comma or space.")


def read_matrix(path: str, sep_name: str) -> pd.DataFrame:
    # Read a genes x samples count matrix
    try:
        # pandas' default (C) parser keeps quoted names with spaces, e.g. "Gene ID", in one piece
        df = pd.read_csv(path, sep=SEPARATORS[sep_name], index_col=0)
    except (pd.errors.ParserError, ValueError):
        raise SystemExit(
            f"ERROR: could not read the file as {sep_name}-separated. "
            f"Its first line looks {detect_separator(path)}-separated: run again and choose that."
        ) from None

    def strip(x):
        return str(x).strip().strip('"').strip("'")

    df.columns = [strip(c) for c in df.columns]
    df.index = [strip(i) for i in df.index]
    df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed")], errors="ignore")
    if "Genes" in df.columns:  # already in Prepro_data.csv layout
        df = df.set_index("Genes")
    # Ensembl IDs sometimes carry a version suffix, remove it
    df.index = pd.Index(df.index.astype(str)).str.split(".").str[0]
    if df.shape[1] == 0:
        raise SystemExit(f"ERROR: no sample columns found. Is the file really {sep_name}-separated?")
    return df.apply(pd.to_numeric, errors="coerce")


def same_sample(name: str) -> str:
    # Compare sample names across files
    return re.sub(r"^X(?=\d)", "", re.sub(r"[-_]", ".", name)).upper()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("raw_counts", help="raw count matrix: genes in rows, samples in columns")
    parser.add_argument("--out", default="prepared_samples.csv")
    parser.add_argument("--api", default=API)
    parser.add_argument("--sep", choices=sorted(SEPARATORS), help="skip the question: tab, comma or space")
    parser.add_argument(
        "--exclude-samples-in",
        metavar="CSV",
        help="drop samples that are also in this file, e.g. the training data (data/raw/Prepro_data.csv)",
    )
    parser.add_argument(
        "--drop-unknown", action="store_true", help="drop samples named 'Type.Unknown...' (as the R script did)"
    )
    args = parser.parse_args()

    genes = requests.get(f"{args.api}/model-info", timeout=60).json()["required_genes"]
    sep_name = args.sep or ask_separator(args.raw_counts)
    raw = read_matrix(args.raw_counts, sep_name)
    print(f"Read {raw.shape[0]} genes x {raw.shape[1]} samples")

    if args.drop_unknown:
        unknown = [c for c in raw.columns if str(c).startswith("Type.Unknown")]
        raw = raw.drop(columns=unknown)
        print(f"Dropped {len(unknown)} 'Type.Unknown' samples")

    if args.exclude_samples_in:
        seen = pd.read_csv(args.exclude_samples_in, nrows=0).columns
        seen = {same_sample(c) for c in seen if c not in ("Genes",) and not c.startswith("Unnamed")}
        overlap = [c for c in raw.columns if same_sample(str(c)) in seen]
        raw = raw.drop(columns=overlap)
        print(f"Excluded {len(overlap)} samples that are also in {args.exclude_samples_in}: {overlap[:3]}")

    missing = [g for g in genes if g not in raw.index]
    if missing:
        print(f"ERROR: {len(missing)} of the model's 90 genes are not in this file, e.g. {missing[:5]}")
        print("The model needs all 90; check that rows are Ensembl gene IDs (ENSG...).")
        return 1
    if raw.index.duplicated().any():
        raw = raw.groupby(level=0).sum()  # same gene listed twice: add its counts

    data = raw.loc[genes]

    if data.isna().any().any():
        bad = data.columns[data.isna().any()].tolist()
        print(f"ERROR: empty or non-numeric values in samples {bad[:5]}")
        return 1
    if (data < 0).any().any():
        print("ERROR: negative values found. The model needs raw read counts (log or z-scored data won't work).")
        return 1
    if not np.allclose(data.to_numpy(), np.round(data.to_numpy())):
        print("WARNING: values are not whole numbers, so they look normalized (CPM/TPM/log).")
        print("         The model was trained on RAW read counts; predictions may be unreliable.")
    low_depth = data.columns[data.sum() < 1000].tolist()
    if low_depth:
        print(f"WARNING: very low total counts in {low_depth[:5]}; check sequencing depth.")

    out = data.reset_index().rename(columns={"index": "Genes", data.index.name or "index": "Genes"})
    out.to_csv(args.out, index=False)
    print(f"Wrote {args.out}: {data.shape[1]} samples x 90 genes, ready for: python predict_csv.py {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
