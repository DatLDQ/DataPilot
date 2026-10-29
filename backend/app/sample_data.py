"""Deterministic synthetic e-commerce dataset (Vietnam, 2024-2025) for demos and tests.

It contains patterns worth discovering: Tet and 11.11 / 12.12 sales spikes, a Mobile App
channel that keeps gaining share, deep discounts that turn some orders unprofitable, slower
delivery outside the two big cities (with lower ratings), and missing ratings.
"""

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

SAMPLE_FILENAME = "ecommerce_sales.csv"

CITIES = {"TP.HCM": 0.35, "Hà Nội": 0.30, "Đà Nẵng": 0.12, "Hải Phòng": 0.12, "Cần Thơ": 0.11}
DELIVERY = {"TP.HCM": (1, 3), "Hà Nội": (1, 3), "Đà Nẵng": (2, 4), "Hải Phòng": (2, 5), "Cần Thơ": (3, 6)}
MARKETPLACES = {"Shopee", "Lazada", "Tiki"}
SEGMENTS = {"New": 0.45, "Returning": 0.42, "VIP": 0.13}

# category -> (cost ratio, [(product, base unit price in USD)])
CATALOG: dict[str, tuple[float, list[tuple[str, float]]]] = {
    "Electronics": (0.72, [("Wireless Earbuds", 45), ("Smartwatch", 120), ("Bluetooth Speaker", 60),
                           ("Power Bank", 25), ("Mechanical Keyboard", 80)]),
    "Fashion": (0.45, [("Cotton T-Shirt", 12), ("Sneakers", 55), ("Backpack", 35), ("Sunglasses", 20)]),
    "Home & Kitchen": (0.65, [("Air Fryer", 75), ("Rice Cooker", 50), ("Blender", 40), ("Coffee Maker", 65)]),
    "Beauty": (0.40, [("Sunscreen", 15), ("Vitamin C Serum", 22), ("Lipstick", 12)]),
    "Sports": (0.55, [("Yoga Mat", 18), ("Dumbbell Set", 45), ("Running Shoes", 70)]),
}
CATEGORY_WEIGHTS = {"Electronics": 0.28, "Fashion": 0.26, "Home & Kitchen": 0.18, "Beauty": 0.16, "Sports": 0.12}
TET = [date(2024, 2, 10), date(2025, 1, 29)]

COLUMNS = [
    "order_id", "order_date", "city", "channel", "category", "product", "customer_segment", "quantity",
    "unit_price", "discount_pct", "revenue", "cost", "profit", "rating", "delivery_days",
]


def _pick(rng: random.Random, weights: dict[str, float]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _day_multiplier(d: date) -> float:
    progress = (d - date(2024, 1, 1)).days / 730
    m = 1.0 + 0.35 * progress  # steady growth
    m *= 1.0 + 0.12 * math.sin(2 * math.pi * (d.timetuple().tm_yday / 365))  # mild seasonality
    if d.weekday() >= 5:
        m *= 1.15
    for tet in TET:
        days_before = (tet - d).days
        if 0 < days_before <= 21:
            m *= 1.9  # pre-Tet shopping rush
        elif -5 <= days_before <= 0:
            m *= 0.35  # holiday lull
    if (d.month, d.day) in {(11, 11), (12, 12)}:
        m *= 4.5
    elif (d.month, d.day) in {(9, 9), (10, 10)}:
        m *= 2.2
    return m


def _channel_weights(d: date, sale_day: bool) -> dict[str, float]:
    progress = (d - date(2024, 1, 1)).days / 730
    weights = {
        "Website": 0.30 - 0.12 * progress,
        "Mobile App": 0.10 + 0.22 * progress,
        "Shopee": 0.28,
        "Lazada": 0.17,
        "Tiki": 0.15 - 0.05 * progress,
    }
    if sale_day:
        for m in MARKETPLACES:
            weights[m] *= 2.0
    return weights


def generate_rows(seed: int = 42, base_orders_per_day: float = 9.0) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    order_no = 0
    d = date(2024, 1, 1)
    while d <= date(2025, 12, 31):
        sale_day = (d.month, d.day) in {(9, 9), (10, 10), (11, 11), (12, 12)}
        n = max(0, round(rng.gauss(base_orders_per_day * _day_multiplier(d), 2.0)))
        channel_weights = _channel_weights(d, sale_day)
        for _ in range(n):
            order_no += 1
            city = _pick(rng, CITIES)
            channel = _pick(rng, channel_weights)
            category = _pick(rng, CATEGORY_WEIGHTS)
            cost_ratio, products = CATALOG[category]
            product, base_price = rng.choice(products)
            segment = _pick(rng, SEGMENTS)
            quantity = rng.choices([1, 2, 3, 4, 5], weights=[60, 22, 10, 5, 3])[0]
            if segment == "VIP":
                quantity += rng.choice([0, 1, 1, 2])
            unit_price = round(base_price * rng.uniform(0.9, 1.1), 2)
            discounts = [0, 5, 10, 15, 20, 30, 40] if sale_day else [0, 0, 0, 5, 10, 15]
            discount = rng.choice(discounts)
            if channel in MARKETPLACES and rng.random() < 0.25:
                discount = min(discount + 10, 50)
            gross = quantity * unit_price
            revenue = round(gross * (1 - discount / 100), 2)
            cost = round(gross * cost_ratio, 2)
            lo, hi = DELIVERY[city]
            delivery_days = rng.randint(lo, hi) + (2 if sale_day and rng.random() < 0.5 else 0)
            rating: int | None = None
            if rng.random() > 0.17:
                score = 4.6 - 0.28 * max(0, delivery_days - 2) + rng.gauss(0, 0.7)
                rating = int(min(5, max(1, round(score))))
            rows.append({
                "order_id": f"ORD-{order_no:06d}",
                "order_date": d.isoformat(),
                "city": city,
                "channel": channel,
                "category": category,
                "product": product,
                "customer_segment": segment,
                "quantity": quantity,
                "unit_price": unit_price,
                "discount_pct": discount,
                "revenue": revenue,
                "cost": cost,
                "profit": round(revenue - cost, 2),
                "rating": rating,
                "delivery_days": delivery_days,
            })
        d += timedelta(days=1)
    return rows


def write_sample_csv(path: Path, seed: int = 42) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = generate_rows(seed)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def ensure_sample(samples_dir: Path) -> Path:
    path = samples_dir / SAMPLE_FILENAME
    if not path.exists():
        write_sample_csv(path)
    return path
