"""Data-driven offer evaluation using integer-paise calculations."""

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.domain.cart import Cart, CartLine


@dataclass(frozen=True)
class Offer:
    offer_id: str
    name: str
    active: bool
    valid_from: date
    valid_to: date
    stackable: bool
    priority: int
    rule: dict


@dataclass(frozen=True)
class OfferContext:
    customer_tags: Tuple[str, ...] = ()
    staff_role: Optional[str] = None
    reason_code: Optional[str] = None
    manager_discount_bps: Optional[int] = None


@dataclass(frozen=True)
class OfferRejection:
    offer_id: str
    reason_code: str
    message: str


@dataclass(frozen=True)
class DiscountAllocation:
    offer_id: str
    line_id: str
    amount_paise: int


@dataclass(frozen=True)
class AppliedOffer:
    offer_id: str
    amount_paise: int
    allocations: Tuple[DiscountAllocation, ...]


@dataclass(frozen=True)
class OfferEvaluation:
    applied: Tuple[AppliedOffer, ...]
    rejected: Tuple[OfferRejection, ...]
    total_discount_paise: int
    manager_reason_code: Optional[str] = None


def parse_offer(raw: dict) -> Offer:
    try:
        return Offer(
            offer_id=str(raw["offer_id"]),
            name=str(raw["name"]),
            active=bool(raw["active"]),
            valid_from=date.fromisoformat(str(raw["valid_from"])),
            valid_to=date.fromisoformat(str(raw["valid_to"])),
            stackable=bool(raw["stackable"]),
            priority=int(raw["priority"]),
            rule=dict(raw["rule"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Malformed offer definition.") from error


def evaluate_offers(
    cart: Cart,
    offers: Iterable[Offer],
    *,
    as_of: date,
    context: OfferContext = OfferContext(),
) -> OfferEvaluation:
    valid: List[Tuple[Offer, Tuple[str, ...]]] = []
    rejected: List[OfferRejection] = []
    for offer in sorted(offers, key=lambda item: (item.priority, item.offer_id)):
        reason = _eligibility_reason(offer, cart, as_of, context)
        if reason is not None:
            rejected.append(OfferRejection(offer.offer_id, reason[0], reason[1]))
            continue
        eligible = _eligible_line_ids(offer, cart)
        if not eligible:
            rejected.append(OfferRejection(
                offer.offer_id,
                "no_eligible_lines",
                "Offer has no eligible cart lines.",
            ))
            continue
        valid.append((offer, eligible))

    scenarios = _scenarios(valid)
    best_applied: Tuple[AppliedOffer, ...] = ()
    best_total = 0
    best_key: Optional[Tuple[int, Tuple[str, ...]]] = None
    for scenario in scenarios:
        applied = _apply_scenario(cart, scenario, context)
        total = sum(item.amount_paise for item in applied)
        key = (total, tuple(item.offer_id for item in applied))
        if best_key is None or key[0] > best_key[0] or (key[0] == best_key[0] and key[1] < best_key[1]):
            best_applied, best_total, best_key = applied, total, key

    selected_ids = {item.offer_id for item in best_applied}
    for offer, _ in valid:
        if offer.offer_id not in selected_ids:
            rejected.append(OfferRejection(
                offer.offer_id,
                "stacking_conflict",
                "Offer was not selected because the chosen combination gives a better valid discount.",
            ))
    return OfferEvaluation(
        best_applied, tuple(rejected), best_total, context.reason_code
    )


def _eligibility_reason(
    offer: Offer,
    cart: Cart,
    as_of: date,
    context: OfferContext,
) -> Optional[Tuple[str, str]]:
    if not offer.active or as_of < offer.valid_from or as_of > offer.valid_to:
        return "outside_validity", "Offer is inactive or outside its validity dates."
    rule = offer.rule
    condition = rule.get("condition", {})
    if "customer_tag" in condition and condition["customer_tag"] not in context.customer_tags:
        return "customer_tag_missing", "Customer does not have the required tag."
    if "min_pairs_in_cart" in condition and cart.pair_count < int(condition["min_pairs_in_cart"]):
        return "pair_count_too_low", "Cart does not contain enough pairs."
    if condition.get("has_frame_and_lens") and not _has_frame_and_lens(cart):
        return "frame_and_lens_required", "Cart must contain a frame and a lens."
    matching_lenses = [line for line in cart.lines if line.line_type == "lens"]
    if "lens_type" in condition and not any(line.lens_type == condition["lens_type"] for line in matching_lenses):
        return "lens_type_missing", "Cart has no lens of the required type."
    if "index_min" in condition and not any(
        line.lens_index is not None and line.lens_index >= float(condition["index_min"])
        for line in matching_lenses
    ):
        return "index_too_low", "Cart has no lens meeting the required index."
    if "coating" in condition and not any(line.coating == condition["coating"] for line in matching_lenses):
        return "coating_missing", "Cart has no lens with the required coating."
    requires = rule.get("requires", {})
    if requires.get("role") == "store_manager" and context.staff_role != "store_manager":
        return "manager_required", "A store manager must apply this offer."
    if requires.get("reason_code") and not context.reason_code:
        return "reason_code_required", "A reason code is required for this offer."
    discount = rule.get("discount", {})
    if "value_bps_max" in discount:
        bps = context.manager_discount_bps
        if bps is None:
            return "discount_percentage_required", "A manager discount percentage is required."
        if bps < 0 or bps > int(discount["value_bps_max"]):
            return "discount_percentage_too_high", "Manager discount exceeds the allowed maximum."
    return None


def _eligible_line_ids(offer: Offer, cart: Cart) -> Tuple[str, ...]:
    scope = offer.rule.get("applies_to")
    if scope == "frame":
        return tuple(line.line_id for line in cart.lines if line.line_type == "frame")
    if scope == "lens":
        lines = [line for line in cart.lines if line.line_type == "lens"]
        if offer.rule.get("condition", {}).get("lens_type"):
            required = offer.rule["condition"]["lens_type"]
            lines = [line for line in lines if line.lens_type == required]
        if offer.rule.get("discount", {}).get("on") == "lowest_priced_lens_line":
            lines = sorted(lines, key=lambda line: (line.gross_paise, line.line_id))[:1]
        return tuple(line.line_id for line in lines)
    if scope == "pair":
        return tuple(line.line_id for line in cart.lines if line.pair_ref is not None)
    if scope == "cart":
        return tuple(line.line_id for line in cart.lines)
    return ()


def _has_frame_and_lens(cart: Cart) -> bool:
    return any(line.line_type == "frame" for line in cart.lines) and any(
        line.line_type == "lens" for line in cart.lines
    )


def _scenarios(valid: List[Tuple[Offer, Tuple[str, ...]]]) -> List[Tuple[Tuple[Offer, Tuple[str, ...]], ...]]:
    stackable = tuple(item for item in valid if item[0].stackable)
    scenarios: List[Tuple[Tuple[Offer, Tuple[str, ...]], ...]] = []
    if stackable:
        scenarios.append(stackable)
    scenarios.extend((item,) for item in valid if not item[0].stackable)
    if not scenarios:
        scenarios.append(())
    return scenarios


def _apply_scenario(
    cart: Cart,
    scenario: Tuple[Tuple[Offer, Tuple[str, ...]], ...],
    context: OfferContext,
) -> Tuple[AppliedOffer, ...]:
    remaining: Dict[str, int] = {line.line_id: line.gross_paise for line in cart.lines}
    result: List[AppliedOffer] = []
    for offer, eligible_ids in sorted(scenario, key=lambda item: (item[0].priority, item[0].offer_id)):
        discount = offer.rule.get("discount", {})
        if discount.get("type") == "percent":
            bps = int(
                context.manager_discount_bps
                if "value_bps_max" in discount
                else discount.get("value_bps", 0)
            )
            amount_by_line = {
                line_id: remaining[line_id] * bps // 10000
                for line_id in eligible_ids
            }
            max_discount = discount.get("max_discount_paise")
            if max_discount is not None:
                amount_by_line = _cap_allocations(amount_by_line, int(max_discount))
        elif discount.get("type") == "flat":
            amount_by_line = _allocate_flat(remaining, eligible_ids, int(discount["value_paise"]))
        else:
            continue
        allocations = tuple(
            DiscountAllocation(offer.offer_id, line_id, amount)
            for line_id, amount in amount_by_line.items()
            if amount > 0
        )
        amount = sum(item.amount_paise for item in allocations)
        for item in allocations:
            remaining[item.line_id] -= item.amount_paise
        result.append(AppliedOffer(offer.offer_id, amount, allocations))
    return tuple(result)


def _allocate_flat(remaining: Dict[str, int], eligible_ids: Tuple[str, ...], amount: int) -> Dict[str, int]:
    allocations: Dict[str, int] = {}
    left = max(0, amount)
    for line_id in eligible_ids:
        applied = min(left, remaining[line_id])
        allocations[line_id] = applied
        left -= applied
        if left == 0:
            break
    return allocations


def _cap_allocations(amounts: Dict[str, int], cap: int) -> Dict[str, int]:
    result: Dict[str, int] = {}
    left = max(0, cap)
    for line_id, amount in amounts.items():
        applied = min(amount, left)
        result[line_id] = applied
        left -= applied
        if left == 0:
            break
    return result
