from __future__ import annotations

import numpy as np
import pandas as pd

TARGET = "FLAG"
ADDRESS = "Address"
EXCLUDED = {TARGET, ADDRESS, "Index", ""}


def clean_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame.loc[:, ~frame.columns.duplicated()]


def load_dataset(path: str) -> tuple[pd.DataFrame, pd.Series, pd.Series, list[str]]:
    frame = clean_columns(pd.read_csv(path))
    if TARGET not in frame or ADDRESS not in frame:
        raise ValueError("Dataset must contain FLAG and Address columns")

    # Leakage check: identifiers, the label, and post-hoc token names are excluded.
    # Only numeric behavior aggregates available in the snapshot are model inputs.
    features = [
        column
        for column in frame.select_dtypes(include=[np.number]).columns
        if column not in EXCLUDED and not column.lower().startswith("unnamed")
    ]
    if not features:
        raise ValueError("Dataset contains no usable numeric features")

    x = frame[features].replace([np.inf, -np.inf], np.nan)
    y = frame[TARGET].astype(int)
    if set(y.unique()) - {0, 1}:
        raise ValueError("FLAG must be binary (0 or 1)")
    return x, y, frame[ADDRESS].astype(str).str.lower(), features


def row_to_frame(features: dict, feature_names: list[str]) -> pd.DataFrame:
    return rows_to_frame([features], feature_names)


def rows_to_frame(rows: list[dict], feature_names: list[str]) -> pd.DataFrame:
    return pd.DataFrame([
        {name: features.get(name, np.nan) for name in feature_names}
        for features in rows
    ])
