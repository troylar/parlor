"""Structured audit log with optional HMAC chain tamper protection.

Writes JSONL entries to a configurable path. Each entry includes a
timestamp, event type, severity, and contextual identifiers. When
tamper protection is enabled, entries are chained via HMAC-SHA256 so
that any modification or deletion is detectable.

Designed for SIEM integration (Splunk, ELK/OpenSearch) — one JSON
object per line, no framing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import json
import logging
import os
import stat
import time

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Genesis value for the first entry in an HMAC chain
_GENESIS_HMAC = "genesis"


@dataclass
class AuditEntry:
    """A single audit log entry."""

    timestamp: str
    event_type: str
    severity: str  # "info", "warning", "error", "critical"
    session_id: str = ""
    user_id: str = ""
    source_ip: str = ""
    conversation_id: str = ""
    tool_name: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        event_type: str,
        severity: str = "info",
        *,
        session_id: str = "",
        user_id: str = "",
        source_ip: str = "",
        conversation_id: str = "",
        tool_name: str = "",
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            severity=severity,
            session_id=session_id,
            user_id=user_id,
            source_ip=source_ip,
            conversation_id=conversation_id,
            tool_name=tool_name,
            details=details or {},
        )


def _derive_hmac_key(private_key_pem: str) -> bytes:
    """Derive a dedicated HMAC key from the Ed25519 identity key via HKDF-SHA256."""
    from cryptography.hazmat.primitives.hashes import SHA256
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    ikm = private_key_pem.encode("utf-8")
    hkdf = HKDF(
        algorithm=SHA256(),
        length=32,
        salt=b"anteroom-audit-hmac-salt-v1",
        info=b"anteroom-audit-v1",
    )
    return hkdf.derive(ikm)


def _compute_hmac(key: bytes, entry_json: bytes, prev_hmac: str) -> str:
    """Compute HMAC-SHA256 over prev_hmac || entry_json."""
    msg = prev_hmac.encode("utf-8") + b"|" + entry_json
    raw = _hmac.new(key, msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _redact_entry(entry_dict: dict[str, Any]) -> dict[str, Any]:
    """Strip sensitive content from details, preserving metadata."""
    redacted = dict(entry_dict)
    details = dict(redacted.get("details", {}))
    for key in ("message_content", "tool_input", "tool_output", "prompt", "response"):
        if key in details:
            details[key] = "[REDACTED]"
    redacted["details"] = details
    return redacted


class AuditWriter:
    """Append-only JSONL audit log writer with optional HMAC chain.

    File locking via fcntl (Unix) with graceful degradation on platforms
    without fcntl (Windows). Entries are flushed and fsynced on every
    write for crash safety.

    Note: The HMAC chain state is maintained in-process. Multiple
    concurrent processes writing to the same log file will produce
    independent chains that cannot be verified together. Use a single
    writer process per log file for chain integrity.
    """

    def __init__(
        self,
        log_dir: Path,
        *,
        enabled: bool = False,
        tamper_protection: str = "hmac",
        private_key_pem: str = "",
        rotation: str = "daily",
        rotate_size_bytes: int = 10_485_760,
        retention_days: int = 90,
        redact_content: bool = True,
        events: dict[str, bool] | None = None,
    ) -> None:
        self.enabled = enabled
        self.log_dir = log_dir
        self.tamper_protection = tamper_protection
        self.rotation = rotation
        self.rotate_size_bytes = rotate_size_bytes
        self.retention_days = retention_days
        self.redact_content = redact_content
        self.events = events or {}

        self._hmac_key: bytes | None = None
        if tamper_protection == "hmac" and private_key_pem:
            self._hmac_key = _derive_hmac_key(private_key_pem)

        self._prev_hmac: str = _GENESIS_HMAC
        self._current_date: str = ""

        if enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.log_dir.chmod(stat.S_IRWXU)  # 0700
            except OSError:
                pass
            self._load_last_hmac()

    def _log_path(self, date_str: str | None = None) -> Path:
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.log_dir / f"audit-{date_str}.jsonl"

    def _load_last_hmac(self) -> None:
        """Read the last HMAC from the current day's log to resume the chain."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._current_date = today
        path = self._log_path(today)
        if not path.exists():
            self._prev_hmac = _GENESIS_HMAC
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                last_line = ""
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        last_line = stripped
                if last_line:
                    entry = json.loads(last_line)
                    self._prev_hmac = entry.get("_hmac", _GENESIS_HMAC)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to resume audit HMAC chain from %s: %s — starting new chain", path, e)
            self._prev_hmac = _GENESIS_HMAC

    def is_event_enabled(self, event_type: str) -> bool:
        """Check if a specific event category is enabled."""
        if not self.enabled:
            return False
        category = event_type.split(".")[0]
        return self.events.get(category, True)

    def emit(self, entry: AuditEntry) -> None:
        """Append an entry to the audit log. No-op if disabled."""
        if not self.enabled:
            return
        if not self.is_event_enabled(entry.event_type):
            return

        entry_dict = asdict(entry)

        if self.redact_content:
            entry_dict = _redact_entry(entry_dict)

        self._maybe_rotate()

        entry_json = json.dumps(entry_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        if self._hmac_key:
            entry_hmac = _compute_hmac(self._hmac_key, entry_json, self._prev_hmac)
            entry_dict["_prev_hmac"] = self._prev_hmac
            entry_dict["_hmac"] = entry_hmac
            # Re-serialize with HMAC fields
            entry_json = json.dumps(entry_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._prev_hmac = entry_hmac

        log_path = self._log_path()
        try:
            with open(log_path, "ab") as f:
                if fcntl is not None:
                    fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.write(entry_json + b"\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    if fcntl is not None:
                        fcntl.flock(f, fcntl.LOCK_UN)
            try:
                log_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
            except OSError:
                pass
        except OSError:
            logger.warning("Failed to write audit log entry to %s", log_path)

    def _maybe_rotate(self) -> None:
        """Handle daily rotation and size-based rotation."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date:
            self._current_date = today
            self._prev_hmac = _GENESIS_HMAC

        if self.rotation == "size":
            path = self._log_path()
            if path.exists():
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                if size >= self.rotate_size_bytes:
                    suffix = int(time.time() * 1000)
                    rotated = path.with_suffix(f".{suffix}.jsonl")
                    try:
                        path.rename(rotated)
                    except OSError:
                        logger.warning("Failed to rotate audit log %s", path)
                    self._prev_hmac = _GENESIS_HMAC

    def purge_old_logs(self) -> int:
        """Delete audit logs older than retention_days. Returns count of deleted files."""
        if not self.enabled or self.retention_days <= 0:
            return 0
        cutoff = time.time() - (self.retention_days * 86400)
        deleted = 0
        try:
            for f in self.log_dir.glob("audit-*.jsonl"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        deleted += 1
                except OSError:
                    pass
        except OSError:
            pass
        return deleted


def verify_chain(log_path: Path, private_key_pem: str) -> list[dict[str, Any]]:
    """Verify the HMAC chain integrity of an audit log file.

    Returns a list of result dicts, one per entry:
        {"line": N, "valid": bool, "event_type": str, "timestamp": str}
    """
    hmac_key = _derive_hmac_key(private_key_pem)
    results: list[dict[str, Any]] = []
    prev_hmac = _GENESIS_HMAC

    with open(log_path, "r", encoding="utf-8") as f:
        for line_num, raw_line in enumerate(f, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                results.append(
                    {
                        "line": line_num,
                        "valid": False,
                        "event_type": "?",
                        "timestamp": "?",
                        "error": "invalid JSON",
                    }
                )
                prev_hmac = _GENESIS_HMAC
                continue

            stored_hmac = entry.get("_hmac", "")
            stored_prev = entry.get("_prev_hmac", "")

            # Reconstruct the entry without HMAC fields for verification
            verify_dict = {k: v for k, v in entry.items() if k not in ("_hmac", "_prev_hmac")}
            verify_json = json.dumps(verify_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

            expected_hmac = _compute_hmac(hmac_key, verify_json, stored_prev)

            hmac_ok = _hmac.compare_digest(stored_hmac, expected_hmac)
            chain_ok = _hmac.compare_digest(stored_prev, prev_hmac)
            valid = hmac_ok and chain_ok
            results.append(
                {
                    "line": line_num,
                    "valid": valid,
                    "event_type": entry.get("event_type", "?"),
                    "timestamp": entry.get("timestamp", "?"),
                }
            )
            prev_hmac = stored_hmac

    return results


def create_audit_writer(
    config: Any,
    private_key_pem: str = "",
) -> AuditWriter:
    """Factory: create an AuditWriter from an AppConfig."""
    audit_cfg = getattr(config, "audit", None)
    if audit_cfg is None:
        return AuditWriter(Path.home() / ".anteroom" / "audit", enabled=False)

    log_dir = Path(audit_cfg.log_path) if audit_cfg.log_path else config.app.data_dir / "audit"

    events = {}
    if hasattr(audit_cfg, "events"):
        events = audit_cfg.events

    return AuditWriter(
        log_dir=log_dir,
        enabled=audit_cfg.enabled,
        tamper_protection=audit_cfg.tamper_protection,
        private_key_pem=private_key_pem,
        rotation=audit_cfg.rotation,
        rotate_size_bytes=audit_cfg.rotate_size_bytes,
        retention_days=audit_cfg.retention_days,
        redact_content=audit_cfg.redact_content,
        events=events,
    )
