# Score predictions on an dataset whose sample names reveal the truth. Sample names containing 'HD' or 'Control'
# Shell : python validate_external.py predictions.csv

import argparse
import re

import numpy as np
import pandas as pd

HEALTHY = re.compile(r"(?:^|[-_.])(?:HD|Control)(?:[-_.]|$)", re.IGNORECASE)

parser = argparse.ArgumentParser(description="Score predictions against labels read from sample names.")
parser.add_argument("predictions", nargs="?", default="predictions.csv")
parser.add_argument(
    "--cancer-pattern",
    help="regex: names matching it are cancer, ALL others non-cancer (e.g. 'NSCLC|LGG' for GSE89843). "
    "Default: names with HD or Control are healthy, all others cancer.",
)
args = parser.parse_args()

p = pd.read_csv(args.predictions)
if args.cancer_pattern:
    cancer = re.compile(rf"(?:^|[-_.])(?:{args.cancer_pattern})(?:[-_.]|$)", re.IGNORECASE)
    p["truth"] = np.where(p["sample"].str.contains(cancer), "cancer", "healthy")
else:
    p["truth"] = np.where(p["sample"].str.contains(HEALTHY), "healthy", "cancer")
y = (p["truth"] == "cancer").to_numpy()
pred = (p["prediction"] == "cancer").to_numpy()
score = p["cancer_probability"].to_numpy()

tp, fn = int((pred & y).sum()), int((~pred & y).sum())
tn, fp = int((~pred & ~y).sum()), int((pred & ~y).sum())
sens = tp / (tp + fn) if tp + fn else float("nan")
spec = tn / (tn + fp) if tn + fp else float("nan")
# ROC-AUC = probability a random cancer sample scores higher than a random healthy one
pos, neg = score[y], score[~y]
if len(pos) and len(neg):
    auc = (pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean()
else:
    auc = float("nan")

print(f"Samples: {len(p)}  (cancer {int(y.sum())}, non-cancer {int((~y).sum())})")
print("Check the labels look right, e.g.:")
for label in ("cancer", "healthy"):
    print(f"  {label:<10} {', '.join(p.loc[p['truth'] == label, 'sample'].head(3))}")
print()
print("                 predicted cancer   predicted healthy")
print(f"actual cancer    {tp:>16}   {fn:>17}")
print(f"actual non-cancer{fp:>16}   {tn:>17}\n")
print(f"Sensitivity (cancer correctly flagged):   {sens:.3f}")
print(f"Specificity (healthy correctly cleared):  {spec:.3f}")
print(f"Balanced accuracy:                        {(sens + spec) / 2:.3f}")
print(f"ROC-AUC:                                  {auc:.3f}")
p.to_csv("predictions_with_truth.csv", index=False)
print("\nSaved predictions_with_truth.csv")
