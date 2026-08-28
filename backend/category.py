"""
ANALYSIS 5 — Category Affinity.

Two questions, deliberately kept apart because they come from different
evidence and only one of them has more than a single source:

  (a) WHO over-indexes on a category?
      Segment x category purchase penetration, indexed against each
      platform's own baseline. Available from Amazon Marketing Cloud
      (retail purchases) and Instacart Data Hub (grocery baskets).

  (b) WHAT ELSE is in the basket with energy drinks?
      Co-purchase lift within a single shopping basket. Instacart only,
      because it is the only one of the four that sees more than one item
      per trip.

THE COVERAGE POINT, WHICH IS THE POINT
--------------------------------------
Google Ads Data Hub observes content consumption. Meta Advanced Analytics
observes declared interests. Neither observes purchases, so neither can
contribute anything to a purchase-based category affinity -- and pretending
otherwise by substituting an interest signal would be the exact error the
rest of this tool exists to warn about. Two of four platforms answer this
question; the screen says so in the first thing you read.

WHY ENERGY DRINKS IS THE ANCHOR
-------------------------------
The reference client for this prototype is an energy-drink CPG, so the
category is treated as the anchor throughout: the spotlight ranks audiences
by their energy-drink index, and the basket analysis measures what travels
with energy drinks specifically.
"""

import math

# Amazon and Instacart differ in provenance (purchase-behavioural versus
# basket-composition), so anything compared across the two is grade B by the
# same rule the heatmap screen uses: compare rank and direction, not levels.
CROSS_PLATFORM_GRADE = "B"
CROSS_PLATFORM_GRADE_EXPLAIN = (
    "Amazon reads purchases; Instacart reads grocery baskets. Related evidence, "
    "not identical evidence \u2014 compare which audiences rank highest, not the "
    "exact index numbers."
)

PLATFORM_ROLE = {
    "AMC": "Retail purchases, by product category.",
    "ICDH": "Grocery baskets, by category \u2014 and the only source that sees "
            "several items in one basket.",
}
PLATFORM_ABSENCE = {
    "ADH": "Observes content consumption and search, never purchases. "
           "A 'fitness content viewer' is not a 'protein bar buyer'.",
    "META_AA": "Observes declared interests and engagement, never purchases. "
               "Substituting interest for purchase here would invent a signal "
               "that does not exist.",
}

# An index has to clear this before it is called an over-index rather than noise.
OVER_INDEX_AT = 120
UNDER_INDEX_AT = 85


def _band(idx):
    if idx >= OVER_INDEX_AT:
        return "over"
    if idx <= UNDER_INDEX_AT:
        return "under"
    return "average"


def _index(pen, base_pen):
    return (pen / base_pen * 100.0) if base_pen else None


def _lift_interval(both, n_anchor, n_cat, n_total):
    """
    Lift = P(category | anchor in basket) / P(category), with a 95% interval.

    The interval uses the standard delta-method error on log lift, which is
    the same machinery used for a relative risk. It matters here because a
    lift of 1.06 on a big category and a lift of 1.06 on a small one are very
    different claims, and a bare number hides that.
    """
    if both <= 0 or n_anchor <= 0 or n_cat <= 0 or n_total <= 0:
        return None, None, None, False
    p_given = both / n_anchor
    p_base = n_cat / n_total
    if p_base <= 0:
        return None, None, None, False
    lift = p_given / p_base
    se = math.sqrt(max(1e-12, (1.0 / both) - (1.0 / n_anchor) + (1.0 / n_cat) - (1.0 / n_total)))
    lo = lift * math.exp(-1.96 * se)
    hi = lift * math.exp(1.96 * se)
    # "Significant" here means the interval clears 1.0 in one direction.
    significant = (lo > 1.0) or (hi < 1.0)
    return lift, lo, hi, significant


def category_affinity(conn, anchor="ENERGY_DRINKS"):
    cur = conn.cursor()

    cur.execute("SELECT category_code, category_name, department, is_anchor FROM categories")
    categories = [dict(r) for r in cur.fetchall()]
    cat_name = {c["category_code"]: c["category_name"] for c in categories}
    cat_dept = {c["category_code"]: c["department"] for c in categories}

    cur.execute("SELECT platform_code, platform_name, provenance_type FROM platforms")
    platforms = {r["platform_code"]: dict(r) for r in cur.fetchall()}

    cur.execute("SELECT canonical_id, canonical_name, category FROM segments_canonical")
    canon = {r["canonical_id"]: dict(r) for r in cur.fetchall()}

    # ---------- who can answer this question at all ----------
    cur.execute("SELECT DISTINCT platform_code FROM fact_category_affinity")
    contributing_codes = sorted(r["platform_code"] for r in cur.fetchall())
    coverage = {
        "contributing": [{
            "platform_code": p,
            "platform_name": platforms[p]["platform_name"],
            "provenance": platforms[p]["provenance_type"],
            "what_it_sees": PLATFORM_ROLE.get(p, ""),
        } for p in contributing_codes],
        "missing": [{
            "platform_code": p,
            "platform_name": platforms[p]["platform_name"],
            "provenance": platforms[p]["provenance_type"],
            "why_not": PLATFORM_ABSENCE.get(p, "No purchase data available."),
        } for p in platforms if p not in contributing_codes],
    }
    coverage["note"] = (
        f"{len(contributing_codes)} of {len(platforms)} platforms can answer this question. "
        "The other two see interest and content, not purchases, so they contribute nothing "
        "here \u2014 an absence worth showing rather than averaging over."
    )

    # ---------- platform baselines ----------
    cur.execute("""
        SELECT platform_code, category_code, penetration, buyers, users_in_cell
        FROM fact_category_affinity WHERE raw_id = 'ALL'
    """)
    base = {(r["platform_code"], r["category_code"]): dict(r) for r in cur.fetchall()}

    # ---------- roll raw segments up to canonical, per platform ----------
    # Raw variants of the same segment hold disjoint slices of the population,
    # so buyers and users add cleanly. Suppressed cells are excluded from the
    # rollup and counted separately -- a suppressed cell means "we were not
    # allowed to know", which is not the same as zero.
    cur.execute("""
        SELECT platform_code, canonical_id, category_code,
               SUM(CASE WHEN suppressed = 0 THEN buyers END)        AS buyers,
               SUM(CASE WHEN suppressed = 0 THEN users_in_cell END) AS users,
               SUM(suppressed)                                      AS n_suppressed,
               COUNT(*)                                             AS n_cells
        FROM fact_category_affinity
        WHERE raw_id != 'ALL' AND canonical_id IS NOT NULL
        GROUP BY platform_code, canonical_id, category_code
    """)
    cells = []
    for r in cur.fetchall():
        b = base.get((r["platform_code"], r["category_code"]))
        buyers, users = r["buyers"], r["users"]
        if not b or not users:
            idx = None
            pen = None
        else:
            pen = buyers / users
            idx = _index(pen, b["penetration"])
        cells.append({
            "platform_code": r["platform_code"],
            "canonical_id": r["canonical_id"],
            "category_code": r["category_code"],
            "index": round(idx, 1) if idx is not None else None,
            "penetration": round(pen, 4) if pen is not None else None,
            "buyers": int(buyers) if buyers else 0,
            "users": int(users) if users else 0,
            "suppressed_cells": int(r["n_suppressed"] or 0),
            "total_cells": int(r["n_cells"] or 0),
        })

    total_cells = sum(c["total_cells"] for c in cells)
    suppressed_cells = sum(c["suppressed_cells"] for c in cells)

    # A clean room suppresses the RAW query output, not your rollup, so the
    # honest denominator is every raw segment x category cell -- including the
    # unmatched long-tail segments that never reach a canonical rollup. Those
    # are the small ones, and they are exactly what a minimum-users floor
    # removes first.
    cur.execute("""
        SELECT COUNT(*) AS n, SUM(suppressed) AS s
        FROM fact_category_affinity WHERE raw_id != 'ALL'
    """)
    _raw = cur.fetchone()
    raw_total, raw_suppressed = int(_raw["n"] or 0), int(_raw["s"] or 0)

    # ---------- the anchor spotlight ----------
    by_seg = {}
    for c in cells:
        if c["category_code"] != anchor:
            continue
        by_seg.setdefault(c["canonical_id"], {})[c["platform_code"]] = c

    spotlight = []
    for cid, per_plat in by_seg.items():
        idxs = [v["index"] for v in per_plat.values() if v["index"] is not None]
        if not idxs:
            continue
        mean_idx = sum(idxs) / len(idxs)

        if len(idxs) < 2:
            only = list(per_plat.keys())[0]
            agreement = {
                "status": "single_source",
                "text": f"Only {platforms[only]['platform_name']} can see this \u2014 "
                        f"no second source to check it against.",
            }
        else:
            spread = max(idxs) - min(idxs)
            bands = {_band(i) for i in idxs}
            if "over" in bands and "under" in bands:
                agreement = {
                    "status": "conflict",
                    "text": "The two platforms disagree about this audience \u2014 one has it "
                            "over-indexing and the other under-indexing. Do not act on this "
                            "until someone has looked at why.",
                }
            elif len(bands) > 1:
                # Same direction of travel, but they do not agree on whether this
                # audience actually clears the bar. Calling that "agreement"
                # because the gap is small would overstate the evidence.
                agreement = {
                    "status": "partial",
                    "text": f"The two sources straddle the line \u2014 {spread:.0f} index points "
                            f"apart, and only one of them puts this audience above the "
                            f"over-index threshold of {OVER_INDEX_AT}.",
                }
            elif spread <= 25:
                agreement = {
                    "status": "agree",
                    "text": f"Both platforms put this audience in the same band, within "
                            f"{spread:.0f} index points of each other.",
                }
            else:
                agreement = {
                    "status": "partial",
                    "text": f"Same band, but {spread:.0f} index points apart \u2014 trust the "
                            f"ranking, not the exact number.",
                }

        spotlight.append({
            "canonical_id": cid,
            "canonical_name": canon.get(cid, {}).get("canonical_name", cid),
            "segment_category": canon.get(cid, {}).get("category", ""),
            "mean_index": round(mean_idx, 1),
            "per_platform": {
                p: {
                    "platform_name": platforms[p]["platform_name"],
                    "index": v["index"],
                    "buyers": v["buyers"],
                    "users": v["users"],
                    "penetration": v["penetration"],
                    # The platform's own overall buy rate for this category --
                    # added so the UI can show two plain percentages side by
                    # side ("42% of this audience buys it vs 28% typical")
                    # instead of asking the viewer to already know what an
                    # index number means.
                    "baseline_penetration": round(base[(p, anchor)]["penetration"], 4)
                                             if (p, anchor) in base else None,
                    "suppressed_cells": v["suppressed_cells"],
                } for p, v in per_plat.items()
            },
            "agreement": agreement,
            "verdict": ("Over-indexes" if mean_idx >= OVER_INDEX_AT
                        else "Under-indexes" if mean_idx <= UNDER_INDEX_AT
                        else "About average"),
        })
    spotlight.sort(key=lambda s: -s["mean_index"])

    # ---------- basket co-purchase, single source ----------
    cur.execute("""
        SELECT platform_code, anchor_category, category_code, baskets_with_both,
               baskets_with_anchor, baskets_with_category, total_baskets, suppressed
        FROM fact_basket_affinity WHERE anchor_category = ?
    """, (anchor,))
    basket_rows = []
    basket_platform = None
    for r in cur.fetchall():
        basket_platform = r["platform_code"]
        lift, lo, hi, sig = _lift_interval(
            r["baskets_with_both"], r["baskets_with_anchor"],
            r["baskets_with_category"], r["total_baskets"])
        if lift is None:
            continue
        basket_rows.append({
            "category_code": r["category_code"],
            "category_name": cat_name.get(r["category_code"], r["category_code"]),
            "department": cat_dept.get(r["category_code"], ""),
            "lift": round(lift, 2),
            "ci_low": round(lo, 2),
            "ci_high": round(hi, 2),
            "significant": bool(sig),
            "baskets_with_both": int(r["baskets_with_both"]),
            "share_of_anchor_baskets": round(r["baskets_with_both"] / r["baskets_with_anchor"], 3)
                                        if r["baskets_with_anchor"] else 0.0,
            "suppressed": bool(r["suppressed"]),
        })
    basket_rows.sort(key=lambda x: -x["lift"])

    basket = {
        "platform_code": basket_platform,
        "platform_name": platforms.get(basket_platform, {}).get("platform_name", basket_platform),
        "anchor_name": cat_name.get(anchor, anchor),
        "rows": basket_rows,
        "note": ("Lift is how much more likely a category is to appear in a basket that already "
                 "contains the anchor, versus a basket picked at random. 1.00 means no "
                 "relationship. The bar shows the 95% range \u2014 where that range crosses 1.00, "
                 "the relationship is not distinguishable from chance."),
        "single_source_warning": (
            "Single source. Only Instacart sees several items in one basket, so there is no "
            "second platform to check this against, and online grocery is not the whole "
            "category \u2014 most energy drink volume moves through convenience stores that are "
            "invisible here."
        ),
    }

    return {
        "anchor": {"code": anchor, "name": cat_name.get(anchor, anchor)},
        "categories": categories,
        "coverage": coverage,
        "spotlight": spotlight,
        "matrix": {
            "platforms": [{"platform_code": p, "platform_name": platforms[p]["platform_name"]}
                          for p in contributing_codes],
            "segments": [{"canonical_id": cid, "canonical_name": v["canonical_name"]}
                         for cid, v in canon.items()],
            "cells": cells,
        },
        "basket": basket,
        "suppression": {
            "raw_total_cells": raw_total,
            "raw_suppressed_cells": raw_suppressed,
            "raw_rate": round(raw_suppressed / raw_total, 3) if raw_total else 0.0,
            "mapped_total_cells": total_cells,
            "mapped_suppressed_cells": suppressed_cells,
            "mapped_rate": round(suppressed_cells / total_cells, 3) if total_cells else 0.0,
            "note": ("A segment \u00d7 category cell is a slice of a slice, so the minimum-users "
                     "floor removes more here than anywhere else in this tool. Suppression is "
                     "never random \u2014 it takes the smallest segments and the smallest "
                     "categories first, which tilts any comparison toward the big ones."),
            "reassurance": ("None of the suppressed cells fed a named audience, so the rankings "
                            "on this screen are unaffected \u2014 but that will not hold once you "
                            "cut by region or week."
                            if suppressed_cells == 0 else
                            "Some suppressed cells sit behind named audiences, so treat those "
                            "rows as partial."),
        },
        "grade": CROSS_PLATFORM_GRADE,
        "grade_explain": CROSS_PLATFORM_GRADE_EXPLAIN,
    }
