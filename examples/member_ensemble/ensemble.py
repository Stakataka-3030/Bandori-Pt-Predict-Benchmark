"""Fixed equal-average of two independently developed causal new-era runners.

The two model states are kept separate. This file reads their Python source
modules only; it never reads benchmark data, truth, or T10 caches directly.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HELPER_ROOT = ROOT
if str(HELPER_ROOT) not in sys.path:
    sys.path.insert(0, str(HELPER_ROOT))
from bandoribench_model import serve  # noqa: E402


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import candidate code: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TAIL = load_module("frozen_tail_pool", ROOT / "tail_pool.py")
MULTIPLIER = load_module("frozen_regime_multiplier_runner", ROOT / "runner_regime_multiplier.py")


class FixedEqualNewEra:
    model_id = "newera-tail-plus-regime-equal"
    model_version = "fixed-2026-09-27"
    training_cutoff_ms = 0
    supports_online_update = True

    def __init__(self):
        self.tail = TAIL.Champion()
        self.multiplier = MULTIPLIER.RegimeMultiplierRunner()

    def initialize(self, context: dict) -> None:
        self.tail.initialize(context)
        self.multiplier.initialize(context)

    def observe_event(self, event: dict) -> None:
        self.tail.observe_event(event)
        self.multiplier.observe_event(event)

    def predict_panel(self, panel: dict) -> list[dict]:
        first = self.tail.predict_panel(panel)
        second = self.multiplier.predict_panel(panel)
        a = {row["case_id"]: row for row in first}
        b = {row["case_id"]: row for row in second}
        expected = {task["case_id"] for task in panel["tasks"]}
        if set(a) != expected or set(b) != expected:
            raise ValueError("expert case coverage mismatch")
        rows = []
        for task in panel["tasks"]:
            case_id = task["case_id"]
            value = 0.5 * (float(a[case_id]["prediction"]) + float(b[case_id]["prediction"]))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid combined prediction: {case_id}")
            rows.append({"case_id": case_id, "prediction": value})
        return rows

    def finalize(self) -> None:
        for expert in (self.tail, self.multiplier):
            if hasattr(expert, "finalize"):
                expert.finalize()


if __name__ == "__main__":
    serve(FixedEqualNewEra())
