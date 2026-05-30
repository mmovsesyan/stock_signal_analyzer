"""Tests for ml_scoring module."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from stock_signal_analyzer.ml_scoring import (
    RankEnsemble,
    _extract_features,
    _load_training_data,
    blend_ml_score,
)


@pytest.fixture
def sample_outcomes_file():
    records = []
    # Generate 40 decisive outcomes (enough for _MIN_SAMPLES=30)
    for i in range(40):
        out = "win_t1" if i % 3 != 0 else "loss"
        rec = {
            "signal_id": f"SIG_{i}",
            "symbol": "AAPL",
            "outcome": out,
            "pnl_pct": 3.0 if out.startswith("win") else -3.0,
            "technical_score": 0.1 + (i % 5) * 0.02,
            "momentum_score": 0.05 + (i % 3) * 0.03,
            "news_score": 0.0 if out == "loss" else 0.2,
            "volume_score": 0.05,
            "score": 0.1 if out == "loss" else 0.3,
            "confidence": 0.5 + (i % 5) * 0.05,
            "direction": "long" if i % 2 == 0 else "short",
            "signal_tier": "A" if i < 10 else ("B" if i < 25 else "C"),
            "kronos_score": 0.1 if i % 4 == 0 else 0.0,
        }
        records.append(rec)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "outcomes.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        yield str(path)


class TestLoadTrainingData:
    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("")
            path = f.name
        try:
            recs, labels = _load_training_data(path)
            assert recs == []
            assert labels == []
        finally:
            os.unlink(path)

    def test_loads_decisive_only(self, sample_outcomes_file):
        recs, labels = _load_training_data(sample_outcomes_file)
        assert len(recs) == 40
        assert all(l in (0.0, 1.0) for l in labels)
        wins = sum(labels)
        losses = len(labels) - wins
        assert wins > 0 and losses > 0


class TestExtractFeatures:
    def test_shapes_and_values(self, sample_outcomes_file):
        recs, _ = _load_training_data(sample_outcomes_file)
        X = _extract_features(recs)
        assert len(X) == len(recs)
        assert all(len(v) == 10 for v in X)
        # direction encoding
        long_dir = X[0][6]  # first record is long
        short_dir = X[1][6]  # second record is short
        assert long_dir == 1.0
        assert short_dir == -1.0


class TestRankEnsemble:
    def test_predict_without_fit_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "model.pkl"
            re = RankEnsemble(outcomes_path="/nonexistent/outcomes.jsonl", model_path=str(model_path))
            assert re.predict(0.1, 0.1, 0.1, 0.1, 0.1, 0.5) is None

    def test_fit_insufficient_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcomes = Path(tmp) / "outcomes.jsonl"
            with open(outcomes, "w") as f:
                for i in range(5):
                    f.write(json.dumps({"outcome": "win_t1", "score": 0.1}) + "\n")
            model_path = Path(tmp) / "model.pkl"
            re = RankEnsemble(outcomes_path=str(outcomes), model_path=str(model_path))
            ok = re.fit(force=True)
            assert ok is False

    def test_fit_and_predict(self, sample_outcomes_file):
        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "model.pkl"
            re = RankEnsemble(outcomes_path=sample_outcomes_file, model_path=str(model_path))
            ok = re.fit(force=True)
            # fit may fail if ML libs not installed; skip gracefully
            if not ok:
                pytest.skip("ML libraries not available")

            ml_score = re.predict(
                technical=0.2, momentum=0.2, news=0.2, volume=0.1,
                score=0.25, confidence=0.7, direction="long", tier="A"
            )
            assert ml_score is not None
            assert -1.0 <= ml_score <= 1.0

            # Persist / reload cycle
            re2 = RankEnsemble(outcomes_path=sample_outcomes_file, model_path=str(model_path))
            ml_score2 = re2.predict(
                technical=0.2, momentum=0.2, news=0.2, volume=0.1,
                score=0.25, confidence=0.7, direction="long", tier="A"
            )
            assert ml_score2 is not None
            assert abs(ml_score2 - ml_score) < 1e-6

    def test_feature_importances(self, sample_outcomes_file):
        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "model.pkl"
            re = RankEnsemble(outcomes_path=sample_outcomes_file, model_path=str(model_path))
            ok = re.fit(force=True)
            if not ok:
                pytest.skip("ML libraries not available")
            imp = re.feature_importances()
            assert imp is not None
            assert "technical" in imp
            assert "kronos" in imp
            assert all(v >= 0 for v in imp.values())

    def test_last_fit_at_persisted(self, sample_outcomes_file):
        with tempfile.TemporaryDirectory() as tmp:
            model_path = Path(tmp) / "model.pkl"
            re = RankEnsemble(outcomes_path=sample_outcomes_file, model_path=str(model_path))
            ok = re.fit(force=True)
            if not ok:
                pytest.skip("ML libraries not available")
            assert re._last_fit_at is not None
            assert "T" in re._last_fit_at

            # Reload and check last_fit_at preserved
            re2 = RankEnsemble(outcomes_path=sample_outcomes_file, model_path=str(model_path))
            assert re2._last_fit_at == re._last_fit_at


class TestBlendMlScore:
    def test_blend_with_none(self):
        assert blend_ml_score(0.5, None) == 0.5

    def test_blend_basic(self):
        assert blend_ml_score(0.0, 1.0, ml_weight=0.5) == 0.5

    def test_blend_clipped_weight(self):
        # weight > 1 is clamped effectively by min/max logic inside function
        assert blend_ml_score(0.0, 1.0, ml_weight=2.0) == pytest.approx(0.3, abs=0.01)

    def test_blend_nan(self):
        assert blend_ml_score(0.5, float("nan")) == 0.5
