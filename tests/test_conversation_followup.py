import pytest

from otravel.conversation_followup import followup_kind, last_owned_country_topic


@pytest.mark.parametrize('text,kind', [('давай','assent'),('ДАВАЙ!','assent'),('да','assent'),
    ('ты поможешь или нет?','help'),('сам разбери','help'),('помоги с поездкой','help')])
def test_complete_followup_cues_only(text, kind):
    assert followup_kind(text) == kind


@pytest.mark.parametrize('text', ['давай из Москвы','давай, добавь ребёнка','давай в Сербию наверное',
    'не помогай','не продолжай','продолжай','давай подожди','давай бюджет 50000','сам разбери и измени даты'])
def test_mixed_facts_edits_negation_and_controls_are_not_swallowed(text):
    assert followup_kind(text) is None


def test_country_topic_requires_matching_server_canonical_packet():
    good={'conversation':{'speech_act':'ASK','subject':{'kind':'country','name':'Сербия','country_code':'RS'}}}
    subject = last_owned_country_topic(good)
    assert {key:subject[key] for key in ('kind','name','country_code')} == {'kind':'country','name':'Сербия','country_code':'RS'}
    assert last_owned_country_topic({'reply':'Сербия'}) is None
    assert last_owned_country_topic({'conversation':{'subject':{'kind':'country','name':'Стамбул','country_code':'RS'}}}) is None
