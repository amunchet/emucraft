#!/usr/bin/env python3
"""
G-code parser
"""
import re

import arc
import linear

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

        self.tool_holder_diameter = 0
        self.tool_holder_length = 0
        self.tool_diameter = 0
        self.tool_length = 0

        self.block = None
        self.block_offsets = None

        self.starting_point = (0,0,10) # Default

        self.block_x_min = 0
        self.block_x_max = 0

        self.block_y_min = 0
        self.block_y_max = 0

        self.block_z_min = 0
        self.block_z_max = 0

        self.lines = []
        self.current = {
            "G" : [],
            "M" : [],
            "X" : self.starting_point[0],
            "Y" : self.starting_point[1],
            "Z" : self.starting_point[2],
            "H" : None,
            "I" : None,
            "J": None,
            "K" : None,
            "F" : None,
            "S" : None,
            "D" : None,
            "T" : None
        }

        self.invalid_gcodes = [
            91, 18, 19, 28, 52
        ]
        self.rapid_code = 0 # Can be 0 or 1
        self.rotation_code = 0 # Can be 0, 2, or 3
        self.coordinate_code = 17 # Can be 17, 18, 19
        self.units_code = 20 # Can be 20 or 21
        self.offset_code = 40 # Can be 40, 41, 42, 43
        self.canned_cycle_code = None # Can be 80, 81, 82, 83, 88
        self.relative_code = 90 # Can be 90, 91
        self.spindle_code = None # Can be 3 or 5

    def translate_to_xyz(self, arr, MULTIPLIER=1000):
        """
        Translates to the XYZ format from the array of points

        arr - [(x1,y1,z1), (x2, y2, z2), ...]

        Output XYZ file format (from `functions.h`)
            [X] [Y] [Z] [Cutter Diameter] [Tool Holder Diameter] [Tool Holder Z (Bottom)] [MOVE TYPE - 0: non-cutting , 1: normal]
            XXXXX XXXXX XXXXX XXXXXX XXXXX XXXXX XXXXX
        """
        output = []
        cutting = 0 if self.rapid_code == 0 else 1
        for (x,y,z) in arr:
            output.append(f"{int(x)} {int(y)} {int(z)} {int(self.tool_diameter * MULTIPLIER)} {int(self.tool_holder_diameter * MULTIPLIER)} {int(self.tool_holder_length * MULTIPLIER)} {cutting}")

        return output

    def motion_parse(self, line, current_codes, MULTIPLIER=1000):
        """
        Parses and returns output from line
            - Note: This is a single line
        
            MULTIPLIER is the conversion factor for XYZ format (probably 1000)
        """

        # Check for invalid G codes or M-codes (not implemented)
        for item in self.invalid_gcodes:
            if f"G{item}" in line:
                raise NotImplementedException("Invalid G-code")
        
        # Check our feed rate and if we're a rapid move
        # TODO: if we have a rapid fly move (F750.) and it cuts something, we should throw a fit
        
        # Check if we're linear or circular
        
        rotation_code = 0
        if ("G", 1) in current_codes:
            self.rapid_code = 1
        elif ("G", 0) in current_codes:
            self.rapid_code = 0
        elif ("G", 2) in current_codes:
            rotation_code = 2   
        elif ("G", 3) in current_codes:
            rotation_code = 3 

        end_x = self.current["X"]
        end_y = self.current["Y"]
        end_z = self.current["Z"]

        i = 0
        j = 0
        k = 0

        logger.debug(f"[motion_parse] current_codes: {current_codes}")

        for (code, val) in current_codes:
            if code == "X":
                end_x = val
            if code == "Y":
                end_y = val
            if code == "Z":
                end_z = val
            
            if code == "I":
                i = val
            if code == "J":
                j = val
            if code == "K":
                k = val

        # Interpolate
        logger.debug(f"[motion_parse] Current: {self.current}")

        if rotation_code < 2:
            retval = linear.generate_points(
                (int(self.current["X"] * MULTIPLIER), int(self.current["Y"] * MULTIPLIER), int(self.current["Z"] * MULTIPLIER)), 
                (int(end_x * MULTIPLIER), int(end_y * MULTIPLIER), int(end_z * MULTIPLIER)))
        else:
            ijk = (int(i * MULTIPLIER),int(j * MULTIPLIER),int(k * MULTIPLIER))
            retval = arc.segment(
                (int(self.current["X"] * MULTIPLIER), int(self.current["Y"] * MULTIPLIER), int(self.current["Z"] * MULTIPLIER)), 
                (int(end_x * MULTIPLIER), int(end_y * MULTIPLIER), int(end_z * MULTIPLIER)), 
                ijk, 
                g2 = rotation_code == 2, 
                g3 = rotation_code == 3)

        # Translate into XYZ format
        retval = self.translate_to_xyz(retval)

        # Add to lines (make sure we use Z heights)
        if retval:
            self.lines.append(retval)


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

    def parse_line(self, lines, minimum_step = 0.001):
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

        
        # Create first line if it doesn't exist, starting point
        if not self.lines:
            try:
                self.lines.append(
                    f"{int(self.starting_point[0] * 1000)} {int(self.starting_point[1] * 1000)} {int(self.starting_point[2] * 1000)} {int(self.tool_diameter * 1000)} {int(self.tool_holder_diameter * 1000)} {int((self.starting_point[2] + self.tool_length) * 1000)} 0"
                )
            except TypeError:
                # Values not found yet
                pass
        

        for line in [x.strip() for x in lines.split("\n") if x.strip() != ""]:
            logger.debug(f"Line:{line}")


            # Update the Current matches
            for match in matches:
                x = re.search(match.format(number_match), line)
                if x is not None:
                    logger.warning(x.group(1))

                    try:
                        output = float(x.group(1))
                    except Exception:
                        output = x.group(1)
                    
                    setattr(self, matches[match], output)
        

            # Get the current codes 
            current_codes = codes_parse(line)[0]

            # Run Motion parsing function
            self.motion_parse(line, current_codes)

            # Update the current set
            for (code, val) in current_codes:
                if code in "GM":
                    self.current[code].append(int(val))
                else:
                    self.current[code] = float(val)

        
        
        # Run all helper functions

        for item in [x for x in dir(self) if x.startswith("helper_")]:
            getattr(self, item)()
        
        return True