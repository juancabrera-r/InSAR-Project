"""Execute ESA SNAP Graph Processing Tool (GPT) workflows."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class SnapExecutionError(RuntimeError):
    """Raised when SNAP GPT execution fails."""


@dataclass(frozen=True)
class SnapExecutionResult:
    """Result of a completed SNAP GPT execution."""

    command: tuple[str, ...]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class SnapAdapter:
    """Interface between the InSAR pipeline and ESA SNAP GPT."""

    def __init__(
        self,
        gpt_path: str | Path,
        timeout_seconds: int = 3600,
    ) -> None:
        self.gpt_path = Path(gpt_path).expanduser()
        self.timeout_seconds = timeout_seconds

        if not self.gpt_path.is_file():
            raise FileNotFoundError(f"SNAP GPT executable not found: {self.gpt_path}")

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def execute(
        self,
        graph_path: str | Path,
        parameters: Mapping[str, str | Path | int | float] | None = None,
        *,
        cache_size: str | None = None,
    ) -> SnapExecutionResult:
        """Execute an XML processing graph with optional parameters."""

        graph = Path(graph_path).expanduser().resolve()

        if not graph.is_file():
            raise FileNotFoundError(f"SNAP graph not found: {graph}")

        if graph.suffix.lower() != ".xml":
            raise ValueError("SNAP graph must be an XML file")

        command = [
            str(self.gpt_path.resolve()),
            str(graph),
        ]

        if cache_size is not None:
            command.append(f"-c={cache_size}")

        for key, value in (parameters or {}).items():
            if not key or not key.replace("_", "").isalnum():
                raise ValueError(f"Invalid SNAP parameter name: {key!r}")

            command.append(f"-P{key}={value}")

        start_time = time.perf_counter()

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as exc:
            raise SnapExecutionError(
                f"SNAP execution timed out after {self.timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            raise SnapExecutionError(f"Unable to start SNAP GPT: {exc}") from exc

        duration = time.perf_counter() - start_time

        result = SnapExecutionResult(
            command=tuple(command),
            return_code=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
            duration_seconds=duration,
        )

        if process.returncode != 0:
            raise SnapExecutionError(
                f"SNAP GPT failed with exit code "
                f"{process.returncode}\n"
                f"STDOUT:\n{process.stdout[-4000:]}\n"
                f"STDERR:\n{process.stderr[-4000:]}"
            )

        return result

    def check_operator(self, operator: str) -> bool:
        """Return True if a SNAP operator is available."""

        try:
            result = subprocess.run(
                [str(self.gpt_path.resolve()), operator, "-h"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

        return result.returncode == 0
