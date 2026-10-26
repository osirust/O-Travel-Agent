"""Groq profile and global POST spacing; fake clocks/HTTP only, no waiting."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_replay_after80_profiles import metadata, rows, runner


def groq_metadata():
    health, status, evidence = metadata("groq-qwen")
    evidence["limits"]["groq_daily_engineering_bound"] = 1000
    return health, status, evidence


def test_Groq_fixed_profile_separate_cap_and_frozen_post_interval():
    frozen = runner.plan(rows(), llm_profile="groq-qwen", message_interval_seconds=60)
    assert frozen["expected_runtime"] == {"llm_provider": "groq", "model": "qwen/qwen3.8-27b"}
    assert frozen["runtime_evidence"] == "docs/verification/after80-groq-backend-20261026.json"
    assert frozen["message_interval_seconds"] == 60 and frozen["provider_remaining"] == "UNKNOWN"
    assert runner.plan(rows())["message_interval_seconds"] == 0
    runner.check_profile(*groq_metadata(), "groq-qwen")


@pytest.mark.parametrize("fault", ["model", "manager", "free", "cap_missing", "cap40", "cap_bool", "other_router", "other_zai", "per_run", "local", "paid"])
def test_Groq_preflight_rejects_wrong_profile_and_nonexplicit_bound(fault):
    health, status, evidence = groq_metadata()
    if fault == "model": status["_runtime"]["model"] = "paid/model"
    elif fault == "manager": evidence["manager"] = "after80_router_backend.py"
    elif fault == "free": status["groq"]["free_tier_confirmed"] = False
    elif fault == "cap_missing": evidence["limits"].pop("groq_daily_engineering_bound")
    elif fault == "cap40": evidence["limits"]["groq_daily_engineering_bound"] = 40
    elif fault == "cap_bool": evidence["limits"]["groq_daily_engineering_bound"] = True
    elif fault == "other_router": status["openrouter"]["configured"] = True
    elif fault == "other_zai": status["zai"]["configured"] = True
    elif fault == "per_run": evidence["limits"]["calls_per_run"] = 2
    elif fault == "local": evidence["limits"]["local_calls"] = 1
    elif fault == "paid": status["groq"]["paid_fallback"] = True
    with pytest.raises(ValueError): runner.check_profile(health, status, evidence, "groq-qwen")


@pytest.mark.parametrize("interval", [-1, 121, float("nan"), float("inf"), "60", True, None])
def test_interval_is_bounded_finite_numeric_and_not_bool(interval):
    with pytest.raises(ValueError, match="message_interval"):
        runner.plan(rows(), message_interval_seconds=interval)
    with pytest.raises(ValueError, match="message_interval"):
        runner.PostPacer(interval)


def test_groq_paced_dry_cli_has_zeroHTTP_private_output_and_no_real_sleep(monkeypatch, capsys):
    monkeypatch.setattr(runner, "load_source", rows)
    forbidden = Mock(side_effect=AssertionError("Dry planning must not access runtime or wait"))
    for name in ("HTTP", "private_json", "check_profile", "PostPacer"):
        monkeypatch.setattr(runner, name, forbidden)
    assert runner.main(["--llm-profile", "groq-qwen", "--message-interval-seconds", "60"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "DRY_RUN_ZERO_REQUESTS"
    assert value["plan"]["message_interval_seconds"] == 60
    forbidden.assert_not_called()


class Clock:
    def __init__(self): self.value, self.sleeps = 0, []
    def now(self): return self.value
    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def test_pacer_spaces_global_POSTs_in_at_most10second_chunks_and_no_vendor_claim():
    clock = Clock()
    pacer = runner.PostPacer(45, clock=clock.now, sleep=clock.sleep)
    assert [pacer.before_post() for _ in range(3)] == [0, 45, 90]
    assert clock.sleeps and max(clock.sleeps) <= 10 and sum(clock.sleeps) == 90


def test_parallel_workers_share_one_POST_schedule_not_separate_provider_budgets():
    clock = Clock()
    pacer = runner.PostPacer(60, clock=clock.now, sleep=clock.sleep)
    with ThreadPoolExecutor(max_workers=2) as pool:
        starts = list(pool.map(lambda _: pacer.before_post(), range(4)))
    assert sorted(starts) == [0, 60, 120, 180]
    assert max(clock.sleeps) <= 10


def test_resume_waits_full_interval_before_new_POST_but_does_not_resend_unknown():
    clock = Clock()
    pacer = runner.PostPacer(60, clock=clock.now, sleep=clock.sleep, resume=True)
    assert pacer.before_post() == 60
    assert clock.sleeps == [10] * 6


def test_global_stop_interrupts_pacing_before_write_within10seconds():
    clock, stop = Clock(), threading.Event()
    def stop_after_chunk(seconds):
        clock.sleep(seconds)
        stop.set()
    pacer = runner.PostPacer(60, clock=clock.now, sleep=stop_after_chunk, stop=stop)
    assert pacer.before_post() == 0
    with pytest.raises(RuntimeError, match="STOPPED_BEFORE_WRITE"):
        pacer.before_post()
    assert clock.sleeps == [10]


def test_HTTP_clients_share_POSTpacer_and_GETs_never_wait(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(runner, "_POST_PACER", runner.PostPacer(45, clock=clock.now, sleep=clock.sleep))
    response = Mock()
    response.__enter__ = Mock(return_value=SimpleNamespace(status=200, read=lambda _: b"{}", headers={}))
    response.__exit__ = Mock(return_value=False)
    first, second = runner.HTTP(), runner.HTTP()
    first.opener = second.opener = SimpleNamespace(open=Mock(return_value=response))
    first.request("GET", "/health")
    assert clock.sleeps == []
    first.request("POST", "/api/v1/sessions", body={})
    second.request("GET", "/health")
    second.request("POST", "/api/v1/sessions", body={})
    assert sum(clock.sleeps) == 45
    assert first.opener.open.call_count == 4


def test_changed_frozen_interval_blocks_resume_before_anyHTTP_or_case(monkeypatch, tmp_path):
    saved = {"source_sha256": runner.SOURCE_SHA,
             "plan": runner.plan(rows(), llm_profile="groq-qwen", message_interval_seconds=45), "sessions": {}}
    vault = tmp_path / "resume-vault.json"
    vault.write_text(json.dumps(saved)); vault.chmod(0o600)
    monkeypatch.setattr(runner, "load_source", rows)
    forbidden = Mock(side_effect=AssertionError("Changed interval resume stops before runtime access"))
    monkeypatch.setattr(runner, "HTTP", forbidden)
    monkeypatch.setattr(runner, "Replay", forbidden)
    with pytest.raises(ValueError, match="resume_source_or_plan_mismatch"):
        runner.main(["--run", "--llm-profile", "groq-qwen", "--message-interval-seconds", "60", "--resume", str(vault)])
    forbidden.assert_not_called()
