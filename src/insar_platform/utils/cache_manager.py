import pickle
from pathlib import Path
from typing import Any


def load_cached_result(path: Path) -> Any:
    """Load a trusted, previously persisted pipeline result."""

    if not path.is_file():
        raise FileNotFoundError(f"Required pipeline cache not found: {path}")

    with path.open("rb") as file:
        return pickle.load(file)


def save_cached_result(path: Path, result: Any) -> None:
    """Persist a pipeline result for subsequent executions."""

    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_name(path.name + ".tmp")

    with temporary_path.open("wb") as file:
        pickle.dump(result, file, protocol=pickle.HIGHEST_PROTOCOL)

    temporary_path.replace(path)
