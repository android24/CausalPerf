from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


FORBIDDEN_NAMES = {
    ".git", ".gradle", "private-evaluator", "ground-truth.json",
    "expert-patch.diff", "hidden-tests", "evaluator-policy.json",
    "evaluation-canaries.json",
}
PRIVATE_MARKERS = (b"BEGIN PRIVATE GROUND TRUTH", b"CAUSALPERF_PRIVATE_CANARY_")


@dataclass(frozen=True)
class ScanResult:
    phase: str
    finding_codes: tuple[str, ...]
    files_scanned: int
    bytes_scanned: int

    @property
    def passed(self) -> bool:
        return not self.finding_codes

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "status": "PASS" if self.passed else "FAIL",
            "finding_codes": list(self.finding_codes),
            "files_scanned": self.files_scanned,
            "bytes_scanned": self.bytes_scanned,
        }


def _codes(data: bytes, canaries: tuple[bytes, ...], *, name: str = "") -> set[str]:
    findings: set[str] = set()
    encoded_name = name.encode(errors="replace")
    if any(canary in data or canary in encoded_name for canary in canaries):
        findings.add("PRIVATE_CANARY")
    if any(marker in data or marker in encoded_name for marker in PRIVATE_MARKERS):
        findings.add("PRIVATE_MARKER")
    return findings


def scan_values(values: list[str], canary_values: tuple[str, ...], phase: str) -> ScanResult:
    canaries = tuple(item.encode() for item in canary_values)
    findings: set[str] = set()
    size = 0
    for value in values:
        data = value.encode(errors="replace")
        size += len(data)
        findings.update(_codes(data, canaries))
    return ScanResult(phase, tuple(sorted(findings)), 0, size)


def scan_tree(root: Path, canary_values: tuple[str, ...], phase: str,
              *, max_files: int, max_bytes: int) -> ScanResult:
    canaries = tuple(item.encode() for item in canary_values)
    findings: set[str] = set()
    file_count = 0
    byte_count = 0
    if not root.exists():
        return ScanResult(phase, (), 0, 0)
    if root.is_symlink() or not root.is_dir():
        return ScanResult(phase, ("SYMLINK",), 0, 0)
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories + files:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if name in FORBIDDEN_NAMES:
                findings.add("FORBIDDEN_PATH")
            if path.is_symlink():
                findings.add("SYMLINK")
            findings.update(_codes(b"", canaries, name=relative))
        for name in files:
            path = current_path / name
            if path.is_symlink():
                continue
            file_count += 1
            if file_count > max_files:
                findings.add("SCAN_LIMIT_EXCEEDED")
                return ScanResult(phase, tuple(sorted(findings)), file_count, byte_count)
            data = path.read_bytes()
            byte_count += len(data)
            if byte_count > max_bytes:
                findings.add("SCAN_LIMIT_EXCEEDED")
                return ScanResult(phase, tuple(sorted(findings)), file_count, byte_count)
            findings.update(_codes(data, canaries, name=path.relative_to(root).as_posix()))
    return ScanResult(phase, tuple(sorted(findings)), file_count, byte_count)


def tree_digest(root: Path) -> str:
    hasher = hashlib.sha256()
    if not root.exists():
        hasher.update(b"MISSING")
        return hasher.hexdigest()
    if root.is_file():
        hasher.update(root.name.encode())
        hasher.update(root.read_bytes())
        return hasher.hexdigest()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode()
        hasher.update(relative)
        if path.is_symlink():
            hasher.update(b"SYMLINK")
            hasher.update(os.readlink(path).encode())
        elif path.is_file():
            hasher.update(path.read_bytes())
    return hasher.hexdigest()
