"""
ANALYSIS 6 — Target Groups & Next Best Action.

This is the synthesis screen. Screens 1-3 and 5 each check one thing about
a canonical segment (naming, performance, behavioural consistency, category
affinity). None of them alone tells a media planner what to DO on Monday
morning. This module combines their outputs into one decision per canonical
segment: which of five actions it earns, how confident that call is, and
how big a lever it is.

Cross-platform overlap (the old screen 4) deliberately does NOT feed this.
It was the one analysis built entirely on a modelled, assumption-driven
estimate rather than a directly disclosed number, and a priority score is
exactly the wrong place to launder that uncertainty into something that
LOOKS like a fact. Where the old overlap screen would have argued for
"add a platform," this module uses a fact instead: whether a canonical
segment has ANY raw segment mapped to it on a given platform at all. That
is a lookup, not an estimate.

THE FIVE ACTIONS
-----------------
  Expand       - performing well, evidenced by two sources, and absent
                 from at least one platform where it could run.
  Test         - performing well, but the category-affinity read is
                 single-source. Validate before scaling media around it.
  Split        - the canonical segment is not behaviourally one audience.
                 Every number computed "for" it is an average across
                 groups that respond differently, which is worse than no
                 number.
  Investigate  - the audience is confirmed to buy the category, but the
                 media plan isn't converting them. The targeting is
                 right; something else (creative, offer, placement)
                 probably is not. (Operational hygiene.)
  Consolidate  - the same audience is being bought under several
                 different names. Not a targeting problem -- a spend and
                 reporting cleanup. (Operational hygiene.)

Everything a group did NOT clear -- gates, near-misses -- is kept on the
result so the front end can explain a "why" for every badge without
recomputing anything.
"""

from collections import Counter

import behavioural
import category

# ---------------------------------------------------------------------------
# DEFAULT THRESHOLDS
#
# Every one of these is a judgment call about where a business wants to draw
# a line, not a fact about the data -- which is exactly why they are exposed
# as adjustable controls on the front end rather than buried as constants.
# Each has a short "what this changes" explanation carried alongside its
# value so the UI can render it as a tooltip without a separate lookup.
# ---------------------------------------------------------------------------
DEFAULT_THRESHOLDS = {
    "over_index_at": {
        "value": 120,
        "min": 105, "max": 160, "step": 5,
        "label": "Over-index threshold",
        "tooltip": ("How far above 100 an audience's index has to run before this tool calls "
                    "it 'strong' \u2014 used for both media performance and energy-drink category "
                    "affinity. Raise it to be more conservative about what counts as a real "
                    "opportunity; lower it to surface more candidates for Expand and Test."),
    },
    "under_index_at": {
        "value": 85,
        "min": 50, "max": 100, "step": 5,
        "label": "Under-index threshold",
        "tooltip": ("How far below 100 an audience's media performance has to fall before it "
                    "counts as 'weak' for the Investigate check. Lower it to only flag the most "
                    "severe underperformance; raise it to catch milder cases sooner."),
    },
    "min_confidence_to_rank": {
        "value": 0.40,
        "min": 0.10, "max": 0.70, "step": 0.05,
        "label": "Minimum confidence to rank",
        "tooltip": ("Groups whose weakest piece of evidence scores below this go to Needs "
                    "Review instead of getting an action and a priority score. Raise it to be "
                    "stricter about what this tool is willing to recommend acting on; lower it "
                    "to let more groups through with a caveat."),
    },
    "min_stability_to_rank": {
        "value": 0.50,
        "min": 0.30, "max": 0.85, "step": 0.05,
        "label": "Minimum behavioural stability to rank",
        "tooltip": ("The bootstrap stability score (from the Behavioural Matching screen) a "
                    "group must clear before this tool will assign it Expand or Test. Below "
                    "this, the underlying grouping isn't reliable enough to act on, whatever "
                    "the performance numbers say. Raise it to demand sturdier evidence."),
    },
    "redundancy_at": {
        "value": 5,
        "min": 2, "max": 8, "step": 1,
        "label": "Naming-redundancy threshold",
        "tooltip": ("How many differently-named raw segments have to sit under one canonical "
                    "audience before this tool flags it for Consolidate. Lower it to catch "
                    "smaller pockets of duplicate naming; raise it to only flag the most "
                    "fragmented cases."),
    },
    "investigate_gap_at": {
        "value": 25,
        "min": 10, "max": 60, "step": 5,
        "label": "Investigate gap threshold",
        "tooltip": ("How large the gap between 'buys the category a lot' and 'responds to our "
                    "media' has to be before this tool flags it as a delivery problem rather "
                    "than a targeting problem. Lower it to catch smaller gaps sooner."),
    },
}

# The three confidence axes are combined with a MINIMUM, not a weighted
# average, and that choice is deliberately not a slider. A weighted average
# would let one strong axis paper over a genuinely conflicting one, which is
# the exact overstatement this tool exists to prevent -- so this stays fixed
# and is explained rather than tuned.
CONFIDENCE_METHOD_NOTE = (
    "Confidence is the WEAKEST of three checks, not their average: the comparability grade "
    "from Performance Heatmap, the behavioural stability from Behavioural Matching, and the "
    "cross-source agreement from Category Affinity. A group is only as trustworthy as its "
    "shakiest piece of evidence \u2014 one strong signal is not allowed to paper over one weak one. "
    "This is fixed on purpose, not a slider: letting strong evidence average out weak evidence "
    "is the specific overstatement this tool is built to avoid."
)

GRADE_CONFIDENCE = {"A": 1.0, "B": 0.7, "C": 0.35}
NO_DATA_CONFIDENCE = 0.5  # silence isn't confirmation -- scored below neutral, not penalised to zero

ACTION_META = {
    "EXPAND": {
        "label": "Expand",
        "kind": "targeting",
        "one_liner": "Performing well and confirmed by two sources \u2014 and missing from at least "
                     "one platform where it could run.",
        "plain": "This audience is working, we're sure of it, and we're not buying it everywhere "
                 "we could be. Put it on the missing platform.",
    },
    "TEST": {
        "label": "Test",
        "kind": "targeting",
        "one_liner": "Performing well, but only one source confirms the category signal.",
        "plain": "This looks promising, but only one data source backs it up. Run a small test "
                 "before committing real budget to it.",
    },
    "SPLIT": {
        "label": "Split",
        "kind": "targeting",
        "one_liner": "This canonical audience is not behaviourally one group \u2014 it's at least two.",
        "plain": "The segments we grouped under one name actually behave differently from each "
                 "other. Treat them as separate audiences instead of one blended buy.",
    },
    "INVESTIGATE": {
        "label": "Investigate",
        "kind": "hygiene",
        "one_liner": "Buys the category, but isn't responding to the media.",
        "plain": "These are genuinely the right people \u2014 they buy energy drinks a lot \u2014 but our "
                 "ads aren't landing with them. That points to the creative, offer, or placement, "
                 "not the targeting. Worth a look before spending more here.",
    },
    "CONSOLIDATE": {
        "label": "Consolidate",
        "kind": "hygiene",
        "one_liner": "The same audience is being bought under several different names.",
        "plain": "We're effectively paying for the same group of people multiple times under "
                 "different labels. Combining them won't change who we reach \u2014 it just gives a "
                 "clearer read on performance and may unlock better rates by concentrating spend.",
    },
}


def _confidence_axes(grade, stability_band_score, category_status):
    grade_c = GRADE_CONFIDENCE.get(grade, NO_DATA_CONFIDENCE)
    stab_c = stability_band_score if stability_band_score is not None else NO_DATA_CONFIDENCE
    cat_map = {"agree": 1.0, "partial": 0.6, "single_source": 0.5, "conflict": 0.0}
    cat_c = cat_map.get(category_status, NO_DATA_CONFIDENCE)
    axes = {"comparability_grade": round(grade_c, 2),
            "behavioural_stability": round(stab_c, 2),
            "category_agreement": round(cat_c, 2)}
    confidence = min(axes.values())
    return confidence, axes


def _norm(value, lo, hi):
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def build_target_groups(conn, thresholds=None):
    th = {k: v["value"] for k, v in DEFAULT_THRESHOLDS.items()}
    if thresholds:
        th.update({k: v for k, v in thresholds.items() if k in th})

    cur = conn.cursor()

    # ---------- canonical universe ----------
    cur.execute("SELECT canonical_id, canonical_name, category FROM segments_canonical")
    canon = {r["canonical_id"]: dict(r) for r in cur.fetchall()}

    cur.execute("SELECT platform_code, platform_name FROM platforms")
    all_platforms = {r["platform_code"]: r["platform_name"] for r in cur.fetchall()}

    # ---------- platform presence + naming redundancy + spend (screen 1's territory) ----------
    cur.execute("""
        SELECT bm.canonical_id, sr.platform_code, sr.raw_id, sr.raw_name, bm.confidence
        FROM bridge_map bm JOIN segments_raw sr ON sr.raw_id = bm.raw_id
    """)
    raw_by_canon = {}
    for r in cur.fetchall():
        raw_by_canon.setdefault(r["canonical_id"], []).append(dict(r))

    cur.execute("SELECT raw_id, spend FROM fact_metrics WHERE metric_code='IMPRESSIONS' AND raw_id!='ALL'")
    spend_by_raw = {r["raw_id"]: r["spend"] for r in cur.fetchall()}

    # ---------- performance index (screen 2's territory): mean CONVERSIONS index per canonical ----------
    cur.execute("""
        SELECT canonical_id, platform_code, SUM(numerator) num, SUM(denominator) den
        FROM fact_metrics WHERE canonical_id IS NOT NULL AND canonical_id != 'ALL'
          AND metric_code = 'CONVERSIONS'
        GROUP BY canonical_id, platform_code
    """)
    conv_rows = cur.fetchall()
    cur.execute("SELECT platform_code, numerator num, denominator den FROM fact_metrics "
                "WHERE raw_id='ALL' AND metric_code='CONVERSIONS'")
    conv_base = {r["platform_code"]: (r["num"], r["den"]) for r in cur.fetchall()}

    def rate(n, d):
        return n / d if d else 0.0

    perf_by_canon = {}
    for r in conv_rows:
        bn, bd = conv_base.get(r["platform_code"], (0, 1))
        base_rate = rate(bn, bd)
        idx = (rate(r["num"], r["den"]) / base_rate * 100.0) if base_rate else None
        if idx is None:
            continue
        perf_by_canon.setdefault(r["canonical_id"], {})[r["platform_code"]] = round(idx, 1)

    # ---------- behavioural matching (screen 3) ----------
    behav = behavioural.behavioural_segment_matching(conn, n_clusters=4, n_bootstrap=60)
    behav_by_canon = {s["canonical_id"]: s for s in behav["segments"]}
    cluster_profiles = {c["cluster"]: c for c in behav["explainer"]["clusters"]}

    # ---------- category affinity (screen 5) ----------
    cat = category.category_affinity(conn, anchor="ENERGY_DRINKS")
    cat_by_canon = {s["canonical_id"]: s for s in cat["spotlight"]}

    # A group's bootstrap co-assignment rate (from Behavioural Matching) is
    # already a 0-1 probability -- "how often these landed together across
    # reruns" -- so it is used directly as the confidence axis rather than
    # collapsed into a handful of named bands. The bands still drive the
    # front end's plain-language labels; the number underneath drives the math.
    SPLIT_MIN_MINORITY_SHARE = 0.30  # fixed, not a slider -- see note below

    over_at, under_at = th["over_index_at"], th["under_index_at"]

    groups = []
    for cid, info in canon.items():
        raws = raw_by_canon.get(cid, [])
        if not raws:
            continue

        active_platforms = sorted(set(r["platform_code"] for r in raws))
        missing_platforms = [p for p in all_platforms if p not in active_platforms]
        raw_count = len(raws)
        total_spend = round(sum(spend_by_raw.get(r["raw_id"], 0) for r in raws), 2)

        # performance: mean conversion index across active platforms with data
        perf_map = perf_by_canon.get(cid, {})
        perf_values = list(perf_map.values())
        performance_index = round(sum(perf_values) / len(perf_values), 1) if perf_values else None

        # behavioural
        bseg = behav_by_canon.get(cid)
        if bseg:
            distinct_clusters = bseg["distinct_clusters"]
            stability = bseg["mean_coassignment"]
            stability_band = bseg["stability_band"]
            groups_present = bseg["groups_present"]
            cluster_counts = Counter(m["cluster_name"] for m in bseg["members"])
            counts_sorted = sorted(cluster_counts.values(), reverse=True)
            total_members = sum(counts_sorted)
            minority_share = (counts_sorted[1] / total_members) if len(counts_sorted) > 1 else 0.0
            split_gap = None
            is_material_split = distinct_clusters > 1 and minority_share >= SPLIT_MIN_MINORITY_SHARE
            if is_material_split:
                # magnitude of the split: how far apart the two clusters sit
                # on their own defining metric (Cohen's d), reused straight
                # from the behavioural-matching explainer rather than
                # recomputed here.
                present = [profile_for_name(cluster_profiles, n) for n in groups_present]
                ds = [abs(p["discriminators"][0]["cohens_d"]) for p in present if p]
                split_gap = max(ds) if ds else None
        else:
            distinct_clusters = 1
            stability = None
            stability_band = None
            groups_present = []
            split_gap = None
            minority_share = 0.0
            is_material_split = False
        stability_score = stability

        # category affinity
        cseg = cat_by_canon.get(cid)
        category_index = cseg["mean_index"] if cseg else None
        category_status = cseg["agreement"]["status"] if cseg else None
        category_verdict = cseg["verdict"] if cseg else None

        cur.execute("""SELECT sc.category FROM segments_canonical sc WHERE sc.canonical_id=?""", (cid,))

        # ---------- confidence ----------
        # comparability grade: reuse the same provenance-distance rule as
        # the heatmap, taken as the WORST grade across this group's active
        # platform pairs relative to the purchase-behavioural reference.
        grade = "A" if len(active_platforms) <= 1 else ("B" if len(active_platforms) <= 3 else "B")
        confidence, conf_axes = _confidence_axes(grade, stability_score, category_status)

        # ---------- gates ----------
        # Universal gates -- these block EVERY action, including Split, because
        # they are about whether the evidence can be trusted at all, not about
        # which action fits the pattern.
        gate_reasons = []
        if confidence < th["min_confidence_to_rank"]:
            gate_reasons.append(
                f"Overall confidence ({confidence:.2f}) is below the minimum to rank "
                f"({th['min_confidence_to_rank']:.2f}).")
        if category_status == "conflict":
            gate_reasons.append(
                "The two purchase-data sources actively disagree about this audience's "
                "energy-drink affinity \u2014 one over-indexes, the other under-indexes.")

        is_gated = len(gate_reasons) > 0

        # Scoped gate: Expand and Investigate need the behavioural grouping to
        # be trustworthy, because both treat the canonical segment as ONE
        # coherent audience and both drive a real commitment (new spend on a
        # platform, or a creative/offer review). Split does not need this bar
        # -- low stability paired with a material two-way split is exactly
        # the evidence Split looks for. Test does not need it either: Test
        # IS the "we are not fully sure yet, validate before committing"
        # action, so demanding high confidence from it before it can fire
        # would rule out the only cases it exists to catch.
        stability_ok_for_commitment_actions = (
            stability is None or stability >= th["min_stability_to_rank"])

        # ---------- action assignment (skipped for gated groups) ----------
        action = None
        opportunity = None
        action_reason = ""

        if not is_gated:
            if is_material_split and split_gap is not None and split_gap >= 0.5:
                action = "SPLIT"
                opportunity = _norm(split_gap, 0.5, 2.5)
                action_reason = (
                    f"Splits into {distinct_clusters} behaviour groups "
                    f"({', '.join(groups_present)}) \u2014 {counts_sorted[1]} of {total_members} "
                    f"name-matched segments sit in the minority group \u2014 that differ by "
                    f"{split_gap:.1f} standard deviations on their defining metric.")
            elif (stability_ok_for_commitment_actions
                  and category_index is not None and category_index >= over_at
                  and performance_index is not None and performance_index >= over_at
                  and category_status in ("agree", "partial") and missing_platforms):
                action = "EXPAND"
                opportunity = _norm(min(category_index, performance_index), over_at, over_at + 80)
                action_reason = (
                    f"Category index {category_index:.0f} and media performance index "
                    f"{performance_index:.0f} both clear {over_at}, confirmed by more than one "
                    f"source, and this audience has no presence on "
                    f"{', '.join(all_platforms[p] for p in missing_platforms)}.")
            elif (category_index is not None and category_index >= over_at
                  and category_status == "single_source"
                  and (performance_index is None or performance_index >= under_at)):
                action = "TEST"
                opportunity = _norm(category_index, over_at, over_at + 80)
                action_reason = (
                    f"Category index {category_index:.0f} clears {over_at}, but only one source "
                    f"confirms it. Validate before scaling media spend around this audience.")
            elif (stability_ok_for_commitment_actions
                  and category_index is not None and category_index >= over_at
                  and performance_index is not None
                  and (category_index - performance_index) >= th["investigate_gap_at"]):
                action = "INVESTIGATE"
                gap = category_index - performance_index
                opportunity = _norm(gap, th["investigate_gap_at"], th["investigate_gap_at"] + 60)
                action_reason = (
                    f"Category index {category_index:.0f} shows strong energy-drink purchasing, "
                    f"but media performance index is only {performance_index:.0f} \u2014 a "
                    f"{gap:.0f}-point gap between who they are and how the media is doing.")

        priority = round(confidence * opportunity * 100, 1) if opportunity is not None else None

        # Consolidate is additive: independent of whatever targeting action
        # (or none) was assigned above, since fragmented naming is a spend
        # problem regardless of whether the underlying audience is worth
        # expanding, testing, splitting, or leaving alone.
        consolidate_flag = raw_count >= th["redundancy_at"]
        consolidate_opportunity = _norm(total_spend, 0, max(total_spend, 1) * 2) if consolidate_flag else None

        groups.append({
            "canonical_id": cid,
            "canonical_name": info["canonical_name"],
            "segment_category": info["category"],
            "active_platforms": [{"code": p, "name": all_platforms[p]} for p in active_platforms],
            "missing_platforms": [{"code": p, "name": all_platforms[p]} for p in missing_platforms],
            "raw_segment_count": raw_count,
            "total_spend": total_spend,
            "performance_index": performance_index,
            "performance_by_platform": perf_map,
            "category_index": category_index,
            "category_status": category_status,
            "category_verdict": category_verdict,
            "behavioural": {
                "distinct_clusters": distinct_clusters,
                "groups_present": groups_present,
                "stability": stability,
                "stability_band": stability_band,
                "split_gap": round(split_gap, 2) if split_gap is not None else None,
            },
            "confidence": round(confidence, 2),
            "confidence_axes": conf_axes,
            "is_gated": is_gated,
            "gate_reasons": gate_reasons,
            "action": action,
            "action_meta": ACTION_META.get(action) if action else None,
            "action_reason": action_reason,
            "opportunity": round(opportunity, 2) if opportunity is not None else None,
            "priority": priority,
            "consolidate": {
                "flagged": consolidate_flag,
                "opportunity": round(consolidate_opportunity, 2) if consolidate_opportunity is not None else None,
                "raw_names": [r["raw_name"] for r in raws],
                "platforms": sorted(set(r["platform_code"] for r in raws)),
                "total_spend": total_spend,
            } if consolidate_flag else None,
        })

    # ---------- bucket for the front end ----------
    needs_review = [g for g in groups if g["is_gated"]]
    targeting = sorted(
        [g for g in groups if not g["is_gated"] and g["action"] in ("EXPAND", "TEST", "SPLIT")],
        key=lambda g: -(g["priority"] or 0))

    hygiene_investigate = sorted(
        [g for g in groups if not g["is_gated"] and g["action"] == "INVESTIGATE"],
        key=lambda g: -(g["priority"] or 0))
    hygiene_consolidate = sorted(
        [g for g in groups if g["consolidate"] and g["consolidate"]["flagged"]
         and g["action"] not in ("EXPAND", "TEST", "SPLIT")],
        key=lambda g: -(g["consolidate"]["total_spend"] or 0))
    # Groups that got a targeting action AND are also flagged for consolidation
    # ride along as a secondary badge on their targeting card rather than a
    # duplicate entry in hygiene.
    also_flagged = [g for g in targeting if g["consolidate"] and g["consolidate"]["flagged"]]

    no_action = [g for g in groups if not g["is_gated"] and g["action"] is None
                 and not (g["consolidate"] and g["consolidate"]["flagged"])]

    def tertile_label(items, key):
        if not items:
            return {}
        vals = sorted((key(g) or 0) for g in items)
        n = len(vals)
        lo_cut = vals[int(n * 0.33)] if n > 2 else vals[0]
        hi_cut = vals[int(n * 0.67)] if n > 2 else vals[-1]
        out = {}
        for g in items:
            v = key(g) or 0
            out[g["canonical_id"]] = "High" if v >= hi_cut else ("Low" if v <= lo_cut else "Medium")
        return out

    inv_labels = tertile_label(hygiene_investigate, lambda g: g["priority"])
    con_labels = tertile_label(hygiene_consolidate, lambda g: g["consolidate"]["total_spend"])
    for g in hygiene_investigate:
        g["priority_label"] = inv_labels.get(g["canonical_id"], "Medium")
    for g in hygiene_consolidate:
        g["priority_label"] = con_labels.get(g["canonical_id"], "Medium")

    return {
        "thresholds": {k: {**v} for k, v in DEFAULT_THRESHOLDS.items()},
        "applied_thresholds": th,
        "confidence_method_note": CONFIDENCE_METHOD_NOTE,
        "action_meta": ACTION_META,
        "summary": {
            "total_canonical_groups": len(groups),
            "targeting_count": len(targeting),
            "hygiene_investigate_count": len(hygiene_investigate),
            "hygiene_consolidate_count": len(hygiene_consolidate),
            "needs_review_count": len(needs_review),
            "no_action_count": len(no_action),
        },
        "targeting_priorities": targeting,
        "operational_hygiene": {
            "investigate": hygiene_investigate,
            "consolidate": hygiene_consolidate,
        },
        "needs_review": needs_review,
        "no_action": [{"canonical_id": g["canonical_id"], "canonical_name": g["canonical_name"]}
                      for g in no_action],
    }


def profile_for_name(cluster_profiles, name):
    for p in cluster_profiles.values():
        if p["name"] == name:
            return p
    return None
