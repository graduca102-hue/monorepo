"""Deterministic delivery-path benchmark; never calls or charges providers."""

import argparse
import asyncio
import json
import statistics
import time
from unittest.mock import patch

import miniapp


class _DummyBot:
    token = "0" * 10 + ":" + "A" * 35


def _percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


async def run_proxy_benchmark(iterations: int, concurrency: int, provider_delay_ms: float) -> dict:
    states = {
        order_id: {
            "id": order_id,
            "status": "paid",
            "category_id": 10_000 + order_id,
            "item_id": order_id,
            "quantity": 1,
            "protocol": "HTTP",
            "provider_order_id": None,
        }
        for order_id in range(1, iterations + 1)
    }

    async def create_order(**kwargs):
        order_id = int(kwargs["item_id"])
        await asyncio.sleep(provider_delay_ms / 1000)
        return {
            "order": {
                "id": 10_000_000 + order_id,
                "details": [{
                    "host": "proxy.example",
                    "portHttp": 8080,
                    "portSocks": 1080,
                    "login": "user",
                    "password": "pass",
                }],
            }
        }

    def set_reference(order_id, provider_order_id, status="delivery_pending"):
        state = states[int(order_id)]
        state["provider_order_id"] = provider_order_id
        state["status"] = status

    def complete(order_id, provider_order_id, delivery_text):
        state = states[int(order_id)]
        state["provider_order_id"] = provider_order_id
        state["delivery_text"] = delivery_text
        state["status"] = "delivered"

    server = miniapp.MiniAppServer(_DummyBot())
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def measured(order_id: int) -> float:
        async with semaphore:
            started = time.perf_counter()
            result = await server._fulfill_proxy_order_for_miniapp(order_id)
            elapsed_ms = (time.perf_counter() - started) * 1000
            assert result and result["status"] == "delivered"
            return elapsed_ms

    started = time.perf_counter()
    with (
        patch.object(miniapp, "get_order", lambda order_id: dict(states[int(order_id)])),
        patch.object(miniapp, "create_proxy_provider_order", create_order),
        patch.object(miniapp, "set_proxy_order_provider_reference", set_reference),
        patch.object(miniapp, "complete_order", complete),
    ):
        samples = await asyncio.gather(*(measured(order_id) for order_id in states))
    wall_ms = (time.perf_counter() - started) * 1000
    assert all(
        state["delivery_text"] == "user:pass@proxy.example:8080" and state["status"] == "delivered"
        for state in states.values()
    )
    return {
        "scenario": "proxy delivery with mocked provider (no real purchase)",
        "iterations": iterations,
        "concurrency": concurrency,
        "mock_provider_delay_ms": provider_delay_ms,
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(_percentile(samples, 0.95), 2),
        "max_ms": round(max(samples), 2),
        "wall_ms": round(wall_ms, 2),
        "throughput_per_second": round(iterations / (wall_ms / 1000), 2),
    }


async def run_market_benchmark(iterations: int, concurrency: int, provider_delay_ms: float) -> dict:
    """Measure the complete goods path with all supplier I/O mocked."""
    states = {
        order_id: {
            "id": order_id,
            "status": "delivery_pending",
            "item_id": order_id,
            "quantity": 1,
            "supplier_order_uuid": None,
            "supplier_order_number": None,
        }
        for order_id in range(1, iterations + 1)
    }

    async def create_order(product_id, _quantity, *, product_prevalidated=False):
        assert product_prevalidated is True
        await asyncio.sleep(provider_delay_ms / 1000)
        return {"uuid": f"goods-{product_id}", "order_number": f"N-{product_id}"}

    def update_supplier(order_id, supplier_order_uuid, supplier_order_number=None):
        state = states[int(order_id)]
        state["supplier_order_uuid"] = supplier_order_uuid
        state["supplier_order_number"] = supplier_order_number

    async def get_supplier_order(order_uuid):
        await asyncio.sleep(provider_delay_ms / 1000)
        order_id = int(str(order_uuid).rsplit("-", 1)[1])
        return {
            "uuid": order_uuid,
            "order_number": f"N-{order_id}",
            "status": "completed",
            "items": [{"link_to_file": f"https://example.test/{order_uuid}.txt"}],
        }

    async def download_file(url):
        await asyncio.sleep(provider_delay_ms / 1000)
        return f"delivered:{url.rsplit('/', 1)[-1]}"

    def complete(order_id, supplier_order_uuid, supplier_order_number, delivery_text):
        state = states[int(order_id)]
        state["supplier_order_uuid"] = supplier_order_uuid
        state["supplier_order_number"] = supplier_order_number
        state["delivery_text"] = delivery_text
        state["status"] = "delivered"

    server = miniapp.MiniAppServer(_DummyBot())
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def measured(order_id: int) -> float:
        async with semaphore:
            started = time.perf_counter()
            result = await server._fulfill_market_order_for_miniapp(order_id)
            elapsed_ms = (time.perf_counter() - started) * 1000
            assert result and result["status"] == "delivered" and result.get("delivery_text")
            return elapsed_ms

    started = time.perf_counter()
    with (
        patch.object(miniapp, "get_order", lambda order_id: dict(states[int(order_id)])),
        patch.object(miniapp, "create_market_order", create_order),
        patch.object(miniapp, "update_order_supplier_data", update_supplier),
        patch.object(miniapp, "get_market_order", get_supplier_order),
        patch.object(miniapp, "download_text_file", download_file),
        patch.object(miniapp, "complete_market_order", complete),
    ):
        samples = await asyncio.gather(*(measured(order_id) for order_id in states))
    wall_ms = (time.perf_counter() - started) * 1000
    return {
        "scenario": "goods delivery with mocked provider (no real purchase)",
        "iterations": iterations,
        "concurrency": concurrency,
        "mock_delay_per_provider_request_ms": provider_delay_ms,
        "provider_requests_per_delivery": 3,
        "p50_ms": round(statistics.median(samples), 2),
        "p95_ms": round(_percentile(samples, 0.95), 2),
        "max_ms": round(max(samples), 2),
        "wall_ms": round(wall_ms, 2),
        "throughput_per_second": round(iterations / (wall_ms / 1000), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--provider-delay-ms", type=float, default=25)
    parser.add_argument("--scenario", choices=("proxy", "market"), default="proxy")
    args = parser.parse_args()
    if args.iterations <= 0 or args.concurrency <= 0 or args.provider_delay_ms < 0:
        parser.error("iterations/concurrency must be positive; delay must be non-negative")
    benchmark = run_market_benchmark if args.scenario == "market" else run_proxy_benchmark
    result = asyncio.run(benchmark(args.iterations, args.concurrency, args.provider_delay_ms))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
