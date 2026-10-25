# Moonshot: cylinder contact orientations on a solid

Moonshot plans how a long cylinder, such as a sanding drum, touches every point of a part. Given a solid model,
it:

1. **lays a grid of points over the entire surface** at a fixed interval (default 0.01 in),
2. **finds, at every point, the orientations** (rotations about X, Y and Z) at which a cylinder of radius R
   (default 0.5 in) and length L (default 10 in) touches the point tangentially at the middle of its length
   (`o==[HERE]==`) without hitting the part, or the fixture holding it, anywhere else,
3. **picks one orientation per point**: the cylinder axis as level as the part allows (the **rotation about Y**,
   which for a part with a vertical wood grain means sanding with the grain), and as little change as possible
   between neighboring points,
4. optionally, **tracks the contact along the drum** (`--track`). The part makes passes along Y; during a pass the
   contact holds still, and between passes it moves a configurable step along the drum, back and forth across the
   drum's middle 9 in, so the drum wears along its length instead of in one groove. Each point keeps its
   orientation and the drum sands exactly the same patch.
5. **writes a FANUC robot program** (`python -m moonshot.fanuc`): an `.LS` file for Roboguide that runs every pass,
   with the robot holding the part against the fixed drum, relative to a part frame given by three points.

Every pose is written **both ways**: the cylinder moving over a fixed part, and the part moving against a fixed
cylinder, for a robot that holds the part up to a stationary drum.

Every step runs in parallel on the CPU (numba threads). The collision step also has a CUDA kernel for
NVIDIA GPUs.

## Quick start

```bash
pip install -e .            # Python >= 3.10: numpy, scipy, numba, trimesh
python -m moonshot "ingest/COLT ANACONDA FG MOLD MASTER  RIGHT.stl" --rotate -90 0 0 --mount 0 -1 0 --track --out results/grip
```

```
step 1: 110,715 grid points at 0.01 in, 432,107 neighbor pairs (8.7 s)
step 2: 103,814 of 110,715 points reachable (93.8%), 56,054 collision probes (25.2 s)
step 3: neighbor turn mean 2.50 -> 2.09 deg, pairs turning > 10 deg 6.40% -> 5.73%; |ry| mean 3.37 -> 3.12 deg, p95 24.9 deg (5.2 s)
step 4: tracking over the middle 9 in of the drum, 95% of points free over all of it; 27 passes 0.1 in apart, contact still during each and stepping 0.35 in between them (58% of steps exactly that, largest 3.97 in); busiest inch of the drum 99% -> 25.4% of the work (even: 11.1%) (10.9 s)
wrote results/grip/orientations.csv, result.npz, summary.json
```

Tracking is optional. Without `--track`, step 4 is skipped and every contact stays at the drum's middle; steps 1–3
print and write exactly the same either way.

Then the robot program, from the saved results (see [The FANUC program](#the-fanuc-program)):

```bash
python -m moonshot.fanuc results/grip --frame 0 0 0  1 0 0  0 1 0
```

```
program: 27 passes in 90 pieces, 5,325 points (5,227 in contact, 41 gaps bridged, 48 lifts); 120 in of contact, cycle about 239 s (53.2 s)
wrote results/grip/fanuc/sand.ls, program.csv, program.json
```

| Option | Default | Meaning |
|---|---|---|
| `--units` | `in` | Units of the model file (`in`, `mm`, `cm`, `m`). STL carries no units. |
| `--rotate RX RY RZ` | `0 0 0` | Turn the model first: fixed-axis X, Y, Z angles in degrees, the same convention as the output |
| `--spacing` | `0.01` | Grid interval, inches |
| `--radius` | `0.5` | Cylinder radius, inches |
| `--length` | `10` | Cylinder length, inches (contact is at its middle unless `--track` moves it along) |
| `--tolerance` | `0.001` | Penetration allowed before a pose counts as a collision, inches |
| `--bins` | `180` | Axis-angle resolution over 180° (180 = 1°) |
| `--margin` | `1` | Bins trimmed off each end of every collision-free arc, as a safety band |
| `--holder` | `fixture` | What holds the part: `fixture` (held from its mounting side), `table` (rests on a plane), or `none` |
| `--mount X Y Z` | `0 0 -1` | Direction from the part toward its holder |
| `--inset` | `0.05` | How far the fixture's outline stays inside the part's silhouette, inches |
| `--grain X Y Z` | `0 0 1` | Wood grain direction; the axis is kept square to it. Along Z this minimizes the rotation about Y. `0 0 0` turns it off |
| `--grain-weight` | `1e6` | Strength of the grain term per square inch. The default holds the tilt at the lowest the collisions allow; smaller values trade tilt for smoother motion |
| `--prefer X Y Z` | `1 0 0` | Tie-break axis direction where nothing else decides |
| `--track [WIDTH]` | off | Tracking: move the contact along the drum, between passes, over its middle WIDTH inches to spread its wear. `--track` alone uses 90% of the drum (9 in). Orientations do not change |
| `--track-step` | auto | How far the contact moves along the drum between passes, inches. The default is the band over the number of passes, so one part uses all of it |
| `--pass-spacing` | `0.1` | Distance between neighboring passes along Y, inches |
| `--track-gap` | `0.01` | Part material this close to the drum counts as touching it. Tracking never changes what touches, inches |
| `--track-sweep` | off | Sweep the contact continuously instead, during passes too |
| `--track-sweeps` | `1` | With `--track-sweep`: how many times the contact crosses the band over the part |
| `--track-slope` | `8` | With `--track-sweep`: most the contact may move along the drum per inch along the surface |

**Outputs** (in `results/<model>/` by default):

- `orientations.csv`: one row per grid point.
  - **Where:** position `x,y,z`, normal `nx,ny,nz`, `reachable`, and `feasible_pct`, the share of collision-free
    axis angles.
  - **Cylinder moving, part fixed:** cylinder axis `ax,ay,az`, cylinder center `cx,cy,cz`, rotations
    `rx,ry,rz` (degrees) and quaternion `qw,qx,qy,qz`, all in the part's frame. The cylinder's frame sits at
    `tool_x,tool_y,tool_z`, the middle of its contact line: the point itself without tracking.
  - **Part moving, cylinder fixed:** the part's pose in the cylinder's frame, as position `part_x,part_y,part_z`,
    rotations `part_rx,part_ry,part_rz` and quaternion `part_qw,part_qx,part_qy,part_qz`.
  - **Tracking:** `pass`, the pass the point belongs to (−1 without passes), and `track`, how far along the drum
    from its middle the point touches: one value per pass, 0 without `--track`. `track_min` and `track_max` are the
    range of offsets at which the drum still touches exactly the same material at that point (0 and 0 without
    `--track`). The center, `tool_*` and `part_*` positions already include `track`.
  - **Quality:** `grain_deg`, the tilt of the axis toward the grain (equal to |ry| with the default grain), and
    `change_deg`, the mean turn to neighboring points.
  - Unreachable points have empty (`nan`) orientation columns.
- `result.npz`: every array, including the per-point, per-angle collision masks.
- `summary.json`: parameters, timings, statistics (including tracking's) and the optimizer's energy history.

## The grip: a real part

`ingest/COLT ANACONDA FG MOLD MASTER  RIGHT.stl` is the right-hand panel of a finger-groove grip for a Colt
Anaconda. It is a VXelements scan: binary STL in inches, 1.04 million triangles, watertight, 2.36 × 4.64 ×
0.77 in. `ingest/` is kept out of git, so the results below can only be reproduced with the file in place.

**Orientation.** The panel was scanned lying on its inner (frame-side) face, with the outer face up, its length
along Y and the origin at the grip-screw hole. The wood grain runs along a grip's length. The rotation about Y
measures how far the drum axis tips out of level, so it only tracks the grain when the grain is vertical. The
run therefore stands the panel on its butt with `--rotate -90 0 0`, which sends scan Y to −Z: the butt faces
down, the outer face looks along +Y, and the finger grooves face −X. In that frame **|ry| is exactly how far the
drum axis tips toward the grain**, and level means the drum's scratches run along the grain.

**Holder.** A robot moves the panel against a stationary drum, holding it by its inner face: `--mount 0 -1 0`.
The fixture is modeled as the panel's outline seen from the front, shrunk by 0.05 in, and reaching 5.1 in
behind the panel, as far as a 10 in drum can reach. The inner face, and the pin holes and recesses in it, get no
grid points.

**Results at 0.01 in:**

- 110,715 grid points, 94% reachable.
- The drum stays level (|ry| under 5°) at 87% of reachable points. Elsewhere the part forces a tilt, mostly
  under the raised lip of the horn behind the trigger guard, where a level drum would hit the lip, and around the
  rounded top and butt.
- The unreachable 6% are the grip-screw recess (2,500 points), and the finger grooves and front corner of the
  butt, where they curve tighter than the 1 in drum (about 3,000). Also unreachable: a small U-shaped mark near
  the top of the back edge, and about 800 points on the straps where they face slightly backward, so a level
  drum would swing into the fixture.

How strongly to favor the grain over smoothness is the one real trade-off. On this grip, measured with the
same collision masks:

| Grain weight | Mean \|ry\| | Points over 5° | Mean neighbor turn | Neighbor pairs turning over 10° |
|---|---:|---:|---:|---:|
| none (smoothest field) | 13.43° | 66.0% | 0.81° | 0.97% |
| 1e3 | 5.03° | 22.4% | 1.07° | 2.18% |
| 1e4 | 3.73° | 16.8% | 1.36° | 3.36% |
| 1e5 | 3.25° | 13.8% | 1.77° | 4.87% |
| **1e6 (default)** | **3.12°** | **13.2%** | **2.09°** | **5.73%** |

At the default, fewer than 1% of points sit more than 1° above the lowest tilt their collisions allow. The 13%
of points that tilt over 5° are all forced by collisions; even the per-point best choice tilts as much.

**Tracking.** With `--track`, the panel makes 27 passes along Y, up and down the drum, 0.1 in apart. During each
pass the contact holds still; between passes it moves 0.35 in along the drum, back and forth across the middle 9 in.

- **Wear.** Without tracking, the busiest inch of the drum does 99% of the work. Stepping between passes brings
  that down to 25%; perfectly even wear would be 11.1%.
- **What holds it back.** A pass keeps one offset, so it can only use what every point on it allows. Six
  passes, most of them where they run onto the corner of the butt, can barely leave the drum's middle, because
  there the drum's axis points back into the modeled fixture. Ten more, along the straps and over the top, can use
  only one half of the band. The fixture is my assumption, a 9.6 in deep prism of the panel's outline. If it were
  5 in deep the busiest inch would be 20%, and with nothing behind the panel 14%.
- **The continuous sweep** (`--track-sweep`) gets 11.9%, because each point can take its own offset, but it moves the
  panel along the drum during every pass.

## The FANUC program

`python -m moonshot.fanuc <results>` turns a planned part into a FANUC program, in ASCII form, for Roboguide to
simulate. It runs every pass with the robot holding the part against a fixed drum, the setup step 2 checked. A real
controller loads an `.LS` file with FANUC's ASCII Upload option, or after converting it to `.TP` with `maketp`.

**Two frames.** Every position is the part frame (UTOOL 1) in the drum frame (UFRAME 1):

- **The part frame** comes from `--frame`, in the model file's own coordinates and units, before `--rotate`. It
  takes nine numbers for FANUC's three-point method: the origin, a point along +X, and a point on the +Y side of the
  XY plane. It also takes six numbers (an origin and W, P, R) or three (an origin, with the file's axes). The default
  is the file's own origin and axes; on the grip scan that is the grip-screw hole. In Roboguide, set UTOOL 1 to where
  that frame sits on the gripper, relative to the faceplate.
- **The drum frame** has its origin on the drum's surface at the middle of its length, where the part touches it.
  X runs along the drum's axis, Z points into the drum toward its axis, and Y = Z × X is the way the part moves across
  the drum. For a drum lying along world X and touched on top, UFRAME 1 is the top of the drum at its middle, with
  W = 180°, P = 0, R = 0.

**The moves.**

- The program starts with a joint move to 2 in off the first contact (FINE). Add your own moves from and to home.
- During a pass the part stays on the drum, except where it has to lift over something. Between passes it lifts
  0.25 in off (higher, or tilted, where that is needed to clear the drum), crosses over and touches down. The
  program ends 2 in off the last contact.
- Every move carries its own time (`msec`), so the drum crosses the part at `--feed` wherever the part frame is.
  A move slows down where the part would otherwise turn faster than `--max-turn`.
- After the first, every move is linear, and all but the last end in CNT100, so the robot blends from one point
  into the next.
- Every point gets the same configuration (`--config`). Roboguide shows whether it suits the cell.

| Option | Default | Meaning |
|---|---|---|
| `--frame N...` | file origin | Part frame (UTOOL): 3, 6 or 9 numbers, as above |
| `--name` | `SAND` | Program name; the file is `<name>.ls` |
| `--out` | `<results>/fanuc` | Output directory |
| `--setup` | `part` | `part`: the robot holds the part. `drum`: the robot holds the drum; then UFRAME is the part frame and UTOOL the drum's |
| `--rtcp` | off | Sand with FANUC's Remote TCP option: the drum frame's UFRAME is the remote TCP, and the sanding moves carry `RTCP` |
| `--uframe`, `--utool` | `1`, `1` | Frame numbers |
| `--config` | `N U T, 0, 0, 0` | Robot configuration written on every point |
| `--feed` | `120` | Speed of the contact across the part, in/min |
| `--rapid` | `240` | Speed off the part, in/min |
| `--max-turn` | `90` | Fastest the part may turn, °/s |
| `--swing` | `480` | Fastest the drum axis may turn along a stroke, relative to the surface, °/in. Higher follows step 3's axis more closely, with more twisting |
| `--lift` | `0.25` | Lift between passes, inches |
| `--approach` | `2` | Distance off the part of the first and last points, inches |
| `--tol`, `--angle-tol` | `0.001`, `0.5` | How far, in inches and degrees, a move between two points may stray from the planned contact |
| `--max-step` | `0.25` | Longest move between points, inches |

**Outputs** (in `<results>/fanuc/`):

- `sand.ls`: the program.
- `program.csv`: one row per point. It has the program line, the P number, X, Y, Z (mm) and W, P, R (degrees), the
  move's time, and the pass. It also has the contact point, normal and drum axis in the part frame (inches), the
  tracking offset, and the kind of move (`approach`, `touch`, `sand`, `bridge`, `lift`, `transit`, `retract`).
- `program.json`: the frames, the options and the statistics below.

**The grip**, with the defaults:

- **Size.** 5,325 points over 120 in of contact. The 27 passes run as 90 pieces. 41 gaps are bridged in contact.
  The part lifts off 48 times: 26 between passes, and 22 within passes, where the drum cannot cross or turn without
  hitting something. No lift is higher than 2 in.
- **Time.** About 4 minutes a part (239 s), of which 158 s is spent turning the part at the 90°/s limit. That is
  mostly over the top, around the butt and through the finger grooves.
- **Accuracy.** Between points, the moves stay within 0.001 in and 0.5° of the planned contact.
- **The check.** The drum goes at most 0.0017 in into the part; the planner allows 0.001 in plus `--tol`. It comes
  within 0.0009 in of the modeled fixture, inside the planner's 0.001 in tolerance, and clears the part by at least
  0.146 in between passes.
- **Tracking.** Every pass keeps its planned offset.
- **Grain.** Along the strokes the drum tilts 5.2° toward the grain on average, against step 3's 3.3° at the same
  points. That is the price of not swinging at the corners; a higher `--swing` trades some of it back for more
  twisting.
- **Read back.** Rebuilt from the file's X, Y, Z, W, P, R, every contact lands on the drum's contact line within
  0.00005 in.

The four samples pass the same check on a 0.03 in grid.

## Which file format?

**Use binary STL, exported from CAD at a fine tolerance** (chordal deviation ≤ 0.0005 in, angle ≤ 5°), and pass
`--units` if it is not in inches. Scans work as they come: the grip loads straight from VXelements.

- The algorithm needs a triangle mesh anyway (for sampling and collision probes), and every CAD and CAM
  package exports STL. OBJ, PLY, 3MF and GLB load the same way through trimesh.
- STEP is exact and carries units, but reading it needs an OpenCascade kernel. trimesh reads STEP when the
  optional `cascadio` package is installed (`pip install -e .[step]`). That path is wired up but untested here:
  no wheel exists for the Alpine build machine.
- **Mesh accuracy matters more than format.** When the cylinder lies along a straight or gently curved stretch of
  surface, it touches along a whole line. A normal that is off by an angle *a* lifts the cylinder's ends by about
  (L/2)·*a*. An early version of the sample parts, decimated with trimesh's default aggression, deviated
  0.003 in from the true surface and flagged 13% of a convex edge round as colliding. Gentler decimation
  (0.00014 in) brought that to 0%. Angle-weighted vertex normals were a second culprit: a single sliver triangle
  could tilt a flat face's normal by several degrees, which is why the normals are area-weighted. The grip scan
  is smooth: median dihedral angle 0.4° between neighboring triangles, with its sharp edges on the inner face.

## How it works

### Step 1: a grid of points over the entire surface

The grid is a **tri-dexel lattice**. Rays on a square lattice of pitch `spacing` are cast along X, Y and Z.
Each triangle is sampled only by the ray family most perpendicular to it,
so every face, including vertical walls and undercuts, gets a true square grid in its dominant
projection, and nothing is sampled twice. Shared edges follow a top-left fill rule, the same rule GPUs
use, so a lattice point on an edge belongs to exactly one triangle. On sloped faces the spacing
stretches by up to 1.41× (1.73× at worst) along the slope.

Normals are interpolated from **crease-aware vertex normals**: smooth across curved faces, never
smoothed across edges sharper than 30°. They are area-weighted, so the sliver triangles that
decimation leaves behind cannot tilt them. Neighbors are points within 1.75 grid steps whose normals turn by
less than 120°, so the two sides of a thin wall are not neighbors.

Faces on the **mounting side** are skipped. With a fixture, these are faces that have part material in front of
them when seen from the side opposite the holder: the face the part is held by, and the recesses in it. A height
map of the part along the mount direction decides this. With a table, only the faces resting on it are skipped.

### Step 2: collision-free orientations at every point

A cylinder that touches point *p* tangentially at the middle of its length has its axis through
**c = p + R·n**, lying in the tangent plane. The pose therefore has exactly **one free parameter**: the axis
angle θ in the tangent plane, over [0, 180°), because the cylinder is symmetric end to end.

The surface is sampled again, densely, as **collision probes**: a lattice at `sqrt(4·R·tol/3)` (0.026 in at the
defaults), plus samples every quarter step along sharp edges. For a probe *q*, write q − c = (x, y, h) in the
local frame and (x, y) = r(cos φ, sin φ). The probe is inside the cylinder at angle θ exactly when

```
h² + r² sin²(θ − φ) < (R − tol)²     and     r² cos²(θ − φ) < (L/2)²
```

That is the band `1 − (L/2)²/r² < sin²(θ − φ) < ((R − tol)² − h²)/r²`: at most two θ intervals, in closed
form. The colliding orientations of *p* are the union of these intervals over every probe the cylinder could
reach. They are accumulated per angle bin with a circular difference array, so the cost is O(1) per probe, and
any bin an interval touches is marked. That makes the bins **conservative**: an orientation between two bin
centers can never be missed. The test suite checks this against brute-force point-in-cylinder tests, for 1 in
and 10 in cylinders. It finds zero missed collisions, and allows at most 0.2% extra bins at interval edges; the
current runs flag none.

A 10 in cylinder can reach a pancake-shaped region 10 in across, which covers most of a small part. Only probes
inside the cylinder's slab, within R of its axis plane, can collide, though. The probes are therefore indexed
in two levels, coarse cells of a third of the reach split into fine cells about 0.08 in across, each with a
tight bounding box. Whole cells below the tangent plane are skipped, so a convex region costs about as much as it
does for a short cylinder.

The **holder** is an obstacle too:

- **Fixture** (default). The part's silhouette seen along `--mount`, shrunk by `--inset`, is swept behind the
  part as far as the cylinder can reach. It is sampled with its own probes at 0.05 in. This keeps a long drum
  from swinging into whatever holds the part.
- **Table.** The plane x·m = d at the part's far end along the mount direction is analytic. The cylinder reaches
  c·m + (L/2)|a·m| + R·sqrt(1 − (a·m)²) along m, which is checked exactly over each bin. Probes are skipped for
  points the table already blocks.

Each collision-free arc is then trimmed by `--margin` bins on both ends as a safety band.

On the S-channel, for example, nothing inside the groove is reachable with a 10 in drum, while the flat top leaves
every angle free. The 1 in roller of the first version could still ride the inner wall of each bend.

### Step 3: level, and the least angular change between neighbors

Each reachable point picks an axis angle θᵢ from its collision-free set, minimizing

```
E = Σ over neighbor pairs (i, j) of  sin²(θᵢ − θⱼ − δᵢⱼ)   +   w · Σ over points i of  Aᵢ (aᵢ · g)²
```

δᵢⱼ carries j's tangent frame into i's by the minimal rotation of nⱼ onto nᵢ, so the first sum counts how far
the cylinder turns relative to the surface. For a symmetric cylinder this has the same optimum as minimizing the
rotation of the full tool frames. The second sum is the **grain term**. aᵢ is the axis, g the grain direction
and Aᵢ the area per grid point. With g = Z, |aᵢ · g| = sin|ryᵢ|, so it is the rotation about Y. A level axis
always lies in the tangent plane, along n × g, so the term only bites where a collision blocks the level
direction. Its weight w is the trade-off in the table above. With uᵢ = e^(2iθᵢ), the first sum is the quadratic
form of a **connection Laplacian** and the second is linear in uᵢ, so the solver uses sparse linear algebra:

1. **Global relaxation.** Sparse linear solves (conjugate gradients with a two-level preconditioner) with the
   per-point pulls (the grain, plus a weak tie-break toward `--prefer`), and penalties that pull points whose
   relaxed angle collides toward their nearest free angle, with the penalty raised each round.
2. **Discrete polish.** Snap to collision-free bins, then graph-colored coordinate descent. Every move takes
   one point to its locally best free bin, so E only decreases, and points of one color update in parallel.
3. **Continuous refine.** The same descent with continuous angles, each point confined to its own
   collision-free arc, so the output is not quantized to bins.

The baseline in the statistics is the obvious per-point rule: each point's own best axis, ignoring its
neighbors. On the dome the level direction runs around it, so both fields are concentric circles. On the flat
base any axis is level, and the per-point choice jumps near the fillet ring and the corners, while the optimized
field spreads those forced rotations out.

Axis signs are then chosen so that neighbors point the same way, giving continuous rotations. The
**rotations** are fixed-axis X, then Y, then Z: R = Rz·Ry·Rx, roll-pitch-yaw, the same matrix as KUKA
A, B, C = rz, ry, rx. The tool frame is X = cylinder axis, Z = surface normal, so **all zeros means a
cylinder lying along world X on a level surface**, and ry is minus the elevation of the axis.

### Step 4 (optional): tracking

A drum that always meets the part at its middle wears a groove there. Tracking moves the contact along the drum
instead, and only that: each point keeps its orientation from step 3, and steps 1–3 run exactly as without it.
A cylinder is the same all along its length, so moving the contact *s* inches along the axis *a* is a shift of
the drum by −*s*·*a*, or of the part by +*s* along the drum when the part moves. The contact stays the same as
long as the drum's ends do not change what it touches.

- **Safe range.** Each point's drum, at its final orientation, is projected against the collision probes. Part
  material within `--track-gap` (0.01 in) of the drum's surface counts as touching it. Within the range
  [`track_min`, `track_max`], material that touches the drum at its middle stays on the drum, and material past
  its ends stays past them. The drum then sands exactly the same patch and touches nothing new. The holder only
  has to stay clear: the fixture model reaches as deep as the shifted drum can (9.6 in), and the table is checked
  analytically. A numba kernel over the same probe index as step 2 finds these ranges, and the tests check it
  against brute force.
- **Passes.** The surface is cut into passes: strips along Y, square to the drum's axis, the way the part feeds past
  the drum, `--pass-spacing` (0.1 in) apart. The distance across passes comes from integrating the drum's axis over
  the surface (least squares over neighboring points, one sparse solve), so passes follow the surface. On the grip
  they run up and down the outer face and round onto the straps. Where the axis turns all the way round a feature,
  as it does around the samples' walls, pockets and dome, the integral cannot close, and passes bend and widen there.
- **Steps.** During a pass the contact holds still. After each pass it moves `--track-step` along the drum, back and
  forth across the band. The default step is the band over the number of passes, so one part crosses it once. A pass
  keeps one offset, so it gets the overlap of its points' safe ranges. A pass whose range does not reach its turn
  takes the nearest offset it can, and the next passes carry on. Of 16 starting points along the way, the one that
  spreads the work most evenly is used.

The continuous sweep (`--track-sweep`) instead lets every point take its own offset, so it moves the part during
passes too. Its offset follows the distance along the surface from each region's lowest point along the grain, ranked
by area so every inch of the band gets the same share of the surface. It is clipped to each point's safe range,
eased so it never changes faster than `--track-slope` (8 in along the drum per inch of surface), and re-spread
where clipping piles work onto the middle.

The drum spins, so its wear depends only on position along it. In the statistics and pictures, each grid point
stands for its share of the surface, and its work is spread over the stretch of drum it touches. That stretch is
its contact patch, about 0.3 in on the grip and 3–5 in where a drum lies along one of the samples' flat faces.

| Part | Passes, step | Busiest inch of the drum: without tracking | stepping between passes | continuous sweep |
|---|---|---:|---:|---:|
| grip | 27, 0.35 in | 99% | 25% | 11.9% |
| `wave_plate` | 75, 0.12 in | 49% | 21% | 13.7% |
| `dome_fillet` | 51, 0.18 in | 54% | 25% | 12.5% |
| `s_channel` | 61, 0.15 in | 37% | 23% | 14.4% |
| `pocket_block` | 54, 0.17 in | 38% | 17% | 14.2% |

Even wear over a 9 in band would be 11.1% per inch. The samples' flat faces are narrower than the drum, so a drum
lying along one must keep covering it, which limits most of their points. Their long contact patches already spread
the untracked work over several inches. On the S-channel, for example, the drum lies along flat tops and walls that
are narrower than it, so most passes can use only part of the band, and the passes bend around the groove's ends,
where the drum's axis turns.

### Step 5: the robot program

- **Strokes.** Each pass becomes a stroke through the middle of its strip. The stroke is a shortest path over the
  neighbor graph, pulled toward the strip's middle, from one end of each connected piece to the other. Each stroke
  runs whichever way starts nearer to where the last one ended.
- **The drum axis along a stroke.** Step 3 holds the drum level wherever that is collision-free, point by point. In
  places, a level axis is free at one point and blocked at the next. Over the grip's corners, step 3's axis
  therefore swings back and forth, which a robot would follow as twisting. Along each stroke the axis is chosen
  again by dynamic programming over the same collision-free angles. The cost is step 3's turning and grain terms,
  plus a limit on how fast the axis may turn (`--swing`). A turn that would sweep the drum through blocked angles
  costs as much as lifting off, and the stroke is cut wherever one is taken. The 1° bins are then smoothed, inside
  each point's collision-free arc.
- **Offsets.** Each pass keeps its tracking offset from step 4. The offset is checked again for the stroke's axes,
  and moved to the nearest safe offset if needed.
- **Edges.** Where the orientation turns by more than 2° between neighboring points, as it does over an edge, the
  drum rolls over in 2° steps, each one settled onto the probes so the drum just touches. Where it cannot turn that
  way without hitting something, the stroke is cut there and the part lifts off instead.
- **Points.** A point is kept only where it is needed: where a straight move to a later point would stray more than
  `--tol` from the planned contact or more than `--angle-tol` from its orientation, judged as the controller moves.
  Holding the part, the controller moves the part frame's origin in a straight line and turns about one fixed axis.
  With Remote TCP, or holding the drum, it moves the drum frame's origin straight across the part instead. Every
  move is then sampled against all the probes, and split where the drum would dig in further than the planner's
  tolerance plus `--tol`.
- **Between pieces.** A gap within a pass is bridged, staying low, where the drum stays clear; otherwise the part
  lifts off. Each lift tries five directions (straight off the drum, or tilted 45° along or across it) at heights
  doubling from `--lift`. It takes the lowest one whose lift, move across and touchdown all clear the drum by
  0.1 in.
- **The check.** Every move is sampled as the controller runs it, against the part's probes, the fixture and the
  drum. The program reports the deepest the drum goes into the part, the closest it comes to the fixture, and its
  clearance between passes, and warns when any is out of bounds.

### Both ways: cylinder moving or part moving

The tool pose (R, p) puts the cylinder's frame at contact point *p* with rotation R. When the part moves
instead, the cylinder stays in its own frame: X along its axis, Z toward its center, the contact point at the
origin. The part then sits at the inverse pose: rotation Rᵀ and position −Rᵀp, so a part point x lands at
Rᵀ(x − p). Those are the `part_*` columns. A robot running in external-TCP (stationary tool) mode can use the
tool pose directly. The part pose drives a simulation, or a robot that is programmed in flange coordinates
with the drum's frame as its base. With tracking, the cylinder's frame sits at p − s·a instead (the `tool_*`
columns), and the part lands with each contact at (s, 0, 0).

## Parallelism and performance

Every step uses numba `prange` threads:

- Step 1 rasterizes triangles in parallel (count pass, then fill), and computes crease-aware normals per
  triangle corner in parallel.
- Step 2 is embarrassingly parallel per grid point. It visits probe cells nearest-first, culls coarse and fine
  cells with tight bounding boxes, and stops as soon as every angle is blocked.
- Step 3 colors the neighbor graph greedily, then updates each color class in parallel.
- Step 4 finds every point's safe range in parallel over the same two-level probe index as step 2. The passes
  come from one sparse solve; the continuous sweep's phase, easing and re-spreading are shortest-path searches
  over the neighbor graph.

For an NVIDIA GPU, `collision_masks(..., device="cuda")` runs Step 2 with one thread block per grid point,
sharing a difference array in shared memory with atomic updates (`pip install numba-cuda`). It is tested
for exact agreement with the CPU kernel in numba's CUDA simulator, at 1 in and 10 in. **It has not been
benchmarked on real hardware**, because no GPU was available while building this.

Timings at the defaults with `--track` (0.01 in grid, 10 in cylinder, 4 CPU threads, Alpine Linux VM):

| Part | Grid points | Reachable | Step 1 | Step 2 | Step 3 | Step 4 | Mean neighbor turn (naive → optimized) | Pairs turning > 10° | Mean \|ry\| (naive → optimized) |
|---|---:|---:|---:|---:|---:|---:|---|---|---|
| grip (scan) | 110,715 | 94% | 8.7 s | 25.2 s | 5.2 s | 10.9 s | 2.50° → 2.09° | 6.40% → 5.73% | 3.37° → 3.12° |
| `wave_plate` | 248,296 | 67% | 2.7 s | 24.7 s | 4.8 s | 8.8 s | 1.11° → 1.00° | 2.55% → 2.26% | 1.65° → 1.25° |
| `dome_fillet` | 381,344 | 94% | 3.1 s | 116.1 s | 29.6 s | 26.3 s | 0.57° → 0.29° | 0.77% → 0.02% | 0.23° → 0.00° |
| `s_channel` | 329,917 | 73% | 2.4 s | 34.0 s | 22.1 s | 17.9 s | 0.55° → 0.33° | 1.23% → 0.41% | 1.19° → 0.84° |
| `pocket_block` | 366,925 | 71% | 2.6 s | 17.8 s | 26.4 s | 14.8 s | 0.55° → 0.21° | 0.75% → 0.03% | 0.37° → 0.00° |

Most of the grip's step 1 is loading its 52 MB scan. Step 2 costs most where a long drum lies across a large flat
area next to a tall feature, as on the dome's base. Step 4 finds the safe ranges, lays out the passes and picks the
steps in 9–26 s. Times vary from run to run on this shared VM: the dome's step 3 has taken anywhere from 29 to 76 s.

A "turn" is the angle the cylinder rotates, relative to the surface, between two neighboring grid
points. The naive baseline is each point's own best axis.

## Sample solids

`samples/` holds four procedural parts (binary STL, inches, about 120k triangles each). They are
generated by `python -m moonshot.samples` from implicit models with true rounded fillets, meshed with
marching cubes and gently decimated (needs `pip install -e .[dev]`).

| Sample | What it exercises, with a 10 in drum |
|---|---|
| `wave_plate` | Egg-crate top. A straight drum bridges every valley and saddle, so only the peaks, the ridges between them and the walls are reachable |
| `dome_fillet` | Dome blended into a base by a 0.75 in concave fillet. The level direction runs around the dome and nothing blocks it, so the output is level everywhere. Only axes running around the ring fit the fillet, and its lowest band is unreachable |
| `s_channel` | S-shaped full-round groove (0.7 in radius). A straight drum this long cannot follow either bend, so the whole groove is unreachable; the tops and walls are free |
| `pocket_block` | Pocket with 0.2 in floor fillets and a 0.6 in through hole. Both are far smaller than the drum, so they are unreachable; everything else is free |

## Pictures

`python -m moonshot.assets` renders step-by-step pictures of each part into `assets/<part>/`, which is not kept in
git (needs `pip install -e .[assets]`). They show the model, the grid, reachability, the collision checks at three
points, the axis field, the grain, the rotations and statistics, a rollout of three strokes both ways, the tracking
maps and drum wear, and a tracked rollout of six neighboring passes. For the grip there is also
`5_track_full.gif`, the whole tracked run. The script reuses `results/`, includes the grip when `ingest/` has it,
and takes about 100 minutes on 4 CPU threads.

## MCP server

`python -m moonshot.server` (or `moonshot-mcp`) serves the whole pipeline over the
[Model Context Protocol](https://modelcontextprotocol.io). A client uploads a model, plans it, renders its
pictures and GIFs, writes the robot program, and downloads any of it. Install with `pip install -e .[server]`.

```bash
python -m moonshot.server                                   # stdio, for a local client (Claude Code, Desktop)
python -m moonshot.server --http --host 0.0.0.0 --port 8000 # streamable HTTP at /mcp
docker build -t moonshot-mcp . && docker run -p 8000:8000 -v moonshot-data:/data \
    -e MOONSHOT_TOKEN=... -e MOONSHOT_URL=https://moonshot.example.com moonshot-mcp
```

| Tool | What it does |
|---|---|
| `upload_start`, `upload_chunk`, `upload_finish` | Send a model in base64 chunks (1 MB suggested, 8 MB at most) in order. Resending a chunk is harmless. The finished file is checked against its size and sha256 and loaded once as a mesh before it is kept |
| `list_models` | Uploaded models, with triangle count, watertightness and bounds |
| `plan` | Queue steps 1-4 with the command line's options. Tracking is on by default, because the program and the tracking pictures need it. Returns a run id |
| `render_assets` | Queue the pictures into `assets/`: the step PNGs and `4_rollout.gif`, `5_track.png` and `5_track.gif`, and `5_track_full.gif`, every pass of the run |
| `robot_program` | Queue the FANUC program into `fanuc/` with `python -m moonshot.fanuc`'s options |
| `status`, `list_runs` | Each task's state (queued, running, done, failed), its progress log, and the error if it failed |
| `stats` | Parameters, timings, reachability, neighbor turn, grain tilt, tracking and drum wear, and the program's cycle time once written |
| `screenshot` | Render one view now, colored by `model`, `reach`, `grain`, `track` or `pass`, with an optional camera. Shown inline and saved in `shots/` |
| `view_image` | Show any PNG or GIF from a run inline (up to 8 MB) |
| `list_files`, `read_file` | Every file of a run with its size, sha256 and download URL, and base64 chunks of any of them |

Planning, assets and programs take from seconds to tens of minutes, so their tools queue the work and return
immediately. Poll `status` for progress. One task runs at a time, since each already uses every CPU thread. A
restart marks unfinished tasks failed, and finished runs are kept. Everything lives in the data directory
(`--data`, or `$MOONSHOT_DATA`, default `./serve`, `/data` in the image): `models/<id>/` and `runs/<id>/`.

Over HTTP, files also move without base64: `PUT /files/models/<filename>` uploads a model as the raw request
body, and `GET /files/runs/<run>/<path>` downloads any file of a run, with range requests. `GET /health` is a
liveness check. When `$MOONSHOT_TOKEN` is set, `/mcp` and `/files/...` require `Authorization: Bearer <token>`.
With `$MOONSHOT_URL` set, `list_files` gives each file's download URL. On localhost the SDK's DNS-rebinding guard
is on. A deployment on a public address should set the token or sit behind an authenticating proxy.

Assets for an uploaded part are framed automatically from its holder direction and size, because the
hand-tuned framings in `assets.SHOTS` apply only to the samples and the grip.

## Conventions and caveats

- **Units** are inches throughout. **Z is up.** Turn a model into its working pose with `--rotate`; every other
  direction (`--mount`, `--grain`, `--prefer`) is given in the turned frame.
- **The grain direction is an assumption.** For the grip it is taken along the panel's length, which is how
  grip blanks are usually cut. If the grain runs at an angle, pass that direction to `--grain`.
- **The fixture is an assumption.** It is modeled as the panel's outline, 0.05 in inside its edge, swept behind
  it. A smaller fixture frees a few more poses near the edges, and an arm that reaches past the outline would
  block more. Describe the real fixture and it can be modeled exactly.
- **Tolerance.** A pose may penetrate the surface by up to `--tolerance` (0.001 in). Probe spacing keeps a missed
  flat-face penetration below a third of that. Where the rim of a cylinder end digs into a face at an angle,
  sparse probes can miss up to about 0.7 × the probe spacing. The one-bin margin covers that: about 0.09 in of
  end travel at L = 10 in.
- **Euler angles.** A vertical cylinder axis is a gimbal lock for this convention; there rz is set to 0 and
  rx carries the rotation. The orientation field is a line field, so around half-turn singularities a
  180° flip about the normal is unavoidable. The cylinder is identical after the flip, but rx and rz can
  jump by 180° along such a seam. The quaternion columns have the same property.
- **Only the drum is modeled, not its mounts.** The drum is treated as a bare 10 in cylinder; arbors or guards
  beyond its ends are not.
- **Passes are the planner's own layout.** They are strips along Y, `--pass-spacing` apart, numbered in the `pass`
  column, and every point of a pass shares one offset. The FANUC program runs one stroke per pass. Set
  `--pass-spacing` to your stepover.
- **The robot program is checked against the part, the modeled fixture and the drum, not the robot.** Reach, joint
  limits, singularities, the configuration and the gripper's real shape are for Roboguide to show.
- **Points are dense where the part turns.** Over edges a move can last only a few hundredths of a second. CNT100
  blends them, but the robot may run slower than the estimated cycle. A larger `--angle-tol` thins them.
- **Remote TCP is untested here.** `--rtcp` writes the `RTCP` motion option and spaces the points for it. It needs
  the option on the controller, with UFRAME 1 set up as its remote TCP. No controller was available to try it on.
- **Tracking's contact gap stands in for the drum's give.** Part material within `--track-gap` (0.01 in) of the
  drum counts as touching it. A softer drum, whose sleeve squashes further, touches a wider patch: raise the gap.
  The fixture model reaches 9.6 in behind the part for tracking, as far as the shifted drum can. A real fixture
  that is shallower leaves the drum more room on the straps and the butt, and so lets more passes use the whole band.

## Tests

```bash
pip install -e .[dev] && pytest
```

The tests cover the following:

- Grid counts that are exact on a box, and points that lie on the surface.
- Crease handling, and the mounting side being hidden under a fixture.
- Euler round trips, |ry| as the axis tilt, and the part pose inverting the tool pose.
- Collision masks against brute force, for 1 in and 10 in cylinders.
- The closed-form table clearances on a wall (R lying flat, L/2 standing, more at 45°).
- A fixture that blocks a cylinder reaching behind the part but not one beside it.
- A trough that forces alignment.
- The optimizer staying feasible, propagating a constraint, and leveling the axis under the grain term.
- Tracking's safe ranges against brute force, and in closed-form cases: a flat face, a post past the drum's end,
  a holder that grazes the drum without hitting it, and a tilted drum above a table.
- Passes that follow a curved surface as strips of the right width, and offsets that hold through each pass, step
  once per pass, turn back at the ends of the band, and give way to a restricted pass.
- Swept offsets that stay in range, change no faster than the slope limit, and use the band evenly.
- Tracked runs, stepped and swept, whose orientations match the untracked run exactly, and where the drum, at
  each chosen offset, touches the same probes and hits none.
- Sign consistency.
- An end-to-end run.
- The FANUC program: frames from three points and from W, P, R, and moves that interpolate the way a linear move
  does. It also covers a capped drum axis that does not swing where an uncapped one would, and an `.LS` file that
  reads back line for line. Rebuilt from X, Y, Z, W, P, R, every contact lands on the drum's contact line at its
  pass's offset, normal into the drum and axis along it, on the part's surface. An independent simulation of the
  robot's straight moves never cuts into the part. Holding the drum writes the inverse poses, and only the sanding
  moves carry `RTCP`.
- The GPU kernel against the CPU kernel, in the CUDA simulator.
- The MCP server through a real MCP client: a model uploaded in chunks arrives byte for byte, and a bad sha256,
  a non-mesh, a disallowed file type, or a chunk that skips bytes is refused. A planned run logs all four steps
  and reports its stats. Every file downloads in chunks to its listed sha256. Paths that climb out of a run are
  refused. Screenshots come back as PNGs and are saved, and the HTTP file routes and `/mcp` turn away requests
  without the token.
