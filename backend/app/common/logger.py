import logging

import colorlog

from core.config import settings

_configured = False


def setup_logging() -> None:
    """配置根日志，所有模块用 logging.getLogger(__name__) 即可。重复调用无副作用。"""
    global _configured
    if _configured:
        return
    handler = colorlog.StreamHandler()
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s.%(msecs)03d %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
    for noisy in ("httpx", "elastic_transport", "urllib3", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get_logger(name: str = "app") -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
