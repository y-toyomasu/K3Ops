"""Flask entry point for the K3Ops read-only dashboard."""

from __future__ import annotations

import os

from flask import Flask, jsonify, render_template

from .data import build_dashboard


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config.from_mapping(
        ROADMAP_PATH=os.environ.get(
            "K3OPS_ROADMAP_PATH", "/data/roadmap/development-operations.toml"
        ),
        TASKS_DIRECTORY=os.environ.get("K3OPS_TASKS_DIR", "/data/tasks"),
    )
    if config:
        app.config.update(config)

    @app.after_request
    def secure_read_only_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def dashboard_data() -> dict:
        return build_dashboard(app.config["ROADMAP_PATH"], app.config["TASKS_DIRECTORY"])

    @app.get("/")
    def index():
        return render_template("index.html", dashboard=dashboard_data())

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @app.get("/api/dashboard")
    def api_dashboard():
        return jsonify(dashboard_data()), 200

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8890, debug=False)
