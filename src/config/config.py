

import yaml

from pathlib import Path
from dotenv import dotenv_values,load_dotenv


class ConfigLoader:
    """Load application configuration from a YAML file."""

    def __init__(
            self,
            config_path: Path,
            env_path: Path,
        ):
        
        self.config_path = config_path
        self.env_path = env_path

        load_dotenv(dotenv_path=env_path, override=False)


    def load_yaml(self) -> dict:
        """Load and return the YAML configuration."""
        try:
            with self.config_path.open("r", encoding="utf-8") as config_file:
                return yaml.safe_load(config_file) or {}

        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(
                f"Failed to load configuration from {self.config_path}"
            ) from exc

    def load_env(self) -> dict:
        """Load and return the environment file configuration."""
        try:
            return dict(dotenv_values(self.env_path))

        except OSError as exc:
            raise RuntimeError(
                f"Failed to load environment configuration from {self.env_path}"
            ) from exc

