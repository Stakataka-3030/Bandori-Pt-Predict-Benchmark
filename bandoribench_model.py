"""Helper for serving external BandoriBench models over JSON Lines.

A model runner keeps stdout reserved for protocol JSON. Write logs to stderr.
The benchmark process sends only previously completed event truth through
observe_event; current/future truth is never part of forecast_panel messages.
"""

from __future__ import annotations

import json
import sys
from typing import Any

MODEL_API_VERSION = "bandoribench-model-api-v1"


def _write(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
    sys.stdout.flush()


def serve(model: Any) -> None:
    """Serve an object implementing optional initialize/observe_event and predict_panel."""
    for raw in sys.stdin:
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError("message must be a JSON object")
            kind = message.get("type")
            if kind == "init":
                if message.get("api_version") != MODEL_API_VERSION:
                    raise ValueError("model API version mismatch")
                context = message.get("context")
                if not isinstance(context, dict):
                    raise ValueError("init context must be an object")
                initialize = getattr(model, "initialize", None)
                if initialize is not None:
                    initialize(context)
                _write({
                    "type": "ready",
                    "api_version": MODEL_API_VERSION,
                    "model_id": str(getattr(model, "model_id")),
                    "model_version": str(getattr(model, "model_version")),
                    "training_cutoff_ms": getattr(model, "training_cutoff_ms", None),
                    "supports_online_update": bool(getattr(model, "supports_online_update", False)),
                })
            elif kind == "observe_event":
                event = message.get("event")
                if not isinstance(event, dict):
                    raise ValueError("observe_event requires an event object")
                observe = getattr(model, "observe_event", None)
                if observe is not None:
                    observe(event)
                _write({"type": "observed", "event_id": event.get("event_id")})
            elif kind == "forecast_panel":
                panel = message.get("panel")
                if not isinstance(panel, dict):
                    raise ValueError("forecast_panel requires a panel object")
                predict = getattr(model, "predict_panel", None)
                if predict is None:
                    raise ValueError("model must implement predict_panel(panel)")
                predictions = predict(panel)
                if not isinstance(predictions, list) or any(not isinstance(row, dict) for row in predictions):
                    raise ValueError("predict_panel must return a list of objects")
                _write({
                    "type": "forecast",
                    "panel_id": panel.get("panel_id"),
                    "predictions": predictions,
                })
            elif kind == "finish":
                finalize = getattr(model, "finalize", None)
                if finalize is not None:
                    finalize()
                _write({"type": "finished"})
                return
            else:
                raise ValueError(f"unknown model API message type: {kind}")
        except Exception as exc:
            _write({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            return


__all__ = ["MODEL_API_VERSION", "serve"]
