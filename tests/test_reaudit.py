"""Regression tests for the security re-audit: invisible characters and strict export encoding.
Offline and deterministic. The strict parsing of a published file is covered in tests/test_snapshot_archive_only.py.
"""
import unicodedata

import pytest

from live_genesis import guards, snapshot

ORDINARY = 'Zoë 日本語 مرحبا \U0001F469‍\U0001F469‍\U0001F467 ✌️ 1️⃣ na‌me ℹ️'
INVISIBLE = ['\U000e0041', '\U000e007f', '\U000e0100', '͏', '᠎', '︀', '︍', '￹', '￻', '⁪', '⁯',
             '￿', '﷐', '\U0010fffe', '', '\U000f0000', 'ᅟ', 'ᅠ', 'ㅤ', 'ﾠ', '­', '​',
             '⁠', '﻿', '͸']


def test_character_rule_by_category_and_what_stays_allowed():
    assert not guards.has_forbidden_character(ORDINARY)
    assert not guards.has_forbidden_character('line one\n\tline two\r\nline three ' + ORDINARY, multiline=True)
    # Every format (Cf), private-use (Co) and surrogate (Cs) character except the two joiners.
    for code in range(0x110000):
        ch = chr(code)
        if unicodedata.category(ch) in ('Cf', 'Co', 'Cs') and ch not in '‌‍':
            assert guards.has_forbidden_character('a' + ch + 'b', multiline=True), hex(code)
    for code in list(range(0xE0000, 0xE1000, 37)) + [0xE0FFF, 0xFDD0, 0xFFFE, 0x1FFFF]:   # tags, selectors supplement, noncharacters
        assert guards.has_forbidden_character('a' + chr(code) + 'b'), hex(code)
    # Variation selectors: FE00-FE0D never; FE0E/FE0F only directly after a symbol, digit or punctuation mark.
    for code in range(0xFE00, 0xFE0E):
        assert guards.has_forbidden_character('❤' + chr(code))
    for fine in ('❤️', '❤︎', '#️⃣', '7️⃣', 'ℹ️', '\U0001F3F3️‍\U0001F308'):
        assert not guards.has_forbidden_character(fine), ascii(fine)
    for refused in ('️', 'a️', ' ️', 'é️', '❤️️'):
        assert guards.has_forbidden_character(refused), ascii(refused)


@pytest.mark.parametrize('split', ['͏', '‍', '‌', '​', '­', '⁠', '️', '\U000e0020', '᠎', 'ㅤ', '￹'])
def test_pattern_guards_see_through_invisible_characters(split):
    assert guards.solicitation_reason(f'see pay{split}pal.me/garden') == 'contains a payment or donation link'
    assert guards.solicitation_reason(f'see paypal{split}.{split}me/garden') == 'contains a payment or donation link'
    assert guards.pii_reason(f'sk-abcdefgh{split}ijklmnopqrstuvwx') == 'looks like an API key or credential'
    assert guards.pii_reason(f'write to someone{split}@example{split}.org') == 'looks like an email address'


def test_fold_strips_every_format_and_default_ignorable_code_point():
    for code in range(0x110000):
        if unicodedata.category(chr(code)) == 'Cf':
            assert guards._fold('a' + chr(code) + 'b') == 'ab', hex(code)
    for code in (0x034F, 0x115F, 0x1160, 0x17B4, 0x180B, 0x180F, 0x3164, 0xFE00, 0xFE0F, 0xFFA0, 0xFFF0, 0xE0000, 0xE0041, 0xE01EF, 0xE0FFF):
        assert guards._fold('a' + chr(code) + 'b') == 'ab', hex(code)


def test_an_export_cannot_contain_nan():
    with pytest.raises(ValueError):
        snapshot._encode({'value': float('nan')})
