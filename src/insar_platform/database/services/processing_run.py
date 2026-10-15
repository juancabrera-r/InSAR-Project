from collections.abc import Callable
from datetime import UTC, datetime
from logging import Logger
from typing import ParamSpec, TypeVar

from insar_platform.database.connection import SessionLocal
from insar_platform.database.models.processing_run import (
    ProcessingRun,
    ProcessingStatus,
)

P = ParamSpec("P")
R = TypeVar("R")


def run_tracked_pipeline[**P, R](
    pipeline_name: str,
    pipeline: Callable[P, R],
    tracking_logger: Logger,
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:

    # 1. Register processing run
    with SessionLocal.begin() as session:
        run = ProcessingRun(
            pipeline_name=pipeline_name,
            status=ProcessingStatus.RUNNING,
            started_at=datetime.now(UTC),
        )

        session.add(run)
        session.flush()

        run_id = run.id

    # Initial transaction is now committed

    tracking_logger.info(
        "Pipeline %s started | Run ID: %s",
        pipeline_name,
        run_id,
    )

    # 2. Execute pipeline
    try:
        result = pipeline(*args, **kwargs)

    except Exception as exc:
        # 3. Register failure
        with SessionLocal.begin() as session:
            run = session.get(ProcessingRun, run_id)

            if run is None:
                raise RuntimeError(
                    f"Processing run with ID {run_id} not found"
                ) from exc

            run.status = ProcessingStatus.FAILED
            run.ended_at = datetime.now(UTC)
            run.error_message = str(exc)

        tracking_logger.exception(
            "Pipeline %s failed | Run ID: %s",
            pipeline_name,
            run_id,
        )

        raise

    # 4. Register successful completion
    with SessionLocal.begin() as session:
        run = session.get(ProcessingRun, run_id)

        if run is None:
            raise RuntimeError(f"Processing run with ID {run_id} not found")

        run.status = ProcessingStatus.COMPLETED
        run.ended_at = datetime.now(UTC)
        run.error_message = None

    tracking_logger.info(
        "Pipeline %s completed successfully | Run ID: %s",
        pipeline_name,
        run_id,
    )

    return result
