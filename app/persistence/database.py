"""SQLite connection management and foundation schema."""

from contextlib import contextmanager
import sqlite3
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS checkout_records (
    checkout_id TEXT PRIMARY KEY,
    store_code TEXT NOT NULL,
    invoice_number TEXT,
    status TEXT NOT NULL,
    terminal_order_ref TEXT,
    terminal_charge_id TEXT,
    payment_amount_paise INTEGER,
    payment_method TEXT,
    shopify_order_id TEXT,
    payload_hash TEXT,
    last_error TEXT,
    last_reconciliation_result TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoice_sequences (
    store_code TEXT PRIMARY KEY,
    invoice_prefix TEXT NOT NULL,
    next_number INTEGER NOT NULL CHECK (next_number >= 1),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliation_state (
    reconciliation_id TEXT PRIMARY KEY,
    checkout_id TEXT,
    operation TEXT NOT NULL,
    state TEXT NOT NULL,
    details_json TEXT,
    last_error TEXT,
    next_attempt_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (checkout_id) REFERENCES checkout_records(checkout_id)
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_state ON reconciliation_state(state);

CREATE TABLE IF NOT EXISTS checkout_manual_resolutions (
    resolution_id TEXT PRIMARY KEY,
    checkout_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    resolved_at TEXT NOT NULL,
    FOREIGN KEY (checkout_id) REFERENCES checkout_records(checkout_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_checkout_manual_resolution
ON checkout_manual_resolutions(checkout_id);

CREATE TABLE IF NOT EXISTS refund_records (
    refund_request_id TEXT PRIMARY KEY,
    checkout_id TEXT,
    shopify_order_id TEXT NOT NULL,
    status TEXT NOT NULL,
    terminal_refund_id TEXT,
    shopify_refund_id TEXT,
    amount_paise INTEGER,
    charge_id TEXT,
    payload_json TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (checkout_id) REFERENCES checkout_records(checkout_id)
);

CREATE INDEX IF NOT EXISTS idx_refund_records_order ON refund_records(shopify_order_id);

CREATE TABLE IF NOT EXISTS till_sessions (
    session_id TEXT PRIMARY KEY,
    store_code TEXT NOT NULL,
    location_id INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    opened_by TEXT NOT NULL,
    opening_float_paise INTEGER NOT NULL CHECK (opening_float_paise >= 0),
    closed_at TEXT,
    closed_by TEXT,
    expected_cash_paise INTEGER,
    counted_cash_paise INTEGER,
    variance_paise INTEGER,
    variance_flagged INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_open_till_store
ON till_sessions(store_code) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS till_movements (
    movement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    movement_type TEXT NOT NULL,
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    reference TEXT NOT NULL,
    reason TEXT,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES till_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_till_movements_session
ON till_movements(session_id);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    actor_id TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_ref TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    reason TEXT,
    metadata_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_entity
ON audit_events(entity_type, entity_ref, occurred_at);
"""


class Database:
    """Owns SQLite connections and initializes the local POS schema."""

    def __init__(self, path: str) -> None:
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(checkout_records)")
            }
            if "payment_amount_paise" not in columns:
                connection.execute(
                    "ALTER TABLE checkout_records ADD COLUMN payment_amount_paise INTEGER"
                )
            if "payment_method" not in columns:
                connection.execute(
                    "ALTER TABLE checkout_records ADD COLUMN payment_method TEXT"
                )
            if "last_error" not in columns:
                connection.execute(
                    "ALTER TABLE checkout_records ADD COLUMN last_error TEXT"
                )
            if "last_reconciliation_result" not in columns:
                connection.execute(
                    "ALTER TABLE checkout_records ADD COLUMN last_reconciliation_result TEXT"
                )
