import json
import tempfile
import unittest
from pathlib import Path

from app.application.till import TillService
from app.persistence.audit import AuditTrail
from app.persistence.database import Database


class AuditTrailTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Database(str(Path(self.directory.name) / "pos.sqlite3"))
        self.database.initialize()

    def tearDown(self):
        self.directory.cleanup()

    def test_events_are_append_only_and_structured(self):
        audit = AuditTrail(self.database)
        audit.record(
            actor_id="S120", action="test_action", entity_type="checkout",
            entity_ref="CHK-1", reason="testing",
            metadata={"amount_paise": 1250, "payment_method": "upi"},
        )
        audit.record(
            actor_id="S121", action="test_action_2", entity_type="checkout",
            entity_ref="CHK-1", metadata={"status": "completed"},
        )
        events = audit.list_events(entity_type="checkout", entity_ref="CHK-1")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["actor_id"], "S120")
        self.assertEqual(events[0]["reason"], "testing")
        self.assertEqual(json.loads(events[0]["metadata_json"])["amount_paise"], 1250)
        self.assertFalse(hasattr(audit, "update"))
        self.assertFalse(hasattr(audit, "delete"))

    def test_till_operations_write_events_after_successful_operations(self):
        service = TillService(self.database)
        service.open_session(
            store_code="JN", location_id=70000005, opened_by="S120",
            opening_float_paise=300000, session_id="TILL-AUDIT",
        )
        service.record_movement(
            session_id="TILL-AUDIT", movement_type="paid_out",
            amount_paise=1200, reference="PO-1", reason="courier",
        )
        service.close_session(
            session_id="TILL-AUDIT", closed_by="S121", counted_cash_paise=298800,
        )
        events = AuditTrail(self.database).list_events(
            entity_type="till_session", entity_ref="TILL-AUDIT"
        )
        self.assertEqual(
            [event["action"] for event in events],
            ["till_opened", "till_movement_recorded", "till_closed"],
        )
        self.assertEqual(events[1]["actor_id"], "S120")
        self.assertEqual(json.loads(events[1]["metadata_json"])["reference"], "PO-1")
