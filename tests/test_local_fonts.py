from pathlib import Path
import re


def test_fonts_resolve_offline_with_licenses():
    static = Path(__file__).resolve().parents[1] / 'live_genesis' / 'static'
    css = (static / 'style.css').read_text(encoding='utf-8')
    assert 'fonts.googleapis.com' not in css
    assert 'fonts.gstatic.com' not in css
    fonts = re.findall(r"url\(['\"]?(fonts/[^)'\"]+)", css)
    assert len(fonts) == 2
    for font in fonts:
        path = static / font
        assert path.read_bytes()[:4] == b'\x00\x01\x00\x00'
        assert 'SIL OPEN FONT LICENSE' in path.with_name(path.stem + '-OFL.txt').read_text()
