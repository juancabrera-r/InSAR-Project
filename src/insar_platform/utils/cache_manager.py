import logging
import pickle
from collections.abc import Callable
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def run_or_load_pipeline[T](
    *,
    name: str,
    skip: bool,
    cache_path: Path,
    pipeline: Callable[[], T],
    logger: logging.Logger,
) -> T:
    """Execute a pipeline stage or restore its cached result."""

    if skip:
        if not cache_path.is_file():
            raise FileNotFoundError(
                f"Cached file for {name} not found: {cache_path}. "
                "Run the pipeline without skipping this stage."
            )

        logger.info("Restoring cached %s: %s", name, cache_path)
        return _load_cached_result(cache_path)

    logger.info("Executing pipeline: %s", name)

    result = pipeline()

    _save_cached_result(cache_path, result)

    return result


def _load_cached_result(path: Path) -> Any:
    """Load a trusted, previously persisted pipeline result."""

    if not path.is_file():
        raise FileNotFoundError(f"Required pipeline cache not found: {path}")

    with path.open("rb") as file:
        return pickle.load(file)


def _save_cached_result(path: Path, result: Any) -> None:
    """Persist a pipeline result for subsequent executions."""

    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = path.with_name(path.name + ".tmp")

    with temporary_path.open("wb") as file:
        pickle.dump(result, file, protocol=pickle.HIGHEST_PROTOCOL)

    temporary_path.replace(path)
