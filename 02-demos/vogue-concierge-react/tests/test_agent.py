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
Test Agent — Vogue Concierge
================================
Validates the ADK agent initializes correctly with all tools.

Usage:
    python tests/test_agent.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_agent_creation():
    """Test that the agent creates successfully."""
    print("Testing agent creation...")

    from agents.agent import create_agent
    agent = create_agent()

    print(f"  Agent name: {agent.name}")
    print(f"  Model: {agent.model}")
    print(f"  Tools: {len(agent.tools)}")
    for tool in agent.tools:
        name = getattr(tool, '__name__', getattr(tool, 'name', str(tool)))
        print(f"    - {name}")

    assert agent.name == "vogue_concierge"
    print("  PASSED")
    return True


def test_catalog_search():
    """Test the catalog search tool."""
    print("\nTesting catalog_search tool...")

    from agents.tools.catalog_search import catalog_search
    result = catalog_search("summer wedding dress")

    print(f"  Source: {result['source']}")
    print(f"  Results: {len(result['results'])}")
    for r in result['results'][:3]:
        if 'name' in r:
            print(f"    - {r['sku']}: {r['name']} (${r['price']})")
        else:
            print(f"    - {r.get('content', '')[:80]}...")

    assert len(result['results']) > 0
    print("  PASSED")
    return True


def test_trend_search():
    """Test the trend search tool."""
    print("\nTesting trend_search tool...")

    from agents.tools.trend_rag import trend_search
    result = trend_search("evening glamour cocktail")

    print(f"  Source: {result['source']}")
    print(f"  Results: {len(result['results'])}")
    for r in result['results'][:2]:
        print(f"    - {r['content'][:80]}...")

    assert len(result['results']) > 0
    print("  PASSED")
    return True


def test_fallback_tools():
    """Test the fallback BigQuery tools."""
    print("\nTesting fallback tools...")

    from agents.tools.toolbox_tools import check_inventory, get_loyalty_discount

    inv = check_inventory("SKU-001")
    print(f"  Inventory for SKU-001: {inv['total_units']} units")
    assert 'sku' in inv

    loyalty = get_loyalty_discount("CUST-1042")
    print(f"  Loyalty for CUST-1042: {loyalty['tier']} ({loyalty['discount_percent']}% off)")
    assert 'tier' in loyalty

    print("  PASSED")
    return True


def main():
    print("=" * 60)
    print("Vogue Concierge — Agent Tests")
    print("=" * 60)

    results = []
    results.append(test_catalog_search())
    results.append(test_trend_search())
    results.append(test_fallback_tools())
    results.append(test_agent_creation())

    print(f"\n{'=' * 60}")
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    print(f"{'=' * 60}")

    return all(results)


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
