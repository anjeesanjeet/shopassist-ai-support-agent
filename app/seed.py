"""Builds the demo store database.

Contains two kinds of data:
  * FIXTURE orders (ORD-1001..ORD-1008) with known states, used by the evaluation scenarios.
  * Synthetic filler customers/orders (ORD-2001+) so the store looks realistic.

All data is synthetic. Dates are relative to "today" so return windows always behave the same way.
Usage:  python -m app.seed            (rebuilds data/shop.db)
"""
from __future__ import annotations

import argparse
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from app.config import get_settings
from app.db import connect, init_schema

PRODUCTS = [
    # sku, name, category, price, final_sale
    ("SKU-BLND-01", "ProBlend 900 Blender", "Kitchen appliances", 89.00, 0),
    ("SKU-PAN-03", "Cast Iron Skillet 12in", "Cookware", 54.00, 0),
    ("SKU-ESP-02", "Barista Pro Espresso Machine", "Kitchen appliances", 640.00, 0),
    ("SKU-THR-05", "Handwoven Cotton Throw (Clearance)", "Textiles", 42.00, 1),
    ("SKU-KNF-07", "Damascus Chef Knife 8in", "Cutlery", 120.00, 0),
    ("SKU-MUG-11", "Stoneware Mug Set of 4", "Tableware", 36.00, 0),
    ("SKU-AIR-04", "AirCrisp Air Fryer 5L", "Kitchen appliances", 149.00, 0),
    ("SKU-LMP-09", "Brass Table Lamp", "Decor", 95.00, 0),
    ("SKU-RUG-12", "Jute Area Rug 5x7", "Decor", 180.00, 0),
    ("SKU-KTL-06", "Electric Gooseneck Kettle", "Kitchen appliances", 79.00, 0),
    ("SKU-CTB-08", "Acacia Cutting Board", "Cookware", 45.00, 0),
    ("SKU-TWL-10", "Turkish Towel Set (Clearance)", "Textiles", 38.00, 1),
    ("SKU-DUT-13", "Enamel Dutch Oven 5qt", "Cookware", 210.00, 0),
    ("SKU-PLT-14", "Ceramic Dinner Plates (6)", "Tableware", 72.00, 0),
    ("SKU-CND-15", "Soy Candle Trio", "Decor", 28.00, 0),
    ("SKU-MXR-16", "Stand Mixer 6qt", "Kitchen appliances", 389.00, 0),
]

FIXTURE_CUSTOMERS = [
    ("C001", "Priya Sharma", "priya.sharma@example.com", "+1-415-555-0101", "San Francisco"),
    ("C002", "Rahul Verma", "rahul.verma@example.com", "+1-212-555-0102", "New York"),
    ("C003", "Ananya Iyer", "ananya.iyer@example.com", "+1-512-555-0103", "Austin"),
    ("C004", "Michael Chen", "michael.chen@example.com", "+1-206-555-0104", "Seattle"),
    ("C005", "Sofia Martinez", "sofia.martinez@example.com", "+1-305-555-0105", "Miami"),
    ("C006", "Arjun Mehta", "arjun.mehta@example.com", "+1-312-555-0106", "Chicago"),
]

# order_id, customer, status, order_days_ago, ship_days_ago, delivery_days_ago, items[(sku, qty)], tracking profile
FIXTURE_ORDERS = [
    ("ORD-1001", "C001", "delivered", 13, 11, 8, [("SKU-BLND-01", 1)], "delivered"),
    ("ORD-1002", "C002", "delivered", 58, 56, 52, [("SKU-PAN-03", 1)], "delivered"),
    ("ORD-1003", "C003", "processing", 1, None, None, [("SKU-LMP-09", 1)], None),
    ("ORD-1004", "C004", "delivered", 11, 9, 6, [("SKU-ESP-02", 1)], "delivered"),
    ("ORD-1005", "C005", "delivered", 9, 7, 4, [("SKU-THR-05", 1)], "delivered"),
    ("ORD-1006", "C006", "shipped", 4, 2, None, [("SKU-AIR-04", 1)], "in_transit"),
    ("ORD-1007", "C001", "delivered", 17, 15, 12, [("SKU-KNF-07", 1), ("SKU-MUG-11", 1)], "delivered"),
    ("ORD-1008", "C002", "shipped", 9, 7, None, [("SKU-RUG-12", 1)], "delayed"),
]

FIRST = ["Aarav", "Diya", "Kabir", "Meera", "Liam", "Emma", "Noah", "Olivia", "Ishaan", "Zara", "Ethan", "Ava",
         "Vihaan", "Sara", "Lucas", "Mia", "Reyansh", "Anika", "James", "Chloe"]
LAST = ["Kapoor", "Nair", "Reddy", "Singh", "Smith", "Johnson", "Brown", "Garcia", "Patel", "Das", "Wilson",
        "Lopez", "Bose", "Khan", "Taylor", "Moore"]
CITIES = ["Boston", "Denver", "Portland", "Atlanta", "Phoenix", "San Diego", "Dallas", "Minneapolis"]
CARRIERS = ["UPS", "FedEx", "USPS"]
HUBS = ["Reno, NV", "Memphis, TN", "Louisville, KY", "Indianapolis, IN", "Ontario, CA", "Dallas, TX"]


def _d(days_ago: int | None, today: date) -> str | None:
    return None if days_ago is None else (today - timedelta(days=days_ago)).isoformat()


def _ts(day_iso: str, hour: int) -> str:
    return datetime.fromisoformat(day_iso).replace(hour=hour).isoformat()


def _tracking(order_id: str, ship_iso: str | None, delivery_iso: str | None, profile: str | None,
              city: str, rng: random.Random) -> list[tuple]:
    if not ship_iso or not profile:
        return []
    hub1, hub2 = rng.sample(HUBS, 2)
    events = [(order_id, _ts(ship_iso, 9), "Loom & Ladle Warehouse, Reno, NV", "Picked up by carrier"),
              (order_id, _ts(ship_iso, 21), hub1, "Departed facility")]
    ship_day = date.fromisoformat(ship_iso)
    if profile in ("in_transit", "delivered", "delayed"):
        events.append((order_id, _ts((ship_day + timedelta(days=1)).isoformat(), 14), hub2, "In transit"))
    if profile == "delayed":
        events.append((order_id, _ts((ship_day + timedelta(days=3)).isoformat(), 8), hub2,
                       "Delayed: severe weather at carrier facility. New delivery estimate pending"))
    if profile == "delivered" and delivery_iso:
        events.append((order_id, _ts(delivery_iso, 10), city, "Out for delivery"))
        events.append((order_id, _ts(delivery_iso, 16), city, "Delivered, left at front door"))
    return events


def build_database(db_path: Path | str, seed: int = 42, today: date | None = None) -> Path:
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    today = today or date.today()
    rng = random.Random(seed)
    conn = connect(db_path)
    init_schema(conn)

    conn.executemany("INSERT INTO products VALUES (?,?,?,?,?)", PRODUCTS)
    prices = {p[0]: p[3] for p in PRODUCTS}

    customers = list(FIXTURE_CUSTOMERS)
    for i in range(30):
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        customers.append((f"C{100 + i}", f"{fn} {ln}", f"{fn.lower()}.{ln.lower()}{i}@example.com",
                          f"+1-555-555-{1000 + i}", rng.choice(CITIES)))
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)
    city_of = {c[0]: c[4] for c in customers}

    def insert_order(order_id, cust, status, od, sd, dd, items, profile):
        order_iso, ship_iso, deliv_iso = _d(od, today), _d(sd, today), _d(dd, today)
        carrier = rng.choice(CARRIERS) if ship_iso else None
        tracking = f"{carrier[:2].upper()}{rng.randint(10**9, 10**10 - 1)}" if carrier else None
        total = round(sum(prices[s] * q for s, q in items), 2)
        conn.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
                     (order_id, cust, status, order_iso, ship_iso, deliv_iso, carrier, tracking, total))
        conn.executemany("INSERT INTO order_items (order_id, sku, qty, unit_price) VALUES (?,?,?,?)",
                         [(order_id, s, q, prices[s]) for s, q in items])
        conn.executemany("INSERT INTO tracking_events (order_id, ts, location, status) VALUES (?,?,?,?)",
                         _tracking(order_id, ship_iso, deliv_iso, profile, city_of[cust], rng))

    for row in FIXTURE_ORDERS:
        insert_order(*row)

    filler_customers = [c[0] for c in customers[len(FIXTURE_CUSTOMERS):]]
    skus = [p[0] for p in PRODUCTS]
    for n in range(90):
        od = rng.randint(1, 120)
        if od <= 2:
            status, sd, dd, profile = "processing", None, None, None
        elif od <= 6:
            status, sd, dd, profile = "shipped", od - 1, None, "in_transit"
        else:
            status, sd, dd, profile = "delivered", od - 2, od - 5 if od > 7 else od - 3, "delivered"
        items = [(s, rng.choice([1, 1, 1, 2])) for s in rng.sample(skus, rng.choice([1, 1, 2, 3]))]
        insert_order(f"ORD-{2001 + n}", rng.choice(filler_customers), status, od, sd, dd, items, profile)

    conn.commit()
    conn.close()
    return db_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the demo store database.")
    parser.add_argument("--db", default=None, help="Path to the SQLite file (default: DB_PATH from .env)")
    args = parser.parse_args()
    path = build_database(args.db or get_settings().db_path)
    print(f"Demo store database created at {path}")


if __name__ == "__main__":
    main()
