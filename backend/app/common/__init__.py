# set global timezone
from time import tzset
tzset()

from .logger import get_logger, setup_logging

__all__ = ["get_logger", "logger", "setup_logging"]

logger = get_logger()
