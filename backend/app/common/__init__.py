# set global timezone
from time import tzset
tzset()

from .logger import get_logger
logger = get_logger()
