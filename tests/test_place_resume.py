"""Server-only continuation markers; no SQL, model or HTTP."""
from uuid import uuid4

import pytest

from otravel.place_resume import PREFIX, read_resume_marker, resume_marker


def test_server_marker_roundtrip_is_bounded_exact_and_contains_no_old_state_hash():
    trip, identity, run = str(uuid4()), str(uuid4()), str(uuid4())
    text = resume_marker(trip, identity, run)
    assert read_resume_marker(text) == {"kind": "resume_place", "trip_id": trip,
        "resume_id": identity, "resume_run_id": run}
    assert "base_state_hash" not in text


@pytest.mark.parametrize("text", [None, "continue", PREFIX + '{}', PREFIX + '[]', PREFIX + 'null',
    PREFIX + '{"kind":"resume_place","kind":"resume_place"}', PREFIX + "x" * 1025])
def test_user_or_malformed_marker_does_not_become_internal_work(text):
    assert read_resume_marker(text) is None
