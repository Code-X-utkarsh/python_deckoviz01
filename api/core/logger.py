import logging
import logging.handlers
import os


# Configure logging with detailed format
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s %(asctime)s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Create logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create file handler with rotation
file_handler = logging.handlers.RotatingFileHandler(
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5
)
file_handler.setLevel(logging.INFO)

# Create request-specific logger for API requests
request_logger = logging.getLogger('fastapi.requests')
request_file_handler = logging.handlers.RotatingFileHandler(
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=5
)
request_file_handler.setLevel(logging.INFO)

# Set formatters
formatter = logging.Formatter(
    'REQUEST %(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
request_file_handler.setFormatter(formatter)
request_logger.addHandler(request_file_handler)

# Don't propagate to root logger to avoid duplication
request_logger.propagate = False