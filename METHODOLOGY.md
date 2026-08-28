# How the Demo Works: Logic, Methods, and Why

This document explains what's actually happening behind each of the four screens in
the demo — the reasoning, the specific techniques used, and why those techniques
(rather than something simpler or fancier) were the right fit for a prototype like
this one. It assumes no statistics background; anywhere a method is named, it's
explained in plain terms first.

Everything described here runs on **made-up data**. Before getting into the four
analyses, it's worth understanding how that data was built, because all four
analyses depend on it.

---

## 0. The foundation: one hidden population, four biased views of it

**The problem this solves:** if you generate four separate, independent, random
datasets — one per platform — you have no way to check whether your matching or
overlap logic actually works, because there's no real answer to compare against.
Two "Fitness Enthusiast" segments from two random datasets have no true
relationship to each other; any match you find is coincidence.

**What was done instead:** a single pretend population of 20,000 people was created
first, and each of the four platforms was made to observe a *partial, imperfect,
differently-labeled* slice of that same population. Because the underlying people
are shared, there's a real, known relationship between segments across platforms —
and because it's synthetic, that true relationship can be kept as a hidden answer
key and used later to grade how well the demo's own methods performed.

Concretely, in `data/generate_data.py`:

1. **Give every person hidden traits.** Each of the 20,000 people gets a score on
   nine traits — how fitness-oriented they are, how much they game, how
   price-sensitive they are, and so on. These traits aren't independent of each
   other; a correlation structure was built in (for example, gaming affinity and
   caffeine dependence move together), because real audiences aren't made of
   independent attributes either.

   *Model used:* a **multivariate normal distribution** — a standard way to
   generate several correlated numeric traits at once from a specified correlation
   matrix. It was chosen because it's the simplest tool that produces realistic
   correlation *and* is fully controllable — every correlation in the data is one
   we explicitly typed in, so nothing is a modeling artifact.

2. **Define the true (hidden) segments.** About ten "true" audiences were defined
   as threshold rules on those traits — e.g., "Fitness Enthusiast" = top quartile
   on fitness affinity. This is the answer key. It is never shown to the matching
   or clustering logic; it only exists so the demo can check itself later.

3. **Simulate each platform's imperfect view.** For each platform, every person is:
   - **Reachable or not**, based on a coverage rate (Amazon's ad network reaches a
     different slice of people than Instacart's grocery app does).
   - **Correctly or incorrectly classified** into each segment, based on a noise
     rate (platforms don't perfectly detect who's "into fitness" — some
     misclassification is realistic and platform-specific: Google's broader,
     content-based signals were given a higher noise rate than Amazon's
     purchase-based ones, matching the actual construct differences described in
     the source plan).
   - **Given a platform-specific name** for each segment, drawn from a pool of
     realistic synonyms ("Fitness Enthusiasts" / "Fitness Freaks" / "Fit Forever"),
     sometimes split into two near-duplicate segments on the same platform — this
     is what creates realistic naming redundancy for Analysis 1 to find.

4. **Simulate ad performance.** For each resulting raw segment, impressions,
   clicks, and conversions were simulated with a built-in performance lift or
   drag tied to the *true* segment (so, for example, everyone who is genuinely
   fitness-oriented gets a consistent click-through boost, regardless of which
   platform or which raw segment name they ended up under).

   *Models used:*
   - **Negative binomial distribution** for impressions per person — the standard
     choice for "count of events per person" data like ad impressions, because
     unlike a simple average, it naturally produces the lopsided pattern real
     impression data has (most people see an ad a few times, a few people see it
     many times).
   - **Binomial distribution** for clicks (given impressions) and conversions
     (given clicks) — the standard model for "how many yes/no events out of N
     chances," which is exactly what a click or a conversion is.

This step matters because it's what makes the rest of the demo *checkable*. Every
one of the four analyses below is, in effect, being tested against a ground truth
that a real-world deployment would never have access to.

---

## 1. Segment Inventory Audit

**The question:** "How many differently-named segments are we effectively buying
for the same audience?"

**The logic:** this one doesn't require any statistical modeling — it's a
grouping and counting exercise, but the grouping is the interesting part. Each
platform-specific segment ("Gaming Session Audience" on Google, "Hardcore Gamers"
on Amazon) is already linked to a shared canonical ID in a mapping table
(`bridge_map` — the output of a matching process, described in Analysis 3 below,
that would normally involve lexical scoring, semantic embeddings, and human
review). Given that mapping already exists, the audit simply:

1. Groups every raw, platform-specific segment by its shared canonical ID.
2. Counts how many differently-named raw segments landed in each group.
3. Attaches the estimated ad spend behind each raw segment (from the impression
   volume and an assumed cost-per-thousand-impressions for that platform).
4. Flags any canonical group with four or more raw variants as a "naming
   redundancy" — a working threshold, not a statistically derived one; it's meant
   to surface the clearest cases, not to be a precise cutoff.

**Why this approach:** the value of this analysis is entirely in the grouping,
not in any modeling — it's the fastest of the four to compute and, per the source
plan, often the fastest to pay for itself, because it turns into a very concrete,
non-technical finding ("we're paying for the same audience under seven different
names") that a media team can act on immediately without needing to trust a model.

---

## 2. Performance Index Heatmap with Grades

**The question:** "Where does each audience over- or under-perform, and how much
should we trust that comparison?"

**The logic — indexing:** you cannot compare a raw click-through rate on Meta to
a raw click-through rate on Amazon, because the two platforms count impressions,
attribute clicks, and measure their overall audience completely differently. A
raw number comparison would be comparing apples to oranges. Instead, every
segment's metric is converted into an **index against that same platform's own
overall average**:

```
index = (segment's rate on this platform) / (that platform's overall rate) × 100
```

A value of 100 means "performs exactly like the platform average." A value of 130
means "performs 30% better than this platform's own baseline." Because the
comparison is now relative to each platform's own yardstick, indexes *can* be
compared across platforms even though raw rates can't — this is standard practice
in media measurement, not something invented for this demo, and it's the same
technique described in Section 8.2 of the build plan.

*Why this and not something fancier:* indexing needs no model at all, no
training data, and no assumptions — just two sums. That simplicity is exactly
why it's trustworthy: everyone looking at the number can see precisely how it was
built, which matters a great deal when the output is going to inform a media
spend decision.

**The logic — grading:** an index number on its own hides an important fact: some
comparisons are much safer than others. Amazon's audience data comes from actual
purchase behavior; Meta's comes from declared interests and engagement. Comparing
two purchase-behavior numbers is safer than comparing a purchase-behavior number
to an interest-based one, even if both are expressed as the same kind of index.
Each platform is tagged with a **provenance type** (purchase-behavioral,
basket-composition, content-affinity, interest-declared), and every comparison
gets a grade from a lookup table of provenance-pair combinations:

- **Grade A** — same provenance type. Safe to compare directly.
- **Grade B** — related but distinct provenance types. Compare direction and
  rank, not the exact numbers.
- **Grade C** — very different kinds of evidence. Treat as a hint only.

*Why a lookup table and not a formula:* this grading is a judgment call about
what kinds of underlying evidence are "close enough" to compare, and that
judgment doesn't reduce to arithmetic — it's exactly the kind of thing the source
plan says should be decided by domain and media expertise, not inferred
statistically. A simple, inspectable table means anyone reviewing the demo can
see and challenge exactly which pairs were graded which way, rather than trying
to reverse-engineer a formula.

---

## 3. Behavioural Segment Matching (and how the grouping explains itself)

**The question:** "The segments were matched by name — do they actually behave
alike, and if the answer is no, *why* did they split?"

**The logic:** this is the demo's way of stress-testing its own name-based
matching. Two segments called "Fitness Enthusiasts" on different platforms might
describe completely different groups of people underneath the shared label — the
build plan's central warning (Section 7.5) is that name similarity is not
population similarity. The way to check is to ignore the names entirely and
compare how each segment actually *behaves*.

The clustering itself is a handful of lines. Everything else in
`backend/behavioural.py` exists to answer the follow-up question a media team
will always ask, which is "why?" — because a grouping presented without a reason
does not survive its first meeting.

### 3.1 The five named features

Every segment gets a "fingerprint" of five numbers, each indexed against its own
platform's baseline so 100 always means "typical for this platform":

| Feature | In plain English | Weight |
|---|---|---|
| Click rate | how often people click | ×2 |
| Click-to-purchase rate | how often a click turns into a purchase | ×1 |
| End-to-end response | how often an ad ends in a purchase | ×1 |
| Frequency | how many times we hit each person | ×1 |
| Audience size | how big the audience is | ×1 |

*Why named features and not components:* no PCA, no UMAP, no embeddings. Those
would separate the groups more cleanly and destroy the explanation — "component
2" cannot be described to anyone, and the moment a stakeholder asks what it means
the analysis is over. Five is deliberate: enough to separate segments, few enough
that a planner can hold the whole list in their head.

*Why click rate is weighted double:* conversions are rare events built on small
counts, so a conversion-based index is naturally noisier than a click-based one
built on much larger volumes. Weighting toward the stabler signal produces
cleaner groupings. The important part is that the weight is a *declared number
the interface prints*, not something hidden in the maths. (An earlier version
achieved the same weighting by listing the click column twice — which works, but
is invisible to anyone reading the screen.)

### 3.2 Finding the groups

The fingerprints are standardized, weighted, and clustered with **hierarchical
(agglomerative) clustering using Ward linkage** into four behaviour groups.

*Why Ward rather than average linkage:* on this data average linkage chains, and
produces groups containing one or two segments. A group of one cannot be
profiled, cannot be named, and cannot be acted on — so it is not a finding,
whatever its silhouette score says. Ward merges whichever pair adds the least
extra spread, which keeps the groups balanced and large enough to describe. Both
are deterministic, so re-running gives the same answer, which matters for a tool
people compare notes on.

### 3.3 Explaining the groups — four devices

**(a) Group profiles with effect sizes.** For each group and each feature, the
demo computes **Cohen's d** — the group versus all the other groups, in standard
deviations. Cohen's d rather than an F-statistic for one reason only: "this group
converts nearly two standard deviations more than the others" is a sentence
people can picture, and "F = 43.2" is not. The top three features by absolute
effect size become the group's description, and the group's name is generated
from the strongest two via a small lookup table (so "high click rate" plus "small
audience" becomes *Narrow engagers*).

Each group also gets a one-sentence description generated straight from those
numbers, so the words can never drift away from the data. Both the group average
and the all-segment average are printed side by side, because an index of 122
that is nonetheless *below* the other groups is baffling unless you can see both
figures at once.

**(b) A surrogate rule set.** A deliberately shallow decision tree (depth 2, or 3
if depth 2 isn't good enough) is fitted to *predict the group label from the same
five named features*. The tree is not the model — it is a readable approximation
of it, and it prints as plain if/then rules on the real index values, so anyone
can check a segment by hand:

```
IF  Click rate is between 108.8 and 137.6
    → High-converting engagers   (32 segments, 91% pure)
```

Its **fidelity** — how often the simple rule reproduces the actual grouping — is
always reported. If fidelity falls below 85%, the interface says the groups are
genuinely more complicated than a simple rule rather than showing a rule that
quietly misdescribes them.

**(c) A platform-confounding check.** The classic failure mode in this kind of
analysis is that the groups turn out to be "the Amazon ones, the Meta ones, the
Google ones" — rediscovering that platforms differ, which we already knew, and
dressing it up as an audience insight. The demo computes the **adjusted mutual
information** between the group labels and the platform each segment came from.
Near zero means the groups carry no platform information, which is what we want;
high means the finding is void. This is printed on screen every time, not
buried, and is the reason the features are all platform-indexed to begin with.

**(d) Per-segment attribution.** Every row in the segment table says why *that*
segment landed in *that* group, expressed in standard deviations against all
segments. Two refinements matter here: the sentence leads with the *strongest*
reason that points the same way as the group's defining trait (leading with
whichever came first produces nonsense like "sits in the high-click group because
its click rate is below average"); and where no reason is strong enough, the row
is flagged as a **borderline member** and highlighted, rather than given a
confident-sounding explanation it doesn't deserve.

### 3.4 Stability — the part that is easiest to skip and shouldn't be

An explanation attached to an unstable grouping is worse than no explanation,
because it is persuasive. So the clicks and purchases behind every segment are
re-drawn from their own **binomial sampling distribution** and the entire
clustering is re-run 100 times. For every pair of name-matched segments, the demo
records how often the two landed in the same group.

This translates directly into language anyone can act on: *"these two landed in
the same behaviour group in 94 of 100 reruns."* The bands used:

| Co-assignment | Verdict |
|---|---|
| 85%+ | Solid — safe to present as a finding |
| 70–85% | Directional — present, but label it indicative |
| 50–70% | Tentative — a hypothesis worth testing |
| under 50% | Unstable — do not present this pairing |

Platform-wide baselines are held fixed during the bootstrap, since they are built
on totals orders of magnitude larger than any single segment and their own
sampling noise is negligible.

### 3.5 What comes out

For every canonical (name-matched) group: whether all its members landed in the
same behaviour group, which groups they split across, each member's reason for
being where it is, and the pairwise stability of every pairing. Splits are sorted
to the top — those are the ones worth a human review before the money moves.

---

## 4. Modelled Cross-Platform Overlap

**The question:** "How much duplication is there between our reach for the same
audience on two different platforms?"

**The constraint that shapes everything here:** no clean room in this setup
exposes a hashed identifier, so there is no way — not even a small verified
sample — to check whether a specific person appears in two platforms' audiences
at once. That rules out the classic "1st-party anchor panel" approach entirely.
The only inputs available are numbers a platform will actually disclose in
aggregate: how big a segment is, and how much of the market that platform
reaches overall. The estimate is built in three deliberately honest layers,
each one weaker than it sounds:

1. **Hard bounds — pure set logic, no assumptions.** For two segments of size
   `n_a` and `n_b` drawn from a market of size `N`, the overlap must satisfy:

   ```
   max(0, n_a + n_b − N)  ≤  overlap  ≤  min(n_a, n_b)
   ```

   This is always true regardless of any relationship between the platforms —
   it's the honest floor and ceiling before any modelling assumption is added.
   `N` itself is estimated from numbers the platforms disclose: each platform's
   total reach divided by its coverage of the market (`market_size_estimate` in
   the data), rather than from anything only visible at the individual level.

2. **Independence guess — "what if reach on A and B were unrelated?"** The
   textbook estimate under that assumption is simply `(n_a × n_b) / N`. This is
   almost always an *underestimate* in real media data, because people who are
   reachable on one platform tend to be more reachable on others too — the
   underlying audience correlates, chance doesn't. Running the numbers in this
   dataset confirms exactly that pattern: the independence guess undershoots
   the true overlap by roughly 60–70% on average.

3. **Adjusted estimate — the independence guess scaled by a duplication
   multiplier**, standing in for a rate you'd normally source externally (for
   example, from a syndicated cross-platform panel like Comscore or Nielsen
   ONE, which does track overlapping reach for the general population, just
   not for your specific audience). The multiplier is a slider in the
   interface, defaulting to 1.5× — a plausible, defensible starting point, but
   explicitly an assumption, not something derived from a matched sample. The
   adjusted number is always clipped back inside the hard bounds from step 1,
   so it can never claim something the set logic rules out.

*Why this shape instead of a confidence interval on a single number:* each
layer is honest about what kind of claim it's making. The bounds are a fact.
The independence guess is a named, checkable assumption. The adjusted estimate
is a second named assumption layered on top. Nothing in the chain pretends to
be a measurement, which matters a great deal when the underlying constraint is
"we structurally cannot measure this."

**The validation step (only possible because this is a demo):** because the
underlying true population was kept as an answer key from Step 0, the actual
true overlap can be computed directly and shown alongside the modelled range —
explicitly framed as something no real deployment could ever check. It exists
here so you can see, before trusting this approach on real platform data,
roughly how far a plausible duplication multiplier can land from the truth —
and, just as importantly, that even a well-chosen multiplier only *narrows* the
uncertainty inherited from the hard bounds, it doesn't remove it.

---

## 5. Category Affinity

**The question:** "Which audiences over-buy energy drinks, and what else ends
up in the basket with them?"

**Coverage comes first, deliberately.** Only Amazon Marketing Cloud (retail
purchases) and Instacart Data Hub (grocery baskets) observe actual purchases.
Google Ads Data Hub sees content consumption; Meta Advanced Analytics sees
declared interest. Neither can contribute a purchase-based read, and the demo
says so as the first thing on the screen rather than quietly computing an
average over four sources when only two of them are real.

**Two separate computations, kept apart on purpose:**

1. **Segment-level penetration.** For each canonical audience, on each of the
   two purchase-capable platforms: what share of that audience bought energy
   drinks, indexed against that platform's own overall buyer rate (100 =
   typical). The two platforms' indices are shown *side by side*, never
   averaged into one number, because Amazon and Instacart see different
   shopping trips — Amazon skews toward stockable multipacks, Instacart toward
   single-serve top-ups — and averaging them would hide exactly the
   disagreement worth surfacing. An **agreement check** classifies every
   audience as agree / partial / conflict / single-source, using the same
   over-index (120) and under-index (85) bands as the rest of the demo:
   "conflict" means one platform has the audience over-indexing while the
   other has it under-indexing on the *same* audience, which the synthetic
   generator produces on purpose for Health-Conscious Shopper (Section 0
   below) so the demo has a real example rather than a hypothetical one.

2. **Basket co-purchase lift**, Instacart only, since it is the only source
   that sees more than one item per shopping trip. Lift is P(category |
   energy drink in basket) ÷ P(category), with a 95% confidence interval
   computed the same way a relative-risk interval would be (the delta method
   on log lift). Bars whose interval crosses 1.0 are shown greyed out — a lift
   of 1.03 on a big category is statistical noise, not a halo effect, and
   presenting it with the same visual weight as a real lift would overstate
   it.

**Suppression is reported at the raw-cell level**, not the canonical rollup,
because a clean room suppresses what it was actually queried on. A segment ×
category cell is a slice of a slice of the population, so the minimum-users
floor removes more here than on any other screen in the demo — worth knowing
before trusting a comparison cut any finer than what's already shown.

---

## 6. Target Groups & Next Best Action

**The question:** "Given everything the other screens found, what should we
actually do?" This is the synthesis screen — it doesn't generate new
measurements, it combines four existing ones (segment naming from Analysis 1,
performance from Analysis 2, behavioural consistency from Analysis 3, category
affinity from Analysis 5) into one ranked action per canonical audience.

**Cross-Platform Overlap (Analysis 4) is deliberately excluded.** It's the one
analysis built on a modelled, assumption-driven estimate rather than a directly
disclosed number, and a priority score is exactly the wrong place to launder
that uncertainty into something that looks like a fact. Where a real overlap
number would have argued "add this platform," this screen uses a fact instead:
whether a canonical audience has *any* raw segment mapped to it on a given
platform at all — a lookup against the bridge map, not an estimate.

**Five actions, each triggered by a specific evidence pattern:**

| Action | Trigger |
|---|---|
| **Expand** | Strong performance AND strong category affinity, confirmed by more than one source, AND missing from at least one platform |
| **Test** | Strong category affinity, but only one source confirms it |
| **Split** | The canonical audience's raw segments land in more than one behaviour group (Analysis 3), *and* the minority group holds at least 30% of the members — a single outlier is not a split |
| **Investigate** | Strong category affinity but weak media performance — a delivery problem, not a targeting problem |
| **Consolidate** | The canonical audience is bought under enough different raw names to clear a redundancy threshold — independent of whichever other action applies, since fragmented naming is a spend problem regardless of whether the audience itself is worth expanding |

**Confidence is the *weakest* of three checks, not their average:**
comparability grade (from Analysis 2), behavioural stability (the bootstrap
co-assignment rate from Analysis 3), and category-source agreement (from
Analysis 5). This is fixed on purpose, not a slider — a weighted average would
let one strong signal paper over one genuinely weak one, which is the specific
overstatement the whole demo is built to avoid. Where an axis has no data
(e.g., an audience never appeared in the category-affinity spotlight), it
scores 0.5 — below neutral, since silence isn't confirmation.

**Priority = Confidence × Opportunity size**, scaled to 0–100. Opportunity
size is defined differently per action (how far an index clears its
threshold for Expand/Test, the effect size of the behavioural split for
Split, dollars at stake for Consolidate) and always normalized to 0–1 before
multiplying, so the two ingredients stay legible on their own rather than
producing one opaque number. Dollars-at-stake is shown as its own column
rather than folded into the score, so a planner can sort by trustworthiness or
by dollar exposure without the two being tangled together.

**Every threshold is a business judgment call, exposed as an adjustable
control** rather than a constant buried in the code: the over/under-index
bands, the minimum confidence and minimum stability required to rank a group
at all, the naming-redundancy count, and the gap size that triggers
Investigate. Each carries the same explanation in its UI tooltip and in
`backend/targeting.py`'s `DEFAULT_THRESHOLDS` dict, so the two never drift
apart.

**Two of the six thresholds are scoped rather than universal.** The minimum
behavioural-stability bar only gates Expand and Investigate — both treat the
canonical audience as one coherent group, so both need that trust. Test is
exempt on purpose: it *is* the "we're not fully sure yet, validate before
committing" action, so requiring high confidence from it before it can fire
would rule out the only cases it exists to catch. Split is exempt from the
opposite direction: low stability paired with a material two-way split is
exactly the evidence Split is looking for, so a high stability bar would
disqualify the only cases that should trigger it.

**Groups that fail the universal gates — confidence below the minimum, or the
two category-affinity sources actively disagree — go to Needs Review with the
specific reason shown, rather than being silently dropped or given a
best-guess action.**

---

## Summary table

| Analysis | Core technique | Why this technique |
|---|---|---|
| Segment Inventory Audit | Grouping by existing canonical mapping, spend roll-up | No modeling needed — the value is entirely in the grouping and is instantly explainable |
| Performance Index Heatmap | Ratio indexing against platform baseline; rule-based provenance grading | Removes platform-level measurement differences without requiring a model; grading stays human-auditable |
| Behavioural Segment Matching | Named-feature fingerprint + Ward hierarchical clustering, explained via Cohen's d profiles, a surrogate decision rule, a platform-confounding check, and a bootstrap stability test | Deterministic and fully traceable back to metrics a media team already understands; every grouping arrives with a reason, a checkable rule, and its own reliability |
| Modelled Cross-Platform Overlap | Set-theoretic bounds + independence baseline + adjustable duplication multiplier | Uses only aggregate, platform-disclosed numbers — no individual-level identifiers required anywhere; every layer is an explicit, checkable assumption |
| Category Affinity | Platform-baseline indexing (two purchase-capable sources only) + basket co-purchase lift with confidence intervals | Coverage honesty — two of four platforms simply cannot answer a purchase question, and pretending otherwise would invent a signal |
| Target Groups & Next Best Action | Rule-based action assignment across four other analyses' outputs, weakest-link confidence, adjustable business thresholds | A prioritization score has to be conservative by construction — averaging away a weak signal, or laundering a modelled estimate into it, would make the ranking look more certain than the evidence supports |

## What was simplified for the demo, and why

Every method above is a deliberately smaller version of what the full build plan
describes, chosen to keep the demo fast to generate, easy to explain, and
runnable on a laptop with no setup:

- The matching in Analysis 3 (and behind Analysis 1's grouping) skips the
  lexical-score → semantic-embedding → LLM-adjudication → human-review pipeline
  described in Section 7 of the plan, and instead starts from a mostly-correct
  match with a small, deliberately injected error rate — enough to demonstrate
  what a mismatch looks like without needing a real matching pipeline.
- The overlap model in Analysis 4 uses a simple independence-baseline-plus-
  multiplier rather than the **iterative proportional fitting** or
  **hierarchical partial pooling across many platform-pairs** approaches the
  plan recommends for production use (Sections 8.4 and 10) — those let the
  duplication rate itself be learned from whatever real cross-platform
  benchmarks you do have access to (a syndicated panel, a handful of
  same-room aggregate overlaps), rather than set once by hand as a slider.
- Suppression, geography, time windows, and attribution-window mismatches — all
  real complications covered at length in the plan — are simplified or omitted
  here so the four analyses stay easy to follow end to end.

None of these simplifications change the underlying logic; they trade some
statistical sophistication for speed and clarity, which is the right trade for a
first working demo. The README's closing note and the plan's own Section 13
timeline describe what upgrading each of these pieces to production strength
would involve.
