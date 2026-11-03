"""Supported sales-channel vocabulary."""

from typing import Optional, Tuple


SUPPORTED_CHANNELS: Tuple[str, ...] = (
    "walk_in",
    "whatsapp_booked",
    "online_pickup",
)
UNKNOWN_CHANNEL = "unknown"


def validate_channel(value: str) -> str:
    if value not in SUPPORTED_CHANNELS:
        raise ValueError(
            "Sales channel must be one of: " + ", ".join(SUPPORTED_CHANNELS) + "."
        )
    return value


def report_channel(value: Optional[str]) -> str:
    """Keep legacy/missing values visible without inventing attribution."""
    return value if value in SUPPORTED_CHANNELS else UNKNOWN_CHANNEL
