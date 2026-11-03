"""Catalog lookup port."""

from typing import Protocol

from app.domain.prescription import LensVariant


class CatalogGateway(Protocol):
    def get_lens_variant(self, variant_id: int) -> LensVariant:
        ...
