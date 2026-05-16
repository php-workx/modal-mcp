"""``modal-mcp doctor`` — run partial diagnostic checks on the installation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import ClassVar


class DoctorCommand:
    """Run diagnostic checks on the installation."""

    name: ClassVar[str] = "doctor"

    @classmethod
    def register(
        cls, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]
    ) -> None:
        parser = subparsers.add_parser(
            cls.name,
            help="Run diagnostic checks on the installation.",
        )
        parser.add_argument(
            "--env-file",
            metavar="PATH",
            default=None,
            help=(
                "Path to a .env file to probe. "
                "Defaults to '.env' in the current directory."
            ),
        )

    @classmethod
    def run(cls, args: argparse.Namespace) -> int:
        from modal_mcp.doctor import CheckStatus, run_doctor

        env_file_arg: str | None = getattr(args, "env_file", None)
        env_file: Path | None = (
            Path(env_file_arg).expanduser().absolute()
            if env_file_arg is not None
            else None
        )

        report = run_doctor(env_file=env_file)

        prefix = {
            CheckStatus.OK: "ok  ",
            CheckStatus.WARN: "warn",
            CheckStatus.FAIL: "FAIL",
        }

        for item in report.items:
            line = f"{prefix[item.status]} {item.message}"
            if item.status == CheckStatus.FAIL:
                print(line, file=sys.stderr)
            else:
                print(line)

        print()
        if report.has_failures:
            pass  # exit code already reflects failures via report.exit_code
        elif report.has_warnings:
            print("Partial ready: some items need attention (see warnings above).")
        else:
            print("All checks passed.")

        # Surface DiagnosticReport.exit_code directly so callers (CI, shell
        # pipelines) can distinguish: 0 = all OK, 3 = warnings only (partial
        # ready), 1 = hard failures present.
        return report.exit_code


__all__ = ["DoctorCommand"]
