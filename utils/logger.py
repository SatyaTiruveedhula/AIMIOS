import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_PATH = Path("logs") / "aimios.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

def setup_logger(name: str = "aimios", level: int = logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        fh = RotatingFileHandler(LOG_PATH, maxBytes=10_000_000, backupCount=3)
        fh.setFormatter(fmt)
        logger.addHandler(sh)
        logger.addHandler(fh)
    return logger
