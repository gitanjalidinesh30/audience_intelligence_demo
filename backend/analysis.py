"""
The four analyses shown in the demo. Each function returns plain
Python dicts/lists (already sorted and rounded) ready to hand to the
front end as JSON. Written in plain language throughout so the code
itself can double as documentation.
"""

import sqlite3
import os
import numpy as np
import behavioural
import category
import targeting

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "audience_intel.db")

PROVENANCE_GRADE = {
    frozenset(["PURCHASE_BEHAVIOURAL"]): "A",
    frozenset(["PURCHASE_BEHAVIOURAL", "BASKET_COMPOSITION"]): "B",
    frozenset(["PURCHASE_BEHAVIOURAL", "CONTENT_AFFINITY"]): "B",
    frozenset(["PURCHASE_BEHAVIOURAL", "INTEREST_DECLARED"]): "B",
    frozenset(["BASKET_COMPOSITION", "CONTENT_AFFINITY"]): "C",
    frozenset(["BASKET_COMPOSITION", "INTEREST_DECLARED"]): "C",
    frozenset(["CONTENT_AFFINITY", "INTEREST_DECLARED"]): "B",
}

GRADE_EXPLAIN = {
    "A": "Same type of evidence — safe to compare directly.",
    "B": "Related but not identical evidence — compare direction/rank, not exact numbers.",
    "C": "Different kinds of evidence — treat as a hint, not a fact.",
}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def grade_for_provenance(p1, p2):
    if p1 == p2:
        return "A"
    key = frozenset([p1, p2])
    return PROVENANCE_GRADE.get(key, "C")


# ---------------------------------------------------------------------------
# USE CASE 1 — Segment inventory audit
# "How many differently-named segments are we effectively buying for the
# same audience, and what is that costing us?"
# ---------------------------------------------------------------------------
def segment_inventory_audit():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT sc.canonical_id, sc.canonical_name, sc.category,
               sr.raw_id, sr.raw_name, sr.platform_code, p.platform_name,
               bm.confidence, bm.method
        FROM bridge_map bm
        JOIN segments_raw sr ON sr.raw_id = bm.raw_id
        JOIN segments_canonical sc ON sc.canonical_id = bm.canonical_id
        JOIN platforms p ON p.platform_code = sr.platform_code
    """)
    rows = cur.fetchall()

    # spend per raw_id (impressions row carries the spend estimate)
    cur.execute("SELECT raw_id, spend FROM fact_metrics WHERE metric_code = 'IMPRESSIONS' AND raw_id != 'ALL'")
    spend_by_raw = {r["raw_id"]: r["spend"] for r in cur.fetchall()}

    grouped = {}
    for r in rows:
        cid = r["canonical_id"]
        grouped.setdefault(cid, {
            "canonical_id": cid,
            "canonical_name": r["canonical_name"],
            "category": r["category"],
            "raw_segments": [],
            "platforms": set(),
        })
        grouped[cid]["raw_segments"].append({
            "raw_name": r["raw_name"],
            "platform_code": r["platform_code"],
            "platform_name": r["platform_name"],
            "confidence": round(r["confidence"], 2),
            "method": r["method"],
            "spend": spend_by_raw.get(r["raw_id"], 0),
        })
        grouped[cid]["platforms"].add(r["platform_name"])

    out = []
    for cid, g in grouped.items():
        total_spend = round(sum(rs["spend"] for rs in g["raw_segments"]), 2)
        out.append({
            "canonical_id": cid,
            "canonical_name": g["canonical_name"],
            "category": g["category"],
            "raw_segment_count": len(g["raw_segments"]),
            "platform_count": len(g["platforms"]),
            "total_spend": total_spend,
            "raw_segments": sorted(g["raw_segments"], key=lambda x: -x["spend"]),
            "flag": "Naming redundancy" if len(g["raw_segments"]) >= 4 else None,
        })

    out.sort(key=lambda x: -x["raw_segment_count"])
    conn.close()
    return out


# ---------------------------------------------------------------------------
# USE CASE 2 — Performance index heatmap with grades
# "Where does each audience over- or under-perform, on which platform, and
# how much should we trust that comparison?"
# ---------------------------------------------------------------------------
def performance_heatmap():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT platform_code, provenance_type FROM platforms")
    provenance = {r["platform_code"]: r["provenance_type"] for r in cur.fetchall()}
    reference_platform = "AMC"  # purchase-behavioural, used as the trust anchor

    cur.execute("""
        SELECT canonical_id, platform_code, metric_code, SUM(numerator) num, SUM(denominator) den
        FROM fact_metrics
        WHERE canonical_id IS NOT NULL AND canonical_id != 'ALL'
        GROUP BY canonical_id, platform_code, metric_code
    """)
    seg_rows = cur.fetchall()

    cur.execute("""
        SELECT platform_code, metric_code, numerator num, denominator den
        FROM fact_metrics WHERE raw_id = 'ALL'
    """)
    baseline = {}
    for r in cur.fetchall():
        baseline[(r["platform_code"], r["metric_code"])] = (r["num"], r["den"])

    def rate(num, den):
        return num / den if den else 0

    # Reach (IMPRESSIONS) is a volume, not a per-unit rate, so it can't be
    # indexed the same way CTR and conversion rate are. Those two divide a
    # segment's RATE by the platform's average RATE -- audience size cancels
    # out of that math, which is exactly why a typical segment lands near
    # 100 regardless of how big it is.
    #
    # Reach has no natural rate to divide. Indexing a segment's raw
    # impressions against the platform's TOTAL impressions (every segment,
    # summed, heavily overlapping) instead answers "what share of everyone's
    # impressions belong to this segment" -- a number that's structurally
    # small for any one segment and never centers near 100.
    #
    # Fix: index against the platform's MEAN segment size instead of its
    # total. That's the same normalization already used for the "Audience
    # size" feature on the Behavioural Matching screen (see
    # behavioural.py's reach_share_index), so a typical-sized audience now
    # reads as 100 here too, and a segment twice the usual size reads ~200.
    impressions_by_platform = {}
    for r in seg_rows:
        if r["metric_code"] == "IMPRESSIONS":
            impressions_by_platform.setdefault(r["platform_code"], []).append(r["num"])
    mean_impressions = {
        p: (sum(vals) / len(vals) if vals else 0)
        for p, vals in impressions_by_platform.items()
    }

    cells = []
    for r in seg_rows:
        grade = grade_for_provenance(provenance[r["platform_code"]], provenance[reference_platform])

        if r["metric_code"] == "IMPRESSIONS":
            mean_reach = mean_impressions.get(r["platform_code"], 0)
            idx = round((r["num"] / mean_reach) * 100, 1) if mean_reach else None
        else:
            b_num, b_den = baseline.get((r["platform_code"], r["metric_code"]), (0, 1))
            seg_rate = rate(r["num"], r["den"])
            base_rate = rate(b_num, b_den)
            idx = round((seg_rate / base_rate) * 100, 1) if base_rate else None

        cells.append({
            "canonical_id": r["canonical_id"],
            "platform_code": r["platform_code"],
            "metric_code": r["metric_code"],
            "index": idx,
            "grade": grade,
            "grade_explain": GRADE_EXPLAIN[grade],
        })

    cur.execute("SELECT canonical_id, canonical_name FROM segments_canonical")
    names = {r["canonical_id"]: r["canonical_name"] for r in cur.fetchall()}
    cur.execute("SELECT platform_code, platform_name FROM platforms")
    plat_names = {r["platform_code"]: r["platform_name"] for r in cur.fetchall()}

    conn.close()
    return {
        "cells": cells,
        "segment_names": names,
        "platform_names": plat_names,
        "reference_platform": reference_platform,
        "metrics": ["IMPRESSIONS", "CLICKS", "CONVERSIONS"],
    }


# ---------------------------------------------------------------------------
# USE CASE 3 — Behavioural segment matching (with explainability)
# "Do the segments we matched by NAME actually behave alike? If not, that
# mismatch is itself a finding — and we have to be able to say WHY the
# behaviour groups came out the way they did."
#
# The whole analysis lives in backend/behavioural.py, because the
# explainability machinery (named features, group profiles, a readable
# surrogate rule set, a platform-confounding check, and a stability
# bootstrap) is substantially more code than the clustering itself.
# ---------------------------------------------------------------------------
def behavioural_segment_matching(n_clusters=4, n_bootstrap=100):
    conn = get_conn()
    try:
        return behavioural.behavioural_segment_matching(
            conn, n_clusters=n_clusters, n_bootstrap=n_bootstrap
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# USE CASE 4 — Modelled cross-platform overlap
# "How much duplication is there between our reach on platform A and
# platform B for the same audience?"
#
# IMPORTANT CONSTRAINT: no hashed identifiers are available in any clean
# room here, so nothing below ever looks at (or pretends to have) an
# individual-level match between platforms. Every input is an aggregate
# number a platform would actually disclose: a segment's size, and the
# platform's overall reach. The estimate is built in three honest layers:
#
#   1. Hard bounds       -- pure set logic, no assumptions, always true.
#   2. Independence guess -- "what if reach on A and B were unrelated?"
#                             (usually an underestimate in real media data).
#   3. Adjusted estimate  -- the independence guess scaled by a duplication
#                             multiplier standing in for an externally
#                             sourced cross-platform duplication rate
#                             (e.g. a syndicated panel like Comscore/Nielsen
#                             ONE), clipped back inside the hard bounds.
#
# Because this is synthetic data, the true overlap is also known and shown
# for validation only -- a real deployment would never have this column.
# ---------------------------------------------------------------------------
def modelled_cross_platform_overlap(duplication_multiplier=1.5):
    """
    duplication_multiplier: how much more likely a person reached on
    platform A is to also be reached on platform B, versus pure chance.
    1.0 = independence assumption. Real cross-platform duplication in
    media data is very often above 1.0 (people who are reachable on one
    platform tend to be reachable on others too), which is why the
    default here is > 1.0 -- but it is a stated assumption, not something
    derived from a matched sample, and the UI must say so.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT segment_a, platform_a, platform_b, true_overlap_users, true_jaccard FROM true_overlap_reference")
    truth_rows = cur.fetchall()

    cur.execute("SELECT raw_id, true_segment_id, platform_code, reach_count FROM segments_raw WHERE true_segment_id IS NOT NULL")
    raw_info = cur.fetchall()
    reach_by_platform_seg = {}
    for r in raw_info:
        key = (r["platform_code"], r["true_segment_id"])
        reach_by_platform_seg[key] = reach_by_platform_seg.get(key, 0) + r["reach_count"]

    # Estimate each platform's total addressable market size from two
    # numbers a platform actually discloses: its own total reach, and its
    # coverage of the market (typically benchmarked against an external
    # source like census or a syndicated panel -- never derived from
    # another platform's data).
    cur.execute("SELECT platform_code, coverage FROM platforms")
    coverage_by_platform = {r["platform_code"]: r["coverage"] for r in cur.fetchall()}
    cur.execute("SELECT platform_code, users_in_cell FROM fact_metrics WHERE raw_id = 'ALL' AND metric_code = 'IMPRESSIONS'")
    total_reach_by_platform = {r["platform_code"]: r["users_in_cell"] for r in cur.fetchall()}
    market_size_by_platform = {
        p: total_reach_by_platform[p] / coverage_by_platform[p]
        for p in coverage_by_platform if coverage_by_platform[p] > 0
    }

    cur.execute("SELECT canonical_id, canonical_name FROM segments_canonical")
    canon_names = {r["canonical_id"]: r["canonical_name"] for r in cur.fetchall()}
    cur.execute("SELECT platform_code, platform_name FROM platforms")
    plat_names = {r["platform_code"]: r["platform_name"] for r in cur.fetchall()}

    results = []
    for row in truth_rows:
        seg, pa, pb = row["segment_a"], row["platform_a"], row["platform_b"]
        true_overlap = row["true_overlap_users"]
        true_jac = row["true_jaccard"]

        na = reach_by_platform_seg.get((pa, seg), 0)
        nb = reach_by_platform_seg.get((pb, seg), 0)
        if na == 0 or nb == 0:
            continue

        # Market size for this pair: average of each platform's own
        # disclosed-reach-implied market estimate.
        n_market = (market_size_by_platform.get(pa, 0) + market_size_by_platform.get(pb, 0)) / 2
        if n_market <= 0:
            continue

        # 1. Hard bounds -- true regardless of any assumption.
        lower_bound = max(0, na + nb - n_market)
        upper_bound = min(na, nb)

        # 2. Independence guess.
        independence_estimate = (na * nb) / n_market

        # 3. Adjusted estimate, clipped back inside the hard bounds.
        adjusted_estimate = independence_estimate * duplication_multiplier
        adjusted_estimate = max(lower_bound, min(upper_bound, adjusted_estimate))

        results.append({
            "canonical_id": seg,
            "canonical_name": canon_names.get(seg, seg),
            "platform_a": pa, "platform_a_name": plat_names.get(pa, pa),
            "platform_b": pb, "platform_b_name": plat_names.get(pb, pb),
            "reach_a": na, "reach_b": nb,
            "market_size_estimate": int(round(n_market)),
            "lower_bound": int(round(lower_bound)),
            "upper_bound": int(round(upper_bound)),
            "independence_estimate": int(round(independence_estimate)),
            "adjusted_estimate": int(round(adjusted_estimate)),
            "duplication_multiplier": duplication_multiplier,
            "true_overlap_users": int(true_overlap),
            "true_jaccard": round(true_jac, 3),
        })

    results.sort(key=lambda x: -x["adjusted_estimate"])
    conn.close()
    return results


# ---------------------------------------------------------------------------
# USE CASE 5 — Category affinity
# "Which audiences over-index on which CPG categories — and what else ends
# up in the basket alongside energy drinks?"
#
# Lives in backend/category.py. Note that only two of the four platforms can
# answer this at all: Google and Meta observe interest and content, not
# purchases.
# ---------------------------------------------------------------------------
def category_affinity(anchor="ENERGY_DRINKS"):
    conn = get_conn()
    try:
        return category.category_affinity(conn, anchor=anchor)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# USE CASE 6 — Target Groups & Next Best Action
# "Given everything the other screens found, what should we actually do?"
#
# Synthesises screens 1 (naming), 2 (performance), 3 (behavioural matching)
# and 5 (category affinity) into a ranked action per canonical audience.
# Deliberately does not use cross-platform overlap (screen 4) — see
# backend/targeting.py's module docstring for why.
# ---------------------------------------------------------------------------
def target_groups(thresholds=None):
    conn = get_conn()
    try:
        return targeting.build_target_groups(conn, thresholds=thresholds)
    finally:
        conn.close()
