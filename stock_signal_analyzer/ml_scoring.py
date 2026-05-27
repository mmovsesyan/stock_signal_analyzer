"""
ML Scoring — RankEnsemble (LightGBM + ExtraTrees + HistGBM).

Тренируется на outcomes.jsonl, использует component scores как фичи.
Прогноз: вероятность победы сделки → сжимается в [-1, +1].

Использование:
    from stock_signal_analyzer.ml_scoring import RankEnsemble, blend_ml_score
    ensemble = RankEnsemble()
    ml_score = ensemble.predict(technical=0.3, momentum=0.1, news=-0.2, volume=0.05, score=0.12, confidence=0.6)
    final = blend_ml_score(heuristic=0.12, ml=ml_score, ml_weight=0.30)
"""
from __future__ import annotations

import json
import logging
import math
import os
import pickle
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_MIN_SAMPLES = 30          # минимум сделок для обучения
_FIT_FREQ = 50             # переобучать каждые N новых записей
_MODEL_PATH_ENV = "SSA_ML_MODEL_PATH"


class _LazyModels:
    """Ленивая загрузка ML-библиотек (импорт тяжёлый)."""

    def __init__(self):
        self._lgb = None
        self._xgb_style = None  # sklearn ensembles
        self._hist = None
        self._le = None
        self._ready = False

    def _load(self):
        if self._ready:
            return
        try:
            import lightgbm as lgb
            self._lgb = lgb
        except Exception as exc:
            _log.debug("lightgbm unavailable: %s", exc)
        try:
            from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
            self._xgb_style = ExtraTreesClassifier
            self._hist = HistGradientBoostingClassifier
        except Exception as exc:
            _log.debug("sklearn ensembles unavailable: %s", exc)
        try:
            from sklearn.preprocessing import LabelEncoder
            self._le = LabelEncoder
        except Exception as exc:
            _log.debug("sklearn preprocessing unavailable: %s", exc)
        self._ready = True

    def available(self) -> bool:
        self._load()
        return self._lgb is not None and self._xgb_style is not None and self._hist is not None


_LAZY = _LazyModels()


def _outcomes_path() -> str:
    return os.path.join(
        os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer"),
        "outcomes.jsonl",
    )


def _model_path() -> str:
    default = os.path.join(
        os.environ.get("STOCK_SIGNAL_DATA", "/var/lib/stock_signal_analyzer"),
        "ml_rank_ensemble.pkl",
    )
    return os.environ.get(_MODEL_PATH_ENV, default)


def _load_training_data(path: str) -> tuple[list[dict[str, Any]], list[float]]:
    """Загрузить закрытые сделки: (records, binary_labels)."""
    if not Path(path).exists():
        return [], []

    records: list[dict[str, Any]] = []
    labels: list[float] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                out = rec.get("outcome", "")
                if out not in ("win_t1", "win_t2", "loss"):
                    continue
                label = 1.0 if out in ("win_t1", "win_t2") else 0.0
                # нужны хотя бы score и confidence
                if rec.get("score") is None and rec.get("confidence") is None:
                    continue
                records.append(rec)
                labels.append(label)
            except json.JSONDecodeError:
                continue
    return records, labels


def _extract_features(records: list[dict[str, Any]]) -> list[list[float]]:
    """Извлечь числовой вектор фич из записи."""
    X: list[list[float]] = []
    for rec in records:
        ts = float(rec.get("technical_score") or 0.0)
        ms = float(rec.get("momentum_score") or 0.0)
        ns = float(rec.get("news_score") or 0.0)
        vs = float(rec.get("volume_score") or 0.0)
        sc = float(rec.get("score") or 0.0)
        conf = float(rec.get("confidence") or 0.5)
        # direction: long=+1, short=-1, unknown=0
        direction = 1.0 if rec.get("direction") == "long" else (-1.0 if rec.get("direction") == "short" else 0.0)
        # tier: A=3, B=2, C=1
        tier_map = {"A": 3.0, "B": 2.0, "C": 1.0}
        tier = tier_map.get(rec.get("signal_tier"), 1.0)
        # pnl of this signal as a feature (how it performed before, if available)
        prev_pnl = float(rec.get("pnl_pct") or 0.0)
        X.append([ts, ms, ns, vs, sc, conf, direction, tier, prev_pnl])
    return X


class RankEnsemble:
    """Ансамбль LightGBM + ExtraTrees + HistGBM для скоринга сигналов."""

    def __init__(self, outcomes_path: str | None = None, model_path: str | None = None):
        self.outcomes_path = outcomes_path or _outcomes_path()
        self.model_path = model_path or _model_path()
        self._models: dict[str, Any] = {}
        self._trained_count = 0
        self._last_fit_count = 0
        self._load_persisted()

    def _load_persisted(self) -> None:
        p = Path(self.model_path)
        if not p.exists():
            return
        try:
            with open(p, "rb") as f:
                payload = pickle.load(f)
            self._models = payload.get("models", {})
            self._trained_count = payload.get("trained_count", 0)
            self._last_fit_count = self._trained_count
            _log.info("Loaded persisted ML ensemble (%d samples)", self._trained_count)
        except Exception as exc:
            _log.warning("Failed to load persisted model: %s", exc)

    def _save_persisted(self) -> None:
        try:
            Path(self.model_path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.model_path, "wb") as f:
                pickle.dump({"models": self._models, "trained_count": self._trained_count}, f)
        except Exception as exc:
            _log.warning("Failed to persist model: %s", exc)

    def _needs_refit(self) -> bool:
        """Нужно ли переобучать (накопилось достаточно новых записей)."""
        return (self._trained_count - self._last_fit_count) >= _FIT_FREQ

    def fit(self, force: bool = False) -> bool:
        """Обучить ансамбль на текущих outcomes. Возвращает True если успешно."""
        if not _LAZY.available():
            _log.warning("ML libraries unavailable — cannot fit RankEnsemble")
            return False

        records, y = _load_training_data(self.outcomes_path)
        if len(records) < _MIN_SAMPLES:
            _log.debug("Too few decisive outcomes (%d < %d) — skipping ML fit", len(records), _MIN_SAMPLES)
            return False

        if not force and not self._needs_refit() and self._models:
            return True

        X = _extract_features(records)

        try:
            import numpy as np
            Xa = np.array(X)
            ya = np.array(y)

            # 1. LightGBM
            lgb_model = _LAZY._lgb.LGBMClassifier(
                n_estimators=200,
                learning_rate=0.05,
                num_leaves=31,
                max_depth=-1,
                min_child_samples=5,
                subsample=0.8,
                colsample_bytree=0.8,
                random_state=42,
                n_jobs=-1,
                verbosity=-1,
            )
            lgb_model.fit(Xa, ya)

            # 2. ExtraTrees
            et_model = _LAZY._xgb_style(
                n_estimators=200,
                max_depth=12,
                min_samples_split=4,
                min_samples_leaf=2,
                max_features="sqrt",
                random_state=42,
                n_jobs=-1,
            )
            et_model.fit(Xa, ya)

            # 3. HistGradientBoosting
            hist_model = _LAZY._hist(
                max_iter=200,
                learning_rate=0.05,
                max_depth=5,
                min_samples_leaf=5,
                random_state=42,
            )
            hist_model.fit(Xa, ya)

            self._models = {
                "lgb": lgb_model,
                "et": et_model,
                "hist": hist_model,
            }
            self._trained_count = len(records)
            self._last_fit_count = self._trained_count
            self._save_persisted()
            _log.info("RankEnsemble fitted on %d samples", len(records))
            return True
        except Exception as exc:
            _log.error("RankEnsemble fit failed: %s", exc)
            return False

    def predict(self, technical: float, momentum: float, news: float, volume: float,
                score: float, confidence: float, direction: str = "long",
                tier: str = "C", pnl: float | None = None) -> float | None:
        """Предсказать ML-скор [-1, 1] для одного сигнала."""
        if not self._models:
            # попробовать подгрузить persisted
            self._load_persisted()
        if not self._models:
            return None

        if not _LAZY.available():
            return None

        try:
            import numpy as np
            dir_val = 1.0 if direction == "long" else (-1.0 if direction == "short" else 0.0)
            tier_map = {"A": 3.0, "B": 2.0, "C": 1.0}
            tier_val = tier_map.get(tier, 1.0)
            prev_pnl = float(pnl or 0.0)
            vec = np.array([[technical, momentum, news, volume, score, confidence, dir_val, tier_val, prev_pnl]])

            probs: list[float] = []
            for name, model in self._models.items():
                if hasattr(model, "predict_proba"):
                    prob = float(model.predict_proba(vec)[0][1])
                    probs.append(prob)
                elif hasattr(model, "predict"):
                    # fallback for regressors
                    val = float(model.predict(vec)[0])
                    probs.append(np.clip(val, 0.0, 1.0))

            if not probs:
                return None

            avg_prob = sum(probs) / len(probs)
            # map probability [0,1] to score [-1,1] centered around 0.5
            # at p=0.5 -> 0, p=1 -> +1, p=0 -> -1
            ml_score = (avg_prob - 0.5) * 2.0
            return float(np.clip(ml_score, -1.0, 1.0))
        except Exception as exc:
            _log.debug("RankEnsemble predict failed: %s", exc)
            return None

    def feature_importances(self) -> dict[str, float] | None:
        """Средняя важность фич по всем моделям."""
        if not self._models:
            return None
        names = ["technical", "momentum", "news", "volume", "score", "confidence", "direction", "tier", "prev_pnl"]
        sums = [0.0] * len(names)
        counts = [0] * len(names)
        for model in self._models.values():
            if hasattr(model, "feature_importances_"):
                imp = model.feature_importances_
                if len(imp) == len(names):
                    for i, v in enumerate(imp):
                        sums[i] += float(v)
                        counts[i] += 1
        if not any(counts):
            return None
        return {names[i]: round(sums[i] / max(counts[i], 1), 4) for i in range(len(names))}


def blend_ml_score(heuristic: float, ml: float | None, ml_weight: float = 0.30) -> float:
    """Смешать эвристический и ML скор.

    ml_weight=0.30 → 70% heuristic + 30% ML (консервативно, пока мало данных).
    """
    if ml is None or math.isnan(ml):
        return heuristic
    if not (0.0 <= ml_weight <= 1.0):
        ml_weight = 0.30
    return float((1.0 - ml_weight) * heuristic + ml_weight * ml)
