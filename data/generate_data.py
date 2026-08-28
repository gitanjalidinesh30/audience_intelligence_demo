"""
Synthetic data generator for the Audience Intelligence demo.

WHAT THIS DOES (in plain English)
----------------------------------
Real ad platforms (Amazon, Instacart, Google, Meta) will never let you see
their raw user-level data. So to build and test a system that compares
audiences across them, we make up a pretend population of people, give each
one a set of hidden traits (how into fitness they are, how much they game,
etc), and then simulate how FOUR DIFFERENT platforms would each notice a
slightly different, imperfect slice of that same population -- with their
own made-up segment names, their own blind spots, and their own noise.

Because we (the generator) secretly know the truth, we can also grade how
well the demo's matching and overlap tools do -- something you can never do
with real platform data.

Everything below is randomly generated. No real customer or company data is
used anywhere in this project.
"""

import sqlite3
import numpy as np
import os
import json
import hashlib

RNG_SEED = 42
rng = np.random.default_rng(RNG_SEED)

N_PEOPLE = 20_000
DB_PATH = os.path.join(os.path.dirname(__file__), "audience_intel.db")

# ---------------------------------------------------------------------------
# STEP 1 — Give every pretend person a set of hidden traits
# ---------------------------------------------------------------------------
TRAITS = [
    "fitness_affinity", "gaming_affinity", "caffeine_dependence",
    "price_sensitivity", "novelty_seeking", "household_size_score",
    "urbanicity", "shift_work_likelihood", "motorsport_affinity",
]

# A hand-picked correlation matrix so traits move together the way they
# plausibly would in real life (e.g. gaming and caffeine dependence are
# related; fitness and price sensitivity are slightly opposed).
n_traits = len(TRAITS)
corr = np.eye(n_traits)
def set_corr(a, b, v):
    i, j = TRAITS.index(a), TRAITS.index(b)
    corr[i, j] = corr[j, i] = v

set_corr("gaming_affinity", "caffeine_dependence", 0.42)
set_corr("fitness_affinity", "price_sensitivity", -0.25)
set_corr("shift_work_likelihood", "caffeine_dependence", 0.35)
set_corr("motorsport_affinity", "gaming_affinity", 0.30)
set_corr("novelty_seeking", "urbanicity", 0.28)
set_corr("household_size_score", "price_sensitivity", 0.20)

latent = rng.multivariate_normal(mean=np.zeros(n_traits), cov=corr, size=N_PEOPLE)
traits = {name: latent[:, i] for i, name in enumerate(TRAITS)}

# ---------------------------------------------------------------------------
# STEP 2 — Define the "true" segments (the answer key -- never shown as-is
# to the platforms, only used by us to check our work)
# ---------------------------------------------------------------------------
# Each true segment is "top X% of the population on this trait (or blend)".
TRUE_SEGMENTS = {
    "FITNESS_ENTHUSIAST":       lambda t: t["fitness_affinity"] > 0.75,
    "HEALTH_CONSCIOUS_SHOPPER": lambda t: (t["fitness_affinity"] > 0.25) & (t["price_sensitivity"] < 0.10) & (t["fitness_affinity"] < 1.3),
    "GAMING_ENTHUSIAST":        lambda t: t["gaming_affinity"] > 0.75,
    "PRICE_SENSITIVE_SHOPPER":  lambda t: t["price_sensitivity"] > 0.75,
    "NIGHT_SHIFT_WORKER":       lambda t: t["shift_work_likelihood"] > 0.85,
    "MOTORSPORT_FAN":           lambda t: t["motorsport_affinity"] > 0.85,
    "HIGH_CAFFEINE_USER":       lambda t: t["caffeine_dependence"] > 0.80,
    "URBAN_COMMUTER":           lambda t: t["urbanicity"] > 0.80,
    "LARGE_HOUSEHOLD_BUYER":    lambda t: t["household_size_score"] > 0.80,
    "NOVELTY_SEEKER":           lambda t: t["novelty_seeking"] > 0.80,
}

CANONICAL_DEFS = {
    "FITNESS_ENTHUSIAST":       ("Fitness Enthusiast", "People who actively buy or shop for fitness products/behaviour", "Occasion & Lifestyle"),
    "HEALTH_CONSCIOUS_SHOPPER": ("Health-Conscious Shopper", "People whose grocery basket skews health-oriented (a diet signal, not necessarily an exercise signal)", "Occasion & Lifestyle"),
    "GAMING_ENTHUSIAST":        ("Gaming Enthusiast", "People engaged with gaming content, communities or purchases", "Occasion & Lifestyle"),
    "PRICE_SENSITIVE_SHOPPER":  ("Price-Sensitive Shopper", "People who over-index on deals, discounts and lower-priced options", "Value & Behaviour"),
    "NIGHT_SHIFT_WORKER":       ("Night / Shift Worker", "People with a high likelihood of non-standard working hours", "Occasion & Lifestyle"),
    "MOTORSPORT_FAN":           ("Motorsport Fan", "People with strong affinity for motorsport content/events", "Occasion & Lifestyle"),
    "HIGH_CAFFEINE_USER":       ("High Caffeine User", "People with high modelled or observed caffeine consumption", "Value & Behaviour"),
    "URBAN_COMMUTER":           ("Urban Commuter", "People in dense urban areas with commuting behaviour", "Demographic"),
    "LARGE_HOUSEHOLD_BUYER":    ("Large Household Buyer", "People shopping for a larger household", "Demographic"),
    "NOVELTY_SEEKER":           ("Novelty Seeker", "People who over-index on trying new products", "Value & Behaviour"),
}

true_membership = {seg: fn(traits) for seg, fn in TRUE_SEGMENTS.items()}

# ---------------------------------------------------------------------------
# STEP 3 — Platform setup: how each platform sees the world differently
# ---------------------------------------------------------------------------
PLATFORMS = {
    "AMC":     {"name": "Amazon Marketing Cloud",  "provenance": "PURCHASE_BEHAVIOURAL", "min_users": 50,  "coverage": 0.55, "noise": 0.06, "cpm": 12.0},
    "ICDH":    {"name": "Instacart Data Hub",       "provenance": "BASKET_COMPOSITION",   "min_users": 50,  "coverage": 0.22, "noise": 0.05, "cpm": 9.0},
    "ADH":     {"name": "Google Ads Data Hub",      "provenance": "CONTENT_AFFINITY",     "min_users": 50,  "coverage": 0.65, "noise": 0.14, "cpm": 7.5},
    "META_AA": {"name": "Meta Advanced Analytics",  "provenance": "INTEREST_DECLARED",    "min_users": 50,  "coverage": 0.60, "noise": 0.18, "cpm": 8.5},
}

# Name variants each platform might use for the same true segment (the
# "Fitness Enthusiasts / Fitness Freaks / Fit Forever" problem). We also
# sometimes generate a SECOND near-duplicate raw segment for the same true
# segment on the same platform -- this is what makes the "inventory audit"
# / naming-redundancy use case meaningful.
SYNONYM_POOL = {
    "FITNESS_ENTHUSIAST": ["Fitness Enthusiasts", "Fitness Freaks", "Fit Forever", "Active Lifestyle Shoppers", "Gym Regulars"],
    "HEALTH_CONSCIOUS_SHOPPER": ["Health & Wellness Shoppers", "Health-Conscious Buyers", "Wellness Seekers"],
    "GAMING_ENTHUSIAST": ["Gaming Enthusiasts", "Hardcore Gamers", "Gaming Session Audience", "Console & PC Gamers"],
    "PRICE_SENSITIVE_SHOPPER": ["Deal Seekers", "Value Shoppers", "Coupon Clippers", "Price-Conscious Buyers"],
    "NIGHT_SHIFT_WORKER": ["Shift Workers", "Night Owls", "Late Shift Audience"],
    "MOTORSPORT_FAN": ["Motorsport Fans", "Racing Enthusiasts", "Track Day Crowd"],
    "HIGH_CAFFEINE_USER": ["Heavy Caffeine Users", "Energy Seekers", "Caffeine Dependents"],
    "URBAN_COMMUTER": ["Urban Commuters", "City Commuters", "Metro Riders"],
    "LARGE_HOUSEHOLD_BUYER": ["Large Household Shoppers", "Big Family Buyers", "Bulk Buyers"],
    "NOVELTY_SEEKER": ["Novelty Seekers", "Early Adopters", "Trend Chasers"],
}

RECENCY_SUFFIXES = ["", "", "", " - Past 30 Days", " (L90D)", " - US"]

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.executescript("""
CREATE TABLE platforms (
    platform_code TEXT PRIMARY KEY,
    platform_name TEXT,
    provenance_type TEXT,
    min_users INTEGER,
    coverage REAL,
    noise REAL
);

CREATE TABLE segments_canonical (
    canonical_id TEXT PRIMARY KEY,
    canonical_name TEXT,
    definition TEXT,
    category TEXT
);

CREATE TABLE segments_raw (
    raw_id TEXT PRIMARY KEY,
    platform_code TEXT,
    raw_name TEXT,
    provenance_type TEXT,
    true_segment_id TEXT,      -- hidden answer key, not shown to the "matching" logic
    reach_count INTEGER
);

CREATE TABLE bridge_map (
    raw_id TEXT,
    canonical_id TEXT,
    confidence REAL,
    weight REAL,
    method TEXT,
    PRIMARY KEY (raw_id, canonical_id)
);

CREATE TABLE fact_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform_code TEXT,
    raw_id TEXT,
    canonical_id TEXT,
    metric_code TEXT,
    numerator REAL,
    denominator REAL,
    users_in_cell INTEGER,
    suppressed INTEGER,
    comparability_grade TEXT,
    spend REAL
);

CREATE TABLE true_overlap_reference (
    segment_a TEXT,
    platform_a TEXT,
    segment_b TEXT,
    platform_b TEXT,
    true_overlap_users INTEGER,
    true_jaccard REAL
);
""")

for code, p in PLATFORMS.items():
    cur.execute("INSERT INTO platforms VALUES (?,?,?,?,?,?)",
                (code, p["name"], p["provenance"], p["min_users"], p["coverage"], p["noise"]))

for cid, (name, definition, category) in CANONICAL_DEFS.items():
    cur.execute("INSERT INTO segments_canonical VALUES (?,?,?,?)", (cid, name, definition, category))

# ---------------------------------------------------------------------------
# STEP 4 — For each platform: who is reachable, which segments they observe
# (with noise), what raw names those segments get, and how they perform
# ---------------------------------------------------------------------------
METRIC_EFFECTS = {seg: rng.uniform(0.85, 1.6) for seg in TRUE_SEGMENTS}  # CTR lift by true segment
CONV_EFFECTS = {seg: rng.uniform(0.8, 1.7) for seg in TRUE_SEGMENTS}

raw_segment_platform_membership = {}  # raw_id -> boolean array over N_PEOPLE (reachable+observed)
true_segment_platform_membership_reachable = {}  # (platform, true_seg) -> boolean array, for true overlap calc

platform_reachable = {}  # keep reachable masks around for the ALL baseline below

# Deliberate platform gaps: real media teams do not run every audience on
# every platform even when the platform COULD see them -- someone just
# hasn't set it up yet. Without gaps like these, "missing platform" never
# happens in this dataset and the Expand action (which depends on it) could
# never fire. Each entry also removes that (platform, segment) pair from the
# category-affinity simulation below, since a segment that was never built
# on a platform has no purchase read there either.
DELIBERATE_PLATFORM_GAPS = {
    ("ADH", "MOTORSPORT_FAN"),        # strong energy-drink fit, never built on Google
    ("META_AA", "NOVELTY_SEEKER"),    # never built on Meta
    ("AMC", "URBAN_COMMUTER"),        # never built on Amazon
}

for code, p in PLATFORMS.items():
    reachable = rng.random(N_PEOPLE) < p["coverage"]
    platform_reachable[code] = reachable

    for seg_id in TRUE_SEGMENTS:
        true_mem = true_membership[seg_id]
        # The TRUE overlap reference (Step 7) needs this for every platform x
        # segment pair regardless of gaps below -- it is ground truth about
        # the population, independent of which audiences a media team has
        # actually built.
        true_segment_platform_membership_reachable[(code, seg_id)] = true_mem & reachable

        if (code, seg_id) in DELIBERATE_PLATFORM_GAPS:
            continue
        # Observed = true membership XOR noise, but only meaningful among reachable people
        noise_draw = rng.random(N_PEOPLE) < p["noise"]
        observed_mem = true_mem ^ noise_draw
        observed_and_reachable = observed_mem & reachable

        # Decide how many raw segment variants this platform uses for this true segment
        n_variants = 1
        if rng.random() < 0.35:
            n_variants = 2  # near-duplicate naming
        variant_names = rng.choice(SYNONYM_POOL[seg_id], size=n_variants, replace=False)

        # Split the observed population across the variants (roughly evenly, with noise)
        if n_variants == 1:
            splits = [observed_and_reachable]
        else:
            coinflip = rng.random(N_PEOPLE) < 0.5
            splits = [observed_and_reachable & coinflip, observed_and_reachable & ~coinflip]

        for i, name in enumerate(variant_names):
            suffix = rng.choice(RECENCY_SUFFIXES)
            raw_name = f"{name}{suffix}"
            raw_id = f"{code}__{seg_id}__{i}"
            mem = splits[i]
            raw_segment_platform_membership[raw_id] = mem
            cur.execute("INSERT INTO segments_raw VALUES (?,?,?,?,?,?)",
                        (raw_id, code, raw_name, p["provenance"], seg_id, int(mem.sum())))

    # A few platform-unique "noise" segments with no real true-segment backing
    # (so the taxonomy/matching has some genuinely unmatched long-tail too)
    for j in range(2):
        junk_mem = (rng.random(N_PEOPLE) < 0.03) & reachable
        raw_id = f"{code}__UNMATCHED__{j}"
        raw_name = rng.choice(["Misc In-Market Segment", "Custom Uploaded Audience", "Lookalike Seed Pool", "Beta Segment (Unlabeled)"])
        cur.execute("INSERT INTO segments_raw VALUES (?,?,?,?,?,?)",
                    (raw_id, code, raw_name, p["provenance"], None, int(junk_mem.sum())))
        raw_segment_platform_membership[raw_id] = junk_mem

conn.commit()

# ---------------------------------------------------------------------------
# STEP 5 — Bridge map: mostly-correct matching with a few deliberately wrong
# assignments injected, so the "behavioural matching" use case has something
# real to catch.
# ---------------------------------------------------------------------------
cur.execute("SELECT raw_id, true_segment_id, platform_code FROM segments_raw")
raw_rows = cur.fetchall()

WRONG_MATCH_RATE = 0.08
seg_ids = list(TRUE_SEGMENTS.keys())

for raw_id, true_seg, plat in raw_rows:
    if true_seg is None:
        continue  # unmatched junk segments stay unmatched
    assigned = true_seg
    method = "SEMANTIC"
    confidence = float(np.clip(rng.normal(0.88, 0.08), 0.4, 0.99))
    if rng.random() < WRONG_MATCH_RATE:
        # inject a plausible-looking but wrong match to a different segment
        assigned = rng.choice([s for s in seg_ids if s != true_seg])
        method = "LEXICAL"
        confidence = float(np.clip(rng.normal(0.68, 0.08), 0.4, 0.9))
    weight = 1.0
    # occasionally split weight across the true segment and a plausible neighbour
    # (mirrors the Health-Conscious/Fitness split in the source document)
    cur.execute("INSERT INTO bridge_map VALUES (?,?,?,?,?)", (raw_id, assigned, confidence, weight, method))

conn.commit()

# ---------------------------------------------------------------------------
# STEP 6 — Simulate impressions / clicks / conversions per raw segment, then
# aggregate + apply clean-room-style suppression
# ---------------------------------------------------------------------------
cur.execute("SELECT raw_id, platform_code, true_segment_id FROM segments_raw")
raw_rows = cur.fetchall()

BASE_CTR = 0.012
BASE_CVR = 0.06

for raw_id, plat, true_seg in raw_rows:
    mem = raw_segment_platform_membership[raw_id]
    n_users = int(mem.sum())
    if n_users == 0:
        continue

    ctr_lift = METRIC_EFFECTS.get(true_seg, rng.uniform(0.9, 1.1))
    cvr_lift = CONV_EFFECTS.get(true_seg, rng.uniform(0.9, 1.1))

    impressions_per_user = rng.negative_binomial(4, 0.3, size=n_users) + 1
    impressions = int(impressions_per_user.sum())
    click_p = float(np.clip(BASE_CTR * ctr_lift, 0, 0.9))
    clicks = int(rng.binomial(impressions_per_user, click_p).sum())
    conv_p = float(np.clip(BASE_CVR * cvr_lift, 0, 0.9))
    conversions = int(rng.binomial(max(clicks, 0), conv_p))

    min_users = PLATFORMS[plat]["min_users"]
    suppressed = 1 if n_users < min_users else 0
    grade = "A"  # provenance-consistency grade gets assigned later per canonical rollup
    cpm = PLATFORMS[plat]["cpm"]
    spend = round(impressions / 1000 * cpm, 2)

    def ins(metric, num, den):
        cur.execute("""INSERT INTO fact_metrics
            (platform_code, raw_id, canonical_id, metric_code, numerator, denominator,
             users_in_cell, suppressed, comparability_grade, spend)
            VALUES (?,?,NULL,?,?,?,?,?,?,?)""",
            (plat, raw_id, metric, num, den, n_users, suppressed, grade, spend if metric == "IMPRESSIONS" else 0))

    ins("IMPRESSIONS", impressions, 1)
    ins("CLICKS", clicks, impressions if impressions else 1)
    ins("CONVERSIONS", conversions, clicks if clicks else 1)

# Attach each fact row to its best-confidence canonical segment (a raw
# segment could in principle map to more than one canonical id; we take the
# top match here for the purposes of the heatmap/inventory views)
cur.execute("""
    UPDATE fact_metrics
    SET canonical_id = (
        SELECT bm.canonical_id FROM bridge_map bm
        WHERE bm.raw_id = fact_metrics.raw_id
        ORDER BY bm.confidence DESC LIMIT 1
    )
    WHERE raw_id != 'ALL'
""")
conn.commit()

# ---------------------------------------------------------------------------
# STEP 6b — Platform-wide "ALL" baseline (needed to index any segment's
# performance against its own platform's overall average)
# ---------------------------------------------------------------------------
for code, p in PLATFORMS.items():
    reachable = platform_reachable[code]
    n_users = int(reachable.sum())
    impressions_per_user = rng.negative_binomial(4, 0.3, size=n_users) + 1
    impressions = int(impressions_per_user.sum())
    clicks = int(rng.binomial(impressions_per_user, BASE_CTR).sum())
    conversions = int(rng.binomial(max(clicks, 0), BASE_CVR))
    for metric, num, den in [
        ("IMPRESSIONS", impressions, 1),
        ("CLICKS", clicks, impressions if impressions else 1),
        ("CONVERSIONS", conversions, clicks if clicks else 1),
    ]:
        cur.execute("""INSERT INTO fact_metrics
            (platform_code, raw_id, canonical_id, metric_code, numerator, denominator,
             users_in_cell, suppressed, comparability_grade, spend)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (code, "ALL", "ALL", metric, num, den, n_users, 0, "A", 0))

conn.commit()

# ---------------------------------------------------------------------------
# STEP 6c — CPG category purchasing, for the Category Affinity analysis
#
# WHICH PLATFORMS CAN EVEN DO THIS, AND WHY IT MATTERS
# ----------------------------------------------------
# Category-level PURCHASE data exists on only two of the four platforms:
#
#   AMC  (purchase-behavioural)  -- Amazon retail purchases, by product category
#   ICDH (basket composition)    -- grocery baskets, by category
#
# Google Ads Data Hub sees content consumption and Meta sees declared
# interests. Neither observes what anyone actually bought, so neither can
# contribute a purchase-based category affinity. That is not a gap in this
# demo -- it is the real shape of the data, and the screen says so rather
# than quietly averaging over it.
#
# Only Instacart sees MULTIPLE ITEMS IN ONE BASKET, so the co-purchase
# ("what else travels with energy drinks") half of the analysis is
# single-source by necessity.
# ---------------------------------------------------------------------------
CATEGORIES = {
    # code                    (display name,                department,        anchor?)
    "ENERGY_DRINKS":          ("Energy Drinks",             "Beverages",        1),
    "SPORTS_DRINKS":          ("Sports & Isotonic Drinks",  "Beverages",        0),
    "RTD_COFFEE":             ("Coffee & RTD Coffee",       "Beverages",        0),
    "CARBONATED_SOFT_DRINKS": ("Carbonated Soft Drinks",    "Beverages",        0),
    "BOTTLED_WATER":          ("Bottled Water",             "Beverages",        0),
    "BEER_CIDER":             ("Beer & Cider",              "Beverages",        0),
    "PROTEIN_BARS":           ("Protein & Nutrition Bars",  "Snacks",           0),
    "SALTY_SNACKS":           ("Salty Snacks",              "Snacks",           0),
    "CONFECTIONERY":          ("Confectionery & Candy",     "Snacks",           0),
    "BREAKFAST_CEREAL":       ("Breakfast Cereal",          "Pantry",           0),
    "FROZEN_MEALS":           ("Frozen Meals",              "Frozen",           0),
    "FRESH_PRODUCE":          ("Fresh Produce",             "Fresh",            0),
    "DAIRY_YOGURT":           ("Dairy & Yogurt",            "Fresh",            0),
    "VITAMINS_SUPPLEMENTS":   ("Vitamins & Supplements",    "Health",           0),
    "HOUSEHOLD_CLEANING":     ("Household Cleaning",        "Home Care",        0),
    "PAPER_HOME_CARE":        ("Paper & Home Care",         "Home Care",        0),
}
ANCHOR_CATEGORY = "ENERGY_DRINKS"

# How each category's purchase propensity loads onto the hidden traits from
# Step 1. This is what makes category affinity a real signal rather than
# noise: energy drinks load heavily on caffeine dependence, gaming and shift
# work, so the segments built on those traits will genuinely over-index --
# and the analysis has to rediscover that from aggregate data alone.
CATEGORY_LOADINGS = {
    "ENERGY_DRINKS":          {"caffeine_dependence": 0.90, "gaming_affinity": 0.50,
                               "shift_work_likelihood": 0.50, "motorsport_affinity": 0.35,
                               "novelty_seeking": 0.20, "fitness_affinity": 0.15},
    "SPORTS_DRINKS":          {"fitness_affinity": 0.80, "caffeine_dependence": 0.20},
    "RTD_COFFEE":             {"caffeine_dependence": 0.85, "shift_work_likelihood": 0.45,
                               "urbanicity": 0.25},
    "CARBONATED_SOFT_DRINKS": {"price_sensitivity": 0.30, "gaming_affinity": 0.30,
                               "fitness_affinity": -0.40},
    "BOTTLED_WATER":          {"fitness_affinity": 0.50, "urbanicity": 0.30},
    "BEER_CIDER":             {"motorsport_affinity": 0.35, "novelty_seeking": 0.20,
                               "fitness_affinity": -0.15},
    "PROTEIN_BARS":           {"fitness_affinity": 0.85, "novelty_seeking": 0.15},
    "SALTY_SNACKS":           {"gaming_affinity": 0.60, "household_size_score": 0.30,
                               "price_sensitivity": 0.20, "fitness_affinity": -0.30},
    "CONFECTIONERY":          {"gaming_affinity": 0.40, "household_size_score": 0.35,
                               "fitness_affinity": -0.35},
    "BREAKFAST_CEREAL":       {"household_size_score": 0.55, "price_sensitivity": 0.25},
    "FROZEN_MEALS":           {"shift_work_likelihood": 0.50, "household_size_score": 0.30,
                               "urbanicity": 0.25, "fitness_affinity": -0.25},
    "FRESH_PRODUCE":          {"fitness_affinity": 0.45, "household_size_score": 0.20,
                               "price_sensitivity": -0.20},
    "DAIRY_YOGURT":           {"household_size_score": 0.40, "fitness_affinity": 0.25},
    "VITAMINS_SUPPLEMENTS":   {"fitness_affinity": 0.60, "novelty_seeking": 0.25},
    "HOUSEHOLD_CLEANING":     {"household_size_score": 0.60, "price_sensitivity": 0.20},
    "PAPER_HOME_CARE":        {"household_size_score": 0.65, "price_sensitivity": 0.25},
}

# Base purchase rate per category, before traits.
CATEGORY_BASE_RATE = {
    "ENERGY_DRINKS": 0.16, "SPORTS_DRINKS": 0.14, "RTD_COFFEE": 0.26,
    "CARBONATED_SOFT_DRINKS": 0.34, "BOTTLED_WATER": 0.38, "BEER_CIDER": 0.22,
    "PROTEIN_BARS": 0.18, "SALTY_SNACKS": 0.42, "CONFECTIONERY": 0.36,
    "BREAKFAST_CEREAL": 0.30, "FROZEN_MEALS": 0.28, "FRESH_PRODUCE": 0.52,
    "DAIRY_YOGURT": 0.48, "VITAMINS_SUPPLEMENTS": 0.20,
    "HOUSEHOLD_CLEANING": 0.33, "PAPER_HOME_CARE": 0.40,
}

# The two platforms do not see the same shopping trip. Amazon over-observes
# stockable, shippable goods; Instacart over-observes fresh and perishable.
# This is why the two can disagree about the same category -- and why the
# screen shows them side by side instead of averaging them together.
CATEGORY_PLATFORM_SKEW = {
    "AMC":  {"VITAMINS_SUPPLEMENTS": 1.45, "PROTEIN_BARS": 1.35, "ENERGY_DRINKS": 1.10,
             "PAPER_HOME_CARE": 1.20, "HOUSEHOLD_CLEANING": 1.15, "RTD_COFFEE": 1.10,
             "FRESH_PRODUCE": 0.25, "DAIRY_YOGURT": 0.35, "FROZEN_MEALS": 0.30,
             "BEER_CIDER": 0.45},
    "ICDH": {"FRESH_PRODUCE": 1.50, "DAIRY_YOGURT": 1.40, "FROZEN_MEALS": 1.35,
             "BREAKFAST_CEREAL": 1.15, "BEER_CIDER": 1.10, "SALTY_SNACKS": 1.10,
             "VITAMINS_SUPPLEMENTS": 0.70, "PAPER_HOME_CARE": 0.85},
}

# A uniform per-platform multiplier cancels out the moment you index against
# the platform's own baseline, so on its own it would make the two platforms
# agree about every audience -- which is not how this works in practice. The
# real difference is in WHO buys a category on each platform: Amazon energy
# drink volume is multipack stock-up (bigger households, more price-driven),
# while Instacart is single-serve top-up (denser urban, more impulse). These
# shifts are what make the "do the two sources agree about this audience?"
# check on screen 5 do any work.
CATEGORY_PLATFORM_LOADING_SHIFT = {
    "AMC": {
        # On Amazon, a moderately fitness-minded shopper who buys energy
        # drinks at all tends to skip them in favour of protein and
        # electrolyte products -- pushed down here on purpose so this
        # segment's Amazon read and Instacart read genuinely disagree (see
        # ICDH below), giving the demo's "sources conflict" case something
        # real to point to instead of an artificial example.
        "ENERGY_DRINKS":        {"household_size_score": 0.40, "price_sensitivity": 0.25,
                                 "fitness_affinity": -0.85},
        "SALTY_SNACKS":         {"household_size_score": 0.30},
        "PROTEIN_BARS":         {"price_sensitivity": 0.20},
        "CARBONATED_SOFT_DRINKS": {"household_size_score": 0.35},
    },
    "ICDH": {
        # On Instacart, the same fitness-minded shopper more often grabs a
        # zero-sugar or functional energy drink alongside groceries -- an
        # opposite-signed shift from AMC above, for the same trait and the
        # same category, by design.
        "ENERGY_DRINKS":        {"urbanicity": 0.35, "novelty_seeking": 0.25,
                                 "household_size_score": -0.15, "fitness_affinity": 0.85},
        "FRESH_PRODUCE":        {"urbanicity": 0.20},
        "FROZEN_MEALS":         {"urbanicity": 0.25},
        "BEER_CIDER":           {"novelty_seeking": 0.25},
    },
}

CATEGORY_PLATFORMS = ["AMC", "ICDH"]        # who can answer this question at all
BASKET_PLATFORMS = ["ICDH"]                 # who sees multi-item baskets
BASKETS_PER_SHOPPER = 4

cur.executescript("""
CREATE TABLE categories (
    category_code TEXT PRIMARY KEY,
    category_name TEXT,
    department TEXT,
    is_anchor INTEGER
);

CREATE TABLE fact_category_affinity (
    platform_code TEXT,
    raw_id TEXT,              -- 'ALL' marks the platform-wide baseline row
    canonical_id TEXT,
    category_code TEXT,
    buyers INTEGER,
    users_in_cell INTEGER,
    penetration REAL,
    suppressed INTEGER
);

CREATE TABLE fact_basket_affinity (
    platform_code TEXT,
    anchor_category TEXT,
    category_code TEXT,
    baskets_with_both INTEGER,
    baskets_with_anchor INTEGER,
    baskets_with_category INTEGER,
    total_baskets INTEGER,
    suppressed INTEGER
);
""")

for code, (name, dept, anchor_flag) in CATEGORIES.items():
    cur.execute("INSERT INTO categories VALUES (?,?,?,?)", (code, name, dept, anchor_flag))


def _logistic(x):
    return 1.0 / (1.0 + np.exp(-x))


def category_probability(cat_code, platform_code):
    """Per-person probability of buying this category, as seen by this platform."""
    base = CATEGORY_BASE_RATE[cat_code]
    # start from the base rate on the log-odds scale, then add trait effects
    z = np.full(N_PEOPLE, np.log(base / (1 - base)))
    for trait_name, loading in CATEGORY_LOADINGS[cat_code].items():
        z = z + loading * traits[trait_name]
    for trait_name, shift in CATEGORY_PLATFORM_LOADING_SHIFT.get(platform_code, {}).get(cat_code, {}).items():
        z = z + shift * traits[trait_name]
    p = _logistic(z)
    skew = CATEGORY_PLATFORM_SKEW.get(platform_code, {}).get(cat_code, 1.0)
    return np.clip(p * skew, 0.0, 0.97)


# ---- (a) Category penetration per raw segment, on the two purchase platforms ----
cur.execute("SELECT raw_id, platform_code, true_segment_id FROM segments_raw")
raw_rows_cat = cur.fetchall()

bought_by_platform = {}
for plat in CATEGORY_PLATFORMS:
    bought_by_platform[plat] = {
        c: rng.random(N_PEOPLE) < category_probability(c, plat) for c in CATEGORIES
    }

for plat in CATEGORY_PLATFORMS:
    reachable = platform_reachable[plat]
    min_users = PLATFORMS[plat]["min_users"]

    # platform-wide baseline: what share of everyone we can reach buys each category
    n_all = int(reachable.sum())
    for cat in CATEGORIES:
        buyers = int((bought_by_platform[plat][cat] & reachable).sum())
        cur.execute("INSERT INTO fact_category_affinity VALUES (?,?,?,?,?,?,?,?)",
                    (plat, "ALL", "ALL", cat, buyers, n_all,
                     buyers / n_all if n_all else 0.0, 0))

    for raw_id, rp, true_seg in raw_rows_cat:
        if rp != plat:
            continue
        if (plat, true_seg) in DELIBERATE_PLATFORM_GAPS:
            continue
        mem = raw_segment_platform_membership[raw_id]
        n_users = int(mem.sum())
        if n_users == 0:
            continue
        for cat in CATEGORIES:
            buyers = int((bought_by_platform[plat][cat] & mem).sum())
            # Clean-room suppression bites much harder here than on the segment
            # screens: a segment x category cell is a slice of a slice, so a
            # good number of these fall under the minimum-users floor. That is
            # realistic, and the analysis reports the suppression rate rather
            # than silently dropping the rows.
            suppressed = 1 if buyers < min_users else 0
            cur.execute("INSERT INTO fact_category_affinity VALUES (?,?,?,?,?,?,?,?)",
                        (plat, raw_id, None, cat, buyers, n_users,
                         buyers / n_users if n_users else 0.0, suppressed))

cur.execute("""
    UPDATE fact_category_affinity
    SET canonical_id = (
        SELECT bm.canonical_id FROM bridge_map bm
        WHERE bm.raw_id = fact_category_affinity.raw_id
        ORDER BY bm.confidence DESC LIMIT 1
    )
    WHERE raw_id != 'ALL'
""")

# ---- (b) Basket co-purchase, Instacart only ----
for plat in BASKET_PLATFORMS:
    reachable = platform_reachable[plat]
    shopper_idx = np.flatnonzero(reachable)
    n_shoppers = len(shopper_idx)
    n_baskets = n_shoppers * BASKETS_PER_SHOPPER

    # A category's chance of appearing in any ONE basket is lower than its
    # chance of appearing across a whole period -- scale it down accordingly.
    in_basket = {}
    for cat in CATEGORIES:
        p_person = category_probability(cat, plat)[shopper_idx]
        p_basket = np.clip(p_person * 0.45, 0.0, 0.9)
        draws = rng.random((n_shoppers, BASKETS_PER_SHOPPER)) < p_basket[:, None]
        in_basket[cat] = draws.reshape(-1)

    anchor_mask = in_basket[ANCHOR_CATEGORY]
    n_anchor = int(anchor_mask.sum())
    min_users = PLATFORMS[plat]["min_users"]

    for cat in CATEGORIES:
        if cat == ANCHOR_CATEGORY:
            continue
        both = int((anchor_mask & in_basket[cat]).sum())
        n_cat = int(in_basket[cat].sum())
        cur.execute("INSERT INTO fact_basket_affinity VALUES (?,?,?,?,?,?,?,?)",
                    (plat, ANCHOR_CATEGORY, cat, both, n_anchor, n_cat, n_baskets,
                     1 if both < min_users else 0))

conn.commit()

# ---------------------------------------------------------------------------
# STEP 7 — Pre-compute the TRUE cross-platform overlap (our secret answer
# key) for every true-segment pair across platform pairs, so the demo can
# show "modelled estimate vs what actually happened".
# ---------------------------------------------------------------------------
platform_codes = list(PLATFORMS.keys())
for i, pa in enumerate(platform_codes):
    for pb in platform_codes[i+1:]:
        for seg in seg_ids:
            a = true_segment_platform_membership_reachable[(pa, seg)]
            b = true_segment_platform_membership_reachable[(pb, seg)]
            inter = int((a & b).sum())
            union = int((a | b).sum())
            jac = inter / union if union else 0.0
            cur.execute("INSERT INTO true_overlap_reference VALUES (?,?,?,?,?,?)",
                        (seg, pa, seg, pb, inter, jac))

conn.commit()

# ---------------------------------------------------------------------------
# STEP 8 — Assign comparability grades at the canonical rollup level
# (A = same provenance family across the pair being compared isn't decided
# here per-cell; simplify: grade by provenance type distance from a
# reference "purchase-behavioural" baseline, matching Section 8.3's spirit)
# ---------------------------------------------------------------------------
PROVENANCE_GRADE = {
    ("PURCHASE_BEHAVIOURAL", "PURCHASE_BEHAVIOURAL"): "A",
    ("PURCHASE_BEHAVIOURAL", "BASKET_COMPOSITION"): "B",
    ("PURCHASE_BEHAVIOURAL", "CONTENT_AFFINITY"): "B",
    ("PURCHASE_BEHAVIOURAL", "INTEREST_DECLARED"): "B",
    ("BASKET_COMPOSITION", "CONTENT_AFFINITY"): "C",
    ("BASKET_COMPOSITION", "INTEREST_DECLARED"): "C",
    ("CONTENT_AFFINITY", "INTEREST_DECLARED"): "B",
}
def grade_for(p1, p2):
    t1, t2 = PLATFORMS[p1]["provenance"], PLATFORMS[p2]["provenance"]
    if t1 == t2:
        return "A"
    key = (t1, t2) if (t1, t2) in PROVENANCE_GRADE else (t2, t1)
    return PROVENANCE_GRADE.get(key, "C")

conn.commit()
conn.close()

print(f"Done. Database written to {DB_PATH}")
print(f"People simulated: {N_PEOPLE:,}")
cur2 = sqlite3.connect(DB_PATH).cursor()
