"""Docker-based sandbox for running candidate eval commands in isolation.

This is a thin wrapper around ``docker run`` — we deliberately do *not* take a
dependency on the Docker Python SDK. The protocol is:

* Mount the candidate's working directory read-only at ``/workspace``
* Copy it to ``/tmp/work`` inside the container and run the eval there
  (so the candidate's write back into its own checkout is sandboxed)
* Apply strict resource limits (memory, CPU, pids, network)
* Drop all Linux capabilities by default
* No network access by default — the eval command must be self-contained
  (candidates should be evaluated against already-installed deps)

If Docker is not installed or not reachable, :meth:`DockerRunner.is_available`
returns False. Callers should fall back to :func:`agent_evolve.eval.run_eval`
(unsandboxed) with a clear warning — the local backend uses this pattern.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SandboxResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    image: str = ""
    container_id: str | None = None


@dataclass
class ResourceLimits:
    memory: str = "2g"
    cpus: str = "1.5"
    pids: int = 256
    timeout_seconds: float = 120.0


@dataclass
class DockerRunner:
    image: str = "python:3.12-slim"
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    network: str = "none"
    extra_args: tuple[str, ...] = ()

    @staticmethod
    def is_available() -> bool:
        """Return True if the ``docker`` CLI is on PATH and responsive."""
        if shutil.which("docker") is None:
            return False
        try:
            proc = subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            return False
        return proc.returncode == 0

    def run(
        self,
        command: str,
        *,
        workdir: str | Path,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        """Run *command* inside a fresh container with *workdir* mounted."""
        if not self.is_available():
            raise RuntimeError("docker is not available on PATH")

        workdir_path = Path(workdir).resolve()
        if not workdir_path.is_dir():
            raise NotADirectoryError(f"not a directory: {workdir_path}")

        container_id = f"agent-evolve-{int(time.time() * 1000)}"
        inner = (
            "set -euo pipefail; "
            "cp -r /workspace /tmp/work; "
            "cd /tmp/work; "
            f"{command}"
        )

        args = [
            "docker", "run", "--rm",
            "--name", container_id,
            "--network", self.network,
            "--memory", self.limits.memory,
            "--cpus", self.limits.cpus,
            "--pids-limit", str(self.limits.pids),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "-v", f"{_docker_path(workdir_path)}:/workspace:ro",
            "-w", "/workspace",
        ]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        args += [*self.extra_args, self.image, "bash", "-lc", inner]

        start = time.perf_counter()
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True,
                timeout=self.limits.timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired as e:
            duration = (time.perf_counter() - start) * 1000.0
            self._kill(container_id)
            return SandboxResult(
                command=command,
                returncode=-1,
                stdout=_stringify(e.stdout),
                stderr=f"timed out after {self.limits.timeout_seconds}s",
                duration_ms=duration,
                timed_out=True,
                image=self.image,
                container_id=container_id,
            )

        duration = (time.perf_counter() - start) * 1000.0
        return SandboxResult(
            command=command,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_ms=duration,
            image=self.image,
            container_id=container_id,
        )

    def _kill(self, container_id: str) -> None:
        try:
            subprocess.run(
                ["docker", "kill", container_id],
                capture_output=True, timeout=5, check=False,
            )
        except Exception:  # pragma: no cover
            pass


def _docker_path(p: Path) -> str:
    """Convert a Path to a docker-friendly bind mount string.

    On Windows Docker expects either ``/c/Users/...`` or ``C:/Users/...``
    depending on the engine. Docker Desktop handles the drive-letter form
    correctly, so we emit it verbatim after normalising backslashes.
    """
    if os.name == "nt":
        return str(p).replace("\\", "/")
    return str(p)


def _stringify(x: bytes | str | None) -> str:
    if x is None:
        return ""
    if isinstance(x, bytes):
        return x.decode(errors="replace")
    return x


def quote(command: str) -> str:
    """Helper: shell-quote a command for use as the inner string in ``run``."""
    return shlex.quote(command)
