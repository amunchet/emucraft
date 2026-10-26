"""Run the three steps (and optionally tracking, step 4) on a solid and write the results.

    python -m moonshot part.stl --spacing 0.01 --radius 0.5 --length 10 --out results/part
    python -m moonshot part.stl --track            # also step the contact along the drum between passes
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from .geometry import UNITS, angles, load_mesh, part_poses, tangent_frames, tool_rotations
from .grid import neighbor_graph, surface_grid
from .holder import fixture_probes, front_view, hidden_faces, table
from .reach import collision_masks, erode, probe_points
from .smooth import line_change, optimize
from .track import plan


def run(path, spacing=0.01, radius=0.5, length=10.0, tolerance=0.001, bins=180, margin=1, holder="fixture",
        mount=(0.0, 0.0, -1.0), inset=0.05, grain=(0.0, 0.0, 1.0), grain_weight=1e6, prefer=(1.0, 0.0, 0.0),
        rotate=(0.0, 0.0, 0.0), units="in", track=None, mode="passes", step=None, pass_spacing=0.1, sweeps=1, gap=0.01,
        slope=8.0, log=print):
    """Steps 1-3 on one solid, and step 4 when `track` is set. Returns per-point arrays (inches, degrees) and stats.

    `holder` is "fixture", "table" or "none" and `mount` points from the part toward it (see
    `holder`). `grain` is the direction the axis is kept square to (None to ignore). `track` is
    the stretch of drum, in inches, the contact moves over (True for 90% of its length); see
    `track.plan` for the rest. Tracking never changes steps 1-3.
    """
    clock = time.perf_counter()
    times = {}
    mesh = load_mesh(path, units, rotate)
    plane = skip = view = None
    if holder == "table":
        plane, skip = table(mesh, mount)
    elif holder == "fixture":
        view = front_view(mesh, mount, min(spacing, 0.01) / 2)
        skip = hidden_faces(mesh, view)
    points, normals, faces, family = surface_grid(mesh, spacing, skip)
    t1, t2 = tangent_frames(normals)
    edges = neighbor_graph(points, normals, spacing)
    times["grid"] = time.perf_counter() - clock
    log(f"step 1: {len(points):,} grid points at {spacing:g} in, {len(edges):,} neighbor pairs ({times['grid']:.1f} s)")

    clock = time.perf_counter()
    probe_spacing = np.sqrt(4 * radius * tolerance / 3)  # misses at most a third of the tolerance on flat faces
    probes = probe_points(mesh, probe_spacing)
    self_hit, holder_hit = collision_masks(points, normals, t1, t2, probes, radius, length, tolerance, bins, plane)
    if view is not None:  # the fixture only matters where the part itself leaves room
        open_ = np.flatnonzero(~self_hit.all(axis=1))
        fixture = fixture_probes(view, inset, max(probe_spacing, 0.05), np.hypot(length / 2, 2 * radius) + spacing)
        holder_hit[open_] = collision_masks(points[open_], normals[open_], t1[open_], t2[open_], fixture, radius,
                                            length, tolerance, bins)[0]
    feasible = erode(~(self_hit | holder_hit), margin)
    times["reach"] = time.perf_counter() - clock
    reachable = feasible.any(axis=1)
    log(f"step 2: {reachable.sum():,} of {len(points):,} points reachable ({reachable.mean():.1%}), "
        f"{len(probes):,} collision probes ({times['reach']:.1f} s)")

    clock = time.perf_counter()
    res = optimize(points, normals, t1, t2, feasible, edges, prefer, grain=grain, grain_weight=grain_weight)
    times["optimize"] = time.perf_counter() - clock
    ok = reachable
    fields = {k: orientations(points, normals, t1, t2, res[k], ok) for k in ("theta", "naive")}
    out = fields["theta"]

    # Per-point mean turn to its neighbors and angle to the grain, for the optimized field and the naive baseline.
    e = res["edges"]
    per_edge = {"optimized": line_change(res["theta"], res["delta"], e), "naive": line_change(res["naive"], res["delta"], e)}
    degree = np.bincount(e.ravel(), minlength=len(points))
    change = {k: np.bincount(e.ravel(), np.repeat(v, 2), len(points)) / np.maximum(degree, 1) for k, v in per_edge.items()}
    g = np.asarray(grain if grain is not None else (0.0, 0.0, 1.0), float)
    tilt = {k: np.degrees(np.arcsin(np.minimum(np.abs(f["axis"][ok] @ g) / np.linalg.norm(g), 1.0)))
            for k, f in zip(("optimized", "naive"), (fields["theta"], fields["naive"]))}
    stats = {k: {"mean": float(v.mean()), "p95": float(np.percentile(v, 95)), "over_10_pct": float(100 * np.mean(v > 10)),
                 "max": float(v.max()), "grain_mean": float(tilt[k].mean()), "grain_p95": float(np.percentile(tilt[k], 95)),
                 "grain_over_5_pct": float(100 * np.mean(tilt[k] > 5)), "grain_max": float(tilt[k].max())}
             for k, v in per_edge.items()}
    grain_deg = np.full(len(points), np.nan)
    grain_deg[ok] = tilt["optimized"]
    tilt_name = "|ry|" if np.allclose(g / np.linalg.norm(g), (0, 0, 1)) else "tilt toward the grain"
    log(f"step 3: neighbor turn mean {stats['naive']['mean']:.2f} -> {stats['optimized']['mean']:.2f} deg, pairs turning "
        f"> 10 deg {stats['naive']['over_10_pct']:.2f}% -> {stats['optimized']['over_10_pct']:.2f}%; {tilt_name} mean "
        f"{stats['naive']['grain_mean']:.2f} -> {stats['optimized']['grain_mean']:.2f} deg, p95 "
        f"{stats['optimized']['grain_p95']:.1f} deg ({times['optimize']:.1f} s)")

    params = {"source": str(path), "units": units, "rotate": list(rotate), "spacing": spacing, "radius": radius,
              "length": length, "tolerance": tolerance, "bins": bins, "margin": margin, "holder": holder,
              "mount": list(mount), "inset": inset, "grain": None if grain is None else list(grain),
              "grain_weight": grain_weight, "prefer": list(prefer), "probe_spacing": float(probe_spacing),
              "track": None, "track_mode": mode, "track_step": step, "pass_spacing": pass_spacing, "sweeps": sweeps,
              "gap": gap, "slope": slope}
    result = {"mesh": mesh, "points": points, "normals": normals, "faces": faces, "family": family, "t1": t1, "t2": t2,
              "edges": edges, "self_hit": self_hit, "holder_hit": holder_hit, "feasible": feasible, "theta": res["theta"],
              "naive": res["naive"], "axis": out["axis"], "euler": out["euler"], "quat": out["quat"],
              "part_euler": out["part_euler"], "part_quat": out["part_quat"], "naive_euler": fields["naive"]["euler"],
              "grain_deg": grain_deg, "change": change["optimized"], "change_naive": change["naive"], "pair_edges": e,
              "pair_delta": res["delta"], "history": res["history"], "stats": stats, "times": times, "params": params,
              "track_stats": None}
    tracked = {k: np.where(ok, 0.0, np.nan) for k in ("track", "track_lo", "track_hi")}
    tracked["pass"] = np.full(len(points), -1)
    if track:
        clock = time.perf_counter()
        tracked = plan(result, None if track is True else track, mode, step, pass_spacing, sweeps, gap, slope, probes)
        times["track"] = time.perf_counter() - clock
        ts = result["track_stats"] = tracked.pop("stats")
        params.update(track=ts["band"], track_step=ts.get("step"))
        if mode == "sweep":
            motion = (f"offset moves median {ts['rate_p50']:.1f}, at most {ts['rate_max']:.1f} in along the drum per inch "
                      f"of surface")
        else:
            motion = (f"{ts['passes']} passes {pass_spacing:g} in apart, contact still during each and stepping "
                      f"{ts['step']:.2f} in between them ({ts['moves_one_step_pct']:.0f}% of steps exactly that, largest "
                      f"{ts['move_max']:.2f} in)")
        log(f"step 4: tracking over the middle {ts['band']:g} in of the drum, {ts['full_band_pct']:.0f}% of points free over "
            f"all of it; {motion}; busiest inch of the drum {ts['busiest_inch_pct_untracked']:.0f}% -> "
            f"{ts['busiest_inch_pct']:.1f}% of the work (even: {ts['even_inch_pct']:.1f}%) ({times['track']:.1f} s)")
    result.update(tracked)
    result.update(tracked_poses(points, normals, out["axis"], out["part_pos"], result["track"], radius))
    return result


def tracked_poses(points, normals, axis, part_pos, track, radius):
    """Where the cylinder and the part go when the contact sits `track` along the cylinder from its middle.

    The cylinder moves by -track along its axis: its frame's origin (`tool`, the middle of its
    contact line) and its `center` shift, and in the cylinder's frame the part moves by +track
    along X. Unreachable points keep the untracked center and tool at the point itself.
    """
    shift = np.where(np.isfinite(track), track, 0.0)[:, None] * np.nan_to_num(axis)
    return {"tool": points - shift, "center": points + radius * normals - shift,
            "part_pos": part_pos + np.where(np.isfinite(track), track, 0.0)[:, None] * [1.0, 0.0, 0.0]}


def orientations(points, normals, t1, t2, theta, ok):
    """Axis, tool rotation and part pose (NaN where unreachable) for axis angles `theta`."""
    count = len(points)
    out = {"axis": np.full((count, 3), np.nan), "euler": np.full((count, 3), np.nan), "quat": np.full((count, 4), np.nan),
           "part_pos": np.full((count, 3), np.nan), "part_euler": np.full((count, 3), np.nan),
           "part_quat": np.full((count, 4), np.nan)}
    if ok.any():
        axis = np.cos(theta[ok])[:, None] * t1[ok] + np.sin(theta[ok])[:, None] * t2[ok]
        rot = tool_rotations(axis, normals[ok])
        pos, inv = part_poses(points[ok], rot)
        out["axis"][ok] = axis
        out["euler"][ok], out["quat"][ok] = angles(rot)
        out["part_pos"][ok] = pos
        out["part_euler"][ok], out["part_quat"][ok] = angles(inv)
    return out


def save(result, out):
    """Write orientations.csv (one row per grid point), result.npz (arrays) and summary.json."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    ok = result["feasible"].any(axis=1)
    rows = np.column_stack([result["points"], result["normals"], ok, 100 * result["feasible"].mean(axis=1),
                            result["axis"], result["center"], result["euler"], result["quat"], result["grain_deg"],
                            result["change"], result["part_pos"], result["part_euler"], result["part_quat"],
                            result["track"], result["track_lo"], result["track_hi"], result["tool"], result["pass"]])
    header = ("x,y,z,nx,ny,nz,reachable,feasible_pct,ax,ay,az,cx,cy,cz,rx,ry,rz,qw,qx,qy,qz,grain_deg,change_deg,"
              "part_x,part_y,part_z,part_rx,part_ry,part_rz,part_qw,part_qx,part_qy,part_qz,"
              "track,track_min,track_max,tool_x,tool_y,tool_z,pass")
    np.savetxt(out / "orientations.csv", rows, delimiter=",", header=header, comments="", fmt="%.6g")
    arrays = {k: result[k] for k in ("points", "normals", "family", "t1", "t2", "edges", "theta", "naive", "axis",
                                     "center", "euler", "quat", "part_pos", "part_euler", "part_quat", "naive_euler",
                                     "grain_deg", "change", "change_naive", "pair_edges", "pair_delta", "track",
                                     "track_lo", "track_hi", "tool", "pass") + ("patch_lo", "patch_hi") if k in result}
    for k in ("feasible", "self_hit", "holder_hit"):
        arrays[k] = np.packbits(result[k], axis=1)
    np.savez_compressed(out / "result.npz", bins=result["params"]["bins"], **arrays)
    summary = {"params": result["params"], "times_s": result["times"], "points": int(len(ok)),
               "reachable": int(ok.sum()), "neighbor_turn_deg": result["stats"], "tracking": result.get("track_stats"),
               "energy_history": [[k, v] for k, v in result["history"]]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2))


def load(out):
    """Read back what `save` wrote (arrays plus the summary), with the mesh reloaded from its source."""
    out = Path(out)
    summary = json.loads((out / "summary.json").read_text())
    with np.load(out / "result.npz") as data:
        result = {k: data[k] for k in data.files}
    bins = int(result.pop("bins"))
    for k in ("feasible", "self_hit", "holder_hit"):
        result[k] = np.unpackbits(result[k], axis=1, count=bins).astype(bool)
    params = summary["params"]
    result.update(mesh=load_mesh(params["source"], params["units"], params["rotate"]), params=params, times=summary["times_s"],
                  stats=summary["neighbor_turn_deg"], track_stats=summary.get("tracking"),
                  history=[tuple(h) for h in summary["energy_history"]])
    return result


def main(argv=None):
    """Command-line entry point."""
    ap = argparse.ArgumentParser(prog="moonshot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="solid model: STL (recommended), OBJ, PLY, 3MF, GLB, or STEP with cascadio")
    ap.add_argument("--units", default="in", choices=sorted(UNITS), help="units of the model file (default in)")
    ap.add_argument("--rotate", type=float, nargs=3, default=(0.0, 0.0, 0.0), metavar=("RX", "RY", "RZ"),
                    help="turn the model by fixed-axis X, Y, Z angles in degrees before anything else")
    ap.add_argument("--spacing", type=float, default=0.01, help="grid interval in inches (default 0.01)")
    ap.add_argument("--radius", type=float, default=0.5, help="cylinder radius in inches (default 0.5)")
    ap.add_argument("--length", type=float, default=10.0, help="cylinder length in inches (default 10)")
    ap.add_argument("--tolerance", type=float, default=0.001, help="allowed penetration in inches (default 0.001)")
    ap.add_argument("--bins", type=int, default=180, help="axis angle bins over 180 deg (default 180 = 1 deg)")
    ap.add_argument("--margin", type=int, default=1, help="bins trimmed off each end of a feasible arc (default 1)")
    ap.add_argument("--holder", default="fixture", choices=("fixture", "table", "none"),
                    help="what holds the part (default fixture: held from its mounting side, within its outline)")
    ap.add_argument("--mount", type=float, nargs=3, default=(0.0, 0.0, -1.0), metavar=("X", "Y", "Z"),
                    help="direction from the part toward its holder (default 0 0 -1: held from below)")
    ap.add_argument("--inset", type=float, default=0.05, help="fixture outline inside the part's silhouette, inches (0.05)")
    ap.add_argument("--grain", type=float, nargs=3, default=(0.0, 0.0, 1.0), metavar=("X", "Y", "Z"),
                    help="wood grain direction; the axis is kept square to it (default Z, which minimizes the "
                         "rotation about Y; 0 0 0 to ignore)")
    ap.add_argument("--grain-weight", type=float, default=1e6,
                    help="strength of the grain term per sq in (default 1e6: as level as the part allows)")
    ap.add_argument("--prefer", type=float, nargs=3, default=(1.0, 0.0, 0.0), metavar=("X", "Y", "Z"),
                    help="tie-break direction for the cylinder axis where nothing else decides (default X)")
    ap.add_argument("--track", type=float, nargs="?", const=True, metavar="WIDTH",
                    help="tracking: move the contact along the cylinder over its middle WIDTH inches to spread wear "
                         "(default 90%% of its length); the cylinder still touches exactly what it would at its middle")
    ap.add_argument("--track-step", type=float, metavar="STEP",
                    help="how far the contact moves along the cylinder between passes, inches (default: the band "
                         "over the number of passes, so one part uses all of it)")
    ap.add_argument("--pass-spacing", type=float, default=0.1,
                    help="distance between neighboring passes along Y, inches (0.1)")
    ap.add_argument("--track-gap", type=float, default=0.01,
                    help="part material this close to the cylinder counts as touching it; tracking never changes "
                         "what touches, inches (0.01)")
    ap.add_argument("--track-sweep", action="store_true",
                    help="sweep the contact continuously, during passes too, instead of stepping between passes")
    ap.add_argument("--track-sweeps", type=int, default=1, help="with --track-sweep: times the contact crosses the band (1)")
    ap.add_argument("--track-slope", type=float, default=8.0,
                    help="with --track-sweep: most the contact may move along the cylinder per inch of surface (8)")
    ap.add_argument("--out", help="output directory (default results/<model name>)")
    args = ap.parse_args(argv)
    grain = tuple(args.grain) if any(args.grain) else None
    result = run(args.model, args.spacing, args.radius, args.length, args.tolerance, args.bins, args.margin, args.holder,
                 tuple(args.mount), args.inset, grain, args.grain_weight, tuple(args.prefer), tuple(args.rotate), args.units,
                 args.track, "sweep" if args.track_sweep else "passes", args.track_step, args.pass_spacing,
                 args.track_sweeps, args.track_gap, args.track_slope)
    out = args.out or Path("results") / Path(args.model).stem
    save(result, out)
    print(f"wrote {out}/orientations.csv, result.npz, summary.json")


if __name__ == "__main__":
    main()
