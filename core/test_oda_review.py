"""ODA File Converter only as an independent local check in tests.

ODA is not part of ChickenBananaCAD (license): the product never looks for
it.  Tests that compare our DWG output with ODA's reading find it here and
skip that comparison where it is not installed (the server).
"""
from pathlib import Path

_ODA_CANDIDATES = (
    "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
    "/Applications/ODA File Converter.app/Contents/MacOS/ODAFileConverter",
)


def find_oda():
    for candidate in _ODA_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None
