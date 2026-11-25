"""Compatibility shim: the brand generator moved to ``gen_brand_suite.py``.

The old version rendered only a bare four-point star with Pillow. The suite
generator derives the mark, lockups, favicon and every raster icon from one
geometry source, so keep this entry point as an alias for scripts or habits
that still call the old name.
"""

from __future__ import annotations

from gen_brand_suite import main

if __name__ == "__main__":
    raise SystemExit(main())
