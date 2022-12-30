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
    lines = gcode.split('\n')

    # Initialize the list of codes
    codes = []

    # Iterate through each line of gcode
    for line in lines:
        # Split the line into tokens
        tokens = re.findall(r'[A-Z]|[0-9.]+', line)

        # Extract the code and value from the line
        code = tokens[0]
        value = float(tokens[1])

        # Add the code and value to the list of codes
        codes.append((code, value))

    return codes

class Program:
    def __init__(self):
        """
        Initialize Program Instance
        """
        lines = []

    def parse_line(lines):
        """
        Parses Lines
        """

        # Check if the right types, if not, send through codes_parse
        # If that fails, throw an error