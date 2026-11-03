from datetime import date
import json
from pathlib import Path
import unittest

from app.domain.cart import Cart, CartLine
from app.domain.offers import OfferContext, evaluate_offers, parse_offer


ROOT = Path(__file__).parents[2]
OFFER_DATA = json.loads((ROOT / "starter-kit" / "data" / "offers.json").read_text())
OFFERS = [parse_offer(raw) for raw in OFFER_DATA]


def cart(*lines):
    return Cart(tuple(lines))


def frame(line_id="f1", price=100000, pair="1"):
    return CartLine(line_id, "frame", 1, price, pair_ref=pair)


def lens(line_id="l1", price=100000, pair="1", lens_type="progressive", index=1.6, coating="blue_cut"):
    return CartLine(
        line_id, "lens", 2, price, pair_ref=pair,
        lens_type=lens_type, lens_index=index, coating=coating,
    )


class OfferEngineTests(unittest.TestCase):
    def evaluate(self, current_cart, tags=(), role=None, reason=None, manager_bps=None, day=date(2026, 10, 6), offers=OFFERS):
        return evaluate_offers(
            current_cart,
            offers,
            as_of=day,
            context=OfferContext(tags, role, reason, manager_bps),
        )

    def test_frame_percent_offer_allocates_without_changing_price(self):
        result = self.evaluate(cart(frame()))
        festive = next(item for item in result.applied if item.offer_id == "FESTIVE10")
        self.assertEqual(festive.amount_paise, 10000)
        self.assertEqual(festive.allocations[0].amount_paise, 10000)

    def test_second_pair_discount_targets_lowest_lens_line(self):
        result = self.evaluate(cart(
            frame("f1", 100000, "1"), lens("l1", 100000, "1"),
            frame("f2", 100000, "2"), lens("l2", 60000, "2"),
        ))
        offer = next(item for item in result.applied if item.offer_id == "SECONDPAIR50")
        self.assertEqual([(a.line_id, a.amount_paise) for a in offer.allocations], [("l2", 30000)])

    def test_priority_flat_offer_requires_tag(self):
        result = self.evaluate(cart(frame()), tags=("priority",))
        self.assertEqual(next(item for item in result.applied if item.offer_id == "PRIORITY500").amount_paise, 50000)
        missing = self.evaluate(cart(frame()))
        rejection = next(item for item in missing.rejected if item.offer_id == "PRIORITY500")
        self.assertEqual(rejection.reason_code, "customer_tag_missing")

    def test_progressive_campaign_requires_tag_and_progressive_lens(self):
        campaign = [offer for offer in OFFERS if offer.offer_id == "PGX_CAMPAIGN15"]
        result = self.evaluate(cart(lens()), tags=("campaign:progressive_oct26",), offers=campaign)
        self.assertEqual(next(item for item in result.applied if item.offer_id == "PGX_CAMPAIGN15").amount_paise, 15000)
        wrong_lens = self.evaluate(
            cart(lens(lens_type="single_vision")),
            tags=("campaign:progressive_oct26",),
            offers=campaign,
        )
        self.assertEqual(next(item for item in wrong_lens.rejected if item.offer_id == "PGX_CAMPAIGN15").reason_code, "lens_type_missing")

    def test_bluecut_offer_requires_index_and_coating(self):
        result = self.evaluate(cart(lens()))
        self.assertEqual(next(item for item in result.applied if item.offer_id == "FREE_BLUECUT_PGX").amount_paise, 100000)
        wrong = self.evaluate(cart(lens(index=1.56, coating="standard")))
        self.assertEqual(next(item for item in wrong.rejected if item.offer_id == "FREE_BLUECUT_PGX").reason_code, "index_too_low")

    def test_bundle_requires_both_frame_and_lens(self):
        result = self.evaluate(cart(frame(), lens()))
        self.assertEqual(next(item for item in result.applied if item.offer_id == "BUNDLE300").amount_paise, 30000)
        rejected = self.evaluate(cart(frame()))
        self.assertEqual(next(item for item in rejected.rejected if item.offer_id == "BUNDLE300").reason_code, "frame_and_lens_required")

    def test_manager_offer_requires_role_reason_and_maximum(self):
        manager = [offer for offer in OFFERS if offer.offer_id == "MANAGER_OVERRIDE"]
        denied = self.evaluate(cart(frame()), manager_bps=500, offers=manager)
        self.assertEqual(next(item for item in denied.rejected if item.offer_id == "MANAGER_OVERRIDE").reason_code, "manager_required")
        approved = self.evaluate(
            cart(frame()), role="store_manager", reason="walkout", manager_bps=1000, offers=manager
        )
        self.assertEqual(next(item for item in approved.applied if item.offer_id == "MANAGER_OVERRIDE").amount_paise, 10000)
        too_high = self.evaluate(
            cart(frame()), role="store_manager", reason="walkout", manager_bps=1001, offers=manager
        )
        self.assertEqual(next(item for item in too_high.rejected if item.offer_id == "MANAGER_OVERRIDE").reason_code, "discount_percentage_too_high")

    def test_expired_offer_is_rejected(self):
        result = self.evaluate(cart(frame()))
        rejection = next(item for item in result.rejected if item.offer_id == "MONSOON20")
        self.assertEqual(rejection.reason_code, "outside_validity")

    def test_non_stackable_offers_are_never_combined(self):
        result = self.evaluate(cart(frame(), lens()), tags=("campaign:progressive_oct26",))
        ids = {item.offer_id for item in result.applied}
        self.assertFalse({"FESTIVE10", "PGX_CAMPAIGN15"} <= ids)

    def test_stackable_offers_can_combine_and_allocation_is_bounded(self):
        result = self.evaluate(cart(frame(), lens()), tags=("priority",))
        for applied in result.applied:
            self.assertEqual(applied.amount_paise, sum(a.amount_paise for a in applied.allocations))
        self.assertLessEqual(result.total_discount_paise, 200000)
