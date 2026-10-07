#!/usr/bin/env python3

import logging
from pathlib import Path

from src.config.logger import LoggingManager
from src.config.config import ConfigLoader

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "src" / "config" / "config.yaml"
LOG_DIR = BASE_DIR / "logs"

def main() -> None:
    """Main entry point for the application."""

    # Initialize config
    config_obj = ConfigLoader(
        config_path=CONFIG_PATH,
    )
    
    # Initialize logging
    LoggingManager.setup(
        debug=True,
        log_dir=str(LOG_DIR)
    )

    # Initialize app


if __name__ == "__main__":
    main()