from pathlib import Path
import re

STATIC = Path(__file__).resolve().parents[1] / 'live_genesis' / 'static'
# stylesheet -> (number of bundled fonts, the first bytes of that font format)
SHEETS = {'style.css': (2, b'\x00\x01\x00\x00'), 'archive.css': (3, b'wOF2')}


def test_fonts_resolve_offline_with_licenses():
    for sheet, (count, magic) in SHEETS.items():
        css = (STATIC / sheet).read_text(encoding='utf-8')
        assert 'fonts.googleapis.com' not in css, sheet
        assert 'fonts.gstatic.com' not in css, sheet
        # Every url() in the sheet is a bundled font: nothing remote, nothing outside fonts/.
        assert re.findall(r"url\(['\"]?([^)'\"]+)", css) == re.findall(r"url\(['\"]?(fonts/[^)'\"]+)", css), sheet
        fonts = re.findall(r"url\(['\"]?(fonts/[^)'\"]+)", css)
        assert len(fonts) == count, sheet
        for font in fonts:
            path = STATIC / font
            assert path.read_bytes()[:4] == magic, font
            assert 'SIL OPEN FONT LICENSE' in path.with_name(path.stem + '-OFL.txt').read_text(), font


def test_archive_fonts_are_small_latin_subsets_and_every_font_file_is_used():
    used = set()
    for sheet in SHEETS:
        used |= set(re.findall(r"url\(['\"]?fonts/([^)'\"]+)", (STATIC / sheet).read_text(encoding='utf-8')))
    files = {p.name for p in (STATIC / 'fonts').iterdir() if not p.name.endswith('-OFL.txt')}
    assert files == used
    added = sum((STATIC / 'fonts' / name).stat().st_size for name in files if name.endswith('.woff2'))
    assert added < 300_000
