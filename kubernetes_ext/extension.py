"""Meltano kubernetes extension."""
from __future__ import annotations

import subprocess
import json
import os
import sys
import shutil
from typing import Any, Callable, Iterable
from jinja2 import Environment, PackageLoader, select_autoescape
import typer
import structlog
from pathlib import Path

import importlib.metadata

version = importlib.metadata.version("kubernetes_ext")

if sys.version_info >= (3, 8):
    from functools import cached_property
else:
    from cached_property import cached_property

from meltano.edk import models
from meltano.edk.extension import ExtensionBase

log = structlog.get_logger()


class Kubernetes(ExtensionBase):
    """Extension implementing the ExtensionBase interface."""

    def __init__(self, kube_context=None, *args: Any, **kwargs: Any) -> None:
        """Initialize the extension."""
        super().__init__(*args, **kwargs)
        self.env = Environment(
            loader=PackageLoader("kubernetes_ext", "templates"),
            autoescape=select_autoescape(),
        )
        self.meltano_project_dir = Path(os.environ["MELTANO_PROJECT_ROOT"]).resolve()

    @cached_property
    def common_labels(self) -> dict[str, str]:
        """Instance labels to use on kubernetes resources."""
        return {
            "app.kubernetes.io/version": version,
            "app.kubernetes.io/component": "orchestrator",
            "app.kubernetes.io/part-of": "meltano",
            "app.kubernetes.io/managed-by": "kubernetes_ext",
        }

    @cached_property
    def label_selector(self) -> dict[str, str]:
        ignored_labels = {"app.kubernetes.io/version"}
        return ",".join(
            [
                f"{label}={value}"
                for label, value in self.common_labels.items()
                if label not in ignored_labels
            ]
        )

    @cached_property
    def meltano_schedule(self) -> dict[str, list[dict]]:
        """JSON schedule data from Meltano."""
        try:
            process = subprocess.run(
                ("meltano", "schedule", "list", "--format=json"),
                env=os.environ.copy(),
                cwd=self.meltano_project_dir,
                capture_output=True,
                text=True,
            )
            # noqa: DAR201
            process.check_returncode()
            return json.loads(process.stdout)["schedules"]  # type: ignore
        except subprocess.CalledProcessError:
            print("Fatal: Unable to query meltano schedule")
            if process:
                print(
                    f"meltano schedule list exited with code {process.returncode}: {process.stderr}"
                )
            raise typer.Exit(code=1)

    @cached_property
    def meltano_schedule_ids(self) -> set[str]:
        """Schedule IDs from Meltano."""
        # noqa: DAR201
        return {
            schedule["name"]
            for schedule in self.meltano_schedule["elt"] + self.meltano_schedule["job"]
        }

    def _get_elts_and_jobs(
        self, predicate: Callable[[str], bool]
    ) -> Iterable[dict[str, str]]:
        jobs = []
        for schedule in self.meltano_schedule["elt"] + self.meltano_schedule["job"]:
            if not schedule["cron_interval"]:
                log.warn(
                    f"No CronJob will be created for schedule '{schedule['name']} with interval {schedule['interval']}'."
                )
                continue
            if "job" in schedule:
                cmd = "run"
                args = [schedule["job"]["name"]]
            else:
                cmd = "elt"
                args = (
                    arg for arg in schedule["elt_args"] if arg != "--transform=None"
                )
            jobs.append(
                {
                    **schedule,
                    "type": "job" if "job" in schedule else "elt",
                    "cmd": cmd,
                    "args": args,
                }
            )
        return jobs

    def invoke(self, command_name: str | None, *command_args: Any) -> None:
        """Invoke the underlying cli, that is being wrapped by this extension.

        Args:
            command_name: The name of the command to invoke.
            command_args: The arguments to pass to the command.
        """
        raise NotImplementedError

    def _clear_destination(self, destination: Path) -> None:
        if not self.meltano_project_dir in destination.parents:
            return
        for root, dirs, files in os.walk(destination, topdown=False):
            for f in files:
                os.unlink(os.path.join(root, f))
            for d in dirs:
                shutil.rmtree(os.path.join(root, d))

    def render_kustomize(self, destination: Path, schedule_ids: "set[str]") -> None:
        """Render Kubernetes / Kustomize manifests based on meltano schedules to disk"""

        base_destination = destination.joinpath("base")
        base_destination.mkdir(parents=True, exist_ok=True)

        if schedule_ids:
            # individual render(s)
            predicate = lambda x: x in schedule_ids
        else:
            # full re-render, clear existing files only if we're in a project dir
            if self.meltano_project_dir in base_destination.parents:
                self._clear_destination(base_destination)
            predicate = lambda _: True

        schedules = self._get_elts_and_jobs(predicate=predicate)

        template = self.env.get_template("kustomization.yml.jinja")
        template.stream(
            {
                "commonLabels": self.common_labels,
                "schedules": schedules,
                "environment": self.environment,
            }
        ).dump(str(base_destination / "kustomization.yml"))

        for schedule in schedules:
            name = schedule["name"]
            manifest_dest = base_destination / f"{name}-cron-job.yml"
            log.debug("Templating schedule {name} to {manifest_dest}")
            labels = {
                "app.kubernetes.io/name": name,
            }
            if schedule.get("job"):
                labels["meltano.kubernetes.io/job"] = schedule["job"]["name"]
            labels["meltano.kubernetes.io/schedule"] = schedule["name"]
            template = self.env.get_template("cron-job.yml.jinja")
            template.stream(
                {
                    **schedule,
                    "labels": labels,
                }
            ).dump(str(manifest_dest))

        if not self.environment:
            log.info("No MELTANO_ENVIRONMENT set, skipping overlay")
            return

        overlay_destination = destination.joinpath("overlays", self.environment)
        overlay_destination.mkdir(parents=True, exist_ok=True)

        if overlay_destination.joinpath("kustomization.yml").exists():
            return

        template = (
            self.env.get_template("config-map.yml.jinja")
            .stream(
                {
                    "environment": self.environment,
                }
            )
            .dump(str(overlay_destination / "env-config-map.yml"))
        )
        self.env.get_template("resources.yml.jinja").stream().dump(
            str(overlay_destination / "resources.yml")
        )
        self.env.get_template("overlay.yml.jinja").stream().dump(
            str(overlay_destination / "kustomization.yml")
        )

    # if schedule_ids:
    #     unavailable_schedule_ids = schedule_ids - self.meltano_schedule_ids
    #     if unavailable_schedule_ids:
    #         log.error(
    #             "Failed to install all specified schedules: schedules "
    #             f"with IDs {unavailable_schedule_ids!r} were not found"
    #         )
    #         sys.exit(1)

    def describe(self) -> models.Describe:
        """Describe the extension.

        Returns:
            The extension description
        """
        # TODO: could we auto-generate all or portions of this from typer instead?
        return models.Describe(
            commands=[
                models.ExtensionCommand(
                    name="kubernetes", description="extension commands"
                ),
            ],
        )
