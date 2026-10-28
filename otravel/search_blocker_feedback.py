"""Canonical final feedback for two blocked current search outcomes.

No operations, allocation, offer selection, pricing, tools or budget relaxation.
The exact current partial-plan trip/version is required; raw messages/IDs never
become user-facing text.
"""
from __future__ import annotations
from copy import deepcopy
from .domain import TripState
ALLOCATION_ID="HARNESS-004-N80-UNASSIGNED-TRAVELER-NEXT-STEP-010-001"
BUDGET_ID="HARNESS-004-N80-HIDDEN-BUDGET-VIOLATION-012-001"

def _money(value,currency):
    return f"{value//100:,}".replace(","," ")+f",{value%100:02d} {currency}"

def canonical_search_blocker_feedback(state,result):
    empty={"text":"","question":None,"pending_context":None,"summary":{"codes":[],"finding_ids":[]}}
    if not isinstance(result,dict)or result.get("plans"):
        return empty
    try:
        before=TripState.model_validate(state.model_dump(mode="json")if hasattr(state,"model_dump")else state)
    except(ValueError,TypeError):
        return empty
    partial=result.get("partial_plan")
    if(not isinstance(partial,dict)or partial.get("trip_id")!=before.trip_id
        or type(partial.get("state_version"))is not int or partial["state_version"]!=before.state_version):
        return empty
    violations=result.get("violations")
    violations=violations if isinstance(violations,list)else[]
    codes={v.get("code")for v in violations if isinstance(v,dict)and isinstance(v.get("code"),str)}
    text=[];question=None;context=None;summary={"codes":[],"finding_ids":[]}
    if "changed_allocation"in codes:
        unassigned=[]
        ids={person.id for person in before.travelers}
        for stay in before.stays:
            assigned={identifier for room in stay.rooms for identifier in room.traveler_ids}
            missing=ids-assigned
            if stay.rooms and assigned and missing:
                unassigned.append((stay,sorted(missing)))
        if unassigned:
            text.append("Не все участники распределены по сохранённым номерам. Полный план не собран; тестовое оформление пока недоступно.")
            if len(unassigned)==1:
                stay,missing=unassigned[0]
                question=(f"Как распределить всех участников по номерам в городе «{stay.city}»? "
                    "Укажите, кто живёт в каждом номере; если нужен ещё номер, укажите это явно.")
                context={"field":"stay.rooms","target_id":stay.id,"room_reallocation_required":True,
                    "requested_room_count":len(stay.rooms),"unassigned_traveler_ids":missing,"force_clarification":True}
            else:
                question=("Для какой остановки нужно уточнить распределение по номерам? "
                    "Укажите город или остановку и кто будет жить в каждом номере.")
                context={"field":"stay.rooms","target_id":None,"room_target_selection_required":True,
                    "room_reallocation_required":True,"force_clarification":True}
            summary["codes"].append("unassigned_travelers")
            summary["finding_ids"].append(ALLOCATION_ID)
    if ("budget_exceeded"in codes and before.budget is not None
            and before.budget.currency in {"RUB", "USD", "EUR"}):
        for issue in violations:
            if not isinstance(issue,dict)or issue.get("code")!="budget_exceeded":
                continue
            details=issue.get("details");details=details if isinstance(details,dict)else{}
            total,limit=details.get("total_minor"),details.get("budget_minor")
            if(type(total)is not int or type(limit)is not int or not 0<=limit<total
                or limit!=before.budget.amount_minor or partial.get("total_minor")!=total
                or partial.get("currency")!=before.budget.currency):
                continue
            currency=before.budget.currency
            text.append(f"Расчётная сумма {_money(total,currency)} превышает сохранённый бюджет "
                f"{_money(limit,currency)} на {_money(total-limit,currency)}. "
                "Подходящий план не собран. Для продолжения нужно найти более дешёвые варианты или явно изменить условия; бюджет автоматически не увеличен.")
            summary["codes"].append("budget_exceeded")
            summary["finding_ids"].append(BUDGET_ID)
            break
    return {"text":"\n\n".join(text),"question":question,"pending_context":deepcopy(context),"summary":summary}
