"""Shared env-file loading helper for the ``run`` and ``stdio`` subcommands.

Both transports must apply the same load-or-warn semantics (don't overwrite
existing env vars, warn instead of failing on a missing file) so they cannot
drift on env handling.
"""

from __future__ import annotations

import sys
from pathlib import Path


def load_env_file(env_file: str | None) -> None:
    """Load a dotenv file into ``os.environ`` if the path exists.

    No-op when *env_file* is ``None``.  Emits a warning to stderr (but does
    not raise) when the path is set but the file does not exist.
    """
    if env_file is None:
        return

    env_path = Path(env_file)
    if env_path.is_file():
        from dotenv import load_dotenv

        load_dotenv(str(env_path), override=False)
    else:
        print(f"warn: env file not found: {env_path}", file=sys.stderr)


__all__ = ["load_env_file"]
