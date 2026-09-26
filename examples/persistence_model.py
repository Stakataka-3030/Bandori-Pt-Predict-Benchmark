"""Minimal point-forecast runner for the BandoriBench model API."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bandoribench_model import serve


class PersistenceModel:
    model_id = "example-persistence"
    model_version = "1"
    training_cutoff_ms = 0
    supports_online_update = False

    def initialize(self, context: dict) -> None:
        self.context = context

    def observe_event(self, event: dict) -> None:
        pass

    def predict_panel(self, panel: dict) -> list[dict]:
        return [
            {
                "case_id": task["case_id"],
                "prediction": task["history"][-1]["ep"],
            }
            for task in panel["tasks"]
        ]


if __name__ == "__main__":
    serve(PersistenceModel())
