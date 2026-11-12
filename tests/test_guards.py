"""Content guards: PII/secrets, money solicitation, spam/duplicates. Offline; no model calls."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_genesis import guards
from live_genesis.app import app
from live_genesis.cli import demo
from live_genesis.core import agent_context, authenticate, open_round, overview, seed_agents, submit_action

POST={'action':'post','title':'Guard test','body':'A perfectly ordinary note about shared work.','topic':'engineering'}

# Synthetic values only: none of these is a real account, key or address.
FAKE_SK='sk-'+'a1B2c3D4'*5
FAKE_LIVE='live_'+'Zx9-_Qw3'*5
BTC_GENESIS='1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa'  # public, well-known genesis-block address
ETH_SHAPED='0x'+'52908400098527886E0F7030069857D2E4169EE7'
IBAN_EXAMPLE='GB82 WEST 1234 5698 7654 32'      # the standard documentation example

ALLOWED=[
    'In 2024 we ran 1200 trials and 37 of them failed; the 2025 target is 10000.',
    'Request $100 for compute, or 5,000 credits, split 50/50 across 3 rounds.',
    'Budget: $1,000.50 of the proposed $4,000. No payment collection is authorized.',
    'We could ask sponsors for donations someday, perhaps via a crowdfunding site, but only with operator approval.',
    'Version 3.10.12 was released on 2026-11-12 at 10:30; see section 4.2.1, pages 100-120.',
    'Scores were 100 200 300 and then 1000 2000 3000 4000 across the years 2021 2022 2023.',
    'The secret: collaboration. A password should never be shared, and a token is just a unit of text.',
    'Call the next agent in the list and text 2 others; call 3 volunteers.',
    'Read https://example.org/docs/a-long-path/with-many-words-and-sections/Chapter-12-Results for details.',
    'Ping @Orbit about the index. Ratio 3:1, range 0x10-0xFF, commit abc1234, checksum pending.',
    'Supercalifragilisticexpialidocious pseudopseudohypoparathyroidism is internationalization.',
    'Yes yes yes!',
    'Stripes, a cashier app and paypal-like systems are out of scope; Patreon and Venmo are only named here.',
    'Population grew from 7,900,000,000 to 8,100,000,000 while latency fell to 0.000125 seconds.',
]


@pytest.mark.parametrize('text',ALLOWED)
def test_ordinary_prose_and_numbers_pass(text):
    assert guards.content_reason(text) is None
    assert guards.degenerate_reason(text) is None


@pytest.mark.parametrize('text,category',[
    ('Write to astra.bot+lab@example-mail.org for the notes.','email'),
    ('Reach me on +44 20 7946 0958 any time.','phone'),
    ('My number is (555) 123-4567.','phone'),
    ('Number: 555-123-4567','phone'),
    ('phone 0612345678','phone'),
    ('Card 4111 1111 1111 1111 exp 12/29','payment card'),
    ('5500-0000-0000-0004','payment card'),
    ('use 378282246310005 please','payment card'),
    ('The key is '+FAKE_SK,'API key'),
    ('My bearer key: '+FAKE_LIVE,'API key'),
    ('Authorization: Bearer abcdefghijklmnop1234','API key'),
    ('AKIAIOSFODNN7EXAMPLE','API key'),
    ('-----BEGIN RSA PRIVATE KEY-----','API key'),
    ('password: hunter2!','password'),
    ('The admin password is Tr0ub4dor&3.','password'),
    ('API_KEY=abcd1234','password'),
    ('digest d41d8cd98f00b204e9800998ecf8427e here','long secret'),
    ('blob dGhpcyBpcyBhIHNlY3JldCB2YWx1ZSAxMjM0NTY3ODkw ok','long secret'),
    ('ａstra@example.org','email'),             # full-width letter
    ('astra@exa​mple.org','email'),            # zero-width space
])
def test_pii_and_secrets_are_blocked(text,category):
    reason=guards.pii_reason(text)
    assert reason is not None and category in reason


def test_card_guard_uses_luhn():
    assert guards.pii_reason('Card 4111 1111 1111 1112') is None   # fails the checksum
    assert guards.pii_reason('Order 4111111111111111 shipped') is not None


@pytest.mark.parametrize('text,category',[
    ('Donate at paypal.me/livegenesis','payment or donation link'),
    ('https://www.PayPal.com/donate?id=1','payment or donation link'),
    ('venmo.com/u/someone','payment or donation link'),
    ('Send to https://cash.app/$genesis','payment or donation link'),
    ('Support us: patreon.com/genesis','payment or donation link'),
    ('https://www.gofundme.com/f/agents','payment or donation link'),
    ('buymeacoffee.com/astra','payment or donation link'),
    ('https://buy.stripe.com/test_abc','payment or donation link'),
    ('https://checkout.stripe.com/c/pay/cs_x','payment or donation link'),
    ('BTC: '+BTC_GENESIS,'wallet'),
    ('bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4','wallet'),
    ('ETH '+ETH_SHAPED,'wallet'),
    ('bitcoin:anything','wallet'),
    ('Wire to '+IBAN_EXAMPLE,'IBAN'),
    ('GB82WEST12345698765432','IBAN'),
])
def test_money_solicitation_is_blocked(text,category):
    reason=guards.solicitation_reason(text)
    assert reason is not None and category in reason
    assert guards.content_reason(text)==reason


def test_invalid_checksums_are_not_treated_as_accounts():
    assert guards.solicitation_reason('GB83 WEST 1234 5698 7654 32') is None
    assert guards.solicitation_reason(BTC_GENESIS[:-1]+'b') is None


def test_degenerate_text():
    assert 'character' in guards.degenerate_reason('aaaaaaaaaaaaaaaaaaaaaaaa!!')
    assert 'word' in guards.degenerate_reason('spam spam spam SPAM spam, spam. eggs spam')
    assert guards.degenerate_reason('We have streets but no footsteps, memory but no weather.') is None


def test_normalize_ignores_case_whitespace_and_punctuation():
    assert guards.normalize('  Hello,   WORLD!!\n(again) ')==guards.normalize('hello world again')=='hello world again'
    assert guards.normalize('hello world')!=guards.normalize('hello worlds')


@pytest.fixture
def world(isolated_db,monkeypatch):
    """Seeded agents with the duplicate guard on (conftest turns it off for older tests)."""
    monkeypatch.setenv('LIVE_DUPLICATE_GUARD','1')
    return [authenticate(i['key']) for i in seed_agents()]


SECRETS=['astra@example.org',FAKE_SK,FAKE_LIVE,'4111 1111 1111 1111','hunter2!',BTC_GENESIS,'paypal.me/livegenesis',IBAN_EXAMPLE]


@pytest.mark.parametrize('verb',['post','propose'])
@pytest.mark.parametrize('field',['title','body'])
@pytest.mark.parametrize('secret',SECRETS)
def test_rejection_is_422_names_category_and_never_echoes(world,verb,field,secret):
    text=('password: ' if secret=='hunter2!' else 'See ')+secret
    action={'action':verb,'title':'Note','body':'Some ordinary body text.',field:text}
    with pytest.raises(HTTPException) as err:
        submit_action(world[0]['id'],action)
    assert err.value.status_code==422
    detail=err.value.detail
    assert detail.startswith('Content rejected: ')
    assert secret not in detail and secret.split()[-1] not in detail and secret[:8] not in detail
    state=overview()
    assert state['metrics']['posts']==0 and state['metrics']['recorded_actions']==0


def test_rejected_content_uses_no_quota_key_or_event(world):
    a=world[0]['id']
    for bad in ({**POST,'body':'mail me: astra@example.org'},{**POST,'body':'ha '*40},
                {'action':'propose','kind':'fundraising','title':'Fund us','body':'Pay at gofundme.com/f/x','requested_usd':50}):
        with pytest.raises(HTTPException) as err:
            submit_action(a,bad,idempotency_key='guard-key-0001')
        assert err.value.status_code==422
    assert agent_context(a)['round']['actions_used']==0
    state=overview()
    assert state['metrics']['posts']==0 and state['metrics']['recorded_actions']==0 and not state['events']
    # Quota and the idempotency key are both still available for a clean action.
    ok=submit_action(a,POST,idempotency_key='guard-key-0001')
    assert ok['id']==1 and 'replayed' not in ok
    assert agent_context(a)['round']['actions_used']==1


def test_fundraising_proposal_in_words_is_allowed(world):
    out=submit_action(world[0]['id'],{'action':'propose','kind':'fundraising','title':'Sponsor a puzzle tournament',
        'body':'Ask the operator to seek $100 in sponsorship or donations for compute. No outreach or payment collection is authorized yet.',
        'requested_usd':100})
    assert out['id']==1


def test_duplicates_are_rejected_with_409(world):
    astra,moss=world[0]['id'],world[1]['id']
    first=submit_action(astra,POST)
    open_round()
    shouted={**POST,'title':'  GUARD   test!! ','body':'a perfectly ordinary note -- about shared work'}
    for who,phrase in ((astra,'you already published'),(moss,"another agent's recent content")):
        with pytest.raises(HTTPException) as err:
            submit_action(who,shouted,idempotency_key='dup-key-0001')
        assert err.value.status_code==409 and phrase in err.value.detail
        assert POST['body'] not in err.value.detail
        assert agent_context(who)['round']['actions_used']==0
    # The same text as a proposal is still a duplicate; different text is fine.
    with pytest.raises(HTTPException) as err:
        submit_action(astra,{'action':'propose','title':POST['title'],'body':POST['body']})
    assert err.value.status_code==409
    assert submit_action(moss,{**POST,'body':POST['body']+' With one addition.'},idempotency_key='dup-key-0001')['id']==first['id']+1
    assert overview()['metrics']['posts']==2


def test_idempotent_replay_is_not_reported_as_duplicate(world):
    a=world[0]['id']
    first=submit_action(a,POST,idempotency_key='replay-key-01')
    assert submit_action(a,POST,idempotency_key='replay-key-01')=={**first,'replayed':True}


def test_other_agents_duplicates_only_checked_in_recent_window(world,monkeypatch):
    monkeypatch.setattr(guards,'RECENT_WINDOW',2)
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND','10')
    astra,moss=world[0]['id'],world[1]['id']
    submit_action(astra,POST)
    for n in ('first','second'):
        submit_action(astra,{**POST,'body':f'The {n} follow-up on the shared work.'})
    assert submit_action(moss,POST)['id']==4          # outside the window for other agents
    with pytest.raises(HTTPException):
        submit_action(astra,POST)                     # an agent's own history is always checked


def test_duplicate_guard_switch_leaves_other_guards_on(world,monkeypatch):
    monkeypatch.setenv('LIVE_DUPLICATE_GUARD','0')
    monkeypatch.setenv('LIVE_ACTIONS_PER_ROUND','3')
    a=world[0]['id']
    submit_action(a,POST); submit_action(a,POST)
    with pytest.raises(HTTPException) as err:
        submit_action(a,{**POST,'body':'astra@example.org'})
    assert err.value.status_code==422


def test_api_returns_category_without_echo(isolated_db,monkeypatch):
    monkeypatch.setenv('LIVE_DUPLICATE_GUARD','1')
    auth={'Authorization':'Bearer '+seed_agents()[0]['key']}
    client=TestClient(app)
    bad=client.post('/api/agent/action',json={**POST,'body':'Use '+FAKE_SK},headers=auth)
    assert bad.status_code==422 and bad.json()=={'detail':'Content rejected: looks like an API key or credential'}
    # An agent pasting its own bearer key is stopped too, and the response does not repeat it.
    own=client.post('/api/agent/action',json={**POST,'body':'my key is '+auth['Authorization'][7:]},headers=auth)
    assert own.status_code==422 and auth['Authorization'][7:] not in own.text
    assert client.get('/api/agent/context',headers=auth).json()['round']['actions_used']==0
    assert client.post('/api/agent/action',json=POST,headers=auth).status_code==200
    assert overview()['metrics']['posts']==1


def test_demo_still_succeeds_with_guards_on(isolated_db,monkeypatch):
    monkeypatch.setenv('LIVE_DUPLICATE_GUARD','1')
    demo()
    state=overview()
    assert state['metrics']['posts']==7 and state['metrics']['proposals']==3
    assert state['mode']=='DEMO'
    assert agent_context(1)['round']['actions_used']==0
