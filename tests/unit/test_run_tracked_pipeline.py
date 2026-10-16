from logging import Logger
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Ajustar esta importación a la ruta real del módulo.
import src.insar_platform.database.services.processing_run as tracking
from insar_platform.database.models.processing_run import (
    ProcessingStatus,
)


@pytest.fixture
def logger() -> MagicMock:
    return MagicMock(spec=Logger)


@pytest.fixture
def mocked_sessions(monkeypatch):
    """
    Mock SessionLocal.begin() while preserving the same
    ProcessingRun instance across transactions.
    """
    runs = {}
    transactions = []

    def begin():
        session = MagicMock()
        context_manager = MagicMock()

        def add(run):
            run.id = len(runs) + 1
            runs[run.id] = run

        session.add.side_effect = add
        session.get.side_effect = lambda model, run_id: runs.get(run_id)

        context_manager.__enter__.return_value = session
        context_manager.__exit__.return_value = False

        transactions.append(session)
        return context_manager

    session_local = SimpleNamespace(begin=MagicMock(side_effect=begin))

    monkeypatch.setattr(tracking, "SessionLocal", session_local)

    return runs, transactions, session_local


def test_pipeline_success(logger, mocked_sessions):
    runs, transactions, session_local = mocked_sessions

    pipeline = MagicMock(return_value={"result": "ok"})

    result = tracking.run_tracked_pipeline(
        "test_pipeline",
        pipeline,
        logger,
    )

    assert result == {"result": "ok"}
    pipeline.assert_called_once_with()

    assert len(runs) == 1
    assert len(transactions) == 2
    assert session_local.begin.call_count == 2

    run = runs[1]

    assert run.pipeline_name == "test_pipeline"
    assert run.status == ProcessingStatus.COMPLETED
    assert run.started_at is not None
    assert run.ended_at is not None
    assert run.ended_at >= run.started_at
    assert run.error_message is None

    transactions[0].add.assert_called_once_with(run)
    transactions[0].flush.assert_called_once()
    transactions[1].get.assert_called_once_with(
        tracking.ProcessingRun,
        1,
    )

    assert logger.info.call_count == 2
    logger.exception.assert_not_called()


def test_pipeline_failure(logger, mocked_sessions):
    runs, transactions, session_local = mocked_sessions

    error = ValueError("Invalid input")

    pipeline = MagicMock(side_effect=error)

    with pytest.raises(ValueError, match="Invalid input") as exc_info:
        tracking.run_tracked_pipeline(
            "failing_pipeline",
            pipeline,
            logger,
        )

    assert exc_info.value is error

    assert len(runs) == 1
    assert len(transactions) == 2
    assert session_local.begin.call_count == 2

    run = runs[1]

    assert run.status == ProcessingStatus.FAILED
    assert run.started_at is not None
    assert run.ended_at is not None
    assert run.ended_at >= run.started_at
    assert run.error_message == "Invalid input"

    logger.exception.assert_called_once_with(
        "Pipeline %s failed | Run ID: %s",
        "failing_pipeline",
        1,
    )


def test_pipeline_receives_args_and_kwargs(logger, mocked_sessions):
    pipeline = MagicMock(return_value=42)

    result = tracking.run_tracked_pipeline(
        "parameterized_pipeline",
        pipeline,
        logger,
        10,
        20,
        scale=2,
        mode="fast",
    )

    assert result == 42

    pipeline.assert_called_once_with(
        10,
        20,
        scale=2,
        mode="fast",
    )


def test_pipeline_is_registered_before_execution(
    logger,
    mocked_sessions,
):
    runs, transactions, _ = mocked_sessions

    def pipeline():
        assert len(runs) == 1
        assert len(transactions) == 1
        assert runs[1].status == ProcessingStatus.RUNNING
        assert runs[1].ended_at is None

        return "done"

    result = tracking.run_tracked_pipeline(
        "ordered_pipeline",
        pipeline,
        logger,
    )

    assert result == "done"
    assert runs[1].status == ProcessingStatus.COMPLETED


def test_pipeline_failure_preserves_original_exception(
    logger,
    mocked_sessions,
):
    original_error = RuntimeError("Processing failed")

    def pipeline():
        raise original_error

    with pytest.raises(RuntimeError) as exc_info:
        tracking.run_tracked_pipeline(
            "error_pipeline",
            pipeline,
            logger,
        )

    assert exc_info.value is original_error


def test_missing_run_on_success(logger, mocked_sessions):
    runs, transactions, _ = mocked_sessions

    def pipeline():
        runs.clear()
        return "done"

    with pytest.raises(
        RuntimeError,
        match="Processing run with ID 1 not found",
    ):
        tracking.run_tracked_pipeline(
            "missing_run_pipeline",
            pipeline,
            logger,
        )

    assert len(transactions) == 2
    logger.exception.assert_not_called()


def test_missing_run_on_failure(logger, mocked_sessions):
    runs, transactions, _ = mocked_sessions

    def pipeline():
        runs.clear()
        raise ValueError("Original failure")

    with pytest.raises(
        RuntimeError,
        match="Processing run with ID 1 not found",
    ) as exc_info:
        tracking.run_tracked_pipeline(
            "missing_failed_run",
            pipeline,
            logger,
        )

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert str(exc_info.value.__cause__) == "Original failure"
    assert len(transactions) == 2


def test_success_logging(logger, mocked_sessions):
    pipeline = MagicMock(return_value=None)

    tracking.run_tracked_pipeline(
        "logging_pipeline",
        pipeline,
        logger,
    )

    logger.info.assert_any_call(
        "Pipeline %s started | Run ID: %s",
        "logging_pipeline",
        1,
    )

    logger.info.assert_any_call(
        "Pipeline %s completed successfully | Run ID: %s",
        "logging_pipeline",
        1,
    )

    logger.exception.assert_not_called()


def test_failure_logging(logger, mocked_sessions):
    pipeline = MagicMock(side_effect=ValueError("Bad data"))

    with pytest.raises(ValueError):
        tracking.run_tracked_pipeline(
            "logging_failure",
            pipeline,
            logger,
        )

    logger.info.assert_called_once_with(
        "Pipeline %s started | Run ID: %s",
        "logging_failure",
        1,
    )

    logger.exception.assert_called_once_with(
        "Pipeline %s failed | Run ID: %s",
        "logging_failure",
        1,
    )


def test_pipeline_returns_none(logger, mocked_sessions):
    pipeline = MagicMock(return_value=None)

    result = tracking.run_tracked_pipeline(
        "none_pipeline",
        pipeline,
        logger,
    )

    assert result is None


def test_initial_registration_failure_does_not_execute_pipeline(
    logger,
    monkeypatch,
):
    session = MagicMock()
    session.add.side_effect = RuntimeError("Database unavailable")

    context_manager = MagicMock()
    context_manager.__enter__.return_value = session
    context_manager.__exit__.return_value = False

    session_local = SimpleNamespace(begin=MagicMock(return_value=context_manager))

    monkeypatch.setattr(tracking, "SessionLocal", session_local)

    pipeline = MagicMock()

    with pytest.raises(RuntimeError, match="Database unavailable"):
        tracking.run_tracked_pipeline(
            "database_failure",
            pipeline,
            logger,
        )

    pipeline.assert_not_called()
    logger.info.assert_not_called()
    logger.exception.assert_not_called()


def test_pipeline_registers_product_inputs(
    mocked_sessions,
    monkeypatch,
):
    pipeline = MagicMock(return_value={"products": ["S1A_PRODUCT"]})
    logger = MagicMock(spec=Logger)

    register_mock = MagicMock(return_value=1)

    monkeypatch.setattr(
        tracking,
        "register_processing_run_inputs",
        register_mock,
    )

    extractor = lambda result: result["products"]

    result = tracking.run_tracked_pipeline_with_products(
        "catalog_discovery",
        pipeline,
        logger,
        extractor,
    )

    assert result == {"products": ["S1A_PRODUCT"]}

    pipeline.assert_called_once_with()

    register_mock.assert_called_once_with(
        run_id=1,
        product_names=["S1A_PRODUCT"],
    )
