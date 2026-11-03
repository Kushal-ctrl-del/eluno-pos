"""Command-line entry point for the POS foundation."""

import argparse
from datetime import date
import json
import sys
from typing import List, Optional

from app.composition import build_application


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="eluno-pos")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health-check", help="Verify read-only service connectivity")
    report_parser = subparsers.add_parser(
        "day-end-report", help="Generate a read-only Shopify day-end report"
    )
    report_parser.add_argument("--store", required=True, help="Billing store code")
    report_parser.add_argument(
        "--date", required=True, dest="business_date", help="Business date (YYYY-MM-DD)"
    )
    open_parser = subparsers.add_parser(
        "till-open", help="Open a POS cash till session"
    )
    open_parser.add_argument("--store", required=True)
    open_parser.add_argument("--location-id", required=True, type=int)
    open_parser.add_argument("--staff", required=True)
    open_parser.add_argument("--float-paise", required=True, type=int)
    open_parser.add_argument("--session-id", default="")
    movement_parser = subparsers.add_parser(
        "till-movement", help="Record a POS cash till movement"
    )
    movement_parser.add_argument("--session-id", required=True)
    movement_parser.add_argument(
        "--type", required=True,
        choices=("cash_sale", "cash_refund", "paid_in", "paid_out"),
    )
    movement_parser.add_argument("--amount-paise", required=True, type=int)
    movement_parser.add_argument("--reference", required=True)
    movement_parser.add_argument("--reason", default="")
    close_parser = subparsers.add_parser(
        "till-close", help="Close a POS cash till session"
    )
    close_parser.add_argument("--session-id", required=True)
    close_parser.add_argument("--staff", required=True)
    close_parser.add_argument("--counted-paise", required=True, type=int)
    invoice_parser = subparsers.add_parser(
        "invoice", help="Render a printable Shopify invoice"
    )
    invoice_parser.add_argument("--order-id", required=True, type=int)
    args = parser.parse_args(argv)

    application = build_application(
        initialize_database=args.command not in ("health-check", "day-end-report")
    )
    if args.command == "health-check":
        results = application.health_check()
        for result in results:
            print(f"{result.service}: {'OK' if result.ok else 'FAILED'} ({result.detail})")
        return 0 if all(result.ok for result in results) else 1
    if args.command == "day-end-report":
        try:
            report = application.reporting.day_end(
                args.store, date.fromisoformat(args.business_date)
            )
        except ValueError as error:
            print(f"Invalid report request: {error}", file=sys.stderr)
            return 2
        print(json.dumps({
            "store_code": report.store_code,
            "business_date": report.business_date,
            "bill_count": report.bill_count,
            "gross_sales": _money(report.gross_sales_paise),
            "taxable_value": _money(report.taxable_value_paise),
            "cgst": _money(report.cgst_paise),
            "sgst": _money(report.sgst_paise),
            "total_tax": _money(report.total_tax_paise),
            "discounts": _money(report.discounts_paise),
            "refund_count": report.refund_count,
            "refund_amount": _money(report.refund_amount_paise),
            "payment_method_split": {
                method: _money(amount)
                for method, amount in report.payment_method_paise.items()
            },
            "sales_channel_split": {
                channel: _money(amount)
                for channel, amount in report.sales_channel_paise.items()
            },
        }, sort_keys=True))
        return 0
    if args.command == "till-open":
        try:
            result = application.till.open_session(
                store_code=args.store, location_id=args.location_id,
                opened_by=args.staff, opening_float_paise=args.float_paise,
                session_id=args.session_id,
            )
        except ValueError as error:
            print(f"Invalid till request: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "till-movement":
        try:
            result = application.till.record_movement(
                session_id=args.session_id, movement_type=args.type,
                amount_paise=args.amount_paise, reference=args.reference,
                reason=args.reason,
            )
        except ValueError as error:
            print(f"Invalid till request: {error}", file=sys.stderr)
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "till-close":
        try:
            result = application.till.close_session(
                session_id=args.session_id, closed_by=args.staff,
                counted_cash_paise=args.counted_paise,
            )
        except ValueError as error:
            print(f"Invalid till request: {error}", file=sys.stderr)
            return 2
        print(json.dumps({
            "session_id": result.session_id,
            "expected_cash": _money(result.expected_cash_paise),
            "counted_cash": _money(result.counted_cash_paise),
            "variance": _money(result.variance_paise),
            "variance_flagged": result.variance_flagged,
        }, sort_keys=True))
        return 0
    if args.command == "invoice":
        try:
            print(application.invoices.render(args.order_id), end="")
        except ValueError as error:
            print(f"Invalid invoice request: {error}", file=sys.stderr)
            return 2
        return 0
    return 2


def _money(paise: int) -> str:
    return f"{paise // 100}.{paise % 100:02d}"


if __name__ == "__main__":
    sys.exit(main())
