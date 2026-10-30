"""One captured owned ASK discarded into no-change; no model/source inference."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from otravel.conversational import owned_room_target_reask

ROW=json.loads((Path(__file__).parents[1]/'fixtures/regression/final40-owned-room-reask-20261028.json').read_text(encoding='utf-8'))
QUESTION=ROW['before_pending']['text']


def setup():
    state=deepcopy(ROW['before_trip'])
    session={'session_id':'controlled-owned','trip_id':state['trip_id'],'state_version':state['state_version'],
        'state':deepcopy(state),'status':ROW['before_status'],'waiting_reason':ROW['before_waiting'],
        'pending_question':deepcopy(ROW['before_pending'])}
    return session,state


@pytest.mark.parametrize('text',[ROW['user'],'Уточни, каких участников ещё нужно распределить.'])
def test_exact_owned_clarification_is_repeated_without_touching_state_or_pending(text):
    session,state=setup();before=deepcopy((session,state))
    assert owned_room_target_reask(session,state,text,[QUESTION])==QUESTION
    assert (session,state)==before


@pytest.mark.parametrize('change',[
    'session-version','pending-version','bool-version','foreign-trip','foreign-context','foreign-target',
    'context-version','other-field','paused','pause-reason','missing-question','different-text',
    'different-questions','false-room-flag','missing-count','other-unknown','duplicate-unknown',
    'different-topic','different-count','new-budget',
])
def test_stale_foreign_paused_or_other_question_cannot_reuse_owned_clarifier(change):
    session,state=setup();p=session['pending_question'];ctx=p['context'];text=ROW['user'];unknowns=[QUESTION]
    if change=='session-version':session['state_version']-=1
    elif change=='pending-version':p['state_version']-=1
    elif change=='bool-version':p['state_version']=True
    elif change=='foreign-trip':session['trip_id']='foreign'
    elif change=='foreign-context':ctx['trip_id']='foreign'
    elif change=='foreign-target':p['target_id']='foreign-stay'
    elif change=='context-version':ctx['based_state_version']=state['state_version']-1
    elif change=='other-field':p['field']='stay.check_out'
    elif change=='paused':session['waiting_reason']='user_pause'
    elif change=='pause-reason':p['reason']='user_pause'
    elif change=='missing-question':p['question_id']=''
    elif change=='different-text':p['text']='Цена 100 рублей подтверждена?'
    elif change=='different-questions':p['questions']=['Где завтрак?']
    elif change=='false-room-flag':ctx['room_target_selection_required']=False
    elif change=='missing-count':ctx.pop('requested_room_count')
    elif change=='other-unknown':unknowns=['Какова цена отеля?']
    elif change=='duplicate-unknown':unknowns=[QUESTION,QUESTION]
    elif change=='different-topic':text='Какая погода в городе?'
    elif change=='different-count':text=ROW['user'].replace('Нужны 1 номера','Нужны 2 номера')
    elif change=='new-budget':text='Бюджет 50000 рублей. Уточни, каких участников ещё нужно распределить.'
    before=deepcopy((session,state))
    assert owned_room_target_reask(session,state,text,unknowns) is None
    assert (session,state)==before
