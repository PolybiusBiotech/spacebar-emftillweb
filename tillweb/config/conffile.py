import os
import tomllib
from pathlib import Path
from collections import defaultdict
from django.core.exceptions import ImproperlyConfigured


def _config_locations():
    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    home = os.getenv("HOME")
    xdg_config_dirs = os.getenv("XDG_CONFIG_DIRS", default="/etc/xdg")

    if xdg_config_home:
        yield Path(xdg_config_home)
    elif home:
        yield Path(home) / ".config"

    for part in xdg_config_dirs.split(":"):
        yield Path(part)


def read_config():
    for loc in _config_locations():
        if loc.is_dir():
            cf = loc / "emftillweb.toml"
            if cf.exists():
                try:
                    with open(cf, "rb") as f:
                        return defaultdict(dict, tomllib.load(f))
                except tomllib.TOMLDecodeError as e:
                    raise ImproperlyConfigured(f"{cf}: {e}")
    return defaultdict(dict)
