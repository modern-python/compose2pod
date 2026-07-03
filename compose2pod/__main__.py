"""python -m compose2pod entry point."""

import sys

from compose2pod.cli import main


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    sys.exit(main())
