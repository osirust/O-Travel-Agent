"""Compress recognized server-authored guide clauses, never conversation data.

Fingerprints describe the code-static guide at this revision. A changed or custom
clause is intentionally left untouched until its translation is reviewed. This
module imports neither Harness nor the provider and makes no calls or state edits.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

_VERSION = "groq_operation_guide_en_v1"

# SHA-256 of JSON(original clause), UTF-8, sorted keys and compact separators.
# Data fields (cities, known constraints and do_not_invent) have no translations.
_CLAUSES: dict[str, tuple[str, type, Any]] = {
    "instruction": (
        "c9a14a6c2296a101f328c44a3b58a238ae35708f684c817e959e344bf9f84d38", str,
        ("Propose edits for server review from current_message only; they are not already applied. "
        "History is context; never reapply old edits. "
        "No booking or side effects. Incompatible values of one field -> conflicts; the server blocks the entire batch. "
        "Do not re-ask known origin unless the user explicitly intends to change it. "
        "Update an existing child using their traveler.id; never duplicate people. "
        "Omission is not false. Prices/budget: integer amount_minor in the stated currency's minor units. "
        "Never invent people, filters or prices. undo must be the sole operation, reverting the last successful edit; "
        "cancel stops processing: it neither rolls back the trip nor cancels orders."),
    ),
    "duration_contract": (
        "062ebbf1e244da256914c17ce02b456c7c2149d44277c420116c2dbeb18b31a8", str,
        ("Save explicit nights in upsert_stay.value.requested_nights even without check-in. "
        "Days are not nights: ask. Do not overwrite explicit dates without agreement."),
    ),
    "leg_mode_contract": (
        "7983c5f046ddda9ff3b7eea3bbf25840d6dd0d9e53435b5f477249d3a9422a21", str,
        ("set_leg_mode: target_id is an existing leg; value.mode=flight|train. "
        "Changing mode does not change cities, dates or route."),
    ),
    "group_contract": (
        "e2ef96ce9f7fa080a6e51a3a1e9a418e1abf49f01ac79ef2ea8314359fb042c0", str,
        ("Create travelers before upsert_group, then rooms. The full setter binds traveler_ids and group_id atomically. "
        "Never guess families: when the user explicitly forbids mixing but members are unknown, "
        "use an empty keep_separate=true group and ask precisely."),
    ),
    "room_constraint_contract": (
        "93ec93add6b0c501c4d10d1aac7085cf269a40777f97b3ce31cf9bca26bccc3a", str,
        ("Keep each room's terms separate: set_constraint.value {key,value,hard,stay_id,room_id}. "
        "room_id requires stay_id; only breakfast/refundable are room-scoped. Never merge two rooms' terms into a stay-wide condition."),
    ),
    "room_membership_contract": (
        "0e8f52883505e4a67ddd3762bc35ffcc9d5e567d431ce4c4d1356cdba443ff01", str,
        ("For an existing room use set_room_members: target_id=that room ID, value.stay_id=its stay ID, "
        "value.traveler_ids=the full membership of ONLY that room; other rooms remain. Run add_traveler first. "
        "upsert_stay.value.rooms replaces ALL rooms: never submit only the edited room there or invent new IDs for existing rooms."),
    ),
    "selection_contract": (
        "6961725a7c9defded7ca54477bc5cb39e663af128c3b5d900184166e50fb15a9", str,
        ("current_selected_plan is the user's current preview selection: hotel identities, no prices or purchase consent. "
        "On an explicit request to keep a selected hotel, use it unless destination/sources change. If trip.stays.hotel_id is unset, "
        "pin an explicitly preserved selection with replace_hotel and its hotel_id. first_shown_plan is first-display history, "
        "not current selection. These references cannot confirm an old price or booking."),
    ),
    "constraint_contract": (
        "0293bba981182aebdde4c6d53286a00e560c8295f1ead68a472f12b17fe5ec72", dict,
        {
            "breakfast": "boolean: breakfast included in this rate",
            "refundable": "boolean: this rate's refundability",
            "pets_allowed": "boolean: hotel property",
            "pets": {"kind": "dog|cat", "weight_kg": "known positive number"},
            "nonstop": "boolean: true excludes connections/technical stops on ALL flight legs; trip-wide, no stay_id/room_id. "
                "false permits connections; prefer clear_constraint nonstop to remove it.",
        },
    ),
    "route_contract": (
        "7fe69a1df98f08b9cee959e84fa532b788615b60b28c961e9eda6bc65a59b8ad", dict,
        {
            "round_trip": "Return to origin.",
            "one_way": "No return flight; never create it automatically.",
            "open_jaw": "End in another city: kind=open_jaw,end_city. Ending a flight there does not itself create a hotel stay.",
            "undecided": "Route ending undecided: ask; never invent a return.",
            "transit": "A connection without an overnight stay creates no hotel stay.",
        },
    ),
    "coverage_rules": (
        "419f32d9301e6bf64f4b6c1fc7149429ed618114a947aa65624e051cf6b50206", list,
        [
            "Extract only current_message edits; never replay saved requirements.",
            "Never default breakfast, refundability, a different hotel or origin city.",
            "clear_preference removes by key; 'any' does not preserve a prior morning preference.",
            "If allowed operations cannot express a requirement, ask a precise human question in unknowns; invent no operation.",
            "set_search_policy.guest_nationality requires explicitly stated citizenship, ISO-2; never infer it from language or destination.",
        ],
    ),
    "schema_authoritative": (
        "7373a89b0ac5073815509b4361d7c65a0f28181157a29ff1a8904c730e7e3585", str,
        "The required system JSON Schema defines exact operation types, value fields and allowed IDs; invent no others.",
    ),
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(",", ":")).encode("utf-8")


def _fingerprint(value: Any) -> str | None:
    try:
        return hashlib.sha256(_canonical(value)).hexdigest()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return None


def compact_operation_guide(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Copy context and translate only exact known code-static guide values.

    Every unrelated field and unrecognized value is preserved. Metadata contains
    field names, hashes and sizes only, never user text or domain identifiers.
    """
    result = deepcopy(context)
    metadata: dict[str, Any] = {"version": _VERSION, "changed_fields": [],
        "unrecognized_fields": [], "saved_value_utf8_bytes": 0}
    guide = result.get("operation_contract")
    if not isinstance(guide, dict):
        return result, metadata
    for key, (expected_hash, expected_type, translated) in _CLAUSES.items():
        if key not in guide:
            continue
        original = guide[key]
        if type(original) is not expected_type or _fingerprint(original) != expected_hash:
            metadata["unrecognized_fields"].append(key)
            continue
        replacement = deepcopy(translated)
        before, after = len(_canonical(original)), len(_canonical(replacement))
        # Fail safe if a later reviewed translation does not actually save space.
        if after >= before:
            continue
        guide[key] = replacement
        metadata["changed_fields"].append(key)
        metadata["saved_value_utf8_bytes"] += before - after
    return result, metadata
