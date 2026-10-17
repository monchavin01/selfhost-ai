"""`$LOG_DIR/requests.log`: ISO time, model, duration_ms, status. Never bodies, keys or paths with data."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("backend.requests")


class RequestLog:
    def __init__(self, log_dir: Path) -> None:
        self._lock, self._path = threading.Lock(), log_dir / "requests.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            log.warning("cannot create %s; request lines go to the process log only", log_dir)

    def write(self, model: str, duration_ms: float, status: int) -> None:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='milliseconds')} {''.join(model.split()) or '-'} " \
               f"{duration_ms:.0f} {status}"
        log.info(line)
        try:
            with self._lock, self._path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
        except OSError:
            pass
