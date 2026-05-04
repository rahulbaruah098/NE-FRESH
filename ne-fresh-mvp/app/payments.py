
from abc import ABC, abstractmethod

class PaymentProvider(ABC):
    @abstractmethod
    def create_payment(self, order_id: int, amount: int, method: str) -> dict: ...
    @abstractmethod
    def confirm_payment(self, reference: str) -> dict: ...

class MockPaymentProvider(PaymentProvider):
    def __init__(self, sandbox=True): self.sandbox = sandbox
    def create_payment(self, order_id, amount, method):
        return {"reference": f"MOCK-{order_id}", "status": "CREATED", "provider": "MOCK"}
    def confirm_payment(self, reference):
        return {"reference": reference, "status": "PAID"}
