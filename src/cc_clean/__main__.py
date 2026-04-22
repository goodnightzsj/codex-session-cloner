"""Module entrypoint for `python -m cc_clean`."""

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
