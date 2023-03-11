#!/usr/bin/env python3
"""
G-code parser
"""
import re
from gcode_logger import logger

# Exceptions
class NotImplementedException(Exception):
    pass

# Helpers
def codes_parse(gcode):
    """
    Parses the lines and puts in a format for `parse_line`
    """
    # Split the gcode into a list of lines
    lines = [x.strip() for x in gcode.upper().split('\n') if x.strip() != ""]

    # Initialize the list of codes
    codes = []

    # Iterate through each line of gcode
    for line in lines:
        line_codes = []
        # Split the line into tokens
        logger.debug(line)
        tokens = re.findall(r"[A-Z]-?\d*\.?\d+", line)
        logger.debug(tokens)
        # Extract the code and value from the line

        for token in tokens:
            code = token[0]
            if code in "GMT":
                value = int(token[1:])
            else:
                value = float(token[1:])
            # Add the code and value to the list of codes
            line_codes.append((code, value))
        
        codes.append(line_codes)

    logger.debug(f"Codes:{codes}")
    return codes

class Program:
    def __init__(self):
        """
        Initialize Program Instance
        """
        lines = []

        self.tool_holder_diameter = None
        self.tool_holder_length = None
        self.tool_diameter = None
        self.tool_length = None

        self.block = None
        self.block_offsets = None

        self.starting_point = (0,0,10) # Default

        self.block_x_min = None
        self.block_x_max = None

        self.block_y_min = None
        self.block_y_max = None

        self.block_z_min = None
        self.block_z_max = None

        self.lines = []

    def helper_block(self):
        """
        Turns Block Values into one data structure for ease of use

        Also calculates the block_offsets
        """
        logger.debug("In helper block")

        self.block = {
            "x" : (self.block_x_min, self.block_x_max),
            "y" : (self.block_y_min, self.block_y_max),
            "z" : (self.block_z_min, self.block_z_max),
        }
        self.block_offsets = {
            "x" : abs(self.block_x_min),
            "y" : abs(self.block_y_min),
            "z" : 0
        }

    def parse_line(self, lines):
        """
        Parses Lines
        """
        logger.debug(f"Lines:{lines}")
        # Check if the right types, if not, send through codes_parse
        # If that fails, throw an error

        number_match = r"(-?(\d+.?)\d+(\.\d+)?)"

        matches = {
            r"Tool Holder Diameter: {}" : "tool_holder_diameter",
            r"#127\s*=\s*{}" : "tool_holder_length",
            r"DIAMETER:\s*{}" : "tool_diameter",
            r"#128\s*=\s*{}" : "tool_length",
            r"MIN\s*X\s*:\s*{}" : "block_x_min",
            r"MAX\s*X\s*:\s*{}" : "block_x_max",

            r"MIN\s*Y\s*:\s*{}" : "block_y_min",
            r"MAX\s*Y\s*:\s*{}" : "block_y_max",

            r"MIN\s*Z\s*:\s*{}" : "block_z_min",
            r"MAX\s*Z\s*:\s*{}" : "block_z_max",


        }

        for line in [x.strip() for x in lines.split("\n") if x.strip() != ""]:
            logger.debug(f"Line:{line}")

            for match in matches:
                x = re.search(match.format(number_match), line)
                if x is not None:
                    logger.warning(x.group(1))

                    try:
                        output = float(x.group(1))
                    except Exception:
                        output = x.group(1)

                    setattr(self, matches[match], output)
        
        # Run all helper functions

        for item in [x for x in dir(self) if x.startswith("helper_")]:
            getattr(self, item)()
        
        return True