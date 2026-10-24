/*
 * Kernel unit tests. The analytic sweep is compared against an independent
 * brute-force reference that samples the tool position densely along each
 * move. Run with `make test` from kernel/.
 */
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "emucraft.h"

static int failures = 0;
static int checks = 0;

#define CHECK(cond, ...)                                                                           \
    do {                                                                                           \
        checks++;                                                                                  \
        if (!(cond)) {                                                                             \
            failures++;                                                                            \
            printf("FAIL %s:%d: ", __FILE__, __LINE__);                                            \
            printf(__VA_ARGS__);                                                                   \
            printf("\n");                                                                          \
        }                                                                                          \
    } while (0)

#define N 100
#define CELL 0.01

typedef struct {
    int shape;
    double R, corner, slope;
} shape_t;

static double ref_profile(const shape_t *t, double d)
{
    switch (t->shape) {
    case EC_TOOL_BALL:
        return t->R - sqrt(fmax(0, t->R * t->R - d * d));
    case EC_TOOL_BULL: {
        double rf = t->R - t->corner, e = d - rf;
        return e <= 0 ? 0 : t->corner - sqrt(fmax(0, t->corner * t->corner - e * e));
    }
    case EC_TOOL_CONE: {
        double e = d - t->corner;
        return e <= 0 ? 0 : e * t->slope;
    }
    default:
        return 0;
    }
}

/* Lowest tool surface above (px, py) sampled along the move, or 1e9. */
static double ref_envelope(const shape_t *t, const double *a, const double *b, double px, double py)
{
    const int steps = 20000;
    double best = 1e9;
    for (int i = 0; i <= steps; i++) {
        double u = (double)i / steps;
        double cx = a[0] + u * (b[0] - a[0]), cy = a[1] + u * (b[1] - a[1]);
        double z = a[2] + u * (b[2] - a[2]);
        double d = hypot(px - cx, py - cy);
        if (d <= t->R) {
            double f = z + ref_profile(t, d);
            if (f < best)
                best = f;
        }
    }
    return best;
}

static double seg_dist(const double *a, const double *b, double px, double py)
{
    double ux = b[0] - a[0], uy = b[1] - a[1], l2 = ux * ux + uy * uy;
    double t = l2 > 0 ? ((px - a[0]) * ux + (py - a[1]) * uy) / l2 : 0;
    t = t < 0 ? 0 : (t > 1 ? 1 : t);
    return hypot(px - a[0] - t * ux, py - a[1] - t * uy);
}

static ec_sim *fresh(void) { return ec_sim_new(N, N, 0, 0, CELL, 1.0, 0.0); }

static void set_shape(ec_sim *s, int slot, const shape_t *t, double flute)
{
    int ok = ec_sim_set_tool(s, slot, t->shape, t->R, t->corner, t->slope, flute);
    CHECK(ok, "set_tool failed");
}

static void run_one(ec_sim *s, ec_ctx *c, const double *a, const double *b, int flags, int tool,
                    int move)
{
    ec_sim_segment(s, c, a[0], a[1], a[2], b[0], b[1], b[2], flags, tool, move, 0, N);
}

static void test_flat_level_slot(void)
{
    ec_sim *s = fresh();
    ec_ctx *c = ec_ctx_new(16);
    shape_t t = {EC_TOOL_FLAT, 0.1, 0, 0};
    set_shape(s, 0, &t, 0);
    double v0 = ec_sim_volume(s);
    double a[3] = {0.2, 0.5, 0.8}, b[3] = {0.8, 0.5, 0.8};
    run_one(s, c, a, b, EC_MOVE_SPINDLE, 0, 0);

    const float *h = ec_sim_heights(s);
    const uint32_t *m = ec_sim_marks(s);
    int inside = 0, wrong = 0;
    for (int iy = 0; iy < N; iy++)
        for (int ix = 0; ix < N; ix++) {
            double px = (ix + 0.5) * CELL, py = (iy + 0.5) * CELL;
            double d = seg_dist(a, b, px, py);
            float hv = h[iy * N + ix];
            if (fabs(d - t.R) < 1e-9)
                continue;
            if (d < t.R) {
                inside++;
                if (hv != 0.8f || m[iy * N + ix] != 1)
                    wrong++;
            } else if (hv != 1.0f || m[iy * N + ix] != 0) {
                wrong++;
            }
        }
    CHECK(wrong == 0, "flat slot: %d cells wrong", wrong);
    double expect = (0.6 * 0.2 + M_PI * 0.01) * 0.2;
    double got = ec_ctx_volume(c);
    CHECK(fabs(got - expect) / expect < 0.03, "flat slot volume %g, analytic %g", got, expect);
    CHECK(fabs(got - inside * 0.2 * CELL * CELL) < 1e-6, "volume %g vs cells %g", got,
          inside * 0.2 * CELL * CELL);
    CHECK(fabs((v0 - ec_sim_volume(s)) - got) < 1e-6, "stock volume change mismatch");
    CHECK(ec_ctx_event_count(c) == 0, "unexpected events: %d", ec_ctx_event_count(c));

    int32_t d[4];
    ec_ctx_dirty(c, d);
    CHECK(d[0] >= 9 && d[0] <= 11 && d[2] >= 89 && d[2] <= 91, "dirty x %d..%d", d[0], d[2]);
    CHECK(d[1] >= 39 && d[1] <= 41 && d[3] >= 59 && d[3] <= 61, "dirty y %d..%d", d[1], d[3]);
    ec_ctx_free(c);
    ec_sim_free(s);
}

static void test_envelopes(void)
{
    const double slope = 1.0 / tan(59.0 * M_PI / 180.0); /* 118 degree drill */
    shape_t shapes[] = {
        {EC_TOOL_FLAT, 0.1, 0, 0},
        {EC_TOOL_BALL, 0.1, 0, 0},
        {EC_TOOL_BULL, 0.1, 0.03, 0},
        {EC_TOOL_CONE, 0.1, 0, slope},
        {EC_TOOL_CONE, 0.1, 0.02, 1.0},
    };
    const char *names[] = {"flat", "ball", "bull", "cone", "chamfer"};
    double moves[][6] = {
        {0.2, 0.5, 0.8, 0.8, 0.5, 0.8},   /* level */
        {0.2, 0.3, 0.95, 0.8, 0.7, 0.6},  /* diagonal ramp down */
        {0.2, 0.7, 0.6, 0.75, 0.35, 0.9}, /* diagonal ramp up */
        {0.5, 0.5, 1.2, 0.5, 0.5, 0.7},   /* plunge */
        {0.3, 0.5, 0.95, 0.34, 0.5, 0.5}, /* steep short ramp */
        {0.5, 0.2, 0.9, 0.5, 0.8, 0.88},  /* nearly level along y */
    };
    for (int si = 0; si < 5; si++)
        for (int mi = 0; mi < 6; mi++) {
            ec_sim *s = fresh();
            ec_ctx *c = ec_ctx_new(16);
            set_shape(s, 0, &shapes[si], 0);
            run_one(s, c, moves[mi], moves[mi] + 3, EC_MOVE_SPINDLE, 0, 0);
            const float *h = ec_sim_heights(s);
            double worst_inner = 0, worst_all = 0;
            int above = 0;
            for (int iy = 0; iy < N; iy++)
                for (int ix = 0; ix < N; ix++) {
                    double px = (ix + 0.5) * CELL, py = (iy + 0.5) * CELL;
                    double d = seg_dist(moves[mi], moves[mi] + 3, px, py);
                    if (fabs(d - shapes[si].R) < 1e-6)
                        continue;
                    double ref = fmin(1.0, ref_envelope(&shapes[si], moves[mi], moves[mi] + 3, px, py));
                    ref = fmax(ref, 0.0);
                    double diff = h[iy * N + ix] - ref;
                    /* the sampled reference can only be at or above the true envelope */
                    if (diff > 2e-6)
                        above++;
                    if (d < 0.9 * shapes[si].R && fabs(diff) > worst_inner)
                        worst_inner = fabs(diff);
                    if (fabs(diff) > worst_all)
                        worst_all = fabs(diff);
                }
            CHECK(above == 0, "%s move %d: %d cells above the reference", names[si], mi, above);
            /* the reference is sampled: allow for its z step on steep moves */
            double sample_err = fabs(moves[mi][5] - moves[mi][2]) / 20000 * 1.5;
            CHECK(worst_inner < 2e-5 + sample_err, "%s move %d: inner error %g", names[si], mi,
                  worst_inner);
            CHECK(worst_all < 2e-3, "%s move %d: rim error %g", names[si], mi, worst_all);
            ec_ctx_free(c);
            ec_sim_free(s);
        }
}

/* A circle, a spiral ramp and a few straight moves. */
static int make_path(double *pts, int max_moves)
{
    int n = 0;
    pts[0] = 0.75;
    pts[1] = 0.5;
    pts[2] = 0.9;
    for (int i = 1; i <= 180 && n < max_moves; i++, n++) {
        double t = 2 * M_PI * i / 180;
        pts[3 * (n + 1)] = 0.5 + 0.25 * cos(t);
        pts[3 * (n + 1) + 1] = 0.5 + 0.25 * sin(t);
        pts[3 * (n + 1) + 2] = 0.9;
    }
    for (int i = 1; i <= 240 && n < max_moves; i++, n++) {
        double t = 2 * M_PI * i / 120;
        double r = 0.25 - 0.12 * i / 240.0;
        pts[3 * (n + 1)] = 0.5 + r * cos(t);
        pts[3 * (n + 1) + 1] = 0.5 + r * sin(t);
        pts[3 * (n + 1) + 2] = 0.9 - 0.3 * i / 240.0;
    }
    double tail[][3] = {{0.5, 0.5, 0.6}, {0.1, 0.1, 0.6}, {0.1, 0.9, 0.7}, {0.9, 0.9, 0.7}};
    for (int i = 0; i < 4 && n < max_moves; i++, n++) {
        pts[3 * (n + 1)] = tail[i][0];
        pts[3 * (n + 1) + 1] = tail[i][1];
        pts[3 * (n + 1) + 2] = tail[i][2];
    }
    return n;
}

static void test_skip_and_bands(void)
{
    static double pts[3 * 1000];
    int n = make_path(pts, 900);
    shape_t shapes[] = {{EC_TOOL_FLAT, 0.08, 0, 0},
                        {EC_TOOL_BALL, 0.08, 0, 0},
                        {EC_TOOL_BULL, 0.08, 0.02, 0},
                        {EC_TOOL_CONE, 0.08, 0.01, 1.2}};
    for (int si = 0; si < 4; si++) {
        /* reference: continuity dropped before every move, so no start-disk skip */
        ec_sim *ref = fresh();
        ec_ctx *rc = ec_ctx_new(64);
        set_shape(ref, 0, &shapes[si], 0);
        for (int i = 0; i < n; i++) {
            ec_ctx_reset(rc);
            ec_sim_run(ref, rc, pts, NULL, NULL, i, i + 1, 0, N);
        }

        ec_sim *one = fresh();
        ec_ctx *oc = ec_ctx_new(64);
        set_shape(one, 0, &shapes[si], 0);
        ec_sim_run(one, oc, pts, NULL, NULL, 0, n, 0, N);
        CHECK(memcmp(ec_sim_heights(ref), ec_sim_heights(one), sizeof(float) * N * N) == 0,
              "shape %d: start-disk skip changed the result", si);

        ec_sim *band = fresh();
        set_shape(band, 0, &shapes[si], 0);
        int edges[] = {0, 32, 64, N};
        double vol = 0;
        for (int b = 0; b < 3; b++) {
            ec_ctx *bc = ec_ctx_new(64);
            ec_sim_run(band, bc, pts, NULL, NULL, 0, n, edges[b], edges[b + 1]);
            vol += ec_ctx_volume(bc);
            ec_ctx_free(bc);
        }
        CHECK(memcmp(ec_sim_heights(one), ec_sim_heights(band), sizeof(float) * N * N) == 0,
              "shape %d: row bands changed the result", si);
        CHECK(fabs(vol - ec_ctx_volume(oc)) < 1e-9, "shape %d: band volume %g vs %g", si, vol,
              ec_ctx_volume(oc));
        CHECK(memcmp(ec_sim_marks(one), ec_sim_marks(band), sizeof(uint32_t) * N * N) == 0,
              "shape %d: row bands changed the marks", si);
        ec_ctx_free(rc);
        ec_ctx_free(oc);
        ec_sim_free(ref);
        ec_sim_free(one);
        ec_sim_free(band);
    }
}

static const ec_event *find_event(ec_ctx *c, int kind)
{
    for (int i = 0; i < ec_ctx_event_count(c); i++)
        if (ec_ctx_events(c)[i].kind == kind)
            return &ec_ctx_events(c)[i];
    return NULL;
}

static void test_cut_events(void)
{
    struct {
        int flags, kind;
    } cases[] = {
        {EC_MOVE_RAPID | EC_MOVE_SPINDLE, EC_EV_RAPID_CUT},
        {0, EC_EV_SPINDLE_OFF_CUT},
        {EC_MOVE_SPINDLE | EC_MOVE_NOCUT, EC_EV_NOCUT_CUT},
    };
    double a[3] = {0.2, 0.5, 0.8}, b[3] = {0.8, 0.5, 0.8};
    for (int i = 0; i < 3; i++) {
        ec_sim *s = fresh();
        ec_ctx *c = ec_ctx_new(16);
        shape_t t = {EC_TOOL_FLAT, 0.1, 0, 0};
        set_shape(s, 0, &t, 0);
        run_one(s, c, a, b, cases[i].flags, 0, 7);
        const ec_event *e = find_event(c, cases[i].kind);
        CHECK(e != NULL, "case %d: no event of kind %d", i, cases[i].kind);
        if (e) {
            CHECK(e->move == 7, "event move %d", e->move);
            CHECK(fabs(e->depth - 0.2) < 1e-6, "event depth %g", e->depth);
            CHECK(e->cells > 1000, "event cells %d", e->cells);
            CHECK(fabs(e->volume - ec_ctx_volume(c)) < 1e-9, "event volume %g", e->volume);
        }
        CHECK(ec_ctx_event_count(c) == 1, "case %d: %d events", i, ec_ctx_event_count(c));

        /* moving through the same slot again removes nothing: no event */
        ec_ctx_clear_events(c);
        run_one(s, c, a, b, cases[i].flags, 0, 8);
        CHECK(ec_ctx_event_count(c) == 0, "recut produced %d events", ec_ctx_event_count(c));
        ec_ctx_free(c);
        ec_sim_free(s);
    }
}

static void test_flute(void)
{
    double a[3] = {0.2, 0.5, 0.8}, b[3] = {0.8, 0.5, 0.8};
    double ramp_b[3] = {0.8, 0.5, 0.7};
    for (int ramp = 0; ramp < 2; ramp++) {
        ec_sim *s = fresh();
        ec_ctx *c = ec_ctx_new(16);
        shape_t t = {EC_TOOL_FLAT, 0.1, 0, 0};
        set_shape(s, 0, &t, 0.1);
        run_one(s, c, a, ramp ? ramp_b : b, EC_MOVE_SPINDLE, 0, 0);
        const ec_event *e = find_event(c, EC_EV_FLUTE);
        CHECK(e != NULL, "ramp=%d: no flute event", ramp);
        if (e)
            CHECK(fabs(e->depth - (ramp ? 0.2 : 0.1)) < 2e-3, "ramp=%d: flute depth %g", ramp,
                  e->depth);
        ec_ctx_free(c);
        ec_sim_free(s);

        s = fresh();
        c = ec_ctx_new(16);
        set_shape(s, 0, &t, 0.35);
        run_one(s, c, a, ramp ? ramp_b : b, EC_MOVE_SPINDLE, 0, 0);
        CHECK(find_event(c, EC_EV_FLUTE) == NULL, "ramp=%d: long flutes flagged", ramp);
        ec_ctx_free(c);
        ec_sim_free(s);
    }
}

static void test_holder(void)
{
    shape_t small = {EC_TOOL_FLAT, 0.05, 0, 0}, big = {EC_TOOL_FLAT, 0.3, 0, 0};
    double center[3] = {0.5, 0.5, 0.5};

    /* a round pocket of radius 0.3, depth 0.5 */
    ec_sim *s = fresh();
    ec_ctx *c = ec_ctx_new(16);
    set_shape(s, 1, &big, 0);
    set_shape(s, 0, &small, 0);
    ec_sim_set_body(s, 0, 0, 0.2, 0.3); /* holder bottom 0.8 when the tip is at 0.5 */
    run_one(s, c, center, center, EC_MOVE_SPINDLE, 1, 0);

    double a[3] = {0.45, 0.5, 0.5}, b[3] = {0.55, 0.5, 0.5};
    run_one(s, c, a, b, EC_MOVE_SPINDLE, 0, 1);
    CHECK(find_event(c, EC_EV_BODY) == NULL, "holder inside the pocket flagged");

    double a2[3] = {0.35, 0.5, 0.5}, b2[3] = {0.65, 0.5, 0.5};
    run_one(s, c, a2, b2, EC_MOVE_SPINDLE, 0, 2);
    const ec_event *e = find_event(c, EC_EV_BODY);
    CHECK(e != NULL, "holder hitting the pocket wall not flagged");
    if (e) {
        CHECK(e->move == 2 && e->body == 0, "holder event move %d body %d", e->move, e->body);
        CHECK(fabs(e->depth - 0.2) < 1e-6, "holder penetration %g", e->depth);
    }
    ec_ctx_free(c);
    ec_sim_free(s);

    /* slotting deeper than the holder clearance: the holder ring hits the walls */
    shape_t cutter = {EC_TOOL_FLAT, 0.1, 0, 0};
    s = fresh();
    c = ec_ctx_new(16);
    set_shape(s, 0, &cutter, 0);
    ec_sim_set_body(s, 0, 0, 0.15, 0.3);
    double sa[3] = {0.2, 0.5, 0.5}, sb[3] = {0.8, 0.5, 0.5};
    run_one(s, c, sa, sb, EC_MOVE_SPINDLE, 0, 0);
    e = find_event(c, EC_EV_BODY);
    CHECK(e != NULL && fabs(e->depth - 0.2) < 1e-6, "slot holder penetration %g",
          e ? e->depth : -1.0);
    ec_ctx_free(c);
    ec_sim_free(s);

    /* same holder inside a slot that is wide enough: fine */
    shape_t wide = {EC_TOOL_FLAT, 0.2, 0, 0};
    s = fresh();
    c = ec_ctx_new(16);
    set_shape(s, 1, &wide, 0);
    set_shape(s, 0, &cutter, 0);
    ec_sim_set_body(s, 0, 0, 0.15, 0.3);
    run_one(s, c, sa, sb, EC_MOVE_SPINDLE, 1, 0);
    run_one(s, c, sa, sb, EC_MOVE_SPINDLE, 0, 1);
    CHECK(find_event(c, EC_EV_BODY) == NULL, "holder in a wide slot flagged");
    ec_ctx_free(c);
    ec_sim_free(s);

    /* plunge: the holder ring comes down onto uncut material */
    s = fresh();
    c = ec_ctx_new(16);
    set_shape(s, 0, &cutter, 0);
    ec_sim_set_body(s, 0, 0, 0.15, 0.3);
    double pa[3] = {0.5, 0.5, 1.2}, pb[3] = {0.5, 0.5, 0.6};
    run_one(s, c, pa, pb, EC_MOVE_SPINDLE, 0, 0);
    e = find_event(c, EC_EV_BODY);
    CHECK(e != NULL && fabs(e->depth - 0.1) < 1e-6, "plunge holder penetration %g",
          e ? e->depth : -1.0);
    /* holder well above the stock: no event */
    ec_sim_set_body(s, 0, 0, 0.15, 0.45);
    ec_ctx_clear_events(c);
    run_one(s, c, pa, pb, EC_MOVE_SPINDLE, 0, 1);
    CHECK(find_event(c, EC_EV_BODY) == NULL, "high holder flagged");
    ec_ctx_free(c);
    ec_sim_free(s);
}

static void test_bottom_and_tools(void)
{
    ec_sim *s = fresh();
    ec_ctx *c = ec_ctx_new(4);
    shape_t t = {EC_TOOL_BALL, 0.1, 0, 0};
    set_shape(s, 0, &t, 0);
    double a[3] = {0.2, 0.5, -0.5}, b[3] = {0.8, 0.5, -0.5};
    run_one(s, c, a, b, EC_MOVE_SPINDLE, 0, 0);
    CHECK(ec_sim_min_height(s) == 0.0, "cut below the stock bottom: min %g", ec_sim_min_height(s));

    /* an undefined tool slot cuts nothing */
    double v = ec_sim_volume(s);
    run_one(s, c, a, b, EC_MOVE_SPINDLE, 5, 1);
    CHECK(ec_sim_volume(s) == v, "undefined tool removed material");
    CHECK(!ec_sim_set_tool(s, 0, EC_TOOL_FLAT, 0, 0, 0, 0), "zero radius accepted");
    CHECK(!ec_sim_set_tool(s, EC_MAX_TOOLS, EC_TOOL_FLAT, 1, 0, 0, 0), "bad slot accepted");
    CHECK(!ec_sim_set_tool(s, 0, EC_TOOL_CONE, 1, 0, 0, 0), "cone without slope accepted");
    CHECK(ec_sim_new(0, 10, 0, 0, 1, 1, 0) == NULL, "empty grid accepted");

    /* reset restores the block */
    ec_sim_reset(s);
    CHECK(fabs(ec_sim_volume(s) - 1.0) < 1e-9, "reset volume %g", ec_sim_volume(s));

    /* event capacity overflow is counted, not written */
    ec_ctx *tiny = ec_ctx_new(1);
    double r0[3] = {0.1, 0.1, 0.9}, r1[3] = {0.9, 0.1, 0.9}, r2[3] = {0.9, 0.9, 0.9};
    run_one(s, tiny, r0, r1, EC_MOVE_RAPID | EC_MOVE_SPINDLE, 0, 0);
    run_one(s, tiny, r1, r2, EC_MOVE_RAPID | EC_MOVE_SPINDLE, 0, 1);
    CHECK(ec_ctx_event_count(tiny) == 1 && ec_ctx_events_dropped(tiny) == 1,
          "overflow: %d events, %d dropped", ec_ctx_event_count(tiny), ec_ctx_events_dropped(tiny));
    ec_ctx_free(tiny);
    ec_ctx_free(c);
    ec_sim_free(s);
}

static void test_simplify(void)
{
    /* 10 collinear moves, a corner, 5 collinear moves, then a rapid */
    double pts[3 * 18];
    int32_t flags[17], tools[17];
    int n = 0;
    for (int i = 0; i <= 10; i++, n++) {
        pts[3 * n] = 0.1 * i;
        pts[3 * n + 1] = 0.2;
        pts[3 * n + 2] = 0.5 + 0.01 * i; /* a straight ramp */
    }
    for (int i = 1; i <= 5; i++, n++) {
        pts[3 * n] = 1.0;
        pts[3 * n + 1] = 0.2 + 0.1 * i;
        pts[3 * n + 2] = 0.6;
    }
    pts[3 * n] = 0.0;
    pts[3 * n + 1] = 0.0;
    pts[3 * n + 2] = 1.0;
    int moves = n; /* 16 moves */
    for (int i = 0; i < moves; i++) {
        flags[i] = EC_MOVE_SPINDLE;
        tools[i] = 0;
    }
    flags[moves - 1] = EC_MOVE_SPINDLE | EC_MOVE_RAPID;
    double out[3 * 18];
    int32_t of[17], ot[17], first[17];
    int k = ec_simplify(pts, flags, tools, moves, 1e-9, 64, out, of, ot, first);
    CHECK(k == 3, "simplify gave %d moves", k);
    CHECK(first[0] == 0 && first[1] == 10 && first[2] == 15, "simplify map %d %d %d", first[0],
          first[1], first[2]);
    CHECK(out[3] == 1.0 && out[5] == 0.6 && out[6] == 1.0 && fabs(out[7] - 0.7) < 1e-12,
          "simplify points");
    CHECK(of[2] == (EC_MOVE_SPINDLE | EC_MOVE_RAPID), "rapid kept apart");
    /* a run limit splits long runs */
    k = ec_simplify(pts, flags, tools, moves, 1e-9, 4, out, of, ot, first);
    CHECK(k == 3 + 2 + 1, "run limit gave %d moves", k);
    /* a bent path is not merged */
    pts[3 * 5 + 1] += 0.01;
    k = ec_simplify(pts, flags, tools, moves, 1e-3, 64, out, of, ot, first);
    CHECK(k > 3, "bent path merged into %d moves", k);
}

static void test_sample(void)
{
    /* a 1 x 1 stock on a 0.3 grid: 4 x 4 cells, the last row/column overhang */
    ec_sim *s = ec_sim_new(4, 4, 0, 0, 0.3, 1.0, 0.0);
    float *h = ec_sim_heights(s);
    for (int i = 0; i < 16; i++)
        h[i] = (float)(i % 4) + 10 * (float)(i / 4);
    for (int i = 0; i < 4; i++) { /* trimmed overhang */
        h[i * 4 + 3] = 0;
        h[12 + i] = 0;
    }
    float out[100];
    /* a finer grid over the same 1 x 1 stock */
    ec_sim_sample(s, 10, 10, 0, 0, 0.1, 1.0, 1.0, out);
    int wrong = 0;
    for (int iy = 0; iy < 10; iy++)
        for (int ix = 0; ix < 10; ix++) {
            int sx = (int)floor((ix + 0.5) * 0.1 / 0.3), sy = (int)floor((iy + 0.5) * 0.1 / 0.3);
            if (sx > 2)
                sx = 2; /* never the overhang column */
            if (sy > 2)
                sy = 2;
            if (out[iy * 10 + ix] != h[sy * 4 + sx])
                wrong++;
        }
    CHECK(wrong == 0, "sample: %d cells wrong", wrong);
    CHECK(out[99] == h[2 * 4 + 2], "sample reads the trimmed overhang");
    ec_sim_free(s);
}

int main(void)
{
    test_flat_level_slot();
    test_envelopes();
    test_skip_and_bands();
    test_cut_events();
    test_flute();
    test_holder();
    test_bottom_and_tools();
    test_simplify();
    test_sample();
    printf("%d checks, %d failures\n", checks, failures);
    return failures ? 1 : 0;
}
