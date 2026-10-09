"""Train and evaluate a TensorFlow classifier for abandonment patterns."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

try:
    import numpy as np
    import tensorflow as tf
except ModuleNotFoundError as exc:  # Friendly error before a virtualenv is prepared.
    raise SystemExit("Install dependencies first: python3 -m pip install -r requirements.txt") from exc


TARGET = "detected_pattern"
IGNORED = {"case_id", "client_id", "split", TARGET, "recommended_action"}
CATEGORICAL = {"last_funnel_stage", "country_code", "device_type", "traffic_source"}

PATTERN_ACTIONS = {
    "cart_not_checkout": "REVIEW_CART_VALUE",
    "drop_after_checkout_start": "REVIEW_CHECKOUT_ENTRY",
    "drop_after_contact": "SEND_CHECKOUT_REMINDER",
    "drop_after_address": "CHECK_DELIVERY_SETTINGS",
    "drop_after_shipping": "SEND_CHECKOUT_REMINDER",
    "high_shipping_ratio": "OFFER_FREE_SHIPPING",
    "high_extra_costs": "REVIEW_EXTRA_COSTS",
    "payment_error": "SUGGEST_ALTERNATIVE_PAYMENT",
    "discount_error": "CHECK_DISCOUNT_CODE",
    "inventory_error": "CHECK_PRODUCT_STOCK",
    "high_cart_value": "OFFER_PERSONAL_HELP",
    "repeated_abandonment": "AVOID_AUTOMATIC_DISCOUNT",
    "reminder_responsive": "SEND_CHECKOUT_REMINDER",
    "message_non_responder": "STOP_MESSAGES",
    "completed": "NO_ACTION",
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as file:
        return list(csv.DictReader(file))


def build_schema(rows: list[dict[str, str]]) -> dict:
    columns = [column for column in rows[0] if column not in IGNORED]
    numeric = [column for column in columns if column not in CATEGORICAL]
    vocabularies = {
        column: sorted({row[column] for row in rows}) for column in columns if column in CATEGORICAL
    }
    labels = sorted({row[TARGET] for row in rows})
    return {"numeric": numeric, "categorical": vocabularies, "labels": labels}


def encode_features(rows: list[dict[str, str]], schema: dict) -> np.ndarray:
    categorical_indexes = {
        column: {value: index for index, value in enumerate(values)}
        for column, values in schema["categorical"].items()
    }
    width = len(schema["numeric"]) + sum(len(values) + 1 for values in schema["categorical"].values())
    features = np.zeros((len(rows), width), dtype=np.float32)
    for row_index, row in enumerate(rows):
        offset = 0
        for column in schema["numeric"]:
            features[row_index, offset] = float(row.get(column, "") or 0)
            offset += 1
        for column, values in schema["categorical"].items():
            index = categorical_indexes[column].get(row.get(column, ""), len(values))
            features[row_index, offset + index] = 1.0
            offset += len(values) + 1
    return features


def encode(rows: list[dict[str, str]], schema: dict) -> tuple[np.ndarray, np.ndarray]:
    label_index = {label: index for index, label in enumerate(schema["labels"])}
    features = encode_features(rows, schema)
    labels = np.array([label_index[row[TARGET]] for row in rows], dtype=np.int32)
    return features, labels


def make_model(train_features: np.ndarray, class_count: int, seed: int) -> tf.keras.Model:
    tf.keras.utils.set_random_seed(seed)
    normalizer = tf.keras.layers.Normalization()
    normalizer.adapt(train_features)
    model = tf.keras.Sequential([
        tf.keras.Input(shape=(train_features.shape[1],)),
        normalizer,
        tf.keras.layers.Dense(64, activation="relu"),
        tf.keras.layers.Dropout(0.15),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.Dense(class_count, activation="softmax"),
    ])
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def train(project: Path, epochs: int, batch_size: int, seed: int) -> dict:
    processed = project / "data/processed"
    train_rows = read_rows(processed / "train.csv")
    validation_rows = read_rows(processed / "validation.csv")
    test_rows = read_rows(processed / "test.csv")
    schema = build_schema(train_rows)
    x_train, y_train = encode(train_rows, schema)
    x_validation, y_validation = encode(validation_rows, schema)
    x_test, y_test = encode(test_rows, schema)

    model = make_model(x_train, len(schema["labels"]), seed)
    callbacks = [tf.keras.callbacks.EarlyStopping(
        monitor="val_loss", patience=5, restore_best_weights=True
    )]
    history = model.fit(
        x_train, y_train,
        validation_data=(x_validation, y_validation),
        epochs=epochs,
        batch_size=batch_size,
        callbacks=callbacks,
        verbose=2,
    )
    test_loss, test_accuracy = model.evaluate(x_test, y_test, verbose=0)
    probabilities = model.predict(x_test, batch_size=batch_size, verbose=0)
    predictions = probabilities.argmax(axis=1)
    confidence = probabilities.max(axis=1)
    matrix = tf.math.confusion_matrix(y_test, predictions, num_classes=len(schema["labels"])).numpy()

    models_dir = project / "models"
    models_dir.mkdir(exist_ok=True)
    model.save(models_dir / "abandonment_pattern_model.keras")
    metadata = {
        **schema,
        "pattern_actions": PATTERN_ACTIONS,
        "tensorflow_version": tf.__version__,
        "seed": seed,
    }
    (models_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    prediction_rows = []
    correct_actions = 0
    for row, predicted_index, probability in zip(test_rows, predictions, confidence):
        pattern = schema["labels"][int(predicted_index)]
        action = PATTERN_ACTIONS[pattern]
        correct_actions += int(action == row["recommended_action"])
        prediction_rows.append({
            "case_id": row["case_id"],
            "actual_pattern": row[TARGET],
            "predicted_pattern": pattern,
            "confidence": round(float(probability), 6),
            "recommended_action": action,
        })
    with (processed / "tensorflow_predictions.jsonl").open("w", encoding="utf-8") as file:
        for row in prediction_rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "train_cases": len(train_rows),
        "validation_cases": len(validation_rows),
        "test_cases": len(test_rows),
        "epochs_trained": len(history.history["loss"]),
        "test_loss": float(test_loss),
        "pattern_accuracy": float(test_accuracy),
        "action_accuracy": correct_actions / len(test_rows),
        "mean_confidence": float(confidence.mean()),
        "labels": schema["labels"],
        "confusion_matrix": matrix.tolist(),
    }
    (models_dir / "evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    report = train(project, args.epochs, args.batch_size, args.seed)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
