import logging
import os
from logging.handlers import RotatingFileHandler

from rich.logging import RichHandler


class LoggingManager:
    """
    Manages application logging configuration.
    """

    @staticmethod
    def setup(
        debug: bool = False,
        log_dir: str | None = "./logs",
        log_format: str = "",
    ) -> None:
        """
        Configure the root logger with console logging.

        File logging is enabled only when log_dir is not None.
        """
        logger = logging.getLogger()
        level = logging.DEBUG if debug else logging.INFO
        logger.setLevel(level)

        # Close existing handlers to release open file handles.
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

        if not log_format:
            log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

        formatter = logging.Formatter(log_format)

        # Console logging (always enabled).
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

        # File logging (optional).
        if log_dir is not None:
            os.makedirs(log_dir, exist_ok=True)

            # INFO and above.
            info_handler = RotatingFileHandler(
                os.path.join(log_dir, "app.log"),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            info_handler.setLevel(logging.INFO)
            info_handler.setFormatter(formatter)
            logger.addHandler(info_handler)

            # ERROR and above.
            error_handler = RotatingFileHandler(
                os.path.join(log_dir, "error.log"),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            logger.addHandler(error_handler)

            # DEBUG and above (only in debug mode).
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
