#!/usr/bin/env python3
"""
G-code parser
"""
import re

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
        print(line)
        tokens = re.findall(r"[A-Z]\d*\.?\d+", line)
        print(tokens)
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

    print("Codes:", codes)
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


    def parse_line(self, lines):
        """
        Parses Lines
        """
        print("Lines:", lines)
        # Check if the right types, if not, send through codes_parse
        # If that fails, throw an error

        matches = {
            r"Tool Holder Diameter: ((\d+.?)\d+(\.\d+)?)" : "tool_holder_diameter",
            r"#127\s*=\s*(\d+(\.\d+)?)" : "tool_holder_length",
            r"DIAMETER:\s*(\d+(\.\d+)?)" : "tool_diameter",
            r"#128\s*=\s*(\d+(\.\d+)?)" : "tool_length",
        }

        for line in [x.strip() for x in lines.split("\n") if x.strip() != ""]:
            print("Line:", line)

            for match in matches:
                x = re.search(match, line)
                if x is not None:
                    print(x.group(1))

                    try:
                        output = float(x.group(1))
                    except Exception:
                        output = x.group(1)

                    setattr(self, matches[match], output)
        
        return True