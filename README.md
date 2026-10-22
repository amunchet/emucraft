# Emucraft

## TODO: Need to have a block diagram of the pipeline (Gcode parser -> Arc helper -> C Kernel -> Blocks output -> Python renderer web page results)
Emucraft is the child of Emu.  

The goal is to determine whether or not a collision occurs during a G-code program.

This is done through modelling of the block and resulting 3D approxmiation of the machining path, compared with the cutting tool information.

Components:
    - G-code parser to XYZ file (Python)
    - Kernel in C to do actual collision detection and to return an array of block state
    - `Open3D` (Python) to render final block state or any collision states for visualization.

Key improvements over Emu:

- Separation of Visual and Backend
- Full tests and coverage
- Easy API

## MCP Server
`mcp_server.py` exposes Emucraft to a generative CAM pipeline over MCP (stdio):

```bash
pip install -r requirements.txt
python3 mcp_server.py
```

```json
{"mcpServers": {"emucraft": {"command": "python3", "args": ["/path/to/Emucraft/mcp_server.py"]}}}
```

Tools:
- `analyze_program` - tooling and block values detected from comments (with the line and match for each), fields that were missing, move/point counts
- `simulate` - builds and runs the C kernel over the stock block and reports a collision for any material removed by a rapid (G0) or spindle-off move.  `resolution` sets the grid size (default 0.01").  Needs a C compiler (`cc`, or set `EMUCRAFT_CC`)
- `generate_xyz` - writes the XYZ file for a program
- `test_detection`, `get_detection_settings`, `update_detection_settings`, `reset_detection_settings` - tune comment detection

Programs are passed as a file `path` or inline `gcode`.

### Comment detection
Tooling and stock come from program comments.  Detected fields: `tool_holder_diameter`, `tool_holder_length`, `tool_diameter`, `tool_length`, `cutting_length`, `shank_diameter`, `gauge_length` and `block_{x,y,z}_{min,max}`.

Each field has:
- **labels** - aliases matched against `(LABEL: value)` / `(LABEL = value)` / `; LABEL value` comments.  Case, punctuation and spacing are ignored, and typos are accepted down to `fuzz_threshold` similarity (default 0.85, so `GUAGE LENGTH` still matches `gauge length`; set 1.0 for exact only)
- **patterns** - regexes run on the whole line with `{num}` where the value is, e.g. `#127\s*=\s*{num}` for macro variables

Values with an explicit `mm` / `in` suffix are converted to `units` (default `in`).  The last match in a program wins.  Settings are saved to `detection.json` in the repo root (override with `EMUCRAFT_DETECTION`) and layered over the defaults in `gcode/gcode_parser.py`.

## KNOWN ISSUES
- The kernel only models the cutter, so `simulate` catches rapid/spindle-off moves into stock but not tool holder or shank collisions yet.  Holder, shank and cutting length are detected and reported for when it does.

- Helical interpolation is lazy - we need to ensure it checks at the lowest Z value (i.e., the destination) and not anywhere else.  Right now, the helical interpolation is only being applied in X and Y.  This shouldn't matter for normal 3 axis verifications, but it's worth noting.

## Roadmap
1.  [COMPLETE] Get the kernel working.  Be able to simulate cuts and block state.
2.  Translate G-code to `XYZ format` and simulate physical part being machined
    a.  [COMPLETE] Arc helper for helical interpolation
3.  Check performance
4.  UI frontend
5.  Integration into production process (CI/CD)

## Performance
So, using `numpy` turned out to be too slow even still.

At roughly the 5_000 x 5_000 size, it took ~.4 seconds to remove a sample section.

In C, it took .4 seconds to load the entire array, and then remove the sample section.  There wasn't a noticeable amount of time to change the sample section.

I think we're going to create a Python extension to leverage the `Open3d` easy visualization.  

## Resources
- https://pythonspeed.com/articles/python-extension-performance/

- http://www.open3d.org/docs/latest/python_api/open3d.visualization.draw_geometries.html

- http://www.open3d.org/docs/release/tutorial/geometry/pointcloud.html

- http://www.open3d.org/docs/0.9.0/tutorial/Basic/working_with_numpy.html

- http://www.open3d.org/docs/0.14.1/python_api/open3d.visualization.RenderOption.html?highlight=renderoption

- http://www.open3d.org/docs/0.14.1/tutorial/visualization/customized_visualization.html?highlight=renderoption

- https://github.com/isl-org/Open3D/issues/3307