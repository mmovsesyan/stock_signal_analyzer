"""
KronosComponent — zero-risk integration of the Kronos foundation model
for candlestick time-series prediction.

Usage:
    comp = KronosComponent()
    score, detail = comp.score(hist_df)

Environment variables (all optional):
    KRONOS_ENABLED=0          — feature flag (default disabled)
    KRONOS_MODEL=NeoQuasar/Kronos-base
    KRONOS_TOKENIZER=NeoQuasar/Kronos-Tokenizer-base
    KRONOS_DEVICE=            — "cuda", "mps", "cpu", or empty for auto
    KRONOS_PRED_LEN=5         — how many future bars to predict
    KRONOS_MAX_CONTEXT=512
    KRONOS_WEIGHT=0.15        — blending weight in engine.py (informational)
    KRONOS_MAX_PREDICT_SECS=12 — hard timeout for predict() to avoid blocking bot
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
from typing import Any

import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)

# Minimum history bars required for a meaningful prediction
_MIN_HISTORY_BARS: int = 60


def _is_enabled() -> bool:
    return os.environ.get("KRONOS_ENABLED", "0").strip() == "1"


class KronosComponent:
    """Lazy-loaded Kronos wrapper with graceful degradation."""

    def __init__(self) -> None:
        self._enabled = _is_enabled()
        self._model_name = os.environ.get("KRONOS_MODEL", "NeoQuasar/Kronos-base").strip()
        self._tokenizer_name = os.environ.get("KRONOS_TOKENIZER", "NeoQuasar/Kronos-Tokenizer-base").strip()
        self._device = os.environ.get("KRONOS_DEVICE", "").strip() or None
        self._pred_len = int(os.environ.get("KRONOS_PRED_LEN", "5"))
        self._max_context = int(os.environ.get("KRONOS_MAX_CONTEXT", "512"))
        self._max_predict_secs = float(os.environ.get("KRONOS_MAX_PREDICT_SECS", "18.0"))
        self._predictor: Any | None = None
        self._load_error: str | None = None
        self._loaded = False

    def _load(self) -> bool:
        if not self._enabled:
            return False
        if self._loaded:
            return self._predictor is not None
        self._loaded = True
        try:
            # Heavy imports isolated so missing deps never break core engine import
            import torch  # noqa: F401
            from .kronos_model import Kronos, KronosTokenizer, KronosPredictor  # type: ignore[import-untyped]

            tokenizer = KronosTokenizer.from_pretrained(self._tokenizer_name)
            model = Kronos.from_pretrained(self._model_name)

            # PyTorch 2.x workaround: if from_pretrained returns meta tensors,
            # .to() in KronosPredictor fails. Materialize to CPU and reload weights.
            tokenizer = self._materialize_meta(tokenizer, self._tokenizer_name)
            model = self._materialize_meta(model, self._model_name)

            self._predictor = KronosPredictor(
                model, tokenizer, device=self._device, max_context=self._max_context
            )
            _log.info(
                "Kronos loaded: model=%s tokenizer=%s device=%s",
                self._model_name,
                self._tokenizer_name,
                self._predictor.device,
            )
            return True
        except Exception as exc:
            self._load_error = str(exc)
            _log.warning("Kronos failed to load (graceful fallback): %s", exc)
            return False

    @staticmethod
    def _materialize_meta(module, repo_id: str):
        """Ensure module is not on meta device; materialize to CPU and reload weights from cache."""
        if not any(p.device.type == "meta" for p in module.parameters()):
            return module
        _log.warning(
            "Meta tensors detected in %s; materializing to CPU and reloading weights", repo_id
        )
        import torch
        module = module.to_empty(device="cpu")
        try:
            from huggingface_hub import hf_hub_download, constants as hf_constants
            import safetensors.torch
            model_file = hf_hub_download(
                repo_id, hf_constants.SAFETENSORS_SINGLE_FILE, local_files_only=True
            )
            safetensors.torch.load_model(module, model_file, strict=True, device="cpu")
        except Exception:
            _log.warning("Safetensors reload failed for %s, trying pickle fallback", repo_id)
            from huggingface_hub import hf_hub_download, constants as hf_constants
            model_file = hf_hub_download(
                repo_id, hf_constants.PYTORCH_WEIGHTS_NAME, local_files_only=True
            )
            state_dict = torch.load(
                model_file, map_location=torch.device("cpu"), weights_only=True
            )
            module.load_state_dict(state_dict, strict=True)
        return module

    @staticmethod
    def _prepare_dataframe(hist: pd.DataFrame) -> pd.DataFrame:
        """Normalize Yahoo-like columns to Kronos lower-case schema and ensure amount."""
        df = hist.copy()
        rename_map: dict[str, str] = {}
        for col in df.columns:
            lower = col.lower().strip()
            if lower in ("open", "high", "low", "close", "volume"):
                rename_map[col] = lower
        if rename_map:
            df = df.rename(columns=rename_map)

        # Ensure required price columns exist
        for req in ("open", "high", "low", "close"):
            if req not in df.columns:
                raise ValueError(f"Missing required column '{req}' in hist DataFrame")

        if "volume" not in df.columns:
            df["volume"] = 0.0

        if "amount" not in df.columns:
            # Approximate amount = volume * average price of the bar
            avg_price = df[["open", "high", "low", "close"]].mean(axis=1)
            df["amount"] = df["volume"] * avg_price

        # Drop rows with NaNs in critical columns
        critical_cols = ["open", "high", "low", "close", "volume", "amount"]
        df = df.dropna(subset=critical_cols)
        return df

    def score(self, hist: pd.DataFrame) -> tuple[float, str]:
        """
        Predict future close price via Kronos and return a directional score in [-1, 1].

        Returns:
            (score, detail_string)
        """
        if not self._load():
            return 0.0, ""

        try:
            df = self._prepare_dataframe(hist)
            if len(df) < _MIN_HISTORY_BARS:
                return 0.0, f"Kronos: insufficient history ({len(df)} < {_MIN_HISTORY_BARS})"

            # Build timestamps
            if isinstance(df.index, pd.DatetimeIndex):
                x_timestamp = pd.Series(df.index)
            else:
                # Fallback: assume daily frequency ending today
                x_timestamp = pd.Series(
                    pd.date_range(end=pd.Timestamp.now().normalize(), periods=len(df), freq="B")
                )

            last_date = x_timestamp.iloc[-1]
            # Generate next business days for prediction horizon
            future_dates = pd.date_range(
                start=last_date + pd.Timedelta(days=1),
                periods=self._pred_len,
                freq="B",
            )
            y_timestamp = pd.Series(future_dates)

            # Enforce hard timeout to prevent blocking Telegram bot on slow CPU.
            # Retry once: first executor may have a warm-up penalty; second
            # attempt often succeeds on loaded model.
            pred_df = None
            for attempt in range(2):
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        self._predictor.predict,
                        df=df,
                        x_timestamp=x_timestamp,
                        y_timestamp=y_timestamp,
                        pred_len=self._pred_len,
                        T=1.0,
                        top_k=0,
                        top_p=0.9,
                        sample_count=1,
                        verbose=False,
                    )
                    try:
                        pred_df = future.result(timeout=self._max_predict_secs)
                        break
                    except concurrent.futures.TimeoutError:
                        _log.warning(
                            "Kronos prediction timed out (attempt %d/2) after %.1fs for model %s",
                            attempt + 1,
                            self._max_predict_secs,
                            self._model_name,
                        )
                        if attempt == 0:
                            continue
            if pred_df is None:
                return None, f"Kronos: predict timeout ({self._max_predict_secs:.0f}s)"

            current_close = float(df["close"].iloc[-1])
            predicted_final_close = float(pred_df["close"].iloc[-1])
            if current_close <= 0 or np.isnan(predicted_final_close):
                return 0.0, "Kronos: invalid predicted price"

            change = (predicted_final_close - current_close) / current_close
            # Scale: 3% predicted move maps to score=1.0 (or -1.0)
            score = float(np.clip(change / 0.03, -1.0, 1.0))
            detail = (
                f"Kronos {self._pred_len}d pred: {change:+.2%} "
                f"(cur={current_close:.2f} pred={predicted_final_close:.2f})"
            )
            return score, detail
        except Exception as exc:
            _log.warning("Kronos prediction failed (graceful): %s", exc)
            return 0.0, f"Kronos error: {exc}"

# Module-level singleton: one model load per process (Celery fork-safe)
_KRONOS_INSTANCE: KronosComponent | None = None

def get_kronos() -> KronosComponent:
    global _KRONOS_INSTANCE
    if _KRONOS_INSTANCE is None:
        _KRONOS_INSTANCE = KronosComponent()
    return _KRONOS_INSTANCE
