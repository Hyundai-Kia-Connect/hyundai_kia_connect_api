import logging
import os
from dotenv import load_dotenv

load_dotenv()


def get_logger(name: str | None = None):
    logger = logging.getLogger(name or __name__)

    # Add stream handler with formatting
    stream_handler = logging.StreamHandler()
    fmt = logging.Formatter("%(levelname)s - %(message)s")
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    # Set level
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

    return logger
