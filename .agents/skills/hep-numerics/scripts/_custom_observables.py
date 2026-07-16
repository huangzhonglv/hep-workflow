#!/usr/bin/env python3
"""Shared custom-observable stub generation for hep-numerics scripts."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


LOCK_FILENAME = ".init-analysis.lock"


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write_text(path: Path, content: str) -> None:
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def custom_observables_lock(path: Path) -> Iterator[None]:
    """Serialize project-level custom-observable read/modify/write operations."""

    lock_path = path.parent / LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def render_custom_observables_template(project_name: str) -> str:
    """Render the skill-local custom-observables module template."""

    template_path = (
        Path(__file__).resolve().parent.parent
        / "templates"
        / "custom_observables.py.tmpl"
    )
    return template_path.read_text(encoding="utf-8").format(project_name=project_name)


def ensure_custom_observables_file(project_dir: Path) -> tuple[Path, bool]:
    """Create the project-level custom_observables.py header if missing."""

    path = project_dir / "numerics" / "custom_observables.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    with custom_observables_lock(path):
        if path.exists():
            return path, False
        _atomic_write_text(path, render_custom_observables_template(project_dir.name))
        return path, True


def append_custom_observable_stub(
    path: Path,
    *,
    function_name: str,
    parameter_names: list[str],
    constraint: dict[str, Any],
    needs_task_outputs: bool,
    acquire_lock: bool = True,
) -> bool:
    """Append one canonical custom-observable stub unless it already exists."""

    if acquire_lock:
        with custom_observables_lock(path):
            return append_custom_observable_stub(
                path,
                function_name=function_name,
                parameter_names=parameter_names,
                constraint=constraint,
                needs_task_outputs=needs_task_outputs,
                acquire_lock=False,
            )

    existing = path.read_text(encoding="utf-8")
    if f"def {function_name}(" in existing:
        return False

    if needs_task_outputs and "from collections.abc import Mapping" not in existing:
        source_lines = existing.splitlines()
        insertion_index = next(
            (
                index + 1
                for index, line in enumerate(source_lines)
                if line.strip() == "from __future__ import annotations"
            ),
            0,
        )
        source_lines.insert(insertion_index, "from collections.abc import Mapping")
        existing = "\n".join(source_lines) + ("\n" if existing.endswith("\n") else "")

    computed_by = constraint.get("computed_by", {})
    lines = ["", "", f"def {function_name}(", "    *,"]
    if needs_task_outputs:
        lines.append("    task_outputs: Mapping[str, Callable[..., float]],")
    for name in parameter_names:
        lines.append(f"    {name}: float,")
    lines.extend(
        [
            ") -> float:",
            '    """',
            f"    Auto-generated observable stub for constraint {constraint['id']} ({constraint['name']}).",
            "",
        ]
    )

    if computed_by.get("type") == "derived":
        lines.extend(
            [
                "    Derivation note:",
                f"        {computed_by.get('derivation_note', '').strip()}",
            ]
        )
    else:
        lines.extend(
            [
                "    Original formula that could not be parsed safely:",
                f"        {computed_by.get('formula', '').strip()}",
            ]
        )

    lines.extend(
        [
            '    """',
            "    raise NotImplementedError(",
            f'        "{function_name} is not yet implemented; see constraint {constraint["id"]}"',
            "    )",
            "",
        ]
    )
    _atomic_write_text(path, existing.rstrip() + "\n" + "\n".join(lines))
    return True
