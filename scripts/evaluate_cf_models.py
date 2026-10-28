"""Six bounded CF calls; no Z.ai, runtime changes, retries, or paid routes."""
import argparse
import asyncio
import hashlib
import os
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from run_tests import read_values
from otravel.domain import Operation, TripState, apply_operations
from otravel.harness import ModelResult, OPERATION_GUIDE
from otravel.llm_contract import build_operation_schema
from otravel.money import ground_budget_operations
from otravel.observability import model_snapshot
from otravel.providers import LLMGateway, ProviderError

MODELS = ['@cf/google/gemma-4-26b-a4b-it', '@cf/nvidia/nemotron-3-120b-a12b']
OUTPUT = None


class Recorder(httpx.AsyncBaseTransport):
    def __init__(self, settings):
        self.inner = httpx.AsyncHTTPTransport(retries=0, trust_env=False)
        self.settings, self.requests, self.snapshot, self.http_status = settings, 0, None, None

    async def handle_async_request(self, request):
        self.requests += 1
        response = await self.inner.handle_async_request(request)
        await response.aread()
        self.http_status = response.status_code
        if response.status_code == 200:
            try:
                value = json.loads(response.json()['choices'][0]['message']['content'])
                if isinstance(value, dict):
                    self.snapshot = model_snapshot(value, self.settings)
            except (ValueError, TypeError, KeyError, IndexError):
                pass
        return response

    async def aclose(self):
        await self.inner.aclose()


def checks(case, result, operations):
    initial = TripState.model_validate(case['trip'])
    changed, _ = apply_operations(initial, [Operation.model_validate(op) for op in operations])
    if case['id'] == 'CFP-01':
        return {'origin': changed.origin == 'Москва',
            'destination': any(stay.city == 'Стамбул' for stay in changed.stays),
            'adults': sum(t.type == 'adult' for t in changed.travelers) == 1,
            'children': not any(t.type == 'child' for t in changed.travelers),
            'no_invented_budget': changed.budget is None,
            'morning': any('morning' in str(p.value).casefold() or 'утр' in str(p.value).casefold() for p in changed.preferences)}
    if case['id'] == 'CFP-02':
        subject = (result.information_subject or '').casefold()
        return {'information_act': result.speech_act == 'ASK', 'no_operations': not operations,
            'serbia_subject': 'серб' in subject or 'serbia' in subject,
            'trip_unchanged': changed.model_dump(mode='json') == initial.model_dump(mode='json')}
    before = initial.model_dump(mode='json')
    after = changed.model_dump(mode='json')
    previous = {t['id']: t for t in before['travelers']}
    current = {t['id']: t for t in after['travelers']}
    added = [t for key, t in current.items() if key not in previous]
    return {'one_new_child_age7': len(added) == 1 and added[0]['type'] == 'child' and added[0]['age'] == 7,
        'existing_travelers_unchanged': all(current.get(key) == value for key, value in previous.items()),
        'budget': after['budget']['amount_minor'] == 8000000,
        'explicit_wait': result.control.action == 'wait',
        'remaining_fields_unchanged': all(after[key] == before[key] for key in (
            'origin', 'stays', 'legs', 'groups', 'constraints', 'preferences', 'search_policy'))}


async def main(env_file=None):
    fixture = json.loads((ROOT / 'fixtures/evaluation/cf-backup-pilot-20261026.json').read_text())
    if len(fixture['cases']) > 3:
        raise ValueError('This pilot permits at most three cases per model')
    values = read_values(Path(env_file)) if env_file else dict(os.environ)
    report = {'scope': '3 bounded extraction cases on 2 CF candidates, not full conversations',
        'started_at_utc': datetime.now(timezone.utc).isoformat(), 'results': [],
        'zai_calls': 0, 'runtime_changes': False, 'automatic_retries': False, 'expected_cases_per_model': 3, 'fixture_sha256': hashlib.sha256((ROOT / 'fixtures/evaluation/cf-backup-pilot-20261026.json').read_bytes()).hexdigest()}
    for model in MODELS:
        for case in fixture['cases']:
            settings = {key: values.get(key, '') for key in ('CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_FREE_TIER_CONFIRMED')}
            settings.update(LLM_PROVIDER='cloudflare', CLOUDFLARE_MODEL=model, LLM_MAX_OUTPUT_TOKENS=2048)
            recorder = Recorder(settings)
            gateway = LLMGateway({**settings, 'http_transport': recorder})
            context = {'current_message': case['text'], 'trip': deepcopy(case['trip']), 'pending_question': None,
                'recent_messages': [], 'operation_contract': OPERATION_GUIDE,
                'reference_now_utc': fixture['reference_now_utc'], 'default_timezone': fixture['default_timezone'],
                '__response_schema': build_operation_schema(case['trip'])}
            row = {'model': model, 'case_id': case['id']}
            started = time.perf_counter()
            stop_model = False
            try:
                output = await gateway.extract_operations(context)
                result = ModelResult.model_validate(output)
                raw_ops = [op.model_dump(mode='json', exclude_none=True) for op in result.operations]
                row['raw_checks'] = checks(case, result, raw_ops)
                grounded, notes = ground_budget_operations(TripState.model_validate(case['trip']), result.operations, case['text'])
                row['grounded_checks'] = checks(case, result, grounded)
                row['normalizations'] = notes
                row['status'] = 'PASS_BOUND_SUBSET_ONLY' if all(row['grounded_checks'].values()) else 'NOT_ALL_BOUND_CHECKS_PASS'
                row['call_metadata'] = output.call_metadata
            except ProviderError as exc:
                row.update(status='PROVIDER_OR_CONTRACT_FAILED', error=exc.as_dict(), call_metadata=gateway.call_metadata_for_current_call())
                stop_model = True
            except Exception as exc:
                row.update(status='SEMANTIC_VALIDATION_FAILED', error_class=type(exc).__name__, call_metadata=gateway.call_metadata_for_current_call())
            finally:
                row.update(http_status=recorder.http_status, http_attempts=recorder.requests,
                    latency_ms=round((time.perf_counter() - started) * 1000), structured_contract=recorder.snapshot)
                await gateway.close()
            report['results'].append(row)
            OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({key: row[key] for key in ('model', 'case_id', 'status', 'http_status', 'http_attempts', 'latency_ms')}), flush=True)
            if stop_model:
                break
    report['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
    report['attempted_cases'] = len(report['results'])
    report['case_prompt_hashes_match_across_models'] = all(len({
        row['call_metadata']['request_metrics']['prompt_sha256'] for row in report['results'] if row['case_id'] == case['id']
        and row.get('call_metadata', {}).get('request_metrics')}) <= 1 for case in fixture['cases'])
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Explicitly allow at most six Workers Free model calls')
    parser.add_argument('--env-file', type=Path, help='Ignored local configuration; otherwise use environment variables')
    parser.add_argument('--output', type=Path, help='New output file; existing evidence is never overwritten')
    args = parser.parse_args()
    if not args.run:
        print('No model calls made. Configure Workers Free and pass --run for the bounded pilot.')
    else:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        OUTPUT = args.output or ROOT / '.local/evaluation/cf-backup-pilot' / (stamp + '.json')
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        with OUTPUT.open('x', encoding='utf-8') as handle:
            handle.write('{}\n')
        asyncio.run(main(args.env_file))
