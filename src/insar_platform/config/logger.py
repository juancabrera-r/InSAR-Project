import logging
import os
from logging.handlers import RotatingFileHandler

from rich.logging import RichHandler


class LoggingManager:
    """Manage application logging configuration."""

    @staticmethod
    def setup(
        debug: bool = False,
        log_dir: str | None = "./logs",
        log_format: str = "",
        third_party_log_level: int = logging.WARNING,
    ) -> None:
        """Configure console and optional rotating file logging.

        Application logs use DEBUG or INFO depending on `debug`.
        Noisy third-party libraries use `third_party_log_level`.
        """
        logger = logging.getLogger()

        level = logging.DEBUG if debug else logging.INFO
        logger.setLevel(level)

        # Suppress Rasterio DEBUG messages, but keep warnings and errors.
        logging.getLogger("rasterio").setLevel(logging.WARNING)

        # Close existing handlers to release open file handles.
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

        if not log_format:
            log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

        formatter = logging.Formatter(log_format)

        # Console logging.
        console_handler = RichHandler(
            rich_tracebacks=True,
            markup=False,
            show_time=True,
            show_level=True,
            show_path=False,
        )
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console_handler)

        # Optional file logging.
        if log_dir is not None:
            os.makedirs(log_dir, exist_ok=True)

            info_handler = RotatingFileHandler(
                os.path.join(log_dir, "app.log"),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            info_handler.setLevel(logging.INFO)
            info_handler.setFormatter(formatter)
            logger.addHandler(info_handler)

            error_handler = RotatingFileHandler(
                os.path.join(log_dir, "error.log"),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            logger.addHandler(error_handler)

            if debug:
                debug_handler = RotatingFileHandler(
                    os.path.join(log_dir, "debug.log"),
                    maxBytes=10 * 1024 * 1024,
                    backupCount=3,
                    encoding="utf-8",
                )
                debug_handler.setLevel(logging.DEBUG)
                debug_handler.setFormatter(formatter)
                logger.addHandler(debug_handler)

        # Suppress verbose third-party debugging messages.
        noisy_loggers = (
            "rasterio",
            "rasterio._io",
            "rasterio.env",
            "rasterio._env",
            "fiona",
            "pyproj",
            "urllib3",
            "PIL",
        )

        for name in noisy_loggers:
            logging.getLogger(name).setLevel(third_party_log_level)

        logging.getLogger(__name__).debug(
            "Logging configured: application=%s, third_party=%s",
            logging.getLevelName(level),
            logging.getLevelName(third_party_log_level),
        )
