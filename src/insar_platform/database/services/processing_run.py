import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from logging import Logger
from typing import Any

from insar_platform.database.connection import SessionLocal
from insar_platform.database.models import ProcessingRun
from insar_platform.database.models.processing_run import ProcessingStatus
from insar_platform.database.services.processing_run_input import (
    register_processing_run_inputs,
)


@dataclass(frozen=True)
class TrackingOptions:
    product_names_from_result: Callable[[Any], Iterable[str]] | None = None

    result_handler: Callable[[uuid.UUID, Any], None] | None = None


def run_tracked_pipeline[R](
    pipeline_name: str,
    pipeline: Callable[..., R],
    tracking_logger: Logger,
    *args: Any,
    tracking_options: TrackingOptions | None = None,
    **kwargs: Any,
) -> R:
    """
    Execute a pipeline and track its execution.

    Optional operations:
        - Register SAR product associations.
        - Persist pipeline results using a result handler.

    The pipeline result is returned unchanged.
    """

    # 1. Create processing run
    with SessionLocal.begin() as session:
        run = ProcessingRun(
            pipeline_name=pipeline_name,
            status=ProcessingStatus.RUNNING,
            started_at=datetime.now(UTC),
        )

        session.add(run)
        session.flush()
        run_id = run.id

    tracking_logger.info(
        "Pipeline %s started | Run ID: %s",
        pipeline_name,
        run_id,
    )

    # 2. Execute pipeline
    try:
        result = pipeline(*args, **kwargs)

        if tracking_options is not None:
            # Register associated SAR products
            if tracking_options.product_names_from_result is not None:
                product_names = list(tracking_options.product_names_from_result(result))

                linked_count = register_processing_run_inputs(
                    run_id=run_id,
                    product_names=product_names,
                )

                tracking_logger.info(
                    "Linked %d SAR products to processing run %s",
                    linked_count,
                    run_id,
                )

            # Persist pipeline results
            if tracking_options.result_handler is not None:
                tracking_options.result_handler(run_id, result)

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

    # 4. Register completion
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
