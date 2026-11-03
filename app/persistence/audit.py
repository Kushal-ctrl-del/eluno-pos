"""Append-only application audit events."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid

from app.persistence.database import Database


class AuditTrail:
    def __init__(self, database: Database) -> None:
        self._database = database

    def record(
        self,
        *,
        actor_id: Optional[str],
        action: str,
        entity_type: str,
        entity_ref: str,
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not action or not entity_type or not entity_ref:
            raise ValueError("Audit action, entity type and reference are required.")
        encoded = json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))
        with self._database.connection() as connection:
            connection.execute(
                """INSERT INTO audit_events
                   (event_id, actor_id, action, entity_type, entity_ref, occurred_at,
                    reason, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    actor_id,
                    action,
                    entity_type,
                    entity_ref,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    reason,
                    encoded,
                ),
            )

    def list_events(self, *, entity_type: Optional[str] = None,
                    entity_ref: Optional[str] = None) -> list:
        query = "SELECT * FROM audit_events"
        values = []
        clauses = []
        if entity_type is not None:
            clauses.append("entity_type = ?")
            values.append(entity_type)
        if entity_ref is not None:
            clauses.append("entity_ref = ?")
            values.append(entity_ref)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY rowid"
        with self._database.connection() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]
