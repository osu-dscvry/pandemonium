import logging

worker_logger = logging.getLogger("workers")
formatter = logging.Formatter(
    '[workers] %(levelname)s: %(message)s'
)