import subprocess
import sys
from pathlib import Path


class DockerError(Exception):
    pass


class DockerManager:
    """Thin wrapper around docker compose subprocess calls."""

    def __init__(self, compose_file: Path, project_name: str):
        self.compose_file = compose_file
        self.project_name = project_name

    def _base_cmd(self) -> list[str]:
        return [
            "docker", "compose",
            "-f", str(self.compose_file),
            "-p", self.project_name,
        ]

    def _run(self, args: list[str], capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
        cmd = self._base_cmd() + args
        try:
            return subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                check=check,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.strip() if e.stderr else ""
            raise DockerError(f"Command failed: {' '.join(cmd)}\n{stderr}") from e
        except FileNotFoundError:
            raise DockerError(
                "Docker is not installed or not in PATH. "
                "Install Docker: https://docs.docker.com/get-docker/"
            )

    def up(self, detach: bool = True, pull: bool = True) -> None:
        """Start services."""
        args = ["up"]
        if detach:
            args.append("-d")
        if pull:
            args.append("--pull=always")
        self._run(args, capture=False, check=True)

    def down(self, volumes: bool = False) -> None:
        """Stop and remove containers, networks."""
        args = ["down"]
        if volumes:
            args.append("-v")
        self._run(args, capture=False, check=True)

    def stop(self) -> None:
        """Stop services without removing them."""
        self._run(["stop"], capture=False, check=True)

    def start(self) -> None:
        """Start previously stopped services."""
        self._run(["start"], capture=False, check=True)

    def ps(self) -> str:
        """List containers and their status."""
        result = self._run(["ps", "--format", "table"], capture=True)
        return result.stdout

    def logs(self, follow: bool = False, service: str | None = None, tail: int | None = None) -> None:
        """Stream logs to stdout."""
        args = ["logs"]
        if follow:
            args.append("-f")
        if tail is not None:
            args.extend(["--tail", str(tail)])
        if service:
            args.append(service)
        # Stream directly to terminal
        cmd = self._base_cmd() + args
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            raise DockerError(f"Failed to stream logs") from e
        except KeyboardInterrupt:
            pass

    def exec(self, service: str, command: str | list[str] = "/bin/bash") -> None:
        """Exec into a running container."""
        args = ["exec", "-it", service]
        if isinstance(command, str):
            args.append(command)
        else:
            args.extend(command)
        cmd = self._base_cmd() + args
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            raise DockerError(f"Failed to exec into {service}") from e

    def pull(self) -> None:
        """Pull images for all services."""
        self._run(["pull"], capture=False, check=True)
