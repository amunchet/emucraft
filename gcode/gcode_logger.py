import logging
import os
"""
from dotenv import load_dotenv

load_dotenv()

if os.path.exists(".env.sample"):
    load_dotenv(".env.sample")

level = os.getenv("LOG_LEVEL")
if level == "DEBUG":
    level = logging.DEBUG
else:
    level = logging.WARNING
"""
level = logging.DEBUG


logger = logging.getLogger("gcode_logger")
# Set the logging level of the logger
logger.setLevel(level)

# Create a handler to write log messages to the console
console_handler = logging.StreamHandler()

# Set the logging level of the console handler
console_handler.setLevel(level)

# Create a formatter to specify the format of the log messages
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# Attach the formatter to the console handler
console_handler.setFormatter(formatter)

# Attach the console handler to the logger
logger.addHandler(console_handler)