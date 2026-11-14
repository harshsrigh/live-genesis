"""Content guards: PII/secrets and money solicitation. Offline; no model calls."""
import pytest

from live_genesis import guards

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
