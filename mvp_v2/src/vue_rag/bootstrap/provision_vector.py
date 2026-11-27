from __future__ import annotations

import argparse
import importlib
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Explicitly provision Ladybug vector extension"
    )
    value.add_argument("--home", type=Path, required=True)
    value.add_argument(
        "--yes", action="store_true", help="confirm network/install side effect"
    )
    return value


def provision(
    home: Path,
    *,
    module_loader: Callable[[str], Any] = importlib.import_module,
) -> None:
    if home.is_symlink():
        raise ValueError("HOME must not be a symlink")
    home.mkdir(parents=True, exist_ok=True)
    home = home.resolve(strict=True)
    if not home.is_dir():
        raise ValueError("HOME must be a directory")
    os.environ["HOME"] = str(home)
    scratch = Path(tempfile.mkdtemp(prefix="ladybug-vector-", dir=home))
    db: Any = None
    connection: Any = None
    result: Any = None
    try:
        ladybug = module_loader("ladybug")
        db = ladybug.Database(str(scratch / "scratch.lbdb"))
        connection = ladybug.Connection(db)
        for statement in ("INSTALL vector", "LOAD vector"):
            result = connection.execute(statement)
            result.close()
            result = None
    finally:
        if result is not None:
            result.close()
        if connection is not None:
            connection.close()
        if db is not None:
            db.close()
        shutil.rmtree(scratch, ignore_errors=True)


def main(
    argv: Sequence[str] | None = None,
    *,
    provisioner: Callable[[Path], None] = provision,
) -> int:
    args = parser().parse_args(argv)
    if not args.yes:
        print("Refused: pass --yes to confirm extension installation.")
        return 2
    try:
        provisioner(args.home)
    except Exception:
        print("Vector provisioning failed safely.")
        return 1
    print("Vector extension provisioned for the selected HOME.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
