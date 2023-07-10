#!/usr/bin/env python3
"""
Main GCode Runner
- Input: G Code File
- Output: XYZ file
"""
import os
import sys
import gcode_logger
import gcode_parser

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: input filename [output filename]")
        print("Use file to convert to XYZ coords")
    else:
        filename = sys.argv[1]
        if len(sys.argv) < 3:
            output_filename = ".".join(filename.split(".")[:-1]) + ".xyz"
        # Load in the file
        if not os.path.exists(filename):
            raise Exception("No input file found")

        with open(filename) as f:
            lines = f.read()
        
        a = gcode_parser.Program()
        a.parse_line(lines)
        a.adjust_coordinates()
        output = [x for section in a.lines for x in section]

        # Output to new file
        with open(output_filename, "w+") as f:
            f.write("\n".join(output))
        
