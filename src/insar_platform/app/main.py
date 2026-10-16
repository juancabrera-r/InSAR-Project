#!/usr/bin/env python3

import os
import sys
from pathlib import Path

from insar_platform.config.config import ConfigLoader
from insar_platform.config.logger import LoggingManager
from insar_platform.pipelines.application import sentinel1_app

BASE_DIR = Path(__file__).resolve().parents[1]

CONFIG_PATH = BASE_DIR / "config" / "config.yaml"
ENV_PATH = BASE_DIR.parents[1] / ".env"
LOG_DIR = BASE_DIR.parents[1] / "logs"


def main() -> None:
    """Main entry point for the application."""

    print("PYTHON:", sys.executable)
    print("CONDA_PREFIX:", os.getenv("CONDA_PREFIX"))
    print("PIXI_ENVIRONMENT_NAME:", os.getenv("PIXI_ENVIRONMENT_NAME"))
    print("PATH:")
    for path in os.environ.get("PATH", "").split(os.pathsep):
        if "pixi" in path.lower() or "conda" in path.lower():
            print(" ", path)

    # Initialize config
    config_obj = ConfigLoader(
        config_path=CONFIG_PATH,
        env_path=ENV_PATH,
    )

    config = config_obj.load_yaml()
    env = config_obj.load_env()

    # Initialize logging
    LoggingManager.setup(debug=True, log_dir=str(LOG_DIR))

    # Initialize app
    sentinel1_app(
        config=config,
        env=env,
    )


if __name__ == "__main__":
    main()
