import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.persistence.database import Database


class DatabaseTests(unittest.TestCase):
    def test_initialize_creates_foundation_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "pos.sqlite3")
            database = Database(path)
            database.initialize()
            with database.connection() as connection:
                names = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
        self.assertTrue(
            {
                "checkout_records",
                "invoice_sequences",
                "reconciliation_state",
                "till_sessions",
                "till_movements",
            }.issubset(names)
        )

    def test_connection_rolls_back_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "pos.sqlite3"))
            with database.connection() as connection:
                connection.execute(
                    "CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                with database.connection() as connection:
                    connection.execute("INSERT INTO sample (id, value) VALUES (1, NULL)")
            with database.connection() as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM sample"
                    ).fetchone()["count"],
                    0,
                )
