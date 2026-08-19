from __future__ import annotations

from .artifacts import ContractError, digest


class Ledger:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: list[dict] = []

    def append(self, event: dict) -> dict:
        record = {"schema_version": 1, "run_id": self.run_id, "sequence": len(self.events),
                  "previous_sha256": self.events[-1]["event_sha256"] if self.events else None, **event}
        record["event_sha256"] = digest(record, omit=("event_sha256",))
        self.events.append(record)
        return record

    def verify(self) -> str | None:
        previous = None
        for sequence, event in enumerate(self.events):
            if event["run_id"] != self.run_id or event["sequence"] != sequence or event["previous_sha256"] != previous:
                raise ContractError("ledger order or chain is invalid")
            if digest(event, omit=("event_sha256",)) != event["event_sha256"]:
                raise ContractError("ledger event digest mismatch")
            previous = event["event_sha256"]
        return previous
