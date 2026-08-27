from dataclasses import dataclass
import pytest
from tests.support.source_contracts import project_root
from tests.support.extract_source import load_source_definitions

ROOT = project_root()
ORDER_INVENTORY = ROOT / "services" / "order_inventory.py"

@dataclass
class Result:
    modified_count: int

class FakeProducts:
    def __init__(self, docs):
        self.docs = {d["_id"]: dict(d) for d in docs}

    def _matches(self, doc, query):
        for key, expected in query.items():
            actual = doc.get(key)
            if isinstance(expected, dict) and "$gte" in expected:
                if actual is None or actual < expected["$gte"]:
                    return False
            elif actual != expected:
                return False
        return True

    def update_one(self, query, update):
        for key, doc in self.docs.items():
            if self._matches(doc, query):
                for field, delta in update.get("$inc", {}).items():
                    doc[field] = doc.get(field, 0) + delta
                for field, value in update.get("$set", {}).items():
                    doc[field] = value
                return Result(1)
        return Result(0)

    def find_one(self, query):
        for doc in self.docs.values():
            if self._matches(doc, query):
                return dict(doc)
        return None

class FakeMongo:
    def __init__(self, docs):
        self.products = FakeProducts(docs)


def _ns(docs):
    fake = FakeMongo(docs)
    ns = load_source_definitions(
        ORDER_INVENTORY,
        function_names={"_money_float", "_order_item_reserved_products", "_reserve_order_stock_items", "_release_order_stock_items"},
        namespace={"mongo":fake, "ObjectId":lambda value: value},
    )
    return ns, fake

@pytest.mark.pure
def test_product_stock_reserve_and_release_is_exact():
    ns, fake = _ns([{"_id":"p1","is_active":1,"stock_quantity":5.0}])
    item = {"product_id":"p1","quantity":2,"product_name":"Rice"}
    ok, msg = ns["_reserve_order_stock_items"]([item])
    assert ok is True and msg == ""
    assert fake.products.docs["p1"]["stock_quantity"] == 3.0
    ns["_release_order_stock_items"]([item])
    assert fake.products.docs["p1"]["stock_quantity"] == 5.0
    assert fake.products.docs["p1"]["is_active"] == 1

@pytest.mark.pure
def test_partial_reservation_rolls_back_if_next_product_is_short():
    ns, fake = _ns([
        {"_id":"p1","is_active":1,"stock_quantity":5.0},
        {"_id":"p2","is_active":1,"stock_quantity":1.0},
    ])
    items = [
        {"product_id":"p1","quantity":2,"product_name":"Rice"},
        {"product_id":"p2","quantity":2,"product_name":"Oil"},
    ]
    ok, msg = ns["_reserve_order_stock_items"](items)
    assert ok is False
    assert "out of stock" in msg.lower()
    assert fake.products.docs["p1"]["stock_quantity"] == 5.0
    assert fake.products.docs["p2"]["stock_quantity"] == 1.0

@pytest.mark.pure
def test_bundle_reserves_each_child_times_bundle_quantity():
    ns, fake = _ns([
        {"_id":"p1","is_active":1,"stock_quantity":10.0},
        {"_id":"p2","is_active":1,"stock_quantity":20.0},
    ])
    bundle = {
        "item_type":"bundle", "bundle_id":"b1", "quantity":2,
        "bundle_items_snapshot":[
            {"product_id":"p1","quantity":1,"product_name_snapshot":"Rice"},
            {"product_id":"p2","quantity":3,"product_name_snapshot":"Oil"},
        ]
    }
    ok, _ = ns["_reserve_order_stock_items"]([bundle])
    assert ok is True
    assert fake.products.docs["p1"]["stock_quantity"] == 8.0
    assert fake.products.docs["p2"]["stock_quantity"] == 14.0
