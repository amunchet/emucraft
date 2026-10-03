/*
 * Emucraft kernel: 2.5D material-removal simulation and collision checks.
 *
 * The stock is a height field ("Z-map"): a grid of nx * ny cells, each storing
 * the top of material at that cell's center. Moves are straight segments of a
 * rotationally symmetric tool (flat, ball, bull nose or cone). For every cell
 * a segment touches, the lowest point of the swept tool surface is computed
 * analytically, so a move costs O(cells under the swept outline) no matter how
 * long it is -- there is no point stepping along the path.
 *
 * Collision checks happen during the same sweep:
 *   - material removed by a rapid, a spindle-off move or a move flagged as a
 *     non-cutting link,
 *   - material taller than the flute length (the shank would rub),
 *   - material touching any non-cutting body (shank / holder cylinders).
 *
 * The file is freestanding C99 (no libc in the core) so that the same source
 * builds as a native shared library (loaded from Python with ctypes) and as
 * WebAssembly (loaded by the browser viewer). Keep it that way: no stdio, no
 * libm calls other than the builtins used below.
 *
 * Thread safety: cells are only ever touched by the call that owns their row
 * band, so several contexts may run the same move list concurrently over
 * disjoint row bands [row0, row1) of one simulation, provided band edges are
 * multiples of ec_tile_size(). Everything a call writes besides the cells of
 * its band lives in its ec_ctx.
 */
#ifndef EMUCRAFT_H
#define EMUCRAFT_H

#include <stddef.h>
#include <stdint.h>

#define EC_VERSION 1

#if defined(_WIN32)
#define EC_API __declspec(dllexport)
#else
#define EC_API __attribute__((visibility("default")))
#endif

#define EC_MAX_TOOLS 256
#define EC_MAX_BODIES 4

/* Tool shapes */
enum {
    EC_TOOL_FLAT = 0, /* flat end mill */
    EC_TOOL_BALL = 1, /* ball end mill */
    EC_TOOL_BULL = 2, /* bull nose / corner radius end mill */
    EC_TOOL_CONE = 3  /* drill, spot drill, chamfer or engraving tool */
};

/* Move flags */
enum {
    EC_MOVE_RAPID = 1,   /* rapid traverse: must never remove material */
    EC_MOVE_SPINDLE = 2, /* spindle is turning */
    EC_MOVE_NOCUT = 4    /* feed move that is expected to be in air (link) */
};

/* Event kinds */
enum {
    EC_EV_RAPID_CUT = 1,       /* a rapid removed material */
    EC_EV_SPINDLE_OFF_CUT = 2, /* material removed with the spindle stopped */
    EC_EV_NOCUT_CUT = 3,       /* a move flagged EC_MOVE_NOCUT removed material */
    EC_EV_FLUTE = 4,           /* material above the flutes: the shank rubs */
    EC_EV_BODY = 5             /* a shank/holder body touched material */
};
#define EC_EV_KINDS 5

/*
 * One event per (move, kind[, body]) per call. depth is the removed depth for
 * cut events, the amount above the flutes for EC_EV_FLUTE and the penetration
 * for EC_EV_BODY. (x, y) is the worst cell and z the material height there
 * before the move. cells counts the offending cells that were found; for body
 * events the search stops refining once no cell can beat the worst one, so it
 * is a lower bound there.
 */
typedef struct {
    int32_t kind;
    int32_t move;
    int32_t body; /* body index for EC_EV_BODY, -1 otherwise */
    int32_t cells;
    double x, y, z;
    double depth;
    double volume; /* removed volume (cut events), 0 otherwise */
} ec_event;

typedef struct ec_sim ec_sim;
typedef struct ec_ctx ec_ctx;

/* Memory helpers (the browser allocates its input arrays through these). */
EC_API void *ec_alloc(size_t size);
EC_API void ec_free(void *ptr);
EC_API void ec_heap_reset(void); /* WebAssembly only: drop every allocation */
EC_API int32_t ec_version(void);
EC_API int32_t ec_tile_size(void);
EC_API int32_t ec_event_size(void);

/*
 * Simulation. The grid covers [x0, x0 + nx*cell] x [y0, y0 + ny*cell]; cell
 * (ix, iy) is stored at heights[iy * nx + ix] and represents the point
 * (x0 + (ix + 0.5) * cell, y0 + (iy + 0.5) * cell). Returns NULL on bad
 * arguments or allocation failure.
 */
EC_API ec_sim *ec_sim_new(int32_t nx, int32_t ny, double x0, double y0, double cell, double z_top,
                          double z_bottom);
EC_API void ec_sim_free(ec_sim *s);
EC_API void ec_sim_reset(ec_sim *s); /* back to a fresh block of stock */
EC_API float *ec_sim_heights(ec_sim *s);
EC_API uint32_t *ec_sim_marks(ec_sim *s); /* 1 + index of the move that last cut a cell, 0 = uncut */
EC_API int32_t ec_sim_nx(const ec_sim *s);
EC_API int32_t ec_sim_ny(const ec_sim *s);
/* Call after writing heights directly (custom stock shapes, loaded state). */
EC_API void ec_sim_invalidate(ec_sim *s);
/* Depth that counts as a real event (defaults to 1e-4 in model units). */
EC_API void ec_sim_set_tolerance(ec_sim *s, double z_tol);

/*
 * Tool slots. radius is the cutter radius. corner is the corner radius for a
 * bull nose and the flat tip radius for a cone. slope is the cone flank rise
 * per unit radius (1 / tan(half point angle)). flute <= 0 means unlimited.
 */
EC_API int32_t ec_sim_set_tool(ec_sim *s, int32_t slot, int32_t shape, double radius, double corner,
                               double slope, double flute);
/* Non-cutting cylinder: radius, and height of its bottom face above the tip.
 * A radius <= 0 removes the body. */
EC_API int32_t ec_sim_set_body(ec_sim *s, int32_t slot, int32_t index, double radius, double bottom);
EC_API void ec_sim_clear_tool(ec_sim *s, int32_t slot);

/* Sample the height field at the cell centers of another grid (nearest cell;
 * points outside read the nearest edge cell). Cells whose centers lie past
 * (x_max, y_max) -- the grid's overhang beyond the stock -- are never read.
 * Used to hand the stock left by one program to a viewer grid of a
 * different resolution. */
EC_API void ec_sim_sample(const ec_sim *s, int32_t nx, int32_t ny, double x0, double y0, double cell,
                          double x_max, double y_max, float *out);

/* Total material volume above z_bottom, and height range, over the grid. */
EC_API double ec_sim_volume(const ec_sim *s);
EC_API double ec_sim_min_height(const ec_sim *s);

/*
 * Per-call context: collects events, removed volume and the dirty rectangle.
 * move_volume / move_depth are optional per-move outputs indexed by move
 * index (volume is added, depth is maxed); pass NULL to skip them.
 */
EC_API ec_ctx *ec_ctx_new(int32_t events_capacity);
EC_API void ec_ctx_free(ec_ctx *c);
EC_API void ec_ctx_reset(ec_ctx *c);
EC_API void ec_ctx_set_move_outputs(ec_ctx *c, double *move_volume, float *move_depth, int32_t n);
EC_API int32_t ec_ctx_event_count(const ec_ctx *c);
EC_API int32_t ec_ctx_events_dropped(const ec_ctx *c);
EC_API ec_event *ec_ctx_events(ec_ctx *c);
EC_API void ec_ctx_clear_events(ec_ctx *c);
EC_API double ec_ctx_volume(const ec_ctx *c);
EC_API double ec_ctx_cells_cut(const ec_ctx *c);
/* Dirty rectangle of cut cells since the last clear: out = {x0, y0, x1, y1}
 * (x1/y1 exclusive); empty when x0 >= x1. */
EC_API void ec_ctx_dirty(const ec_ctx *c, int32_t *out);
EC_API void ec_ctx_clear_dirty(ec_ctx *c);

/*
 * Run moves [begin, end) of a path over rows [row0, row1). points holds
 * 3 * (n + 1) doubles: move i goes from point i to point i + 1. flags and
 * tools hold one entry per move. Returns the number of moves processed.
 */
EC_API int32_t ec_sim_run(ec_sim *s, ec_ctx *c, const double *points, const int32_t *flags,
                          const int32_t *tools, int32_t begin, int32_t end, int32_t row0,
                          int32_t row1);

/*
 * Merge runs of consecutive moves that share flags and tool while every inner
 * point stays within eps of the merged segment (at most max_run moves per
 * merged move). Outputs hold up to n moves; out_first[k] is the first
 * original move of merged move k. Returns the merged move count.
 */
EC_API int32_t ec_simplify(const double *points, const int32_t *flags, const int32_t *tools,
                           int32_t n, double eps, int32_t max_run, double *out_points,
                           int32_t *out_flags, int32_t *out_tools, int32_t *out_first);

/* Fold per-move outputs of one band into another: dst_volume[i] += src,
 * dst_depth[i] = max(dst, src) for i in [begin, end). */
EC_API void ec_merge_move_outputs(double *dst_volume, float *dst_depth, const double *src_volume,
                                  const float *src_depth, int32_t begin, int32_t end);

/* Run one segment (used by the viewer to animate partial moves). */
EC_API int32_t ec_sim_segment(ec_sim *s, ec_ctx *c, double x0, double y0, double z0, double x1,
                              double y1, double z1, int32_t flags, int32_t tool, int32_t move,
                              int32_t row0, int32_t row1);

#endif
