"""Application orchestration for read-only Shopify day-end reports."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from app.adapters.shopify_orders import ShopifyOrderClient
from app.domain.reporting import DayEndReport, calculate_day_end_report


class ReportingService:
    def __init__(self, shopify: ShopifyOrderClient) -> None:
        self._shopify = shopify

    def day_end(self, store_code: str, business_date: date) -> DayEndReport:
        ist = timezone(timedelta(hours=5, minutes=30))
        start = datetime.combine(business_date, time.min, tzinfo=ist)
        end = datetime.combine(business_date + timedelta(days=1), time.min, tzinfo=ist)
        orders = self._shopify.list_orders(
            created_at_min=start.isoformat(),
            created_at_max=end.isoformat(),
            limit=250,
        )
        return calculate_day_end_report(orders, store_code, business_date.isoformat())
