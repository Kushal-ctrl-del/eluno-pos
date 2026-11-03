"""Composition root for wiring infrastructure and application services."""

from dataclasses import dataclass
from typing import List, Optional

from app.adapters.http_client import JsonHttpClient
from app.adapters.service_health import HealthResult, check_services
from app.adapters.shopify_client import ShopifyClient
from app.adapters.shopify_orders import ShopifyOrderClient
from app.adapters.smt_client import SmtClient
from app.adapters.terminal_client import TerminalClient
from app.application.checkout import CheckoutService
from app.application.refunds import RefundService
from app.application.reporting import ReportingService
from app.application.till import TillService
from app.application.invoices import InvoiceService
from app.application.customer_prescription import CustomerPrescriptionService
from app.config import Settings
from app.persistence.database import Database
from app.persistence.audit import AuditTrail


@dataclass(frozen=True)
class Application:
    settings: Settings
    http_client: JsonHttpClient
    database: Database
    customer_prescription: CustomerPrescriptionService
    checkout: CheckoutService
    refunds: RefundService
    reporting: ReportingService
    till: TillService
    invoices: InvoiceService
    product_catalog: ShopifyClient
    shopify_orders: ShopifyOrderClient
    audit: AuditTrail

    def health_check(self) -> List[HealthResult]:
        return check_services(self.settings, self.http_client)


def build_application(settings: Optional[Settings] = None, *, initialize_database: bool = True) -> Application:
    resolved = settings or Settings.from_env()
    database = Database(resolved.database_path)
    if initialize_database:
        database.initialize()
    http_client = JsonHttpClient(resolved.http_timeout_seconds)
    audit = AuditTrail(database)
    shopify = ShopifyClient(resolved, http_client)
    shopify_orders = ShopifyOrderClient(resolved, http_client)
    smt = SmtClient(resolved, http_client)
    terminal = TerminalClient(resolved, http_client)
    return Application(
        settings=resolved,
        http_client=http_client,
        database=database,
        customer_prescription=CustomerPrescriptionService(shopify, smt, shopify),
        checkout=CheckoutService(database, terminal, shopify_orders, audit),
        refunds=RefundService(database, terminal, shopify_orders, audit),
        reporting=ReportingService(shopify_orders),
        till=TillService(database, audit),
        invoices=InvoiceService(shopify_orders),
        product_catalog=shopify,
        shopify_orders=shopify_orders,
        audit=audit,
    )
