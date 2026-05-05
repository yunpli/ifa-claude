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

# SmartMoney module (lazy import — heavy deps)
from ifa.cli.smartmoney import app as sm_app  # noqa: E402
app.add_typer(sm_app, name="smartmoney")

# Ningbo short-term strategy module (independent from SmartMoney)
from ifa.cli.ningbo import app as ningbo_app  # noqa: E402
app.add_typer(ningbo_app, name="ningbo")

# Research family — equity research reports
from ifa.cli.research import app as research_app  # noqa: E402
app.add_typer(research_app, name="research")

# TA family — technical analysis & regime
from ifa.cli.ta import app as ta_app  # noqa: E402
app.add_typer(ta_app, name="ta")

# Stock Edge family — single-stock trade plan
from ifa.cli.stock import app as stock_app  # noqa: E402
app.add_typer(stock_app, name="stock")


if __name__ == "__main__":
    app()
