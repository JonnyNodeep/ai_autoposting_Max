#!/usr/bin/env python3
"""Generate all 20 postcard motion overlays into fx_overlays/."""
from __future__ import annotations

from app.application.pipeline.postcards.motion import (
    ALL_OVERLAYS,
    FX_DIR,
    ensure_overlays,
)


def main() -> None:
    paths = ensure_overlays(ALL_OVERLAYS, force=True)
    print(f"dir={FX_DIR}")
    print(f"generated={len(paths)}")
    for p in sorted(paths):
        print(f"  {p.name}\t{p.stat().st_size}")


if __name__ == "__main__":
    main()
