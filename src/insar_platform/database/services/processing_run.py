from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from logging import Logger

from insar_platform.database.connection import SessionLocal
from insar_platform.database.models import ProcessingRun
from insar_platform.database.models.processing_run import ProcessingStatus
from insar_platform.database.services.processing_run_input import (
    register_processing_run_inputs,
)


def run_tracked_pipeline[**P, R](
    pipeline_name: str,
    pipeline: Callable[P, R],
    tracking_logger: Logger,
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """
    Execute a pipeline and track its execution in the database.

    Preserves the original pipeline signature, including positional
    and keyword arguments.

    Args:
        pipeline_name: Name of the pipeline.
        pipeline: Callable to execute.
        tracking_logger: Logger used for execution tracking.
        *args: Positional arguments forwarded to the pipeline.
        **kwargs: Keyword arguments forwarded to the pipeline.

    Returns:
        The original pipeline result.

    Raises:
        Exception: Propagates exceptions raised by the pipeline.
    """
    return _execute_tracked_pipeline(
        pipeline_name,
        pipeline,
        tracking_logger,
        None,
        *args,
        **kwargs,
    )


def run_tracked_pipeline_with_products[**P, R](
    pipeline_name: str,
    pipeline: Callable[P, R],
    tracking_logger: Logger,
    product_names_from_result: Callable[[R], Iterable[str]],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """
    Execute a tracked pipeline and associate SAR products with its run.

    Product names are extracted from the pipeline result and registered
    in processing_run_input.

    The SAR products must already exist in sar_product.

    Args:
        pipeline_name: Name of the pipeline.
        pipeline: Callable to execute.
        tracking_logger: Logger used for execution tracking.
        product_names_from_result: Callback that extracts SAR product
            names from the pipeline result.
        *args: Positional arguments forwarded to the pipeline.
        **kwargs: Keyword arguments forwarded to the pipeline.

    Returns:
        The original pipeline result.

    Raises:
        Exception: Propagates pipeline or product-registration errors.
    """
    return _execute_tracked_pipeline(
        pipeline_name,
        pipeline,
        tracking_logger,
        product_names_from_result,
        *args,
        **kwargs,
    )


def _execute_tracked_pipeline[**P, R](
    pipeline_name: str,
    pipeline: Callable[P, R],
    tracking_logger: Logger,
    product_names_from_result: Callable[[R], Iterable[str]] | None,
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """
    Internal implementation shared by both tracking functions.

    Execution lifecycle:
        1. Create processing run with RUNNING status.
        2. Execute pipeline.
        3. Register SAR product associations, if configured.
        4. Mark processing run as COMPLETED or FAILED.
        5. Return the pipeline result.
    """

    # ------------------------------------------------------------------
    # 1. Register processing run
    # ------------------------------------------------------------------

    with SessionLocal.begin() as session:
        run = ProcessingRun(
            pipeline_name=pipeline_name,
            status=ProcessingStatus.RUNNING,
            started_at=datetime.now(UTC),
        )

        session.add(run)
        session.flush()

        run_id = run.id

    # Initial transaction has been committed.

    tracking_logger.info(
        "Pipeline %s started | Run ID: %s",
        pipeline_name,
        run_id,
    )

    # ------------------------------------------------------------------
    # 2. Execute pipeline
    # ------------------------------------------------------------------

    try:
        result = pipeline(*args, **kwargs)

        # --------------------------------------------------------------
        # 2a. Register SAR product associations
        # --------------------------------------------------------------

        if product_names_from_result is not None:
            product_names = list(product_names_from_result(result))

            linked_count = register_processing_run_inputs(
                run_id=run_id,
                product_names=product_names,
            )

            tracking_logger.info(
                "Linked %d SAR products to processing run %s",
                linked_count,
                run_id,
            )

    except Exception as exc:
        # --------------------------------------------------------------
        # 2b. Register pipeline failure
        # --------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 3. Register successful completion
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 4. Return pipeline result
    # ------------------------------------------------------------------

    return result
