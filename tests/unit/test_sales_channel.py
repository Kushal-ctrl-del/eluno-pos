import unittest

from app.domain.sales_channel import SUPPORTED_CHANNELS, report_channel, validate_channel


class SalesChannelTests(unittest.TestCase):
    def test_assignment_vocabulary_is_exact(self):
        self.assertEqual(
            SUPPORTED_CHANNELS,
            ("walk_in", "whatsapp_booked", "online_pickup"),
        )
        for channel in SUPPORTED_CHANNELS:
            self.assertEqual(validate_channel(channel), channel)

    def test_legacy_values_are_not_reclassified(self):
        self.assertEqual(report_channel(None), "unknown")
        self.assertEqual(report_channel("website"), "unknown")
