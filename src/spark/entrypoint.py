"""Console entrypoint with a fast path for ``spark --version``.

The Typer app imports every command module and its help machinery. That is the
right cost for real commands, but it made the version smoke check pay the full
CLI startup bill. Keep the fast path here so packaging still exposes one
``spark`` executable and normal commands behave exactly as before.
"""
from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in {"--version", "-V"}:
        from spark import __version__
        from spark.brand import version_line

        sys.stdout.write(version_line(__version__) + "\n")
        return
    from spark.cli import app

    app()


if __name__ == "__main__":  # pragma: no cover
    main()
