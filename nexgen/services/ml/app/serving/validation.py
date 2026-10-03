"""Model validation gate.

Every model in the registry was marked active the moment training finished. No
step between "training returned" and "the API serves this" ever asked whether
the result was believable, so two models that cannot be right are live:

  sla_predictor    accuracy, precision, recall, F1 and ROC-AUC all exactly 1.0,
                   on a set holding 384 delays against 8,328 on-time trips.
                   Perfectly separating a 4.4% minority class does not happen on
                   operational data. A feature carries the answer.
  route_optimizer  r2 = 0.9996 predicting transit time. Same disease.

Both are worse than having no model. A missing prediction is visibly missing; a
confident wrong one gets planned around.

The gate is deliberately crude and deterministic. It cannot detect leakage —
nothing can, from metrics alone — but it can detect the *signature* of leakage,
which is a score too good to be true. Anything it rejects stays in the registry
with a reason attached, because the reason is what tells you where to look.

Two model kinds, judged differently:

  supervised  learns a target from features. Leakage is the risk, so the
              near-perfect rules bite.
  descriptive scoring, ranking and aggregation. There is no held-out target to
              leak, so only sample size and empty output are checked. A driver
              ranking over 11 drivers is not wrong, it is unpublishable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# A metric at or above this is treated as impossible rather than excellent.
# 0.999 not 1.0: leakage rarely lands exactly on 1.0 — route_optimizer scored
# 0.9996 and would sail through an equality check.
PERFECT = 0.999

# Below this a "good" score is noise. A classifier fitted on 150 rows can hit
# 0.95 by memorising, and no threshold on the score itself will catch it.
MIN_TRAIN_ROWS = 200
MIN_TEST_ROWS = 50

# Descriptive models publish rankings. Ranking 11 people invites conclusions the
# sample cannot support.
MIN_ENTITIES = 30

# Metrics that must be present before a supervised model may serve. A model
# registered with no evaluation at all is the easiest failure to miss, because
# nothing looks wrong — there is simply nothing there.
REQUIRED_CLASSIFIER = ("auc_roc", "precision", "recall")
REQUIRED_REGRESSOR = ("mae",)

# Keys whose value is a score in [0, 1] where ~1.0 means "too good".
_SCORE_KEYS = (
    "auc_roc", "auc", "roc_auc", "accuracy", "precision", "recall",
    "f1_score", "f1", "r2", "r2_score",
)

# Keys that count how much the model actually produced. Zero means the model
# ran, registered, and output nothing.
_OUTPUT_KEYS = (
    "clients_forecasted", "drivers_scored", "routes_scored", "predictions",
    "drivers_analyzed", "unique_routes",
)

# Which registered models are supervised learners. Anything absent is treated as
# descriptive, which is the safer default: descriptive rules never wrongly
# reject a real model, they only decline to publish a thin one.
SUPERVISED = {
    "sla_predictor", "eta_predictor", "fuel_predictor", "lstm_predictor",
    "route_optimizer", "demand_forecaster", "client_demand_forecaster",
    "anomaly_detector",
}


@dataclass
class Verdict:
    model_name: str
    passed: bool = True
    reasons: list[str] = field(default_factory=list)
    kind: str = "descriptive"
    checked: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "passed" if self.passed else "rejected"

    def fail(self, reason: str) -> None:
        self.passed = False
        self.reasons.append(reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name, "status": self.status,
            "kind": self.kind, "reasons": self.reasons, "checked": self.checked,
        }


def _as_dict(metrics: Any) -> dict[str, Any]:
    if isinstance(metrics, dict):
        return metrics
    if isinstance(metrics, (str, bytes)):
        try:
            parsed = json.loads(metrics)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _flatten(d: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Nested metric blobs hide the numbers that matter.

    route_optimizer stores {"predictor": {"r2": 0.9996}} — a top-level scan
    finds no r2 at all and the model passes for the wrong reason.
    """
    out: dict[str, Any] = {}
    if depth > 4:
        return out
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flatten(v, depth + 1))
        else:
            out[k] = v
    return out


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # reject NaN


def validate(
    model_name: str,
    metrics: Any,
    training_count: int | None = None,
    test_count: int | None = None,
    kind: str | None = None,
) -> Verdict:
    """Decide whether a trained model may be served."""
    v = Verdict(model_name=model_name)
    v.kind = kind or ("supervised" if model_name in SUPERVISED else "descriptive")
    flat = _flatten(_as_dict(metrics))

    if not flat:
        v.fail("no metrics recorded — a model with no evaluation cannot be "
               "shown to work")
        return v

    # ── 1. impossible scores ─────────────────────────────────────────────────
    if v.kind == "supervised":
        v.checked.append("near-perfect scores")
        perfect_hits = []
        for key in _SCORE_KEYS:
            score = _num(flat.get(key))
            if score is not None and score >= PERFECT:
                perfect_hits.append(f"{key}={score:g}")
        if perfect_hits:
            v.fail(
                f"score at or above {PERFECT} ({', '.join(sorted(perfect_hits))}) "
                "— operational prediction does not reach this; the most likely "
                "cause is a feature that encodes the target"
            )

        # Perfect precision AND recall together is the leakage signature even
        # when each sits just under the threshold.
        p, r = _num(flat.get("precision")), _num(flat.get("recall"))
        if p is not None and r is not None and p >= 0.99 and r >= 0.99:
            v.fail(
                f"precision {p:g} and recall {r:g} are both near 1.0 — the model "
                "makes no mistakes in either direction, which real data does not "
                "allow"
            )

    # ── 2. required metrics ──────────────────────────────────────────────────
    if v.kind == "supervised":
        v.checked.append("required metrics present")
        is_classifier = any(k in flat for k in ("auc_roc", "accuracy", "f1_score",
                                                "precision", "recall"))
        required = REQUIRED_CLASSIFIER if is_classifier else REQUIRED_REGRESSOR
        missing = [k for k in required if _num(flat.get(k)) is None]
        if missing:
            v.fail(f"missing required metric(s): {', '.join(missing)}")

    # ── 3. sample size ───────────────────────────────────────────────────────
    n_train = training_count if training_count is not None else _num(
        flat.get("training_samples"))
    n_test = test_count if test_count is not None else _num(flat.get("test_samples"))

    if v.kind == "supervised":
        v.checked.append("sample size")
        if n_train is None:
            v.fail("training row count not recorded — the score cannot be "
                   "interpreted without knowing what it was measured on")
        elif n_train < MIN_TRAIN_ROWS:
            v.fail(f"trained on {int(n_train)} rows, below the {MIN_TRAIN_ROWS} "
                   "minimum — a good score here is memorisation")
        if n_test is not None and n_test < MIN_TEST_ROWS:
            v.fail(f"evaluated on {int(n_test)} rows, below the {MIN_TEST_ROWS} "
                   "minimum — the metric has no precision")
    else:
        v.checked.append("entity coverage")
        entities = next(
            (_num(flat[k]) for k in _OUTPUT_KEYS if _num(flat.get(k)) is not None),
            None,
        )
        if entities is not None and 0 < entities < MIN_ENTITIES:
            v.fail(f"covers only {int(entities)} entities, below the "
                   f"{MIN_ENTITIES} minimum for a published ranking")

    # ── 4. produced nothing ──────────────────────────────────────────────────
    v.checked.append("non-empty output")
    for key in _OUTPUT_KEYS:
        count = _num(flat.get(key))
        if count is not None and count == 0:
            v.fail(f"{key} = 0 — the model registered successfully but produced "
                   "no output")

    # ── 5. beat the naive baseline ───────────────────────────────────────────
    # A model that cannot outscore "predict the majority" or "predict the
    # median" has learned nothing, however respectable its headline number.
    # Accuracy of 0.94 sounds fine until you notice 96.8% of trips are on time.
    if "beats_baseline" in flat:
        v.checked.append("beats naive baseline")
        if not flat["beats_baseline"]:
            v.fail("does not beat the naive baseline — the headline metric "
                   "reflects the class balance, not the model")

    # ── 6. negative R2 ───────────────────────────────────────────────────────
    r2 = _num(flat.get("r2"))
    if r2 is not None:
        v.checked.append("R2 above zero")
        if r2 < 0:
            v.fail(f"test r2 = {r2:g} — worse than predicting the mean of the "
                   "test set, so the fit does not transfer")

    # ── 7. enough minority events to measure ─────────────────────────────────
    # A confusion matrix with six events in the minority class produces a
    # precision that moves by 0.17 if one row changes.
    confusion = _as_dict(metrics).get("confusion_matrix")
    if isinstance(confusion, dict) and confusion:
        v.checked.append("minority-class events")
        tp = _num(confusion.get("tp")) or 0
        fn = _num(confusion.get("fn")) or 0
        tn = _num(confusion.get("tn")) or 0
        fp = _num(confusion.get("fp")) or 0
        minority = min(tp + fn, tn + fp)
        if minority < 30:
            v.fail(f"only {int(minority)} minority-class events in the test set "
                   "— every metric derived from them is noise")
        if tn + fp > 0 and tn == 0:
            v.fail("the model never once identified a negative case correctly "
                   "(TN = 0) — it predicts the majority class and nothing else")

    # ── 8. degenerate class balance ──────────────────────────────────────────
    balance = _as_dict(metrics).get("class_balance")
    if isinstance(balance, dict) and len(balance) >= 2:
        v.checked.append("class balance")
        counts = [c for c in (_num(x) for x in balance.values()) if c is not None]
        total = sum(counts)
        if total > 0 and min(counts) / total < 0.01:
            v.fail(f"minority class is {100 * min(counts) / total:.2f}% of the "
                   "data — accuracy is meaningless at this imbalance and the "
                   "model should be scored on recall against a baseline")

    return v


# ── registry-wide audit ─────────────────────────────────────────────────────

def audit(conn) -> list[Verdict]:
    """Validate every registered model without changing anything."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT model_name, version, metrics, training_data_count, is_active
            FROM ml_models ORDER BY model_name, version DESC
        """)
        rows = cur.fetchall()

    verdicts = []
    for r in rows:
        v = validate(r["model_name"], r["metrics"], r.get("training_data_count"))
        verdicts.append(v)
    return verdicts


def ensure_schema(conn) -> None:
    """Add the validation columns if they are not there yet.

    Separate from is_active on purpose. is_active answers "which version is
    current"; validation_status answers "may this be served at all". Keeping
    them apart means re-activating a model by hand cannot silently un-reject it.
    """
    with conn.cursor() as cur:
        cur.execute("SHOW COLUMNS FROM ml_models")
        have = {r["Field"] for r in cur.fetchall()}

        additions = [
            ("validation_status",
             "ALTER TABLE ml_models ADD COLUMN validation_status "
             "ENUM('passed','rejected','unchecked') NOT NULL DEFAULT 'unchecked'"),
            ("validation_reasons",
             "ALTER TABLE ml_models ADD COLUMN validation_reasons TEXT NULL"),
            ("validated_at",
             "ALTER TABLE ml_models ADD COLUMN validated_at DATETIME NULL"),
        ]
        for column, ddl in additions:
            if column not in have:
                cur.execute(ddl)
                log.info("ml_models: added %s", column)
    conn.commit()


def apply_audit(conn, quarantine: bool = True) -> dict[str, Any]:
    """Validate every model and, optionally, deactivate the failures.

    Rejected models are kept, not deleted. The row is the evidence: it records
    what was claimed, when, and why it was not believed — which is what makes
    the next training run checkable against the last.
    """
    ensure_schema(conn)
    verdicts = audit(conn)
    quarantined: list[str] = []

    with conn.cursor() as cur:
        for v in verdicts:
            reasons = json.dumps(v.reasons) if v.reasons else None
            cur.execute("""
                UPDATE ml_models
                   SET validation_status = %s,
                       validation_reasons = %s,
                       validated_at = NOW()
                 WHERE model_name = %s
            """, (v.status, reasons, v.model_name))

            if quarantine and not v.passed:
                cur.execute(
                    "UPDATE ml_models SET is_active = 0 WHERE model_name = %s",
                    (v.model_name,))
                quarantined.append(v.model_name)
    conn.commit()

    return {
        "checked": len(verdicts),
        "passed": [v.model_name for v in verdicts if v.passed],
        "rejected": [v.to_dict() for v in verdicts if not v.passed],
        "deactivated": quarantined,
    }


def register_model(
    conn,
    model_name: str,
    model_type: str,
    target_variable: str,
    metrics: dict[str, Any],
    feature_columns: list[str],
    artifact_path: str | None = None,
    training_count: int | None = None,
    test_count: int | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Register a freshly trained model, active only if it passes the gate.

    Every trainer should route through here rather than writing its own INSERT
    with `is_active = 1` hard-coded — which is how the current registry ended up
    serving a model with an AUC of 1.0.
    """
    ensure_schema(conn)
    verdict = validate(model_name, metrics, training_count, test_count)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM ml_models WHERE model_name = %s",
            (model_name,))
        version = int(cur.fetchone()["v"]) + 1

        # Only stand down the previous version if this one is fit to replace it.
        # Otherwise a failed retrain would take a working model offline with it.
        if verdict.passed:
            cur.execute("UPDATE ml_models SET is_active = 0 WHERE model_name = %s",
                        (model_name,))

        cur.execute("""
            INSERT INTO ml_models
                (model_name, version, model_type, target_variable, metrics,
                 feature_columns, model_artifact_path, training_data_count,
                 is_active, validation_status, validation_reasons, validated_at,
                 notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
        """, (
            model_name, version, model_type, target_variable,
            json.dumps(metrics, default=str), json.dumps(feature_columns),
            artifact_path, training_count,
            1 if verdict.passed else 0,
            verdict.status,
            json.dumps(verdict.reasons) if verdict.reasons else None,
            notes,
        ))
        model_id = cur.lastrowid
    conn.commit()

    if not verdict.passed:
        log.warning("model %s v%s REJECTED and not activated: %s",
                    model_name, version, "; ".join(verdict.reasons))
    else:
        log.info("model %s v%s registered and active", model_name, version)

    return {"model_id": model_id, "version": version, **verdict.to_dict()}
