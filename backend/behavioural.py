"""
ANALYSIS 3 — Behavioural Segment Matching, with explainability.

The question this screen answers is "we matched these segments by NAME —
do they actually BEHAVE alike?"  Answering it needs clustering.  Showing
the answer to a media team needs something clustering does not give you
for free: a reason.

Everything in this file exists to make the grouping explainable to
someone who is comfortable with numbers but is not a data scientist.
Five design rules drive it:

  1. Cluster on NAMED metrics, never on components.  No PCA, no UMAP,
     no embeddings.  "Component 2" cannot be explained to anyone, and
     the moment a stakeholder asks what it means the analysis is over.
     Every input below has a plain-English name written next to it.

  2. Any weighting is DECLARED, not smuggled in.  (The earlier version
     of this analysis weighted click-rate more heavily by listing the
     same column twice, which works but is invisible.  It is now a
     `weight` field that the UI prints.)

  3. Check that we clustered BEHAVIOUR and not PLATFORMS.  The classic
     failure is groups that turn out to be "the Amazon ones", "the Meta
     ones" — rediscovering that platforms differ, which we already knew.

  4. Every group gets a readable RULE and a plain-English SENTENCE,
     both generated from the data so they cannot drift away from it.

  5. Never present a grouping without its STABILITY.  An explanation
     attached to an unstable cluster is worse than no explanation,
     because it is persuasive.
"""

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import adjusted_mutual_info_score, accuracy_score, silhouette_samples


# ---------------------------------------------------------------------------
# RULE 1 — The feature set.  Named, small, and human-readable.
#
# Five features is the right size here: enough to separate segments, few
# enough that a planner can hold the whole list in their head.  Each one
# is indexed against the platform's own baseline, so 100 always means
# "typical for this platform" and the numbers are comparable across
# platforms that have very different underlying rates.
# ---------------------------------------------------------------------------
FEATURES = [
    {
        "code": "ctr_index",
        "short": "Click rate",
        "plain_name": "how often people click",
        "weight": 2.0,
        "description": "Click-through rate versus this platform's own average. 100 = typical.",
        "why_weighted": "Weighted double: clicks are common, so this number is built on far "
                        "more observations than the conversion features and carries much less "
                        "sampling noise.",
        "high": "click {mag} more often than the other groups",
        "low": "click {mag} less often than the other groups",
    },
    {
        "code": "cvr_index",
        "short": "Click-to-purchase rate",
        "plain_name": "how often a click turns into a purchase",
        "weight": 1.0,
        "description": "Purchases per click versus this platform's own average. 100 = typical.",
        "why_weighted": None,
        "high": "turn clicks into purchases {mag} more often than the other groups",
        "low": "turn clicks into purchases {mag} less often than the other groups",
    },
    {
        "code": "response_index",
        "short": "End-to-end response",
        "plain_name": "how often an ad ends in a purchase",
        "weight": 1.0,
        "description": "Purchases per ad shown versus this platform's own average. 100 = typical.",
        "why_weighted": None,
        "high": "end up buying {mag} more often per ad shown than the other groups",
        "low": "end up buying {mag} less often per ad shown than the other groups",
    },
    {
        "code": "frequency_index",
        "short": "Frequency",
        "plain_name": "how many times we hit each person",
        "weight": 1.0,
        "description": "Ads shown per person versus this platform's own average. 100 = typical.",
        "why_weighted": None,
        "high": "are served {mag} more ads per person than the other groups",
        "low": "are served {mag} fewer ads per person than the other groups",
    },
    {
        "code": "reach_share_index",
        "short": "Audience size",
        "plain_name": "how big the audience is",
        "weight": 1.0,
        "description": "Size versus the typical segment on the same platform. 100 = typical.",
        "why_weighted": None,
        "high": "are {mag} larger than the segments in the other groups",
        "low": "are {mag} smaller than the segments in the other groups",
    },
]

FEATURE_CODES = [f["code"] for f in FEATURES]
FEATURE_BY_CODE = {f["code"]: f for f in FEATURES}

# Words used to name a group, keyed by (feature, direction).  Keeping this
# as a lookup table rather than something cleverer means the generated
# names are predictable and a human can override any of them by editing
# two lines.
_NOUN = {
    ("ctr_index", "high"): "engagers",
    ("ctr_index", "low"): "low-attention audiences",
    ("cvr_index", "high"): "closers",
    ("cvr_index", "low"): "window-shoppers",
    ("response_index", "high"): "converters",
    ("response_index", "low"): "non-converters",
    ("frequency_index", "high"): "heavily-served audiences",
    ("frequency_index", "low"): "lightly-served audiences",
    ("reach_share_index", "high"): "mass audiences",
    ("reach_share_index", "low"): "niche audiences",
}
_ADJ = {
    ("ctr_index", "high"): "high-click",
    ("ctr_index", "low"): "low-click",
    ("cvr_index", "high"): "high-closing",
    ("cvr_index", "low"): "low-closing",
    ("response_index", "high"): "high-converting",
    ("response_index", "low"): "low-converting",
    ("frequency_index", "high"): "high-frequency",
    ("frequency_index", "low"): "low-frequency",
    ("reach_share_index", "high"): "broad",
    ("reach_share_index", "low"): "narrow",
}


def _jsonable(obj):
    """
    numpy scalars (np.bool_, np.int64, np.float64) are not JSON
    serializable and leak easily out of any sklearn/numpy pipeline.
    Converting once on the way out is more reliable than remembering to
    cast at every call site.
    """
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    return obj


def _magnitude_word(d):
    """How big is this difference, in words a person can picture."""
    a = abs(d)
    if a >= 1.5:
        return "far"
    if a >= 0.8:
        return "clearly"
    if a >= 0.3:
        return "somewhat"
    return "marginally"


# ---------------------------------------------------------------------------
# Building the fingerprints
# ---------------------------------------------------------------------------
def build_fingerprints(conn):
    """
    One row per raw segment, five named features, all in index units
    (100 = typical for that platform).  Also returns the raw counts,
    which the stability bootstrap needs later.
    """
    cur = conn.cursor()

    cur.execute("""
        SELECT raw_id, platform_code, metric_code, numerator num, denominator den, users_in_cell
        FROM fact_metrics WHERE raw_id != 'ALL'
    """)
    per_raw = {}
    for r in cur.fetchall():
        d = per_raw.setdefault(r["raw_id"], {"platform_code": r["platform_code"],
                                             "users": r["users_in_cell"]})
        d[r["metric_code"]] = (r["num"], r["den"])

    cur.execute("""
        SELECT platform_code, metric_code, numerator num, denominator den, users_in_cell
        FROM fact_metrics WHERE raw_id = 'ALL'
    """)
    base = {}
    base_users = {}
    for r in cur.fetchall():
        base[(r["platform_code"], r["metric_code"])] = (r["num"], r["den"])
        base_users[r["platform_code"]] = r["users_in_cell"]

    def rate(pair):
        num, den = pair
        return num / den if den else 0.0

    def idx(value, baseline):
        return (value / baseline * 100.0) if baseline else 100.0

    # First pass: raw counts and un-indexed rates.
    interim = []
    for raw_id, d in per_raw.items():
        plat = d["platform_code"]
        impressions = d.get("IMPRESSIONS", (0, 1))[0]
        clicks = d.get("CLICKS", (0, 1))[0]
        conversions = d.get("CONVERSIONS", (0, 1))[0]
        users = max(1, d.get("users", 1))
        interim.append({
            "raw_id": raw_id,
            "platform_code": plat,
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "users": users,
        })

    # The "typical segment size" baseline has to be computed across the
    # segments themselves — there is no platform-disclosed number for it.
    share_by_platform = {}
    for row in interim:
        p = row["platform_code"]
        share = row["users"] / max(1, base_users.get(p, 1))
        share_by_platform.setdefault(p, []).append(share)
    mean_share = {p: float(np.mean(v)) for p, v in share_by_platform.items()}

    records = []
    for row in interim:
        p = row["platform_code"]
        b_ctr = rate(base.get((p, "CLICKS"), (0, 1)))
        b_cvr = rate(base.get((p, "CONVERSIONS"), (0, 1)))
        b_impr, b_users = base.get((p, "IMPRESSIONS"), (0, 1))[0], max(1, base_users.get(p, 1))
        b_resp = (rate(base.get((p, "CONVERSIONS"), (0, 1))) *
                  rate(base.get((p, "CLICKS"), (0, 1))))
        b_freq = b_impr / b_users

        impressions = max(1.0, row["impressions"])
        ctr = row["clicks"] / impressions
        cvr = row["conversions"] / max(1.0, row["clicks"])
        resp = row["conversions"] / impressions
        freq = impressions / row["users"]
        share = row["users"] / b_users

        records.append({
            **row,
            "ctr_index": idx(ctr, b_ctr),
            "cvr_index": idx(cvr, b_cvr),
            "response_index": idx(resp, b_resp),
            "frequency_index": idx(freq, b_freq),
            "reach_share_index": idx(share, mean_share.get(p, share)),
        })

    records.sort(key=lambda r: r["raw_id"])
    X = np.array([[r[c] for c in FEATURE_CODES] for r in records], dtype=float)
    return records, X


def _weighted_scale(X):
    """Standardize, then apply the declared per-feature weights."""
    Xs = StandardScaler().fit_transform(X)
    w = np.array([FEATURE_BY_CODE[c]["weight"] for c in FEATURE_CODES], dtype=float)
    return Xs * w


def _cluster(X, n_clusters):
    """
    Hierarchical (agglomerative) clustering with WARD linkage.

    Ward rather than average linkage, and the reason is explainability
    rather than statistics: average linkage on this data chains, and
    produces groups of one or two segments.  A group of one cannot be
    profiled, cannot be named, and cannot be acted on -- so it is not a
    finding, whatever its silhouette score says.  Ward merges whichever
    pair adds the least extra spread, which keeps groups balanced and
    large enough to describe.  Both are deterministic: re-running gives
    the same answer, which matters for a tool people compare notes on.
    """
    Xw = _weighted_scale(X)
    n_clusters = min(n_clusters, len(Xw))
    model = AgglomerativeClustering(n_clusters=n_clusters, linkage="ward")
    return model.fit_predict(Xw)


# ---------------------------------------------------------------------------
# RULE 4a — Cluster profiles: what separated this group, and by how much
#
# For each group and each feature we compute Cohen's d, one group versus
# all the others.  Cohen's d rather than an F-statistic for one reason
# only: "this group is nearly two standard deviations above average on
# conversion" is a sentence people can picture, and "F = 43.2" is not.
# ---------------------------------------------------------------------------
def profile_clusters(records, X, labels):
    # Per-segment silhouette, translated into words rather than printed as a
    # number. The audience for this screen needs "cleanly separated" or
    # "shades into its neighbours", not a coefficient.
    try:
        sil = silhouette_samples(_weighted_scale(X), labels)
    except Exception:
        sil = np.zeros(len(labels))

    profiles = []
    for k in sorted(set(labels.tolist())):
        in_mask = labels == k
        out_mask = ~in_mask
        n_in = int(in_mask.sum())

        discs = []
        for j, code in enumerate(FEATURE_CODES):
            vin, vout = X[in_mask, j], X[out_mask, j]
            mean_in = float(vin.mean())
            if len(vout) == 0:
                d = 0.0
            else:
                # pooled standard deviation
                s_in = vin.std(ddof=1) if len(vin) > 1 else 0.0
                s_out = vout.std(ddof=1) if len(vout) > 1 else 0.0
                n1, n2 = len(vin), len(vout)
                pooled = np.sqrt((((n1 - 1) * s_in ** 2) + ((n2 - 1) * s_out ** 2)) /
                                 max(1, (n1 + n2 - 2)))
                d = float((mean_in - vout.mean()) / pooled) if pooled > 1e-9 else 0.0

            spec = FEATURE_BY_CODE[code]
            direction = "high" if d >= 0 else "low"
            phrase = spec["high"] if d >= 0 else spec["low"]
            discs.append({
                "feature": code,
                "short": spec["short"],
                "plain_name": spec["plain_name"],
                "index_value": round(mean_in, 1),
                # The all-segment average is shown next to the group average so
                # the two numbers can never appear to contradict each other. An
                # index of 122 that is nonetheless BELOW the other groups is
                # confusing unless you can see both figures side by side.
                "all_segment_mean": round(float(X[:, j].mean()), 1),
                "cohens_d": round(d, 2),
                "abs_d": abs(d),
                "direction": direction,
                "phrase": phrase.format(mag=_magnitude_word(d)),
            })

        discs.sort(key=lambda x: -x["abs_d"])
        top = discs[:3]

        # Auto-generated name: noun from the strongest discriminator,
        # adjective from the second.
        noun = _NOUN.get((top[0]["feature"], top[0]["direction"]), "segments")
        adj = _ADJ.get((top[1]["feature"], top[1]["direction"]), "") if len(top) > 1 else ""
        name = f"{adj} {noun}".strip().capitalize()

        clauses = []
        for t in top:
            clauses.append(
                f"{t['phrase']} (group average {t['index_value']:.0f} "
                f"against {t['all_segment_mean']:.0f} across all segments, "
                f"{t['cohens_d']:+.1f} sd)")
        sentence = "Segments that " + ", ".join(clauses[:-1]) + \
                   (", and " if len(clauses) > 1 else "") + clauses[-1] + "."

        for t in top:
            t.pop("abs_d", None)
        for t in discs:
            t.pop("abs_d", None)

        s_mean = float(sil[in_mask].mean()) if n_in else 0.0
        if s_mean >= 0.45:
            separation = "Cleanly separated from the other groups."
        elif s_mean >= 0.25:
            separation = "Reasonably distinct, with some overlap at the edges."
        else:
            separation = "Shades into its neighbours \u2014 treat the boundary as fuzzy."

        profiles.append({
            "cluster": int(k),
            "name": name,
            "size": n_in,
            "sentence": sentence,
            "separation": separation,
            "separation_score": round(s_mean, 2),
            "discriminators": top,
            "all_features": discs,
        })
    return profiles


# ---------------------------------------------------------------------------
# RULE 4b — The surrogate rule set
#
# The clustering is fitted however separates best.  Then a deliberately
# shallow decision tree is fitted to PREDICT the cluster label from the
# same named features.  The tree is not the model; it is a readable
# approximation of it.  Its fidelity — how often it reproduces the actual
# grouping — is reported alongside, because a rule that misdescribes the
# groups is worse than no rule.  Note that the tree is fitted on the
# UNSCALED index values, so the thresholds it prints are readable
# numbers ("click rate above 118") rather than z-scores.
# ---------------------------------------------------------------------------
def surrogate_rules(X, labels, cluster_names, min_fidelity=0.85, max_depth_cap=3):
    best = None
    for depth in range(2, max_depth_cap + 1):
        tree = DecisionTreeClassifier(max_depth=depth, random_state=0).fit(X, labels)
        fidelity = float(accuracy_score(labels, tree.predict(X)))
        best = (tree, depth, fidelity)
        if fidelity >= min_fidelity:
            break

    tree, depth, fidelity = best
    t = tree.tree_
    rules = []

    def walk(node, conds):
        if t.children_left[node] == -1:
            # sklearn >= 1.3 stores tree_.value as class PROPORTIONS for
            # classifiers, not counts, so the segment count has to come from
            # n_node_samples. Reading it off value gives "1 segment" for every
            # leaf, which is silently wrong and looks plausible.
            proportions = t.value[node][0]
            cls = int(tree.classes_[int(np.argmax(proportions))])
            total = int(t.n_node_samples[node])
            rules.append({
                "conditions": conds,
                "cluster": cls,
                "cluster_name": cluster_names.get(cls, f"Group {cls + 1}"),
                "n_segments": total,
                "purity": round(float(proportions.max()), 2),
            })
            return
        code = FEATURE_CODES[t.feature[node]]
        thr = float(t.threshold[node])
        short = FEATURE_BY_CODE[code]["short"]
        walk(t.children_left[node],
             conds + [{"feature": code, "short": short, "op": "\u2264", "threshold": round(thr, 1)}])
        walk(t.children_right[node],
             conds + [{"feature": code, "short": short, "op": ">", "threshold": round(thr, 1)}])

    walk(0, [])
    rules = [r for r in rules if r["n_segments"] > 0]

    def tidy(conditions):
        """
        A decision path can test the same feature twice ("click rate > 109"
        then "click rate <= 138"). Printed literally that reads as a mistake,
        so consecutive tests on one feature are collapsed into a range.
        """
        lo, hi, order = {}, {}, []
        for c in conditions:
            f = c["feature"]
            if f not in order:
                order.append(f)
            if c["op"] == ">":
                lo[f] = max(lo.get(f, float("-inf")), c["threshold"])
            else:
                hi[f] = min(hi.get(f, float("inf")), c["threshold"])
        out = []
        for f in order:
            short = FEATURE_BY_CODE[f]["short"]
            has_lo, has_hi = f in lo, f in hi
            if has_lo and has_hi:
                out.append({"feature": f, "short": short, "op": "between",
                            "low": lo[f], "high": hi[f],
                            "text": f"is between {lo[f]:g} and {hi[f]:g}"})
            elif has_lo:
                out.append({"feature": f, "short": short, "op": ">",
                            "threshold": lo[f], "text": f"is above {lo[f]:g}"})
            else:
                out.append({"feature": f, "short": short, "op": "\u2264",
                            "threshold": hi[f], "text": f"is {hi[f]:g} or below"})
        return out

    for r in rules:
        r["conditions"] = tidy(r["conditions"])

    lines = []
    for r in rules:
        cond_txt = "\n  AND ".join(
            f"{c['short']:<22} {c['text']}" for c in r["conditions"])
        lines.append(f"  IF  {cond_txt}\n      \u2192 {r['cluster_name']}"
                     f"   ({r['n_segments']} segments, {int(r['purity'] * 100)}% pure)")
    text = "\n\n".join(lines)

    if fidelity >= 0.90:
        verdict = "This simple rule reproduces the grouping almost exactly."
    elif fidelity >= min_fidelity:
        verdict = "This simple rule reproduces the grouping well enough to rely on."
    else:
        verdict = ("The groups are genuinely more complicated than a simple rule. "
                   "Use the group profiles above rather than this rule.")

    return {
        "depth": depth,
        "fidelity": round(fidelity, 3),
        "rules": rules,
        "text": text,
        "verdict": verdict,
        "trustworthy": fidelity >= min_fidelity,
    }


# ---------------------------------------------------------------------------
# RULE 3 — Did we cluster behaviour, or did we just cluster platforms?
#
# If the groups turn out to be "the Amazon ones / the Meta ones", the
# finding is void: we would have rediscovered that platforms differ.
# Adjusted mutual information near zero means the groupings carry no
# platform information, which is what we want.
# ---------------------------------------------------------------------------
def platform_confounding_check(records, labels):
    platforms = [r["platform_code"] for r in records]
    ami = float(adjusted_mutual_info_score(platforms, labels.tolist()))

    mix = {}
    for r, k in zip(records, labels.tolist()):
        mix.setdefault(int(k), {}).setdefault(r["platform_code"], 0)
        mix[int(k)][r["platform_code"]] += 1
    cluster_mix = [{"cluster": k, "platforms": v, "n_platforms": len(v)}
                   for k, v in sorted(mix.items())]

    multi = sum(1 for c in cluster_mix if c["n_platforms"] > 1)

    if ami < 0.15:
        verdict = ("Pass — the groupings carry almost no information about which platform "
                   "a segment came from, so they reflect behaviour rather than platform "
                   "measurement differences.")
        status = "pass"
    elif ami < 0.35:
        verdict = ("Caution — the groupings partly track platform. Some of what looks like "
                   "a behavioural difference may be a measurement difference. Read the "
                   "group profiles with that in mind.")
        status = "warn"
    else:
        verdict = ("Fail — these groups largely reproduce the platform split. This is a "
                   "platform artefact, not an audience insight, and should not be presented "
                   "as a finding.")
        status = "fail"

    return {
        "ami": round(ami, 3),
        "status": status,
        "verdict": verdict,
        "cluster_mix": cluster_mix,
        "mixed_cluster_count": multi,
        "total_clusters": len(cluster_mix),
        "explanation": ("Adjusted mutual information between the behaviour groups and the "
                        "platform each segment came from. 0 means the groups tell you nothing "
                        "about platform (good); 1 means the groups ARE the platforms (bad)."),
    }


# ---------------------------------------------------------------------------
# RULE 5 — Stability
#
# The clicks and conversions behind each segment are counts, and counts
# carry sampling noise.  We re-draw them from their own binomial
# sampling distribution, re-run the whole clustering, and record how
# often each pair of segments lands together.  This translates directly
# into readout language: "these two landed in the same group in 94 of
# 100 reruns."
# ---------------------------------------------------------------------------
def stability_bootstrap(records, n_clusters, n_iter=100, seed=7):
    rng = np.random.default_rng(seed)
    n = len(records)
    together = np.zeros((n, n), dtype=float)

    # Cache the per-platform baselines by recomputing from the unperturbed
    # feature matrix: baselines are built on platform-wide totals, which are
    # orders of magnitude larger than any one segment, so their sampling
    # noise is negligible and they are held fixed here.
    base_ratio = {}
    for i, r in enumerate(records):
        base_ratio[i] = r

    for _ in range(n_iter):
        rows = []
        for r in records:
            impressions = max(1, int(r["impressions"]))
            ctr = min(1.0, r["clicks"] / impressions)
            clicks_b = int(rng.binomial(impressions, ctr))
            cvr = min(1.0, r["conversions"] / max(1, r["clicks"]))
            conv_b = int(rng.binomial(max(1, clicks_b), cvr))
            rows.append((impressions, clicks_b, conv_b, r["users"], r))

        Xb = np.zeros((n, len(FEATURE_CODES)))
        for i, (impr, clk, cnv, users, r) in enumerate(rows):
            # Rescale the original index values by the ratio of the
            # resampled rate to the original rate — this preserves the
            # platform baselines without recomputing them.
            ctr0 = max(1e-9, r["clicks"] / max(1, r["impressions"]))
            cvr0 = max(1e-9, r["conversions"] / max(1, r["clicks"]))
            resp0 = max(1e-9, r["conversions"] / max(1, r["impressions"]))
            Xb[i, 0] = r["ctr_index"] * ((clk / impr) / ctr0)
            Xb[i, 1] = r["cvr_index"] * ((cnv / max(1, clk)) / cvr0)
            Xb[i, 2] = r["response_index"] * ((cnv / impr) / resp0)
            Xb[i, 3] = r["frequency_index"]
            Xb[i, 4] = r["reach_share_index"]

        lab = _cluster(Xb, n_clusters)
        same = (lab[:, None] == lab[None, :]).astype(float)
        together += same

    return together / n_iter


def stability_band(freq):
    if freq >= 0.85:
        return ("solid", "Solid — safe to present as a finding.")
    if freq >= 0.70:
        return ("directional", "Directional — present, but label it as indicative.")
    if freq >= 0.50:
        return ("tentative", "Tentative — a hypothesis worth testing, not a conclusion.")
    return ("unstable", "Unstable — do not present this pairing.")


# ---------------------------------------------------------------------------
# Per-segment attribution: why did THIS segment land in THIS group?
# ---------------------------------------------------------------------------
def explain_membership(record, X_row, X_all, profile):
    """
    Express the segment's position on its group's top discriminating
    features, in standard deviations against every segment in the study.
    """
    reasons = []
    for disc in profile["discriminators"]:
        j = FEATURE_CODES.index(disc["feature"])
        col = X_all[:, j]
        sd = col.std(ddof=1) or 1.0
        z = (X_row[j] - col.mean()) / sd
        reasons.append({
            "short": disc["short"],
            "value": round(float(X_row[j]), 1),
            "z": round(float(z), 2),
            "aligned": bool((z >= 0) == (disc["direction"] == "high")),
        })

    # Lead with a reason that actually points the same way as the group's
    # defining trait. Leading with reasons[0] regardless produces sentences
    # like "sits in the high-click group because its click rate is below
    # average", which is worse than saying nothing.
    aligned = [r for r in reasons if r["aligned"]]
    if aligned:
        # Lead with the STRONGEST aligned reason, not merely the first one.
        lead = max(aligned, key=lambda r: abs(r["z"]))
        verb = "above" if lead["z"] >= 0 else "below"
        if abs(lead["z"]) < 0.35:
            # Alignment in the right direction but too small to mean much.
            # Saying "mainly because X is 0.1 sd above average" would dress
            # up a coin-flip as a reason.
            sentence = (f"A weak member of \u201c{profile['name']}\u201d \u2014 it leans the right "
                        f"way on {lead['short'].lower()} (index {lead['value']:.0f}) but only by "
                        f"{abs(lead['z']):.1f} sd, so its placement is not strongly determined.")
            borderline = True
        else:
            sentence = (f"Sits in \u201c{profile['name']}\u201d mainly because its "
                        f"{lead['short'].lower()} index is {lead['value']:.0f} "
                        f"({abs(lead['z']):.1f} sd {verb} the all-segment average).")
            borderline = False
    else:
        sentence = (f"A borderline member of \u201c{profile['name']}\u201d \u2014 it does not "
                    f"strongly show any of the traits that define this group, so its "
                    f"placement is the least certain on this screen and should be "
                    f"reviewed by hand.")
        borderline = True

    return {"reasons": reasons, "sentence": sentence, "borderline": borderline}


# ---------------------------------------------------------------------------
# The assembled analysis
# ---------------------------------------------------------------------------
def behavioural_segment_matching(conn, n_clusters=4, n_bootstrap=100):
    cur = conn.cursor()

    records, X = build_fingerprints(conn)
    labels = _cluster(X, n_clusters)
    profiles = profile_clusters(records, X, labels)
    profile_by_cluster = {p["cluster"]: p for p in profiles}
    cluster_names = {p["cluster"]: p["name"] for p in profiles}

    surrogate = surrogate_rules(X, labels, cluster_names)
    platform_check = platform_confounding_check(records, labels)
    coassign = stability_bootstrap(records, n_clusters, n_iter=n_bootstrap)

    idx_of_raw = {r["raw_id"]: i for i, r in enumerate(records)}
    cluster_of = {r["raw_id"]: int(k) for r, k in zip(records, labels.tolist())}

    # ---- name-based matches, now annotated with the behavioural verdict ----
    cur.execute("""
        SELECT bm.raw_id, bm.canonical_id, bm.confidence, bm.method,
               sr.raw_name, sr.platform_code, p.platform_name
        FROM bridge_map bm
        JOIN segments_raw sr ON sr.raw_id = bm.raw_id
        JOIN platforms p ON p.platform_code = sr.platform_code
    """)
    matches = cur.fetchall()

    cur.execute("SELECT canonical_id, canonical_name FROM segments_canonical")
    canon_names = {r["canonical_id"]: r["canonical_name"] for r in cur.fetchall()}

    by_canonical = {}
    for m in matches:
        k = cluster_of.get(m["raw_id"])
        if k is None:
            continue
        prof = profile_by_cluster[k]
        i = idx_of_raw[m["raw_id"]]
        why = explain_membership(records[i], X[i], X, prof)
        by_canonical.setdefault(m["canonical_id"], []).append({
            "raw_id": m["raw_id"],
            "raw_name": m["raw_name"],
            "platform_code": m["platform_code"],
            "platform_name": m["platform_name"],
            "confidence": round(m["confidence"], 2),
            "cluster": k,
            "cluster_name": prof["name"],
            "why": why["sentence"],
            "why_detail": why["reasons"],
            "borderline": why["borderline"],
        })

    results = []
    for cid, members in by_canonical.items():
        if len(members) < 2:
            continue
        clusters_present = sorted(set(m["cluster"] for m in members))
        agree = len(clusters_present) == 1

        pairs = []
        freqs = []
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                ia = idx_of_raw[members[a]["raw_id"]]
                ib = idx_of_raw[members[b]["raw_id"]]
                f = float(coassign[ia, ib])
                band, band_text = stability_band(f)
                freqs.append(f)
                pairs.append({
                    "a_name": members[a]["raw_name"],
                    "a_platform": members[a]["platform_name"],
                    "b_name": members[b]["raw_name"],
                    "b_platform": members[b]["platform_name"],
                    "coassignment": round(f, 2),
                    "runs_together": int(round(f * n_bootstrap)),
                    "band": band,
                    "band_text": band_text,
                })
        pairs.sort(key=lambda p: -p["coassignment"])
        mean_f = float(np.mean(freqs)) if freqs else 0.0
        band, band_text = stability_band(mean_f)

        results.append({
            "canonical_id": cid,
            "canonical_name": canon_names.get(cid, cid),
            "members": members,
            "behaviourally_agrees": agree,
            "distinct_clusters": len(clusters_present),
            "groups_present": [profile_by_cluster[k]["name"] for k in clusters_present],
            "mean_coassignment": round(mean_f, 2),
            "stability_band": band,
            "stability_text": band_text,
            "pairs": pairs,
        })

    results.sort(key=lambda x: (x["behaviourally_agrees"], -len(x["members"])))

    return _jsonable({
        "explainer": {
            "features": [{
                "code": f["code"], "short": f["short"], "plain_name": f["plain_name"],
                "weight": f["weight"], "description": f["description"],
                "why_weighted": f["why_weighted"],
            } for f in FEATURES],
            "clusters": profiles,
            "surrogate": surrogate,
            "platform_check": platform_check,
            "stability": {
                "n_bootstrap": n_bootstrap,
                "method": ("Clicks and purchases were re-drawn from their own binomial "
                           "sampling distribution and the entire grouping re-run "
                           f"{n_bootstrap} times. The number shown is how often each pair "
                           "of segments landed in the same group."),
                "bands": [
                    {"band": "solid", "from": 0.85, "text": "Safe to present as a finding."},
                    {"band": "directional", "from": 0.70, "text": "Present, but label it indicative."},
                    {"band": "tentative", "from": 0.50, "text": "A hypothesis worth testing."},
                    {"band": "unstable", "from": 0.0, "text": "Do not present this pairing."},
                ],
            },
            "method_note": ("Groups were found by clustering on the five named metrics below — "
                            "no hidden mathematical components are involved, so every grouping "
                            "can be traced back to numbers a media team already understands."),
        },
        "segments": results,
    })
