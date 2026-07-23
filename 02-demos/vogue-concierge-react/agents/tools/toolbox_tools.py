# Copyright 2026 slarbi-web
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Toolbox Tools — Local Fallback
================================
Fallback implementations of check_inventory and get_loyalty_discount
for local development when MCP Toolbox is not running.

In production, these are replaced by MCP Toolbox tools loaded from tools.yaml.
"""

import json
import os

# Mock data paths
CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "products.json")


def _load_catalog() -> list:
    catalog_file = os.path.abspath(CATALOG_PATH)
    if os.path.exists(catalog_file):
        with open(catalog_file, "r") as f:
            return json.load(f)
    return []


async def check_inventory(sku: str) -> dict:
    """Check real-time inventory levels for a specific product SKU.

    Use this tool when a customer asks about stock availability, sizes in stock,
    or whether an item is available.

    Args:
        sku: The product SKU to check (e.g. "SKU-001", "SKU-015").

    Returns:
        Dictionary with current stock levels per size and total units available.
    """
    catalog = _load_catalog()
    product = next((p for p in catalog if p["sku"] == sku), None)

    if not product:
        return {"error": f"Product {sku} not found in catalog."}

    # Simulated inventory data
    import random
    random.seed(hash(sku))
    sizes = product.get("sizes", ["S", "M", "L", "XL"])
    inventory = {}
    total = 0
    for size in sizes:
        qty = random.randint(0, 15)
        inventory[size] = qty
        total += qty

    return {
        "sku": sku,
        "name": product["name"],
        "price": product["price"],
        "inventory_by_size": inventory,
        "total_units": total,
        "low_stock_alert": total < 10,
    }


async def get_loyalty_discount(customer_id: str) -> dict:
    """Look up loyalty program status and available discounts for a customer.

    Use this tool when a customer asks about their loyalty status, available
    discounts, or membership perks.

    Args:
        customer_id: The customer ID (e.g. "CUST-1042").

    Returns:
        Dictionary with loyalty tier, discount percentage, and points balance.
    """
    # Simulated loyalty data
    import random
    random.seed(hash(customer_id))

    tiers = [
        {"tier": "Bronze", "discount": 5, "free_shipping": False},
        {"tier": "Silver", "discount": 10, "free_shipping": False},
        {"tier": "Gold", "discount": 15, "free_shipping": True},
        {"tier": "Platinum", "discount": 20, "free_shipping": True},
    ]

    tier_data = random.choice(tiers)
    points = random.randint(100, 5000)

    return {
        "customer_id": customer_id,
        "tier": tier_data["tier"],
        "discount_percent": tier_data["discount"],
        "free_shipping": tier_data["free_shipping"],
        "points_balance": points,
        "message": f"As a {tier_data['tier']} member, you get {tier_data['discount']}% off!"
    }
