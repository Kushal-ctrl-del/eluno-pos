"""Customer lookup port."""

from typing import Protocol

from app.domain.customer import Customer


class CustomerGateway(Protocol):
    def find_by_phone(self, phone: str) -> Customer:
        ...
