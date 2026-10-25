"""Owned-child crash probe. Only otravel_test; HTTP is MockTransport, not a vendor.

The parent test kills this exact child after its ready marker. Never use this
helper to target a service, Docker process, or an existing worker.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy.engine import make_url

from otravel.store import Store


async def probe(session_id: str, run_id: str) -> None:
    url = os.environ.get("TEST_DATABASE_URL", "")
    if os.environ.get("OTRAVEL_OWNED_CRASH_PROBE") != "1" or make_url(url).database != "otravel_test":
        raise RuntimeError("Explicit isolated test environment required")
    repository = Store(url)
    try:
        run = await repository.get_run(run_id)
        if run["session_id"] != session_id:
            raise RuntimeError("Owned pending run required")
        if run["status"] == "pending" and not await repository.claim_run(run_id):
            raise RuntimeError("Owned pending run could not be claimed")
        # The parent dispatcher may already have claimed the owned lease before
        # spawn. Re-read the actual rows; never invent a stale session snapshot.
        run = await repository.get_run(run_id)
        session = await repository.get_session(session_id)
        if (run["status"] != "running" or session["active_run_id"] != run_id
                or session["state_version"] != run["based_version"]
                or session["waiting_reason"] == "user_pause"):
            raise RuntimeError("Current owned lease required")
        attempts = []

        def controlled_http(request):
            attempts.append(request.method)
            return httpx.Response(200, json={"source_type": "TEST_MOCK_HTTP", "ok": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(controlled_http), trust_env=False) as client:
            response = await client.post("https://crash-probe.invalid/controlled-read", json={"read_only": True})
            response.raise_for_status()
        await repository.save_event(session_id, run_id, "FAULT_PROBE_HTTP_COMPLETED", {
            "source_type": "TEST_MOCK_HTTP", "http_attempts": len(attempts), "read_only": True,
        })
        print(json.dumps({"status": "ready_for_owned_kill", "pid": os.getpid(),
                          "run_id": run_id, "http_attempts": len(attempts)}), flush=True)
        # All DB writes above committed. No transaction spans this waiting point.
        await asyncio.Event().wait()
    finally:
        # SIGKILL deliberately does not execute this path; normal setup failures do.
        await repository.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--run-id", required=True)
    arguments = parser.parse_args()
    try:
        asyncio.run(probe(arguments.session_id, arguments.run_id))
    except Exception:
        # Driver errors may contain credentials: no raw exception/traceback.
        print(json.dumps({"status": "probe_setup_failed"}), flush=True)
        raise SystemExit(2) from None
