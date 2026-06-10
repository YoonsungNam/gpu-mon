#!/usr/bin/env python3
"""Generate keeper-migration-flow.png for docs/clickhouse-keeper-migration.md.

Visualizes the six-phase cutover relative to the OLD keeper. Regenerate in place:
    python3 docs/img/keeper-migration-flow.py
Requires matplotlib (`pip install matplotlib`).
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

# ---- palette (border, fill) -------------------------------------------------
GREEN = ("#2e7d32", "#c8e6c9")
RED   = ("#c62828", "#ffcdd2")
AMBER = ("#e65100", "#ffe0b2")
BLUE  = ("#1565c0", "#bbdefb")
GREY  = ("#546e7a", "#cfd8dc")

fig, ax = plt.subplots(figsize=(16.5, 10.2), dpi=150)
ax.set_xlim(0, 16); ax.set_ylim(0, 10.4); ax.axis("off")

# ---- phase geometry ---------------------------------------------------------
phases = ["0 · Prereqs", "1 · Deploy new keeper", "2 · Cutover (repoint)",
          "3 · Rebuild metadata", "4 · Validate", "5 · Decommission"]
n = len(phases)
px0, px1 = 2.55, 15.85
gap = 0.12
col_w = (px1 - px0 - gap * (n - 1)) / n
cx = [px0 + i * (col_w + gap) + col_w / 2 for i in range(n)]

def cell(x_center, y0, h, text, colors, fs=8.4, bold_first=False):
    b, f = colors
    box = FancyBboxPatch((x_center - col_w / 2 + 0.05, y0), col_w - 0.10, h,
                         boxstyle="round,pad=0.02,rounding_size=0.06",
                         linewidth=1.4, edgecolor=b, facecolor=f, zorder=3)
    ax.add_patch(box)
    ax.text(x_center, y0 + h / 2, text, ha="center", va="center",
            fontsize=fs, color="#10202b", zorder=4, linespacing=1.25)

# faint vertical separators
for i in range(n):
    ax.axvline(cx[i] - col_w / 2 - gap / 2, ymin=0.06, ymax=0.88,
               color="#e3e8ec", lw=1, zorder=0)

# ---- title ------------------------------------------------------------------
ax.text(0.15, 10.05, "ClickHouse Keeper migration — cutover against the OLD keeper",
        ha="left", va="center", fontsize=16.5, fontweight="bold", color="#10202b")
ax.text(0.15, 9.62, "single-node hand-applied keeper (:2181)  →  Helmfile-managed 3-node keeper (:9181)",
        ha="left", va="center", fontsize=10.5, color="#5b6b76")

# legend
xx = 9.7
for lab, col in [("active", GREEN), ("read-only", RED), ("transitional", AMBER),
                 ("new/empty", BLUE), ("idle/gone", GREY)]:
    ax.add_patch(FancyBboxPatch((xx, 9.95), 0.28, 0.22, boxstyle="round,pad=0.01,rounding_size=0.04",
                                edgecolor=col[0], facecolor=col[1], lw=1.2))
    ax.text(xx + 0.36, 10.06, lab, ha="left", va="center", fontsize=8.2, color="#37474f")
    xx += 0.36 + 0.07 * len(lab) + 0.45

# ---- phase headers ----------------------------------------------------------
for i, p in enumerate(phases):
    ax.add_patch(FancyBboxPatch((cx[i] - col_w/2 + 0.05, 9.02), col_w - 0.10, 0.42,
                                boxstyle="round,pad=0.02,rounding_size=0.05",
                                edgecolor="#37474f", facecolor="#37474f", lw=0))
    ax.text(cx[i], 9.23, p, ha="center", va="center", fontsize=9.2,
            fontweight="bold", color="white")

# ---- table-state strip ------------------------------------------------------
ax.text(1.3, 8.58, "Replicated*\ntables", ha="center", va="center",
        fontsize=9, fontweight="bold", color="#37474f", linespacing=1.2)
tbl = [("R/W · writers paused", GREY), ("READ-WRITE", GREEN), ("READ-ONLY", RED),
       ("READ-ONLY\n(rebuilding)", AMBER), ("READ-WRITE", GREEN), ("READ-WRITE", GREEN)]
for i, (t, c) in enumerate(tbl):
    cell(cx[i], 8.30, 0.56, t, c, fs=8.6)

# ---- lane labels ------------------------------------------------------------
lane_y = {"new": 6.55, "ch": 4.35, "old": 2.15}
lane_h = 1.30
ax.text(1.3, lane_y["new"] + lane_h/2, "NEW Keeper\n3 nodes · ns clickhouse\n:9181  (raft :9234)",
        ha="center", va="center", fontsize=8.8, fontweight="bold", color="#1565c0", linespacing=1.3)
ax.text(1.3, lane_y["ch"] + lane_h/2, "ClickHouse\ncluster (CHI)",
        ha="center", va="center", fontsize=9.2, fontweight="bold", color="#37474f", linespacing=1.3)
ax.text(1.3, lane_y["old"] + lane_h/2, "OLD Keeper\n1 node · ns monitoring\n:2181  (hand-applied)",
        ha="center", va="center", fontsize=8.8, fontweight="bold", color="#2e7d32", linespacing=1.3)

# ---- lane cells -------------------------------------------------------------
new_keeper = [("—\nnot deployed", GREY),
              ("deploy 3 nodes\nquorum forms\n(starts EMPTY)", BLUE),
              ("EMPTY —\nCH now points here", AMBER),
              ("metadata rebuilt\nfrom CH on-disk parts", BLUE),
              ("populated\n✓ healthy", GREEN),
              ("sole\ncoordinator", GREEN)]
clickhouse = [("writers paused\nsnapshot inventory", GREY),
              ("serving R/W\nvia OLD keeper", GREEN),
              ("repoint CHI\nzookeeper.nodes\n→ tables READ-ONLY", RED),
              ("SYSTEM RESTORE REPLICA\non EVERY replica", AMBER),
              ("is_readonly=0 ✓\nresume ingestion", GREEN),
              ("running on\nnew keeper", GREEN)]
old_keeper = [("1 node · :2181\nHOLDS ALL METADATA", GREEN),
              ("still serving CH\n(unchanged)", GREEN),
              ("idle\nrollback anchor", GREY),
              ("idle\nrollback anchor", GREY),
              ("idle · keep until\nvalidated", GREY),
              ("DELETE\nsts / cm / pvc", RED)]

for i in range(n):
    cell(cx[i], lane_y["new"], lane_h, new_keeper[i][0], new_keeper[i][1])
    cell(cx[i], lane_y["ch"],  lane_h, clickhouse[i][0], clickhouse[i][1])
    cell(cx[i], lane_y["old"], lane_h, old_keeper[i][0], old_keeper[i][1])

# ---- coordination arrows (CH -> active keeper) ------------------------------
# phase i: which keeper, and arrow colour
active = ["old", "old", "new", "new", "new", "new"]
acolor = [GREY, GREEN, RED, AMBER, GREEN, GREEN]
for i in range(n):
    c = acolor[i][0]
    if active[i] == "new":   # arrow points UP from CH top to new keeper bottom
        ar = FancyArrowPatch((cx[i], lane_y["ch"] + lane_h + 0.02),
                             (cx[i], lane_y["new"] - 0.02),
                             arrowstyle="-|>", mutation_scale=16, lw=2.4,
                             color=c, zorder=2)
        ax.add_patch(ar)
        ax.text(cx[i] + 0.42, (lane_y["ch"] + lane_h + lane_y["new"]) / 2, ":9181",
                ha="left", va="center", fontsize=7.6, color=c, fontweight="bold")
    else:                    # arrow points DOWN from CH bottom to old keeper top
        ar = FancyArrowPatch((cx[i], lane_y["ch"] - 0.02),
                             (cx[i], lane_y["old"] + lane_h + 0.02),
                             arrowstyle="-|>", mutation_scale=16, lw=2.4,
                             color=c, zorder=2)
        ax.add_patch(ar)
        ax.text(cx[i] + 0.42, (lane_y["ch"] + lane_y["old"] + lane_h) / 2, ":2181",
                ha="left", va="center", fontsize=7.6, color=c, fontweight="bold")

# cutover marker between phase 1 and 2
xcut = (cx[1] + cx[2]) / 2 - gap/2
ax.axvline(xcut, ymin=0.20, ymax=0.86, color="#c62828", lw=1.6, ls=(0, (4, 3)), zorder=1)
ax.text(xcut, 7.95, "CUTOVER\n(maintenance window)", ha="center", va="center",
        fontsize=7.8, color="#c62828", fontweight="bold", linespacing=1.1,
        bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#c62828", lw=1.1))

# rollback bracket under old keeper, phases 2..4
rb_x0 = cx[2] - col_w/2 + 0.05
rb_x1 = cx[4] + col_w/2 - 0.05
ax.plot([rb_x0, rb_x0, rb_x1, rb_x1], [1.95, 1.78, 1.78, 1.95],
        color="#1565c0", lw=1.6, ls=(0, (3, 2)), zorder=2)
ax.text((rb_x0 + rb_x1)/2, 1.55, "ROLLBACK ANCHOR — revert keeper.nodes → :2181, no RESTORE needed",
        ha="center", va="center", fontsize=8.6, color="#1565c0", fontweight="bold")

# ---- bottom key notes -------------------------------------------------------
note = ("KEY:  ① The new keeper starts EMPTY — its metadata is NOT copied from the old keeper.\n"
        "          ClickHouse rebuilds it from on-disk parts via SYSTEM RESTORE REPLICA (run on every replica of every shard).\n"
        "      ② The old keeper is never modified during the cutover; it retains the original metadata as the rollback anchor.\n"
        "          Delete it (phase 5) only after validation passes and you are past the rollback window.")
ax.add_patch(FancyBboxPatch((0.15, 0.18), 15.7, 1.12,
                            boxstyle="round,pad=0.04,rounding_size=0.05",
                            edgecolor="#37474f", facecolor="#eceff1", lw=1.3))
ax.text(0.45, 0.74, note, ha="left", va="center", fontsize=9.0, color="#10202b", linespacing=1.45)

plt.tight_layout()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keeper-migration-flow.png")
plt.savefig(out, bbox_inches="tight", facecolor="white")
print("wrote", out)
