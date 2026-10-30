"""One bounded, explicit free/test API check; never creates supplier orders."""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-free", action="store_true")
    args = parser.parse_args()
    if not args.confirm_free:
        print("No network calls. Add --confirm-free for the bounded sandbox/free check.")
        return 2
    if sys.platform == "win32":
        path = str(Path(__file__).resolve())
        script = "/mnt/" + path[0].lower() + path[2:].replace("\\", "/")
        return subprocess.run(["wsl.exe", "-d", "AegML-Hackathon", "-u", "aegml", "--",
                               "/home/aegml/venvs/otravel-harness-20261025/bin/python", script,
                               "--confirm-free"], check=False).returncode
    return asyncio.run(probe())


async def probe():
    import httpx
    from dotenv import dotenv_values
    root = Path("/mnt/e/work/aegml")
    sys.path.insert(0, str(root))
    from otravel.providers import TravelGateway, ProviderError

    native = Path("/home/aegml/projects/otravel")
    journal = native / ".local" / ("travel-probe-" + datetime.now(timezone.utc).date().isoformat() + ".json")
    journal.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with journal.open("x", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"attempted": True, "max_requests": 7}, stream)
    except FileExistsError:
        print("Travel check already reserved today; automatic repeat blocked.")
        return 2

    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "checks": [], "http": [],
              "supplier_orders_created": 0, "llm_calls": 0, "max_http_requests": 7,
              "scope": "synthetic inputs; sandbox and indicative components, not a validated live trip"}
    allowed = {("GET", "api.liteapi.travel", "/v3.0/data/hotels"),
               ("POST", "api.liteapi.travel", "/v3.0/hotels/rates"),
               ("POST", "api.duffel.com", "/air/offer_requests"),
               ("GET", "serpapi.com", "/account.json"),
               ("GET", "serpapi.com", "/search.json"),
               ("GET", "api.open-meteo.com", "/v1/forecast")}

    class BoundedTransport(httpx.AsyncHTTPTransport):
        async def handle_async_request(self, request):
            target = (request.method, request.url.host, request.url.path)
            if target not in allowed or len(report["http"]) >= 7:
                raise RuntimeError("probe_transport_blocked")
            item = {"host": request.url.host, "method": request.method}
            report["http"].append(item)
            try:
                response = await super().handle_async_request(request)
                item["status"] = response.status_code
                return response
            except Exception as exc:
                item["transport_error"] = type(exc).__name__
                raise

    values = dotenv_values(native / ".env")
    names = ("LITEAPI_API_KEY", "LITEAPI_MODE", "DUFFEL_ACCESS_TOKEN", "SERPAPI_API_KEY", "SERPAPI_FREE_TIER_CONFIRMED")
    settings = {key: values.get(key, "") for key in names}
    settings.update(http_transport=BoundedTransport(retries=0), PROVIDER_TIMEOUT=25)
    gateway = TravelGateway(settings)

    async def check(name, action):
        try:
            data = await action()
            if isinstance(data, list):
                entry = {"name": name, "status": "offers_received" if data else "reachable_no_offers",
                         "offers_count": len(data), "source_types": sorted({v["source_type"] for v in data}),
                         "price_kinds": sorted({v["price_kind"] for v in data}),
                         "currencies": sorted({v["currency"] for v in data})}
            else:
                entry = {"name": name, "status": "response_received", **data}
            report["checks"].append(entry)
            return data
        except ProviderError as exc:
            report["checks"].append({"name": name, "status": "blocked_or_failed", "error": exc.as_dict()})
        except Exception as exc:
            report["checks"].append({"name": name, "status": "blocked_or_failed", "error_class": type(exc).__name__})

    now = datetime.now(timezone.utc).date()
    departure = max(now + timedelta(days=16), datetime(2026, 11, 10).date()).isoformat()
    checkout = (datetime.fromisoformat(departure).date() + timedelta(days=3)).isoformat()
    common = {"checkin": departure, "checkout": checkout, "currency": "USD",
              "occupancies": [{"adults": 2, "children": [7]}]}
    try:
        async def lite():
            key = gateway._key("LITEAPI_API_KEY", "liteapi")
            if not key.startswith(("sand_", "sandbox_")):
                raise RuntimeError("sandbox_key_required")
            response = await gateway.client.get("https://api.liteapi.travel/v3.0/data/hotels",
                headers={"X-API-Key": key}, params={"countryCode": "TR", "cityName": "Istanbul", "limit": 3})
            if response.status_code != 200:
                raise ProviderError("CATALOG_HTTP_ERROR", "Sandbox catalog request failed.", provider="liteapi",
                                    details={"http_status": response.status_code})
            rows = response.json().get("data", [])
            ids = [row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)][:3]
            if not ids:
                raise RuntimeError("catalog_hotel_ids_missing")
            return await gateway.search_hotels({**common, "hotelIds": ids, "guestNationality": "RU"}, "liteapi")
        await check("liteapi_sandbox_rates", lite)
        slices = [{"origin": "KZN", "destination": "IST", "departure_date": departure}]
        await check("duffel_test_flights", lambda: gateway.search_flights({"slices": slices,
            "passengers": [{"type": "adult"}, {"type": "adult"}, {"age": 7}], "cabin_class": "economy"}, "duffel"))

        async def account():
            key = gateway._key("SERPAPI_API_KEY", "serpapi")
            response = await gateway.client.get("https://serpapi.com/account.json", params={"api_key": key})
            if response.status_code != 200:
                raise RuntimeError("serp_account_not_available")
            data = response.json()
            free = isinstance(data.get("plan_name"), str) and data["plan_name"].strip().lower() in {"free", "free plan"}
            left = data.get("plan_searches_left")
            approved = free and type(left) is int and left >= 2
            return {"free_plan": free, "searches_left_before": left if type(left) is int else None,
                    "allow_two_searches": approved}
        metadata = await check("serpapi_account", account)
        if metadata and metadata["allow_two_searches"]:
            await check("serpapi_hotels_indicative", lambda: gateway.search_hotels({**common, "city": "Istanbul"}, "serpapi"))
            await check("serpapi_flights_indicative", lambda: gateway.search_flights({"slices": slices,
                "adults": 2, "children": 1, "currency": "USD"}, "serpapi"))
        else:
            report["checks"].append({"name": "serpapi_searches", "status": "blocked_free_quota_unconfirmed"})

        async def weather():
            data = await gateway.weather("Istanbul", 41.0082, 28.9784)
            return {"source_type": data["source_type"], "forecast_days": len(data["daily"].get("time", [])),
                    "attribution": data["attribution"]}
        await check("open_meteo_weather", weather)
    finally:
        await gateway.close()
        journal.write_text(json.dumps({"attempted": True, "requests": len(report["http"])}), encoding="utf-8")
        path = root / "docs/verification/travel-api-probes-20261025.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"http_requests": len(report["http"]), "checks": report["checks"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print("Probe stopped; diagnostic type=" + type(exc).__name__)
        raise SystemExit(2)
