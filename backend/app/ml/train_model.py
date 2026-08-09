from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, average_precision_score, brier_score_loss, confusion_matrix,
    precision_recall_fscore_support, roc_auc_score,
)
from sklearn.model_selection import RandomizedSearchCV, StratifiedGroupKFold, cross_validate
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.ml.feature_engineering import load_dataset
else:
    from .feature_engineering import load_dataset

ROOT = Path(__file__).resolve().parents[3]
DATASET = ROOT / "data" / "transaction_dataset.csv"
MODEL = Path(__file__).with_name("model.pkl")
METRICS = Path(__file__).with_name("metrics.json")
REGISTRY = Path(__file__).with_name("registry")
SEED = 42


def grouped_split_indices(
    labels: pd.Series, groups: pd.Series, n_splits: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a reproducible stratified split with no wallet in both partitions."""
    group_labels = pd.DataFrame({"label": labels.to_numpy(), "group": groups.to_numpy()})
    if (group_labels.groupby("group")["label"].nunique() > 1).any():
        raise ValueError("A wallet cannot have conflicting fraud labels")
    unique_per_class = group_labels.drop_duplicates("group").groupby("label").size()
    if len(unique_per_class) < 2 or int(unique_per_class.min()) < n_splits:
        raise ValueError(f"Need at least {n_splits} unique wallets in each class")
    splitter = StratifiedGroupKFold(
        n_splits=n_splits, shuffle=True, random_state=SEED
    )
    train_index, test_index = next(
        splitter.split(np.zeros(len(labels)), labels, groups)
    )
    if set(groups.iloc[train_index]) & set(groups.iloc[test_index]):
        raise RuntimeError("Wallet-level data leakage detected")
    return train_index, test_index


def evaluate(model, x_test, y_test) -> dict:
    predicted = model.predict(x_test)
    probability = model.predict_proba(x_test)[:, 1]
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_test, predicted, average="binary", zero_division=0
    )
    matrix = confusion_matrix(y_test, predicted)
    tn, fp, fn, tp = matrix.ravel()
    return {
        "accuracy": round(float(accuracy_score(y_test, predicted)), 4),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "roc_auc": round(float(roc_auc_score(y_test, probability)), 4),
        "pr_auc": round(float(average_precision_score(y_test, probability)), 4),
        "brier_score": round(float(brier_score_loss(y_test, probability)), 4),
        "false_positive_rate": round(float(fp / (fp + tn)), 4),
        "false_negative_rate": round(float(fn / (fn + tp)), 4),
        "confusion_matrix": matrix.tolist(),
    }


def threshold_analysis(probability: np.ndarray, labels: pd.Series) -> list[dict]:
    """Expose operating-point tradeoffs; deployment owners choose costs, not training code."""
    rows = []
    for threshold in np.arange(0.1, 1.0, 0.1):
        predicted = probability >= threshold
        tn, fp, fn, tp = confusion_matrix(labels, predicted, labels=[0, 1]).ravel()
        rows.append({
            "threshold": round(float(threshold), 1),
            "false_positives": int(fp), "false_negatives": int(fn),
            "false_positive_rate": round(float(fp / (fp + tn)), 4),
            "false_negative_rate": round(float(fn / (fn + tp)), 4),
        })
    return rows


def train(dataset_path: Path = DATASET, reviewed_path: Path | None = None) -> dict:
    x, y, addresses, features = load_dataset(str(dataset_path))
    reviewed_rows = 0
    reviewed_training_rows = 0
    reviewed_validation_rows = 0
    x_live_test = y_live_test = None
    train_index, test_index = grouped_split_indices(y, addresses, n_splits=5)
    x_train, x_test = x.iloc[train_index].reset_index(drop=True), x.iloc[test_index]
    y_train, y_test = y.iloc[train_index].reset_index(drop=True), y.iloc[test_index]
    train_groups = addresses.iloc[train_index].reset_index(drop=True)
    test_groups = addresses.iloc[test_index]
    if reviewed_path:
        reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
        usable = [item for item in reviewed if item.get("features")]
        if usable:
            reviewed_rows = len(usable)
            live_x = pd.DataFrame(
                [item["features"] for item in usable]
            ).reindex(columns=features)
            live_y = pd.Series([item["label"] for item in usable])
            live_groups = pd.Series([
                str(item.get("address") or item.get("transaction_hash") or index).lower()
                for index, item in enumerate(usable)
            ])
            unique_live = pd.DataFrame({"label": live_y, "group": live_groups}).drop_duplicates("group")
            if (
                reviewed_rows >= 50
                and len(live_y.value_counts()) == 2
                and live_y.value_counts().min() >= 10
                and len(unique_live.groupby("label")) == 2
                and unique_live.groupby("label").size().min() >= 5
            ):
                live_train_index, live_test_index = grouped_split_indices(
                    live_y, live_groups, n_splits=5
                )
                historical_test_wallets = set(test_groups)
                live_train_index = np.array([
                    index for index in live_train_index
                    if live_groups.iloc[index] not in historical_test_wallets
                ])
                live_train_x, x_live_test = live_x.iloc[live_train_index], live_x.iloc[live_test_index]
                live_train_y, y_live_test = live_y.iloc[live_train_index], live_y.iloc[live_test_index]
                x_train = pd.concat([x_train, live_train_x], ignore_index=True)
                y_train = pd.concat([y_train, live_train_y], ignore_index=True)
                train_groups = pd.concat(
                    [train_groups, live_groups.iloc[live_train_index]], ignore_index=True
                )
                if set(train_groups) & set(test_groups):
                    raise RuntimeError("Reviewed data leaked into the historical test set")
                reviewed_training_rows = len(live_train_x)
                reviewed_validation_rows = len(x_live_test)
    models = {
        "Logistic Regression": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=SEED)),
        ]),
        "Random Forest": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("classifier", RandomForestClassifier(n_estimators=150, class_weight="balanced", n_jobs=-1, random_state=SEED)),
        ]),
        "XGBoost": Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("classifier", XGBClassifier(
                n_estimators=150, max_depth=5, learning_rate=0.08, subsample=0.9,
                colsample_bytree=0.9, eval_metric="logloss", n_jobs=-1, random_state=SEED,
            )),
        ]),
    }
    results, cross_validation, live_validation = {}, {}, {}
    explain_pipeline = None
    tuning = {}
    for name, model in models.items():
        if name == "XGBoost":
            fit_index, calibration_index = grouped_split_indices(
                y_train, train_groups, n_splits=7
            )
            x_fit, x_calibration = x_train.iloc[fit_index], x_train.iloc[calibration_index]
            y_fit, y_calibration = y_train.iloc[fit_index], y_train.iloc[calibration_index]
            fit_groups = train_groups.iloc[fit_index]
            search = RandomizedSearchCV(
                model,
                {
                    "classifier__n_estimators": [100, 150, 220],
                    "classifier__max_depth": [3, 5, 7],
                    "classifier__learning_rate": [0.04, 0.08, 0.12],
                    "classifier__subsample": [0.8, 0.9, 1.0],
                    "classifier__colsample_bytree": [0.8, 0.9, 1.0],
                },
                n_iter=6, scoring="roc_auc",
                cv=StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=SEED),
                random_state=SEED, n_jobs=1,
            )
            search.fit(x_fit, y_fit, groups=fit_groups)
            explain_pipeline = search.best_estimator_
            tuning = {
                "method": "RandomizedSearchCV",
                "iterations": 6,
                "folds": 3,
                "best_cv_roc_auc": round(float(search.best_score_), 4),
                "best_parameters": search.best_params_,
            }
            model = CalibratedClassifierCV(
                FrozenEstimator(explain_pipeline), method="sigmoid"
            )
            model.fit(x_calibration, y_calibration)
            models[name] = model
        else:
            model.fit(x_train, y_train)
        results[name] = evaluate(model, x_test, y_test)
        if x_live_test is not None:
            live_validation[name] = evaluate(model, x_live_test, y_live_test)
        cv_model = explain_pipeline if name == "XGBoost" else models[name]
        cv = cross_validate(
            cv_model, x_train, y_train, groups=train_groups,
            cv=StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=SEED),
            scoring=("accuracy", "f1", "roc_auc"), n_jobs=1,
        )
        cross_validation[name] = {
            metric.removeprefix("test_"): {
                "mean": round(float(np.mean(values)), 4),
                "std": round(float(np.std(values)), 4),
            }
            for metric, values in cv.items() if metric.startswith("test_")
        }
        print(f"{name}: {results[name]}")

    feature_reference = {}
    for name in features:
        mean, std = float(x_train[name].mean()), float(x_train[name].std())
        feature_reference[name] = {
            "mean": mean if math.isfinite(mean) else 0.0,
            "std": std if math.isfinite(std) else 0.0,
        }
    anomaly_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
        ("detector", IsolationForest(
            n_estimators=150, contamination="auto", n_jobs=-1, random_state=SEED,
        )),
    ])
    anomaly_pipeline.fit(x_train[y_train == 0])
    normal_scores = anomaly_pipeline.score_samples(x_train[y_train == 0])
    anomaly_reference = {
        "low": float(np.percentile(normal_scores, 5)),
        "high": float(np.percentile(normal_scores, 95)),
    }
    test_anomaly = np.clip(
        (anomaly_reference["high"] - anomaly_pipeline.score_samples(x_test))
        / (anomaly_reference["high"] - anomaly_reference["low"]), 0, 1,
    )
    anomaly_auc = float(roc_auc_score(y_test, test_anomaly))
    anomaly_accepted = anomaly_auc >= 0.6
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = {
        "pipeline": models["XGBoost"],
        "explain_pipeline": explain_pipeline,
        "features": features,
        "calibration": "sigmoid_holdout_15_percent",
        "feature_reference": feature_reference,
        "anomaly_pipeline": anomaly_pipeline if anomaly_accepted else None,
        "anomaly_reference": anomaly_reference if anomaly_accepted else None,
        "model_version": version,
    }
    importance = sorted(
        zip(features, explain_pipeline.named_steps["classifier"].feature_importances_),
        key=lambda item: item[1], reverse=True,
    )
    joblib.dump(artifact, MODEL)
    REGISTRY.mkdir(exist_ok=True)
    joblib.dump(artifact, REGISTRY / f"{version}.pkl")
    payload = {
        "dataset_rows": len(x), "reviewed_live_rows": reviewed_rows,
        "reviewed_training_rows": reviewed_training_rows,
        "reviewed_validation_rows": reviewed_validation_rows,
        "test_rows": len(x_test), "seed": SEED, "model_version": version,
        "data_split": {
            "strategy": "StratifiedGroupKFold by wallet address",
            "train_rows": len(x_train),
            "test_rows": len(x_test),
            "train_unique_wallets": int(train_groups.nunique()),
            "test_unique_wallets": int(test_groups.nunique()),
            "overlapping_wallets": len(set(train_groups) & set(test_groups)),
        },
        "models": results,
        "live_validation": live_validation or {
            "status": (
                "requires at least 50 reviewed rows, 10 examples per class, "
                "and 5 unique wallets per class"
            )
        },
        "cross_validation": cross_validation,
        "xgboost_tuning": tuning,
        "anomaly_detection": {
            "method": "IsolationForest fitted only on legitimate training wallets",
            "roc_auc": round(anomaly_auc, 4),
            "minimum_deployment_roc_auc": 0.6,
            "deployment_status": "accepted" if anomaly_accepted else "rejected",
            "purpose": "separate out-of-pattern signal; it does not replace fraud probability",
        },
        "probability_threshold_analysis": threshold_analysis(
            models["XGBoost"].predict_proba(x_test)[:, 1], y_test
        ),
        "top_feature_importance": [
            {"feature": name, "importance": round(float(value), 6)}
            for name, value in importance[:15]
        ],
        "claims": {
            "dataset_scope": "9,841 labelled wallet snapshots plus reviewed live rows",
            "live_scope": "observed wallets in simulated, pending-provider, or confirmed-block feeds",
            "pre_confirmation": "provider-visible pending transactions and pre-broadcast API screening",
        },
    }
    METRICS.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (REGISTRY / f"{version}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(f"Saved model to {MODEL}")
    return payload


if __name__ == "__main__":
    train(
        Path(sys.argv[1]) if len(sys.argv) > 1 else DATASET,
        Path(sys.argv[2]) if len(sys.argv) > 2 else None,
    )
