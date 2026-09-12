"""Allow ``python -m scruticell ...`` (used by MCP/batch subprocesses)."""

from scruticell.cli import main

if __name__ == "__main__":
    main()