import numpy as np

from liquid_biopsy.data import TARGET, label_from_sample_name, split_features_target
from liquid_biopsy.validate import validate_dataset


def test_label_from_sample_name():
    prefixes = ["HD", "Control"]
    assert label_from_sample_name("HD-1", prefixes) == 0
    assert label_from_sample_name("Control.12", prefixes) == 0
    assert label_from_sample_name("X3.Breast.Her2.ampl", prefixes) == 1
    assert label_from_sample_name("MGH.NSCLC.L58.TR525", prefixes) == 1


def test_dataset_shape_and_labels(dataset):
    X, y = split_features_target(dataset)
    assert X.shape == (280, 90)  # 285 samples minus 5 "Type.Unknown"
    assert set(y.unique()) == {0, 1}
    assert (y == 0).sum() == 54  # healthy donors
    assert (y == 1).sum() == 226  # cancer patients
    assert all(c.startswith("ENSG") for c in X.columns)


def test_real_data_passes_validation(dataset):
    report = validate_dataset(dataset)
    assert report.ok, report.errors


def test_validation_catches_missing_values(dataset):
    broken = dataset.copy()
    broken.iloc[0, 0] = np.nan
    report = validate_dataset(broken)
    assert not report.ok
    assert any("missing" in e for e in report.errors)


def test_validation_catches_negative_counts(dataset):
    broken = dataset.copy()
    broken.iloc[0, 0] = -5
    assert not validate_dataset(broken).ok


def test_validation_catches_single_class(dataset):
    broken = dataset.copy()
    broken[TARGET] = 1
    assert not validate_dataset(broken).ok


def test_uncertain_samples_are_excluded(dataset, params):
    assert params["data"]["exclude_prefixes"] == ["Type.Unknown"]
    assert not dataset.index.str.startswith("Type.Unknown").any()


def test_exclusion_is_optional(params):
    from liquid_biopsy.config import resolve
    from liquid_biopsy.data import load_dataset

    everything = load_dataset(resolve(params["data"]["path"]), params["data"]["healthy_prefixes"])
    assert len(everything) == 285
