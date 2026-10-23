/*
 * Emucraft kernel -- see emucraft.h for the model and the API contract.
 *
 * Geometry of one move: the tool center travels from A to B in XY while the
 * tip goes from z0 to z0 + s. With u = B - A and a = |u|^2, a cell center P is
 * under a disk of radius R while
 *
 *     q(t) = |P - A - t u|^2 = a t^2 - 2 b t + c <= R^2,  b = (P-A).u, c = |P-A|^2
 *
 * which is a single interval [ta, tb] of t. The tool surface above P at time t
 * is z0 + s t + profile(sqrt(q(t))). profile() is convex and non-decreasing in
 * the distance for every supported shape, so that height is a convex function
 * of t and its minimum over [ta, tb] is cheap to find:
 *   - flat: z is linear, the minimum is at ta or tb,
 *   - ball: closed form (see envelope()),
 *   - bull / cone: golden-section search, only when a cheap lower bound says
 *     the cell might actually be cut.
 */
#include "emucraft.h"

#define TILE_SHIFT 5
#define TILE (1 << TILE_SHIFT)
#define NACC (EC_EV_KINDS - 1 + EC_MAX_BODIES)

#define EC_INF __builtin_inf()
#define EC_SQRT(x) __builtin_sqrt(x)
#define EC_FLOOR(x) __builtin_floor(x)
#define EC_CEIL(x) __builtin_ceil(x)

/* ------------------------------------------------------------------ memory */

#ifdef __wasm__
extern unsigned char __heap_base;
static uintptr_t heap_top;

void *ec_alloc(size_t size)
{
    if (!heap_top)
        heap_top = (uintptr_t)&__heap_base;
    if (size > (size_t)0x7fff0000u)
        return 0;
    uintptr_t p = (heap_top + 15u) & ~(uintptr_t)15u;
    uintptr_t end = p + size;
    uintptr_t have = (uintptr_t)__builtin_wasm_memory_size(0) * 65536u;
    if (end < p)
        return 0;
    if (end > have) {
        size_t pages = (size_t)((end - have + 65535u) / 65536u);
        if (__builtin_wasm_memory_grow(0, pages) == (size_t)-1)
            return 0;
    }
    heap_top = end;
    __builtin_memset((void *)p, 0, size);
    return (void *)p;
}

void ec_free(void *ptr) { (void)ptr; }

void ec_heap_reset(void) { heap_top = 0; }
#else
#include <stdlib.h>

void *ec_alloc(size_t size) { return calloc(1, size ? size : 1); }

void ec_free(void *ptr) { free(ptr); }

void ec_heap_reset(void) {}
#endif

/* ------------------------------------------------------------------- types */

typedef struct {
    int32_t defined, shape;
    double R, R2;  /* cutter radius */
    double r, r2;  /* corner radius (bull) */
    double Rf;     /* radius of the flat part of the bottom (bull, cone) */
    double slope;  /* cone flank rise per unit radius */
    double flute;  /* flute length, <= 0 unlimited */
    double rim;    /* profile height at the cutter radius */
    double br[EC_MAX_BODIES], br2[EC_MAX_BODIES], bb[EC_MAX_BODIES];
} tool_t;

struct ec_sim {
    int32_t nx, ny, tnx, tny;
    double x0, y0, cell, inv_cell, area;
    double z_top, z_bottom, z_tol;
    double hmax; /* upper bound of every height */
    float *h;
    uint32_t *mark;
    float *tmax;     /* per tile: upper bound of the heights in the tile */
    uint8_t *tdirty; /* tile max may be stale (it is then too high, never too low) */
    tool_t tools[EC_MAX_TOOLS];
};

typedef struct {
    int32_t cells;
    double depth, x, y, z, volume;
} acc_t;

struct ec_ctx {
    ec_event *ev;
    int32_t cap, len, dropped;
    double *mv;
    float *md;
    int32_t mn;
    int32_t dx0, dy0, dx1, dy1;
    double volume, cells;
    /* end of the previous segment, for skipping the already-cut start disk */
    int32_t pvalid, ptool;
    double px, py, pz;
    acc_t acc[NACC]; /* kinds 1..4 at [kind - 1], bodies at [4 + index] */
};

typedef struct {
    double ax, ay, bx, by, z0, s; /* s = z1 - z0 */
    double ux, uy, a, inv_a, la;  /* u = B - A, a = |u|^2, la = |u| */
    double kball;                 /* 1 / (a (a + s^2)), see ball_ramp() */
} seg_t;

/* ----------------------------------------------------------------- helpers */

static inline double dmin(double a, double b) { return a < b ? a : b; }
static inline double dmax(double a, double b) { return a > b ? a : b; }
static inline double zat(const seg_t *g, double t) { return g->z0 + g->s * t; }

static inline int32_t to_index(double f)
{
    if (f < -1e9)
        return -1000000000;
    if (f > 1e9)
        return 1000000000;
    return (int32_t)f;
}

/* First / last cell whose center lies inside [v_lo, v_hi] along one axis. */
static inline int32_t lo_index(double v, double origin, double inv)
{
    return to_index(EC_CEIL((v - origin) * inv - 0.5));
}
static inline int32_t hi_index(double v, double origin, double inv)
{
    return to_index(EC_FLOOR((v - origin) * inv - 0.5));
}

/* Chord of the disk (cx, cy, sqrt(R2)) on the line y = yc. */
static inline int disk_row(double cx, double cy, double R2, double yc, double *xl, double *xr)
{
    double dy = yc - cy;
    double w2 = R2 - dy * dy;
    if (w2 < 0)
        return 0;
    double w = EC_SQRT(w2);
    *xl = cx - w;
    *xr = cx + w;
    return 1;
}

/* Chord of the capsule (all points within R of segment AB) on y = yc. The
 * capsule is convex, so the chord is the hull of the chords of its end disks
 * and of its middle band. */
static int capsule_row(const seg_t *g, double R, double R2, double yc, double *xl, double *xr)
{
    int any = 0;
    double lo = 0, hi = 0, l, r;
    if (disk_row(g->ax, g->ay, R2, yc, &l, &r)) {
        lo = l;
        hi = r;
        any = 1;
    }
    if (g->a > 0) {
        if (disk_row(g->bx, g->by, R2, yc, &l, &r)) {
            if (!any || l < lo)
                lo = l;
            if (!any || r > hi)
                hi = r;
            any = 1;
        }
        /* band: 0 <= (P-A).u <= a and |(P-A) x u| <= R |u|, with P = (A.x + dx, yc) */
        double dy = yc - g->ay;
        double pc = dy * g->uy, qc = dy * g->ux, rl = R * g->la;
        double t_lo, t_hi, p_lo, p_hi;
        int ok = 1;
        if (g->ux > 0) {
            t_lo = -pc / g->ux;
            t_hi = (g->a - pc) / g->ux;
        } else if (g->ux < 0) {
            t_lo = (g->a - pc) / g->ux;
            t_hi = -pc / g->ux;
        } else {
            t_lo = -EC_INF;
            t_hi = EC_INF;
            ok = pc >= 0 && pc <= g->a;
        }
        if (g->uy > 0) {
            p_lo = (qc - rl) / g->uy;
            p_hi = (qc + rl) / g->uy;
        } else if (g->uy < 0) {
            p_lo = (qc + rl) / g->uy;
            p_hi = (qc - rl) / g->uy;
        } else {
            p_lo = -EC_INF;
            p_hi = EC_INF;
            ok = ok && qc >= -rl && qc <= rl;
        }
        double bl = dmax(t_lo, p_lo), bh = dmin(t_hi, p_hi);
        if (ok && bl <= bh) {
            l = g->ax + bl;
            r = g->ax + bh;
            if (!any || l < lo)
                lo = l;
            if (!any || r > hi)
                hi = r;
            any = 1;
        }
    }
    *xl = lo;
    *xr = hi;
    return any;
}

/* [t0, t1] within [0, 1] during which P is within sqrt(R2) of the center. */
static inline int t_interval(const seg_t *g, double px, double py, double R2, double *t0,
                             double *t1)
{
    double dx = px - g->ax, dy = py - g->ay;
    double c = dx * dx + dy * dy;
    if (g->a <= 0) {
        if (c > R2)
            return 0;
        *t0 = 0;
        *t1 = 1;
        return 1;
    }
    double b = dx * g->ux + dy * g->uy;
    double disc = b * b - g->a * (c - R2);
    if (disc < 0)
        return 0;
    double sq = EC_SQRT(disc);
    double ta = (b - sq) * g->inv_a, tb = (b + sq) * g->inv_a;
    if (tb < 0 || ta > 1)
        return 0;
    *t0 = ta < 0 ? 0 : ta;
    *t1 = tb > 1 ? 1 : tb;
    return 1;
}

/* Squared distance from P to the center at time t. */
static inline double q_at(const seg_t *g, double px, double py, double t)
{
    double ex = px - g->ax - t * g->ux, ey = py - g->ay - t * g->uy;
    return ex * ex + ey * ey;
}

/* Squared distance from P to the segment. */
static inline double seg_dist2(const seg_t *g, double px, double py)
{
    if (g->a <= 0)
        return q_at(g, px, py, 0);
    double t = ((px - g->ax) * g->ux + (py - g->ay) * g->uy) * g->inv_a;
    t = t < 0 ? 0 : (t > 1 ? 1 : t);
    return q_at(g, px, py, t);
}

/* Height of the tool surface above the tip at squared distance d2 <= R2. */
static inline double profile(const tool_t *T, double d2)
{
    double d, e, w;
    switch (T->shape) {
    case EC_TOOL_BALL:
        w = T->R2 - d2;
        return T->R - (w > 0 ? EC_SQRT(w) : 0);
    case EC_TOOL_BULL:
        d = EC_SQRT(d2);
        e = d - T->Rf;
        if (e <= 0)
            return 0;
        w = T->r2 - e * e;
        return T->r - (w > 0 ? EC_SQRT(w) : 0);
    case EC_TOOL_CONE:
        d = EC_SQRT(d2);
        e = d - T->Rf;
        return e > 0 ? e * T->slope : 0;
    default:
        return 0;
    }
}

static inline double surface_at(const tool_t *T, const seg_t *g, double px, double py, double t)
{
    return zat(g, t) + profile(T, q_at(g, px, py, t));
}

/* Lowest point of the swept tool surface above P, given the interval [ta, tb]
 * during which P is under the cutter. */
static double envelope(const tool_t *T, const seg_t *g, double px, double py, double ta, double tb)
{
    const double s = g->s;
    if (T->shape == EC_TOOL_FLAT)
        return zat(g, s >= 0 ? ta : tb);
    if (g->a <= 0) /* plunge or dwell: distance is constant */
        return zat(g, s >= 0 ? 0 : 1) + profile(T, q_at(g, px, py, 0));

    double b = (px - g->ax) * g->ux + (py - g->ay) * g->uy;
    double tstar = b * g->inv_a; /* closest approach */
    if (s == 0) {
        double t = tstar < ta ? ta : (tstar > tb ? tb : tstar);
        return g->z0 + profile(T, q_at(g, px, py, t));
    }
    if (T->shape == EC_TOOL_BALL) {
        /* With t = t* + tau, q = e^2 + a tau^2 and the stationary point of
         * z0 + s t + R - sqrt(R^2 - q) is tau = -s sqrt((R^2 - e^2) / (a (a + s^2))). */
        double e2 = q_at(g, px, py, tstar);
        double w = T->R2 - e2;
        if (w < 0)
            w = 0;
        double t = tstar - s * EC_SQRT(w * g->kball);
        t = t < ta ? ta : (t > tb ? tb : t);
        return surface_at(T, g, px, py, t);
    }
    /* bull nose / cone: golden-section search on the convex surface height */
    const double k = 0.6180339887498949;
    double lo = ta, hi = tb;
    double x1 = hi - k * (hi - lo), x2 = lo + k * (hi - lo);
    double f1 = surface_at(T, g, px, py, x1), f2 = surface_at(T, g, px, py, x2);
    for (int it = 0; it < 22; it++) {
        if (f1 <= f2) {
            hi = x2;
            x2 = x1;
            f2 = f1;
            x1 = hi - k * (hi - lo);
            f1 = surface_at(T, g, px, py, x1);
        } else {
            lo = x1;
            x1 = x2;
            f1 = f2;
            x2 = lo + k * (hi - lo);
            f2 = surface_at(T, g, px, py, x2);
        }
    }
    double best = dmin(f1, f2);
    best = dmin(best, surface_at(T, g, px, py, ta));
    best = dmin(best, surface_at(T, g, px, py, tb));
    return best;
}

/* Ball end mill on a ramp: the closed-form optimum lies inside the tool
 * disk, so [ta, tb] is only needed when it is clamped to a segment end that
 * the cell is not under. */
static inline double ball_ramp(const tool_t *T, const seg_t *g, double px, double py)
{
    const double dx = px - g->ax, dy = py - g->ay;
    const double c = dx * dx + dy * dy;
    const double b = dx * g->ux + dy * g->uy;
    const double tstar = b * g->inv_a;
    double w = T->R2 - (c - b * tstar);
    if (w < 0)
        w = 0;
    double t = tstar - g->s * EC_SQRT(w * g->kball);
    if (t < 0 || t > 1) {
        double end = t < 0 ? 0 : 1;
        if (q_at(g, px, py, end) <= T->R2) {
            t = end;
        } else {
            double ta, tb;
            if (!t_interval(g, px, py, T->R2, &ta, &tb))
                return EC_INF;
            t = t < 0 ? ta : tb;
        }
    }
    return surface_at(T, g, px, py, t);
}

static void acc_add(acc_t *A, int32_t cells, double depth, double x, double y, double z, double vol)
{
    if (cells <= 0)
        return;
    if (!A->cells || depth > A->depth) {
        A->depth = depth;
        A->x = x;
        A->y = y;
        A->z = z;
    }
    A->cells += cells;
    A->volume += vol;
}

static inline double cell_x(const ec_sim *s, int32_t ix) { return s->x0 + (ix + 0.5) * s->cell; }
static inline double cell_y(const ec_sim *s, int32_t iy) { return s->y0 + (iy + 0.5) * s->cell; }

static void refresh_tile(ec_sim *s, int32_t tx, int32_t ty)
{
    int32_t x0 = tx << TILE_SHIFT, y0 = ty << TILE_SHIFT;
    int32_t x1 = x0 + TILE, y1 = y0 + TILE;
    if (x1 > s->nx)
        x1 = s->nx;
    if (y1 > s->ny)
        y1 = s->ny;
    float m = -3.0e38f;
    for (int32_t iy = y0; iy < y1; iy++) {
        const float *row = s->h + (size_t)iy * s->nx;
        for (int32_t ix = x0; ix < x1; ix++)
            if (row[ix] > m)
                m = row[ix];
    }
    s->tmax[(size_t)ty * s->tnx + tx] = m;
    s->tdirty[(size_t)ty * s->tnx + tx] = 0;
}

/* ------------------------------------------------------------- simulation */

int32_t ec_version(void) { return EC_VERSION; }
int32_t ec_tile_size(void) { return TILE; }
int32_t ec_event_size(void) { return (int32_t)sizeof(ec_event); }

ec_sim *ec_sim_new(int32_t nx, int32_t ny, double x0, double y0, double cell, double z_top,
                   double z_bottom)
{
    if (nx <= 0 || ny <= 0 || !(cell > 0) || !(z_top >= z_bottom))
        return 0;
    if ((double)nx * (double)ny > 1.0e9)
        return 0;
    ec_sim *s = (ec_sim *)ec_alloc(sizeof(ec_sim));
    if (!s)
        return 0;
    s->nx = nx;
    s->ny = ny;
    s->tnx = (nx + TILE - 1) >> TILE_SHIFT;
    s->tny = (ny + TILE - 1) >> TILE_SHIFT;
    s->x0 = x0;
    s->y0 = y0;
    s->cell = cell;
    s->inv_cell = 1.0 / cell;
    s->area = cell * cell;
    s->z_top = z_top;
    s->z_bottom = z_bottom;
    s->z_tol = 1e-4;
    size_t n = (size_t)nx * (size_t)ny, nt = (size_t)s->tnx * (size_t)s->tny;
    s->h = (float *)ec_alloc(n * sizeof(float));
    s->mark = (uint32_t *)ec_alloc(n * sizeof(uint32_t));
    s->tmax = (float *)ec_alloc(nt * sizeof(float));
    s->tdirty = (uint8_t *)ec_alloc(nt);
    if (!s->h || !s->mark || !s->tmax || !s->tdirty) {
        ec_sim_free(s);
        return 0;
    }
    ec_sim_reset(s);
    return s;
}

void ec_sim_free(ec_sim *s)
{
    if (!s)
        return;
    ec_free(s->h);
    ec_free(s->mark);
    ec_free(s->tmax);
    ec_free(s->tdirty);
    ec_free(s);
}

void ec_sim_reset(ec_sim *s)
{
    if (!s)
        return;
    size_t n = (size_t)s->nx * (size_t)s->ny, nt = (size_t)s->tnx * (size_t)s->tny;
    float top = (float)s->z_top;
    for (size_t i = 0; i < n; i++) {
        s->h[i] = top;
        s->mark[i] = 0;
    }
    for (size_t i = 0; i < nt; i++) {
        s->tmax[i] = top;
        s->tdirty[i] = 0;
    }
    s->hmax = top;
}

float *ec_sim_heights(ec_sim *s) { return s ? s->h : 0; }
uint32_t *ec_sim_marks(ec_sim *s) { return s ? s->mark : 0; }
int32_t ec_sim_nx(const ec_sim *s) { return s ? s->nx : 0; }
int32_t ec_sim_ny(const ec_sim *s) { return s ? s->ny : 0; }

void ec_sim_invalidate(ec_sim *s)
{
    if (!s)
        return;
    double m = -3.0e38;
    for (int32_t ty = 0; ty < s->tny; ty++)
        for (int32_t tx = 0; tx < s->tnx; tx++) {
            refresh_tile(s, tx, ty);
            m = dmax(m, s->tmax[(size_t)ty * s->tnx + tx]);
        }
    s->hmax = m;
}

void ec_sim_set_tolerance(ec_sim *s, double z_tol)
{
    if (s && z_tol >= 0)
        s->z_tol = z_tol;
}

int32_t ec_sim_set_tool(ec_sim *s, int32_t slot, int32_t shape, double radius, double corner,
                        double slope, double flute)
{
    if (!s || slot < 0 || slot >= EC_MAX_TOOLS || !(radius > 0))
        return 0;
    if (shape < EC_TOOL_FLAT || shape > EC_TOOL_CONE)
        return 0;
    if (shape == EC_TOOL_CONE && !(slope > 0))
        return 0;
    tool_t *T = &s->tools[slot];
    T->shape = shape;
    T->R = radius;
    T->R2 = radius * radius;
    T->r = 0;
    T->Rf = 0;
    T->slope = 0;
    if (shape == EC_TOOL_BULL) {
        if (!(corner > 0))
            T->shape = EC_TOOL_FLAT;
        else if (corner >= radius)
            T->shape = EC_TOOL_BALL;
        else {
            T->r = corner;
            T->Rf = radius - corner;
        }
    } else if (shape == EC_TOOL_CONE) {
        T->slope = slope;
        T->Rf = corner > 0 && corner < radius ? corner : 0;
    }
    T->r2 = T->r * T->r;
    T->flute = flute > 0 ? flute : 0;
    T->defined = 1;
    T->rim = profile(T, T->R2);
    return 1;
}

int32_t ec_sim_set_body(ec_sim *s, int32_t slot, int32_t index, double radius, double bottom)
{
    if (!s || slot < 0 || slot >= EC_MAX_TOOLS || index < 0 || index >= EC_MAX_BODIES)
        return 0;
    tool_t *T = &s->tools[slot];
    if (!(radius > 0)) {
        T->br[index] = T->br2[index] = T->bb[index] = 0;
        return 1;
    }
    T->br[index] = radius;
    T->br2[index] = radius * radius;
    T->bb[index] = bottom;
    return 1;
}

void ec_sim_clear_tool(ec_sim *s, int32_t slot)
{
    if (!s || slot < 0 || slot >= EC_MAX_TOOLS)
        return;
    tool_t zero = {0};
    s->tools[slot] = zero;
}

void ec_sim_sample(const ec_sim *s, int32_t nx, int32_t ny, double x0, double y0, double cell,
                   double x_max, double y_max, float *out)
{
    if (!s || !out || nx <= 0 || ny <= 0 || !(cell > 0))
        return;
    /* last source cells whose centers are inside (x_max, y_max) */
    int32_t lx = hi_index(x_max, s->x0, s->inv_cell), ly = hi_index(y_max, s->y0, s->inv_cell);
    lx = lx < 0 ? 0 : (lx >= s->nx ? s->nx - 1 : lx);
    ly = ly < 0 ? 0 : (ly >= s->ny ? s->ny - 1 : ly);
    for (int32_t iy = 0; iy < ny; iy++) {
        int32_t sy = to_index(EC_FLOOR((y0 + (iy + 0.5) * cell - s->y0) * s->inv_cell));
        sy = sy < 0 ? 0 : (sy > ly ? ly : sy);
        const float *row = s->h + (size_t)sy * s->nx;
        float *dst = out + (size_t)iy * nx;
        for (int32_t ix = 0; ix < nx; ix++) {
            int32_t sx = to_index(EC_FLOOR((x0 + (ix + 0.5) * cell - s->x0) * s->inv_cell));
            sx = sx < 0 ? 0 : (sx > lx ? lx : sx);
            dst[ix] = row[sx];
        }
    }
}

double ec_sim_volume(const ec_sim *s)
{
    if (!s)
        return 0;
    double total = 0, zb = s->z_bottom;
    for (int32_t iy = 0; iy < s->ny; iy++) {
        const float *row = s->h + (size_t)iy * s->nx;
        double rs = 0;
        for (int32_t ix = 0; ix < s->nx; ix++) {
            double d = (double)row[ix] - zb;
            if (d > 0)
                rs += d;
        }
        total += rs;
    }
    return total * s->area;
}

double ec_sim_min_height(const ec_sim *s)
{
    if (!s)
        return 0;
    float m = 3.0e38f;
    size_t n = (size_t)s->nx * (size_t)s->ny;
    for (size_t i = 0; i < n; i++)
        if (s->h[i] < m)
            m = s->h[i];
    return m;
}

/* ---------------------------------------------------------------- context */

ec_ctx *ec_ctx_new(int32_t events_capacity)
{
    ec_ctx *c = (ec_ctx *)ec_alloc(sizeof(ec_ctx));
    if (!c)
        return 0;
    if (events_capacity < 0)
        events_capacity = 0;
    c->cap = events_capacity;
    if (events_capacity) {
        c->ev = (ec_event *)ec_alloc((size_t)events_capacity * sizeof(ec_event));
        if (!c->ev) {
            ec_free(c);
            return 0;
        }
    }
    ec_ctx_reset(c);
    return c;
}

void ec_ctx_free(ec_ctx *c)
{
    if (!c)
        return;
    ec_free(c->ev);
    ec_free(c);
}

void ec_ctx_reset(ec_ctx *c)
{
    if (!c)
        return;
    c->len = c->dropped = 0;
    c->volume = c->cells = 0;
    c->pvalid = 0;
    for (int k = 0; k < NACC; k++) {
        acc_t zero = {0};
        c->acc[k] = zero;
    }
    ec_ctx_clear_dirty(c);
}

void ec_ctx_set_move_outputs(ec_ctx *c, double *move_volume, float *move_depth, int32_t n)
{
    if (!c)
        return;
    c->mv = move_volume;
    c->md = move_depth;
    c->mn = n;
}

int32_t ec_ctx_event_count(const ec_ctx *c) { return c ? c->len : 0; }
int32_t ec_ctx_events_dropped(const ec_ctx *c) { return c ? c->dropped : 0; }
ec_event *ec_ctx_events(ec_ctx *c) { return c ? c->ev : 0; }
double ec_ctx_volume(const ec_ctx *c) { return c ? c->volume : 0; }
double ec_ctx_cells_cut(const ec_ctx *c) { return c ? c->cells : 0; }

void ec_ctx_clear_events(ec_ctx *c)
{
    if (c)
        c->len = c->dropped = 0;
}

void ec_ctx_dirty(const ec_ctx *c, int32_t *out)
{
    if (!c || !out)
        return;
    out[0] = c->dx0;
    out[1] = c->dy0;
    out[2] = c->dx1;
    out[3] = c->dy1;
}

void ec_ctx_clear_dirty(ec_ctx *c)
{
    if (!c)
        return;
    c->dx0 = c->dy0 = 0x7fffffff;
    c->dx1 = c->dy1 = -0x7fffffff;
}

/* ------------------------------------------------------------------ sweeps */

/* Check one non-cutting body (a cylinder from bottom B above the tip,
 * upwards) against the stock as it is before this move cuts. For a cell, the
 * body can only collide while the cell is under the body but not under the
 * cutter: before the cutter reaches it (material still at its old height) or
 * after the cutter has left it (material at the swept envelope). While under
 * the cutter the material is at most rim height above the tip. */
static void body_pass(ec_sim *s, ec_ctx *c, const tool_t *T, int32_t j, const seg_t *g,
                      int32_t row0, int32_t row1)
{
    const double Rb = T->br[j], Rb2 = T->br2[j], B = T->bb[j];
    const double zb = dmin(g->z0, g->z0 + g->s) + B; /* lowest body bottom of the move */
    const double tol = s->z_tol;
    if (zb + tol >= s->hmax)
        return;
    acc_t *A = &c->acc[EC_EV_KINDS - 1 + j];
    double worst = A->cells ? dmax(A->depth, tol) : tol;

    int32_t iy0 = lo_index(dmin(g->ay, g->by) - Rb, s->y0, s->inv_cell);
    int32_t iy1 = hi_index(dmax(g->ay, g->by) + Rb, s->y0, s->inv_cell);
    if (iy0 < row0)
        iy0 = row0;
    if (iy1 > row1 - 1)
        iy1 = row1 - 1;
    for (int32_t iy = iy0; iy <= iy1; iy++) {
        const double py = cell_y(s, iy);
        double xl, xr;
        if (!capsule_row(g, Rb, Rb2, py, &xl, &xr))
            continue;
        int32_t ix0 = lo_index(xl, s->x0, s->inv_cell), ix1 = hi_index(xr, s->x0, s->inv_cell);
        if (ix0 < 0)
            ix0 = 0;
        if (ix1 > s->nx - 1)
            ix1 = s->nx - 1;
        if (ix0 > ix1)
            continue;
        const float *row = s->h + (size_t)iy * s->nx;
        const int32_t ty = iy >> TILE_SHIFT;
        for (int32_t tx = ix0 >> TILE_SHIFT; tx <= (ix1 >> TILE_SHIFT); tx++) {
            size_t ti = (size_t)ty * s->tnx + tx;
            if (s->tdirty[ti])
                refresh_tile(s, tx, ty);
            if ((double)s->tmax[ti] - zb <= worst)
                continue;
            int32_t a = tx << TILE_SHIFT, b = a + TILE - 1;
            if (a < ix0)
                a = ix0;
            if (b > ix1)
                b = ix1;
            for (int32_t ix = a; ix <= b; ix++) {
                const double hv = row[ix];
                if (hv - zb <= worst)
                    continue;
                const double px = cell_x(s, ix);
                double ba, bb, ta, tb;
                if (!t_interval(g, px, py, Rb2, &ba, &bb))
                    continue;
                double pen;
                if (!t_interval(g, px, py, T->R2, &ta, &tb)) {
                    pen = hv - (dmin(zat(g, ba), zat(g, bb)) + B);
                } else {
                    pen = -EC_INF;
                    if (ba < ta)
                        pen = hv - (dmin(zat(g, ba), zat(g, ta)) + B);
                    if (tb < bb) {
                        double f = envelope(T, g, px, py, ta, tb);
                        double after = hv < f ? hv : f;
                        double p2 = after - (dmin(zat(g, tb), zat(g, bb)) + B);
                        if (p2 > pen)
                            pen = p2;
                    }
                }
                if (pen > tol) {
                    acc_add(A, 1, pen, px, py, hv, 0);
                    if (pen > worst)
                        worst = pen;
                }
            }
        }
    }
}

static void cut_pass(ec_sim *s, ec_ctx *c, const tool_t *T, const seg_t *g, int32_t flags,
                     int32_t move, int32_t row0, int32_t row1, int skip_start)
{
    const double zlow = dmin(g->z0, g->z0 + g->s);
    const double zbot = s->z_bottom;
    if (zlow >= s->hmax)
        return;
    const double R = T->R, R2 = T->R2;
    int32_t iy0 = lo_index(dmin(g->ay, g->by) - R, s->y0, s->inv_cell);
    int32_t iy1 = hi_index(dmax(g->ay, g->by) + R, s->y0, s->inv_cell);
    if (iy0 < row0)
        iy0 = row0;
    if (iy1 > row1 - 1)
        iy1 = row1 - 1;
    if (iy0 > iy1)
        return;

    const int32_t nx = s->nx;
    const double tol = s->z_tol;
    const float tolf = (float)tol;
    const double flute = T->flute;
    const uint32_t mk = (uint32_t)move + 1u;
    const int flat_level = T->shape == EC_TOOL_FLAT && g->s == 0;
    const float zlevel = (float)dmax(g->z0, zbot);
    const float zbotf = (float)zbot;
    /* flat and level: the flutes reach flute above z0 everywhere */
    const float flute_thr = flute > 0 ? (float)(flute + tol) : 3.0e38f;

    double vol = 0, ncells = 0;
    int32_t sig = 0; /* cells cut deeper than the tolerance */
    double wd = 0, wx = 0, wy = 0, wz = 0;
    int32_t fcells = 0;
    double fw = 0, fx = 0, fy = 0, fz = 0;

    for (int32_t iy = iy0; iy <= iy1; iy++) {
        const double py = cell_y(s, iy);
        double xl, xr;
        if (!capsule_row(g, R, R2, py, &xl, &xr))
            continue;
        int32_t ix0 = lo_index(xl, s->x0, s->inv_cell), ix1 = hi_index(xr, s->x0, s->inv_cell);
        if (ix0 < 0)
            ix0 = 0;
        if (ix1 > nx - 1)
            ix1 = nx - 1;
        if (ix0 > ix1)
            continue;

        /* Cells of the start disk were cut by the end of the previous segment.
         * On a non-descending move a flat tool cannot cut them any deeper, and
         * no convex tool can cut the ones behind the start point deeper (mode
         * 2). On a descending ball move (mode 3) a cell behind the start is
         * still safe to skip while the surface over it rises from t = 0:
         * f'(0) = s - b / sqrt(R^2 - c) >= 0, i.e. s^2 (R^2 - c) <= b^2. */
        int32_t sk0 = 1, sk1 = 0;
        if (skip_start) {
            double sl, sr;
            int ok = disk_row(g->ax, g->ay, R2, py, &sl, &sr);
            if (ok && skip_start >= 2 && g->a > 0) {
                const double pc = (py - g->ay) * g->uy;
                if (g->ux > 0) {
                    double xm = g->ax - pc / g->ux;
                    if (xm < sr)
                        sr = xm;
                } else if (g->ux < 0) {
                    double xm = g->ax - pc / g->ux;
                    if (xm > sl)
                        sl = xm;
                } else if (pc > 0) {
                    ok = 0;
                }
            }
            if (ok && sl <= sr) {
                sk0 = lo_index(sl, s->x0, s->inv_cell);
                sk1 = hi_index(sr, s->x0, s->inv_cell);
            }
        }
        /* mode 3 tests cells of [sk0, sk1] one by one instead of dropping them */
        const int32_t tk0 = skip_start == 3 ? sk0 : 1, tk1 = skip_start == 3 ? sk1 : 0;
        if (skip_start == 3) {
            sk0 = 1;
            sk1 = 0;
        }
        const double s2 = g->s * g->s;
        int32_t span[2][2], nspan = 0;
        if (sk0 > sk1 || sk1 < ix0 || sk0 > ix1) {
            span[0][0] = ix0;
            span[0][1] = ix1;
            nspan = 1;
        } else {
            if (sk0 > ix0) {
                span[nspan][0] = ix0;
                span[nspan][1] = sk0 - 1;
                nspan++;
            }
            if (sk1 < ix1) {
                span[nspan][0] = sk1 + 1;
                span[nspan][1] = ix1;
                nspan++;
            }
        }

        float *row = s->h + (size_t)iy * nx;
        uint32_t *mrow = s->mark + (size_t)iy * nx;
        int32_t first = -1, last = -1;
        double rsum = 0;
        int32_t rcut = 0, rsig = 0, rfl = 0;
        float rmax = 0;
        int32_t rmax_ix = -1;
        float rmax_h = 0;

        for (int k = 0; k < nspan; k++) {
            const int32_t a = span[k][0], b = span[k][1];
            if (flat_level) {
                for (int32_t ix = a; ix <= b; ix++) {
                    const float hv = row[ix];
                    if (hv > zlevel) {
                        const float d = hv - zlevel;
                        row[ix] = zlevel;
                        mrow[ix] = mk;
                        rsum += d;
                        rcut++;
                        rsig += d > tolf;
                        rfl += d > flute_thr;
                        if (d > rmax) {
                            rmax = d;
                            rmax_ix = ix;
                            rmax_h = hv;
                        }
                        if (first < 0)
                            first = ix;
                        last = ix;
                    }
                }
                continue;
            }
            for (int32_t ix = a; ix <= b; ix++) {
                const float hv = row[ix];
                if ((double)hv <= zlow)
                    continue;
                const double px = cell_x(s, ix);
                double f, ta = -1, tb;
                if (T->shape == EC_TOOL_FLAT) {
                    if (!t_interval(g, px, py, R2, &ta, &tb))
                        continue;
                    f = zat(g, g->s >= 0 ? ta : tb);
                } else {
                    if (ix >= tk0 && ix <= tk1) {
                        const double dx = px - g->ax, dy = py - g->ay;
                        const double b = dx * g->ux + dy * g->uy;
                        if (s2 * (R2 - (dx * dx + dy * dy)) <= b * b)
                            continue;
                    }
                    /* lower bound: lowest tip height at the closest approach;
                     * exact for level moves and plunges */
                    const double d2 = seg_dist2(g, px, py);
                    if (d2 > R2)
                        continue;
                    const double lb = zlow + profile(T, d2);
                    if ((double)hv <= lb)
                        continue;
                    if (g->s == 0 || g->a <= 0) {
                        f = lb;
                    } else if (T->shape == EC_TOOL_BALL) {
                        f = ball_ramp(T, g, px, py);
                    } else {
                        if (!t_interval(g, px, py, R2, &ta, &tb))
                            continue;
                        f = envelope(T, g, px, py, ta, tb);
                    }
                }
                float ff = (float)f;
                if (ff < zbotf)
                    ff = zbotf;
                if (hv > ff) {
                    const float d = hv - ff;
                    row[ix] = ff;
                    mrow[ix] = mk;
                    rsum += d;
                    rcut++;
                    rsig += d > tolf;
                    if (d > rmax) {
                        rmax = d;
                        rmax_ix = ix;
                        rmax_h = hv;
                    }
                    if (first < 0)
                        first = ix;
                    last = ix;
                    if (flute > 0 && (double)hv - zlow > flute) {
                        if (ta < 0 && !t_interval(g, px, py, R2, &ta, &tb))
                            ta = 0;
                        const double over = (double)hv - zat(g, ta) - flute;
                        if (over > tol) {
                            fcells++;
                            if (over > fw) {
                                fw = over;
                                fx = px;
                                fy = py;
                                fz = hv;
                            }
                        }
                    }
                }
            }
        }
        if (first < 0)
            continue;

        vol += rsum;
        ncells += rcut;
        sig += rsig;
        if (rmax > wd) {
            wd = rmax;
            wx = cell_x(s, rmax_ix);
            wy = py;
            wz = rmax_h;
        }
        if (rfl) {
            fcells += rfl;
            if (rmax - flute > fw) {
                fw = rmax - flute;
                fx = cell_x(s, rmax_ix);
                fy = py;
                fz = rmax_h;
            }
        }
        if (first < c->dx0)
            c->dx0 = first;
        if (last + 1 > c->dx1)
            c->dx1 = last + 1;
        if (iy < c->dy0)
            c->dy0 = iy;
        if (iy + 1 > c->dy1)
            c->dy1 = iy + 1;
        uint8_t *td = s->tdirty + (size_t)(iy >> TILE_SHIFT) * s->tnx;
        for (int32_t tx = first >> TILE_SHIFT; tx <= (last >> TILE_SHIFT); tx++)
            td[tx] = 1;
    }

    if (ncells <= 0)
        return;
    vol *= s->area;
    c->volume += vol;
    c->cells += ncells;
    if (c->mv && move >= 0 && move < c->mn)
        c->mv[move] += vol;
    if (c->md && move >= 0 && move < c->mn && wd > c->md[move])
        c->md[move] = (float)wd;
    if (sig) {
        int kind = 0;
        if (flags & EC_MOVE_RAPID)
            kind = EC_EV_RAPID_CUT;
        else if (!(flags & EC_MOVE_SPINDLE))
            kind = EC_EV_SPINDLE_OFF_CUT;
        else if (flags & EC_MOVE_NOCUT)
            kind = EC_EV_NOCUT_CUT;
        if (kind)
            acc_add(&c->acc[kind - 1], sig, wd, wx, wy, wz, vol);
    }
    if (fcells)
        acc_add(&c->acc[EC_EV_FLUTE - 1], fcells, fw, fx, fy, fz, 0);
}

static void flush_events(ec_ctx *c, int32_t move)
{
    for (int k = 0; k < NACC; k++) {
        acc_t *A = &c->acc[k];
        if (!A->cells)
            continue;
        if (c->len < c->cap) {
            ec_event *e = &c->ev[c->len++];
            e->kind = k < EC_EV_KINDS - 1 ? k + 1 : EC_EV_BODY;
            e->body = k < EC_EV_KINDS - 1 ? -1 : k - (EC_EV_KINDS - 1);
            e->move = move;
            e->cells = A->cells;
            e->x = A->x;
            e->y = A->y;
            e->z = A->z;
            e->depth = A->depth;
            e->volume = A->volume;
        } else {
            c->dropped++;
        }
        acc_t zero = {0};
        *A = zero;
    }
}

static void run_segment(ec_sim *s, ec_ctx *c, double x0, double y0, double z0, double x1,
                        double y1, double z1, int32_t flags, int32_t tool, int32_t move,
                        int32_t row0, int32_t row1)
{
    if (tool < 0 || tool >= EC_MAX_TOOLS || !s->tools[tool].defined) {
        c->pvalid = 0;
        return;
    }
    const tool_t *T = &s->tools[tool];
    seg_t g;
    g.ax = x0;
    g.ay = y0;
    g.bx = x1;
    g.by = y1;
    g.z0 = z0;
    g.s = z1 - z0;
    g.ux = x1 - x0;
    g.uy = y1 - y0;
    g.a = g.ux * g.ux + g.uy * g.uy;
    if (g.a <= 1e-24 * s->area) { /* XY motion far below the grid: treat as a plunge */
        g.ux = g.uy = 0;
        g.a = 0;
        g.bx = x0;
        g.by = y0;
    }
    g.inv_a = g.a > 0 ? 1.0 / g.a : 0;
    g.la = EC_SQRT(g.a);
    g.kball = g.a > 0 ? 1.0 / (g.a * (g.a + g.s * g.s)) : 0;

    for (int32_t j = 0; j < EC_MAX_BODIES; j++)
        if (T->br[j] > 0)
            body_pass(s, c, T, j, &g, row0, row1);

    int skip = 0;
    if (c->pvalid && c->ptool == tool && c->px == x0 && c->py == y0 && c->pz == z0) {
        if (g.s >= 0)
            skip = T->shape == EC_TOOL_FLAT ? 1 : 2;
        else if (T->shape == EC_TOOL_BALL && g.a > 0)
            skip = 3;
    }
    cut_pass(s, c, T, &g, flags, move, row0, row1, skip);
    flush_events(c, move);

    c->pvalid = 1;
    c->ptool = tool;
    c->px = x1;
    c->py = y1;
    c->pz = z1;
}

static void clamp_rows(const ec_sim *s, int32_t *row0, int32_t *row1)
{
    if (*row0 < 0)
        *row0 = 0;
    if (*row1 > s->ny || *row1 <= 0)
        *row1 = s->ny;
}

int32_t ec_sim_run(ec_sim *s, ec_ctx *c, const double *points, const int32_t *flags,
                   const int32_t *tools, int32_t begin, int32_t end, int32_t row0, int32_t row1)
{
    if (!s || !c || !points || begin < 0 || end <= begin)
        return 0;
    clamp_rows(s, &row0, &row1);
    for (int32_t i = begin; i < end; i++) {
        const double *p = points + 3 * (size_t)i;
        run_segment(s, c, p[0], p[1], p[2], p[3], p[4], p[5], flags ? flags[i] : EC_MOVE_SPINDLE,
                    tools ? tools[i] : 0, i, row0, row1);
    }
    return end - begin;
}

/* Squared distance from point m to the segment from point i to point j. */
static double path_dist2(const double *pts, int32_t i, int32_t j, int32_t m)
{
    const double *a = pts + 3 * (size_t)i, *b = pts + 3 * (size_t)j, *p = pts + 3 * (size_t)m;
    double ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
    double vx = p[0] - a[0], vy = p[1] - a[1], vz = p[2] - a[2];
    double uu = ux * ux + uy * uy + uz * uz;
    double t = uu > 0 ? (vx * ux + vy * uy + vz * uz) / uu : 0;
    t = t < 0 ? 0 : (t > 1 ? 1 : t);
    double ex = vx - t * ux, ey = vy - t * uy, ez = vz - t * uz;
    return ex * ex + ey * ey + ez * ez;
}

int32_t ec_simplify(const double *points, const int32_t *flags, const int32_t *tools, int32_t n,
                    double eps, int32_t max_run, double *out_points, int32_t *out_flags,
                    int32_t *out_tools, int32_t *out_first)
{
    if (!points || n <= 0)
        return 0;
    if (max_run < 1)
        max_run = 1;
    const double eps2 = eps * eps;
    int32_t k = 0, i = 0;
    out_points[0] = points[0];
    out_points[1] = points[1];
    out_points[2] = points[2];
    while (i < n) {
        int32_t j = i + 1; /* merged move goes from point i to point j */
        while (j < n && j - i < max_run && flags[j] == flags[i] && tools[j] == tools[i]) {
            int ok = 1;
            for (int32_t m = i + 1; m <= j && ok; m++)
                ok = path_dist2(points, i, j + 1, m) <= eps2;
            if (!ok)
                break;
            j++;
        }
        out_points[3 * (size_t)(k + 1)] = points[3 * (size_t)j];
        out_points[3 * (size_t)(k + 1) + 1] = points[3 * (size_t)j + 1];
        out_points[3 * (size_t)(k + 1) + 2] = points[3 * (size_t)j + 2];
        out_flags[k] = flags[i];
        out_tools[k] = tools[i];
        out_first[k] = i;
        k++;
        i = j;
    }
    return k;
}

void ec_merge_move_outputs(double *dst_volume, float *dst_depth, const double *src_volume,
                           const float *src_depth, int32_t begin, int32_t end)
{
    for (int32_t i = begin; i < end; i++) {
        if (dst_volume && src_volume)
            dst_volume[i] += src_volume[i];
        if (dst_depth && src_depth && src_depth[i] > dst_depth[i])
            dst_depth[i] = src_depth[i];
    }
}

int32_t ec_sim_segment(ec_sim *s, ec_ctx *c, double x0, double y0, double z0, double x1,
                       double y1, double z1, int32_t flags, int32_t tool, int32_t move,
                       int32_t row0, int32_t row1)
{
    if (!s || !c)
        return 0;
    clamp_rows(s, &row0, &row1);
    run_segment(s, c, x0, y0, z0, x1, y1, z1, flags, tool, move, row0, row1);
    return 1;
}
