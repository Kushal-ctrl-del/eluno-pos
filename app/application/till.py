"""Application service for POS-owned till sessions and cash movements."""

from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List

from app.domain.till import TillClose, TillError, TillMovement, close_result
from app.persistence.database import Database
from app.persistence.audit import AuditTrail


class TillService:
    def __init__(self, database: Database, audit: AuditTrail = None) -> None:
        self._database = database
        self._audit = audit or AuditTrail(database)

    def open_session(
        self,
        *,
        store_code: str,
        location_id: int,
        opened_by: str,
        opening_float_paise: int,
        session_id: str = "",
    ) -> Dict[str, Any]:
        if not store_code or not opened_by:
            raise TillError("session_fields_required", "Store and opener are required.")
        if opening_float_paise < 0:
            raise TillError("opening_float_invalid", "Opening float cannot be negative.")
        resolved_id = session_id or "till-" + uuid.uuid4().hex
        now = _now()
        try:
            with self._database.connection() as connection:
                connection.execute(
                    """INSERT INTO till_sessions
                    (session_id, store_code, location_id, opened_at, opened_by,
                     opening_float_paise)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (resolved_id, store_code, location_id, now, opened_by,
                     opening_float_paise),
                )
        except Exception as error:
            if "idx_open_till_store" in str(error) or "UNIQUE constraint" in str(error):
                raise TillError(
                    "open_session_exists",
                    "This store already has an open till session.",
                ) from error
            raise
        result = self.get_session(resolved_id)
        self._audit.record(
            actor_id=opened_by, action="till_opened", entity_type="till_session",
            entity_ref=resolved_id,
            metadata={"store_code": store_code, "location_id": location_id,
                      "opening_float_paise": opening_float_paise},
        )
        return result

    def record_movement(
        self,
        *,
        session_id: str,
        movement_type: str,
        amount_paise: int,
        reference: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        movement = TillMovement(
            movement_type, amount_paise, reference, reason or None
        )
        now = _now()
        with self._database.connection() as connection:
            session = connection.execute(
                "SELECT closed_at, opened_by FROM till_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise TillError("session_missing", "Till session does not exist.")
            if session["closed_at"] is not None:
                raise TillError("session_closed", "Till session is already closed.")
            connection.execute(
                """INSERT INTO till_movements
                (session_id, movement_type, amount_paise, reference, reason, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, movement.movement_type, movement.amount_paise,
                 movement.reference, movement.reason, now),
            )
        result = self.get_session(session_id)
        self._audit.record(
            actor_id=session["opened_by"], action="till_movement_recorded", entity_type="till_session",
            entity_ref=session_id, reason=movement.reason,
            metadata={"movement_type": movement.movement_type,
                      "amount_paise": movement.amount_paise,
                      "reference": movement.reference},
        )
        return result

    def close_session(
        self, *, session_id: str, closed_by: str, counted_cash_paise: int
    ) -> TillClose:
        if not closed_by:
            raise TillError("closer_required", "Closer is required.")
        with self._database.connection() as connection:
            session = connection.execute(
                "SELECT * FROM till_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise TillError("session_missing", "Till session does not exist.")
            if session["closed_at"] is not None:
                raise TillError("session_closed", "Till session is already closed.")
            rows = connection.execute(
                """SELECT movement_type, amount_paise, reference, reason
                   FROM till_movements WHERE session_id = ? ORDER BY movement_id""",
                (session_id,),
            ).fetchall()
            movements = [
                TillMovement(row["movement_type"], row["amount_paise"],
                             row["reference"], row["reason"])
                for row in rows
            ]
            result = close_result(
                session_id, session["opening_float_paise"], movements,
                counted_cash_paise,
            )
            connection.execute(
                """UPDATE till_sessions
                   SET closed_at = ?, closed_by = ?, expected_cash_paise = ?,
                       counted_cash_paise = ?, variance_paise = ?,
                       variance_flagged = ?
                   WHERE session_id = ?""",
                (_now(), closed_by, result.expected_cash_paise,
                 result.counted_cash_paise, result.variance_paise,
                 int(result.variance_flagged), session_id),
            )
        self._audit.record(
            actor_id=closed_by, action="till_closed", entity_type="till_session",
            entity_ref=session_id,
            metadata={"expected_cash_paise": result.expected_cash_paise,
                      "counted_cash_paise": result.counted_cash_paise,
                      "variance_paise": result.variance_paise,
                      "variance_flagged": result.variance_flagged},
        )
        return result

    def get_session(self, session_id: str) -> Dict[str, Any]:
        with self._database.connection() as connection:
            session = connection.execute(
                "SELECT * FROM till_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise TillError("session_missing", "Till session does not exist.")
            movements = connection.execute(
                """SELECT movement_type, amount_paise, reference, reason, occurred_at
                   FROM till_movements WHERE session_id = ? ORDER BY movement_id""",
                (session_id,),
            ).fetchall()
        return {
            "session_id": session["session_id"],
            "store_code": session["store_code"],
            "location_id": session["location_id"],
            "opened_at": session["opened_at"],
            "opened_by": session["opened_by"],
            "opening_float_paise": session["opening_float_paise"],
            "closed_at": session["closed_at"],
            "closed_by": session["closed_by"],
            "expected_cash_paise": session["expected_cash_paise"],
            "counted_cash_paise": session["counted_cash_paise"],
            "variance_paise": session["variance_paise"],
            "variance_flagged": bool(session["variance_flagged"]),
            "movements": [dict(row) for row in movements],
        }


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
