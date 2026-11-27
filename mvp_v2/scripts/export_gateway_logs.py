from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

_ALLOWED_JSONL: Final = (
    "backend.jsonl",
    "telegram.jsonl",
    "errors.jsonl",
    "prompt_traces.jsonl",
    "feedback.jsonl",
)
_CONTAINER_LOGS: Final = (
    ("backend", "backend-container.log"),
    ("telegram", "telegram-container.log"),
)
_LOG_ROOT: Final = "/var/lib/mvp-v2/gateway-logs"


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ExportConfig:
    podman: str
    backend_container: str
    telegram_container: str
    output_directory: Path

    def container(self, name: str) -> str:
        return self.backend_container if name == "backend" else self.telegram_container


def parse_arguments() -> ExportConfig:
    parser = argparse.ArgumentParser(description="Export bounded gateway logs")
    parser.add_argument("--podman", default="podman")
    parser.add_argument("--backend-container", required=True)
    parser.add_argument("--telegram-container", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args()
    return ExportConfig(
        podman=arguments.podman,
        backend_container=arguments.backend_container,
        telegram_container=arguments.telegram_container,
        output_directory=arguments.output_directory,
    )


def run_command(command: tuple[str, ...], operation: str) -> bytes:
    try:
        result = subprocess.run(command, check=False, capture_output=True)
    except OSError as error:
        raise ExportError(f"{operation} could not start") from error
    if result.returncode != 0:
        raise ExportError(f"{operation} failed")
    return result.stdout + result.stderr


def prepare_output_directory(path: Path) -> None:
    if path.is_symlink():
        raise ExportError("output directory must not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise ExportError("output directory is invalid")
    path.chmod(0o700)


def copy_jsonl_logs(config: ExportConfig, staging: Path) -> None:
    for filename in _ALLOWED_JSONL:
        run_command(
            (
                config.podman,
                "cp",
                f"{config.backend_container}:{_LOG_ROOT}/{filename}",
                str(staging / filename),
            ),
            f"copy {filename}",
        )
        (staging / filename).chmod(0o600)


def write_container_logs(config: ExportConfig, staging: Path) -> None:
    for container_name, filename in _CONTAINER_LOGS:
        content = run_command(
            (
                config.podman,
                "logs",
                "--tail=500",
                config.container(container_name),
            ),
            f"read {container_name} container logs",
        )
        target = staging / filename
        target.write_bytes(content)
        target.chmod(0o600)


def create_archive(staging: Path, archive: Path) -> None:
    with tarfile.open(archive, mode="x:gz") as bundle:
        for source in sorted(staging.iterdir(), key=lambda item: item.name):
            if source.is_symlink() or not source.is_file():
                raise ExportError("staging contains an unsupported artifact")
            bundle.add(source, arcname=source.name, recursive=False)
    archive.chmod(0o600)


def export_logs(config: ExportConfig) -> Path:
    os.umask(0o077)
    prepare_output_directory(config.output_directory)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    staging = config.output_directory / f".gateway-logs-{stamp}"
    archive = config.output_directory / f"gateway-logs-{stamp}.tar.gz"
    staging.mkdir(mode=0o700)
    try:
        copy_jsonl_logs(config, staging)
        write_container_logs(config, staging)
        create_archive(staging, archive)
    except (ExportError, OSError, tarfile.TarError):
        archive.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return archive


def main() -> int:
    try:
        archive = export_logs(parse_arguments())
    except ExportError as error:
        print(f"log export failed: {error}", file=sys.stderr)
        return 1
    except (OSError, tarfile.TarError):
        print(
            "log export failed: filesystem or archive operation failed", file=sys.stderr
        )
        return 1
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
