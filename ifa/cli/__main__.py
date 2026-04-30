"""Top-level `ifa` CLI."""
from __future__ import annotations

import typer

from ifa.cli.generate import app as generate_app
from ifa.cli.healthcheck import healthcheck_command
from ifa.cli.jobs import app as jobs_app

app = typer.Typer(no_args_is_help=True, add_completion=False, help="iFA China Market Report System")


@app.callback()
def _root() -> None:
    """iFA China Market Report System."""


app.command("healthcheck")(healthcheck_command)
app.add_typer(jobs_app, name="job")
app.add_typer(generate_app, name="generate")


if __name__ == "__main__":
    app()
