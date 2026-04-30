"""Top-level `ifa` CLI."""
from __future__ import annotations

import typer

from ifa.cli.healthcheck import healthcheck_command

app = typer.Typer(no_args_is_help=True, add_completion=False, help="iFA China Market Report System")


@app.callback()
def _root() -> None:
    """iFA China Market Report System."""


app.command("healthcheck")(healthcheck_command)


if __name__ == "__main__":
    app()
