"""kubernetes cli entrypoint."""

import os
import sys

import structlog
import typer
from pathlib import Path
from typing import List, Optional

if sys.version_info >= (3, 8):
    from functools import cached_property
else:
    from cached_property import cached_property

from meltano.edk.extension import DescribeFormat
from meltano.edk.logging import default_logging_config, parse_log_level
from kubernetes_ext.extension import Kubernetes

APP_NAME = "kubernetes"

log = structlog.get_logger(APP_NAME)
ext = Kubernetes()

typer.core.rich = None  # remove to enable stylized help output when `rich` is installed
app = typer.Typer(
    name=APP_NAME,
    pretty_exceptions_enable=False,
)


@app.command()
def initialize(
    force: bool = typer.Option(False, help="Force initialization (if supported)"),
) -> None:
    """Initialize the kubernetes plugin."""
    try:
        ext.initialize(force)
    except Exception:
        log.exception(
            "initialize failed with uncaught exception, please report to maintainer",
        )
        sys.exit(1)


@app.command()
def render(
    kustomize: bool = typer.Option(
        True,
        "-k",
        "--kustomize",
        help="Output manifests as Kustomize base",
    ),
    destination: str = typer.Option(
        Path(os.environ["MELTANO_PROJECT_ROOT"]).resolve()
        / "orchestrate"
        / "kubernetes",
        "-D",
        "--destination",
        help="Output destination",
    ),
    schedule_ids: Optional[List[str]] = typer.Argument(None),
) -> None:
    """Renders meltano schedules as kubernetes CronJobs, outputting manifests as a kustomize base to a destination directory"""
    if kustomize:
        ext.render_kustomize(Path(destination).resolve(), set(schedule_ids or ()))
    else:
        log.exception("Only kustomize output is supported at this time")


@app.command(name="list")
def list_command() -> None:
    ext.list()


@app.command()
def describe(
    output_format: DescribeFormat = typer.Option(
        DescribeFormat.text,
        "--format",
        help="Output format",
    ),
) -> None:
    """Describe the available commands of this extension."""
    try:
        typer.echo(ext.describe_formatted(output_format))
    except Exception:
        log.exception(
            "describe failed with uncaught exception, please report to maintainer",
        )
        sys.exit(1)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    environment: str = typer.Option(
        envvar="MELTANO_ENVIRONMENT",
        help="The meltano environment for which to generate job manifests",
    ),
    log_level: str = typer.Option("INFO", envvar="LOG_LEVEL"),
    log_timestamps: bool = typer.Option(
        False,
        envvar="LOG_TIMESTAMPS",
        help="Show timestamp in logs",
    ),
    log_levels: bool = typer.Option(
        False,
        "--log-levels",
        envvar="LOG_LEVELS",
        help="Show log levels",
    ),
    meltano_log_json: bool = typer.Option(
        False,
        "--meltano-log-json",
        envvar="MELTANO_LOG_JSON",
        help="Log in the meltano JSON log format",
    ),
) -> None:
    """Meltano orchestrator extension for running meltano in Kubernetes"""
    default_logging_config(
        level=parse_log_level(log_level),
        timestamps=log_timestamps,
        levels=log_levels,
        json_format=meltano_log_json,
    )
    ext.environment = environment
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
