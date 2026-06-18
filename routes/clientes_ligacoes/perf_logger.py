from __future__ import annotations

import time
from pathlib import Path


_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "meus_clientes_perf.log"


def log_perf(app, scope, label, started_at, **extra):
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    details = " ".join(f"{key}={value}" for key, value in extra.items() if value is not None)
    message = f"[PERF {scope}] {label} {elapsed_ms:.1f}ms {details}".rstrip()
    app.logger.info(message)

    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")
    except Exception:
        pass
