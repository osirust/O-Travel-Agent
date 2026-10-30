"""Narrow model-date omission for closed price-search plus date-recall text.

This is not a universal date-authority parser. Only two fully matched current
clauses are recognized. Typed calls, new/unbound stays, invalid field shapes,
and any additional date-edit or other clause retain normal validation.
"""
from __future__ import annotations
import re
from copy import deepcopy
from datetime import date
from .domain import MAX_REQUESTED_NIGHTS, TripState

FINDING_ID="HARNESS-004-UNASKED-RETURN-REPLAY-001"
_TYPED="\x1eOTRAVEL_TYPED_V1:"
_PRICE_REQUEST=re.compile(r"(?:(?:и\s+)?все[- ]таки\s+)?(?:пожалуйста[, ]+)?"
    r"(?:ищи|найди|поищи|подбери)\s+(?:под[еи]шевле|дешевле)(?:\s+пожалуйста)?")
_DATE_RECALL=re.compile(r"(?:напомни|покажи|назови)\s+(?:(?:текущие|сохраненные|наши)\s+)?даты"
    r"(?:,\s*а\s+то\s+я\s+(?:уже\s+)?запутал(?:ся|ась))?\??")
_DATE_KEYS={"check_in","check_out","requested_nights"}

def _closed_price_date_recall(text):
    if not isinstance(text,str) or not 0<len(text)<=20000 or text.startswith(_TYPED):
        return False
    key=" ".join(text.casefold().replace("ё","е").split())
    clauses=[part.strip()for part in re.split(r"[.;\n]",key)if part.strip()]
    return (len(clauses)==2 and sum(_PRICE_REQUEST.fullmatch(part)is not None for part in clauses)==1
        and sum(_DATE_RECALL.fullmatch(part)is not None for part in clauses)==1)

def _valid_date(value):
    if not isinstance(value,str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}",value)is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True

def guard_price_date_recall_operations(before_state,operations,text,*,model_generated=True):
    copied=[op.model_dump(mode="json")if hasattr(op,"model_dump")else deepcopy(op)for op in operations]
    meta={"notes":[],"unknowns":[],"conflicts":[]}
    if model_generated is not True or not _closed_price_date_recall(text):
        return copied,meta
    try:
        state=TripState.model_validate(before_state.model_dump(mode="json")if hasattr(before_state,"model_dump")else before_state)
    except (ValueError,TypeError):
        return copied,meta
    for operation in copied:
        if not isinstance(operation,dict)or operation.get("type")!="upsert_stay":
            continue
        value=operation.get("value")
        if not isinstance(value,dict):
            continue
        target=operation.get("target_id")or value.get("id")
        if not isinstance(target,str)or not target or value.get("id",target)!=target:
            continue
        existing=[stay for stay in state.stays if stay.id==target]
        if len(existing)!=1 or existing[0].check_in is None or existing[0].check_out is None:
            continue
        keys=_DATE_KEYS.intersection(value)
        if not keys or any(not _valid_date(value[key])for key in keys-{"requested_nights"}):
            continue
        if "requested_nights"in keys and (type(value["requested_nights"])is not int or not 1<=value["requested_nights"]<=MAX_REQUESTED_NIGHTS):
            continue
        operation["value"]={key:item for key,item in value.items()if key not in keys}
        meta["notes"].append({"finding_id":FINDING_ID,"code":"unasked_stay_dates_suppressed_for_price_date_recall",
            "stay_id":target,"fields":sorted(keys),"status":"suppressed","source":"closed_current_price_request_and_date_recall",
            "canonical_dates_changed":False})
    return copied,meta
