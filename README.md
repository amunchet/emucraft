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

## KNOWN ISSUES
- Helical interpolation is lazy - we need to ensure it checks at the lowest Z value (i.e., the destination) and not anywhere else.  Right now, the helical interpolation is only being applied in X and Y.  This shouldn't matter for normal 3 axis verifications, but it's worth noting.

## Roadmap
1.  [COMPLETE] Get the kernel working.  Be able to simulate cuts and block state.
2.  Translate G-code to `XYZ format` and simulate physical part being machined
    a.  [COMPLETE] Arc helper for helical interpolation
3.  Check performance
4.  UI frontend
5.  Integration into production process (CI/CD)

## Web Deployment
The `web/` folder holds a small Flask service: upload a G-code program, it runs the parser and the C kernel, and renders the resulting stock (plus toolpath) in the browser.  It also flags any rapid or spindle-off move that removed material.

It has no authentication of its own - it's meant to sit behind a reverse proxy (Caddy) that handles that.

The compose file joins an **external** Docker network (`emucraft` by default) and publishes no ports:

```bash
docker network create emucraft   # once, if your Caddy stack doesn't already create it
docker compose up -d --build
```

Then, in the Caddy stack (attached to the same network):

```
emucraft.example.com {
    # ...your auth...
    reverse_proxy emucraft:8000
}
```

The page uses relative URLs, so serving it under a path prefix (`handle_path /emucraft/* { reverse_proxy emucraft:8000 }`) works too.  Long programs simulate synchronously, so keep Caddy's upstream timeouts above `EMUCRAFT_TIMEOUT`.

| Variable | Default | Description |
| --- | --- | --- |
| `EMUCRAFT_NETWORK` | `emucraft` | External Docker network to join |
| `EMUCRAFT_RESOLUTION` | `5` | Default XY grid cell size, in thousandths of a program unit |
| `EMUCRAFT_MAX_CELLS` | `2000` | Max grid cells per side; resolution is coarsened to fit (bounds memory use) |
| `EMUCRAFT_TIMEOUT` | `300` | Seconds before a simulation is killed |
| `EMUCRAFT_MAX_UPLOAD_MB` | `20` | Max upload size |

Endpoints: `GET /` (UI), `GET /healthz`, `POST /api/simulate?resolution=N` (multipart `file`, or the raw G-code as the request body).

Each simulation runs in a forked child process, so a kernel crash or memory leak can't take down the web worker.

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