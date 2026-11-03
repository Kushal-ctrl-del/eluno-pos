import tempfile
import unittest
from pathlib import Path

from app.application.till import TillService
from app.domain.till import TillError
from app.persistence.database import Database


class TillTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Database(str(Path(self.directory.name) / "pos.sqlite3"))
        self.database.initialize()
        self.service = TillService(self.database)

    def tearDown(self):
        self.directory.cleanup()

    def test_open_records_float_and_prevents_second_open_session(self):
        session = self.service.open_session(
            store_code="JN", location_id=70000005, opened_by="S120",
            opening_float_paise=300000, session_id="TILL-1",
        )
        self.assertEqual(session["opening_float_paise"], 300000)
        with self.assertRaisesRegex(TillError, "already has an open"):
            self.service.open_session(
                store_code="JN", location_id=70000005, opened_by="S121",
                opening_float_paise=100000, session_id="TILL-2",
            )

    def test_movements_calculate_expected_cash_and_paid_out_requires_reason(self):
        self.service.open_session(
            store_code="JN", location_id=70000005, opened_by="S120",
            opening_float_paise=300000, session_id="TILL-1",
        )
        self.service.record_movement(
            session_id="TILL-1", movement_type="cash_sale",
            amount_paise=899800, reference="ELU/JN/00001",
        )
        self.service.record_movement(
            session_id="TILL-1", movement_type="paid_out",
            amount_paise=12000, reference="PO-1", reason="courier",
        )
        with self.assertRaisesRegex(TillError, "reason"):
            self.service.record_movement(
                session_id="TILL-1", movement_type="paid_out",
                amount_paise=8000, reference="PO-2",
            )
        session = self.service.get_session("TILL-1")
        self.assertEqual(
            300000 + 899800 - 12000,
            1187800,
        )
        self.assertEqual(len(session["movements"]), 2)

    def test_close_reports_and_flags_variance_over_two_hundred_rupees(self):
        self.service.open_session(
            store_code="JN", location_id=70000005, opened_by="S120",
            opening_float_paise=300000, session_id="TILL-1",
        )
        self.service.record_movement(
            session_id="TILL-1", movement_type="cash_sale",
            amount_paise=899800, reference="ELU/JN/00001",
        )
        result = self.service.close_session(
            session_id="TILL-1", closed_by="S120", counted_cash_paise=1179800,
        )
        self.assertEqual(result.expected_cash_paise, 1199800)
        self.assertEqual(result.variance_paise, -20000)
        self.assertFalse(result.variance_flagged)
        with self.assertRaisesRegex(TillError, "already closed"):
            self.service.close_session(
                session_id="TILL-1", closed_by="S120", counted_cash_paise=0,
            )

    def test_variance_over_two_hundred_rupees_is_flagged(self):
        self.service.open_session(
            store_code="VC", location_id=70000007, opened_by="S128",
            opening_float_paise=200000, session_id="TILL-2",
        )
        result = self.service.close_session(
            session_id="TILL-2", closed_by="S128", counted_cash_paise=179999,
        )
        self.assertEqual(result.variance_paise, -20001)
        self.assertTrue(result.variance_flagged)

    def test_cash_refund_and_paid_in_require_reasons(self):
        self.service.open_session(
            store_code="JN", location_id=70000005, opened_by="S120",
            opening_float_paise=300000, session_id="TILL-1",
        )
        with self.assertRaisesRegex(TillError, "reason"):
            self.service.record_movement(
                session_id="TILL-1", movement_type="cash_refund",
                amount_paise=1000, reference="RF-1",
            )
        self.service.record_movement(
            session_id="TILL-1", movement_type="paid_in",
            amount_paise=1000, reference="PI-1", reason="change from HQ",
        )
        self.service.record_movement(
            session_id="TILL-1", movement_type="cash_refund",
            amount_paise=500, reference="RF-1", reason="customer refund",
        )
        self.assertEqual(
            self.service.close_session(
                session_id="TILL-1", closed_by="S120", counted_cash_paise=300500,
            ).expected_cash_paise,
            300500,
        )
