"""Quantify the data leakage in the original notebook.

Original notebook:  SMOTE on ALL data -> scale -> train/test split -> evaluate
This project:       train/test split -> SMOTE only on the training data -> evaluate

When SMOTE runs before the split, synthetic "healthy" samples are interpolated
between real samples that later land in the test set, so the test set contains
near-copies of training points and the scores look better than they really are.
On top of that, the leaky test set is ~50% synthetic, so it no longer reflects
the real 4:1 cancer/healthy ratio.

Run:  PYTHONPATH=src python scripts/leakage_demo.py
"""

from __future__ import annotations

import numpy as np
from imblearn.over_sampling import SMOTE
from imblearn.pipeline import Pipeline
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

from liquid_biopsy.config import load_params, resolve
from liquid_biopsy.data import load_dataset, split_features_target

N_REPEATS = 20


def scores(y_true, proba):
    pred = (proba >= 0.5).astype(int)
    return (
        roc_auc_score(y_true, proba),
        balanced_accuracy_score(y_true, pred),
        recall_score(y_true, pred, pos_label=0),  # specificity: healthy correctly cleared
    )


def main() -> None:
    params = load_params()
    df = load_dataset(
        resolve(params["data"]["path"]),
        params["data"]["healthy_prefixes"],
        params["data"].get("exclude_prefixes"),
    )
    X, y = split_features_target(df)
    rf = dict(n_estimators=55, max_features=8, n_jobs=-1)  # the notebook's final settings

    leaky, correct = [], []
    for seed in range(N_REPEATS):
        # Original protocol: oversample everything first, then split
        Xs, ys = SMOTE(random_state=seed).fit_resample(X, y)
        Xtr, Xte, ytr, yte = train_test_split(Xs, ys, test_size=1 / 3, stratify=ys, random_state=seed)
        m = RandomForestClassifier(random_state=seed, **rf).fit(Xtr, ytr)
        leaky.append(scores(yte, m.predict_proba(Xte)[:, 1]))

        # Correct protocol: split real data first, oversample only the training part
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=1 / 3, stratify=y, random_state=seed)
        m = Pipeline([("smote", SMOTE(random_state=seed)), ("clf", RandomForestClassifier(random_state=seed, **rf))])
        m.fit(Xtr, ytr)
        correct.append(scores(yte, m.predict_proba(Xte)[:, 1]))

    leaky, correct = np.array(leaky), np.array(correct)
    print(f"Mean over {N_REPEATS} random splits (std in brackets)\n")
    print(f"{'Protocol':<38}{'ROC-AUC':>16}{'Balanced acc':>16}{'Specificity':>16}")
    for name, arr in [("Original (SMOTE before split, leaky)", leaky), ("This project (split first)", correct)]:
        cells = "".join(f"{m:>9.3f} ({s:.3f})" for m, s in zip(arr.mean(0), arr.std(0), strict=True))
        print(f"{name:<38}{cells}")


if __name__ == "__main__":
    main()
