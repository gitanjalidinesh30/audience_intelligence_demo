"""
The web server for the Audience Intelligence demo.

Local use: run this file, then open http://localhost:5050 in your browser.
Hosted use: this same file runs under gunicorn (see README's "Deploying
online" section) — the database auto-generation below runs at import time
specifically so it also fires under gunicorn, which imports this module
rather than executing it as a script.
"""

from flask import Flask, jsonify, send_from_directory, request
import os
import analysis

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")

# Generate the synthetic database on first run, wherever "first run" happens
# to be. This runs at import time (not inside `if __name__ == "__main__"`)
# so it also fires when a production server like gunicorn imports this file
# instead of executing it directly -- and it's what makes an ephemeral disk
# (the norm on most free hosting tiers) a non-issue: the data simply
# regenerates the first time the app boots on a fresh instance.
_db_path = os.path.join(os.path.dirname(__file__), "..", "data", "audience_intel.db")
if not os.path.exists(_db_path):
    print("No database found yet — generating synthetic data first (this takes ~10 seconds)...")
    import subprocess
    subprocess.run(
        ["python3", os.path.join(os.path.dirname(__file__), "..", "data", "generate_data.py")],
        check=True,
    )


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/api/inventory-audit")
def api_inventory_audit():
    return jsonify(analysis.segment_inventory_audit())


@app.route("/api/performance-heatmap")
def api_performance_heatmap():
    return jsonify(analysis.performance_heatmap())


@app.route("/api/behavioural-matching")
def api_behavioural_matching():
    return jsonify(analysis.behavioural_segment_matching())


@app.route("/api/category-affinity")
def api_category_affinity():
    anchor = request.args.get("anchor", default="ENERGY_DRINKS", type=str)
    return jsonify(analysis.category_affinity(anchor=anchor))


@app.route("/api/cross-platform-overlap")
def api_cross_platform_overlap():
    multiplier = request.args.get("multiplier", default=1.5, type=float)
    return jsonify(analysis.modelled_cross_platform_overlap(duplication_multiplier=multiplier))


@app.route("/api/target-groups")
def api_target_groups():
    keys = ["over_index_at", "under_index_at", "min_confidence_to_rank",
            "min_stability_to_rank", "redundancy_at", "investigate_gap_at"]
    overrides = {}
    for k in keys:
        v = request.args.get(k, type=float)
        if v is not None:
            overrides[k] = v
    return jsonify(analysis.target_groups(thresholds=overrides))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"\nReady! Open this link in your browser:  http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
