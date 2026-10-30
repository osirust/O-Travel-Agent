"""Explicit small free-model probe. Run in the isolated WSL project, not with real traveler data."""

import argparse
import asyncio
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def reserve_probe(usage_path: Path, today: str, limit: int = 5) -> bool:
    """Reserve before HTTP; file locking also covers concurrent local invocations."""
    import fcntl
    usage_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with usage_path.with_suffix(".lock").open("a", encoding="utf-8") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        usage = json.loads(usage_path.read_text()) if usage_path.exists() else {}
        count = usage.get(today, 0)
        if type(count) is not int or count < 0 or count >= limit:
            return False
        usage[today] = count + 1
        fd, temporary = tempfile.mkstemp(prefix="probe-usage-", dir=usage_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(usage, stream)
            os.replace(temporary, usage_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["openrouter", "gemini"], default="openrouter")
    parser.add_argument("--model", help="Explicit :free OpenRouter candidate; does not change runtime configuration")
    parser.add_argument("--confirm-free", action="store_true", help="Allow one synthetic inference call")
    args = parser.parse_args()
    if sys.platform == "win32":
        script = "/mnt/" + str(Path(__file__).resolve())[0].lower() + str(Path(__file__).resolve())[2:].replace("\\", "/")
        cmd = ["wsl.exe", "-d", "AegML-Hackathon", "-u", "aegml", "--", "/home/aegml/venvs/otravel-harness-20261025/bin/python", script, "--provider", args.provider]
        if args.confirm_free:
            cmd.append("--confirm-free")
        if args.model:
            cmd.extend(["--model", args.model])
        return subprocess.run(cmd, check=False).returncode
    if not args.confirm_free:
        print("Live call not made. Add --confirm-free for ONE synthetic free-tier probe.")
        return 2
    project = Path("/home/aegml/projects/otravel")
    sys.path.insert(0, "/mnt/e/work/aegml")
    from dotenv import dotenv_values
    from otravel.providers import LLMGateway, ProviderError
    from otravel.domain import Operation, TripState, apply_operations
    from pydantic import ValidationError

    settings = {**dotenv_values(project / ".env"), "LLM_PROVIDER": args.provider}
    if args.model:
        if args.provider != "openrouter" or not args.model.endswith(":free"):
            print(json.dumps({"status": "not_run", "reason": "only_explicit_free_models_allowed"}))
            return 2
        settings["OPENROUTER_MODEL"] = args.model
    required = "OPENROUTER_API_KEY" if args.provider == "openrouter" else "GEMINI_API_KEY"
    if not settings.get(required):
        print(json.dumps({"status": "not_run", "reason": "missing_key", "required": required}))
        return 2
    usage_path = project / ".local" / "probe-usage.json"
    today = date.today().isoformat()
    if not reserve_probe(usage_path, today):
        print(json.dumps({"status": "not_run", "reason": "probe_daily_cap", "limit": 5}))
        return 2

    async def probe() -> int:
        gateway = LLMGateway(settings)
        state = TripState()
        try:
            result = await gateway.extract_operations({
                "state": state.model_dump(mode="json"),
                "user_message": "Город отправления Казань. Общий жёсткий бюджет 140000 рублей.",
                "operation_contracts": {
                    "set_origin": {"value": {"city": "Казань"}},
                    "set_budget": {"value": {"amount_minor": 14000000, "currency": "RUB", "scope": "whole_trip"}},
                },
                "instructions": "Return only set_origin and set_budget for the explicit facts. Prices use integer kopeks. No booking or invented trip dates.",
            })
            ops = [Operation.model_validate(op) for op in result["operations"]]
            updated, changes = apply_operations(state, ops)
            good = updated.origin == "Казань" and updated.budget is not None and updated.budget.amount_minor == 14000000
            print(json.dumps({"status": "passed" if good else "failed_semantics", "provider": args.provider,
                              "model": settings.get("OPENROUTER_MODEL", "google/gemma-4-31b-it:free") if args.provider == "openrouter" else settings.get("GEMINI_MODEL", "gemini-3.8-flash"),
                              "json_and_domain_validation": True, "expected_facts_match": good,
                              "operations": [op.model_dump() for op in ops], "changes": changes,
                              "scope": "one synthetic extraction, not a full model-quality evaluation"}, ensure_ascii=False))
            return 0 if good else 1
        except ProviderError as exc:
            print(json.dumps({"status": "blocked_provider", "error": exc.as_dict()}, ensure_ascii=False))
            return 2
        except (ValidationError, ValueError):
            print(json.dumps({"status": "failed_validation", "reason": "model_output_rejected"}))
            return 1
        finally:
            await gateway.close()

    return asyncio.run(probe())


if __name__ == "__main__":
    raise SystemExit(main())
