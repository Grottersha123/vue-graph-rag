from __future__ import annotations

from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, field_validator


class UiRuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    working_root: Path
    dumps_root: Path
    databases_root: Path
    ladybug_home: Path

    @field_validator("working_root", "dumps_root", "databases_root", "ladybug_home")
    @classmethod
    def confined(cls, value: Path) -> Path:
        runtime = (Path(__file__).resolve().parents[3] / "runtime").resolve()
        resolved = value.resolve()
        if not resolved.is_relative_to(runtime):
            raise ValueError("runtime paths must remain under mvp_v2/runtime")
        return resolved


def default_runtime_config() -> UiRuntimeConfig:
    runtime = Path(__file__).resolve().parents[3] / "runtime"
    return UiRuntimeConfig(
        working_root=runtime / "mvp_data",
        dumps_root=runtime / "dumps",
        databases_root=runtime / "databases",
        ladybug_home=runtime / "ladybug-home",
    )


def safe_session_input(root: Path, session_name: str) -> Path:
    if not session_name or PurePosixPath(session_name).name != session_name:
        raise ValueError("invalid session input name")
    target = (root / "session-inputs" / session_name).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("session input path escapes working root")
    return target
