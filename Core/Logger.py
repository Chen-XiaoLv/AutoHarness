from __future__ import annotations
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


class HarnessLogger:
    def __init__(
        self,
        log_file: Path,
        event_file: Path,
        level: str = "INFO",
    ):
        log_file.parent.mkdir(parents=True, exist_ok=True)
        event_file.parent.mkdir(parents=True, exist_ok=True)

        self.event_file = event_file

        self.logger = logging.getLogger("self_harness")
        self.logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self.logger.handlers.clear()

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"
        )

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def info(self, message: str):
        self.logger.info(message)

    def warning(self, message: str):
        self.logger.warning(message)

    def error(self, message: str):
        self.logger.error(message)

    def event(
        self,
        event_type: str,
        loop_tag: str | None = None,
        round_i: int | None = None,
        agent: str | None = None,
        model: str | None = None,
        case_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ):
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event_type": event_type,
            "loop_tag": loop_tag,
            "round": round_i,
            "agent": agent,
            "model": model,
            "case_id": case_id,
            "payload": payload or {},
        }

        with self.event_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        self.logger.info(
            f"[{event_type}] loop={loop_tag} round={round_i} "
            f"agent={agent} case={case_id}"
        )
