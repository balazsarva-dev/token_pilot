from pathlib import Path

from flask import Flask, abort, jsonify, send_from_directory
from sqlalchemy import Engine, text
from werkzeug.exceptions import HTTPException

from tokenpilot.config import Settings, load_settings
from tokenpilot.db import make_engine, make_session_factory
from tokenpilot.web.spend import bp as spend_bp

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# The built audit page (make web). Served by Flask so one command runs everything locally.
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or load_settings()
    engine = make_engine(settings.database_url)

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.extensions["tokenpilot.settings"] = settings
    app.extensions["tokenpilot.engine"] = engine
    app.extensions["tokenpilot.sessions"] = make_session_factory(engine)
    app.register_blueprint(spend_bp)

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        # Same envelope as the API errors, also for 404 / 405 / 413 from Flask itself.
        body = {"error": {"type": exc.name.lower().replace(" ", "_"), "message": exc.description}}
        return jsonify(body), exc.code

    @app.get("/health")
    def health():
        db_ok = _database_reachable(engine)
        body = {"status": "ok" if db_ok else "degraded", "database": db_ok}
        return jsonify(body), 200 if db_ok else 503

    @app.get("/")
    @app.get("/<path:path>")
    def frontend(path: str = "index.html"):
        if path.startswith("api/"):
            abort(404)  # unknown API route: JSON error, not the page
        if not FRONTEND_DIST.is_dir():
            return "Audit page not built yet: run `make web`, then reload.", 404
        if not (FRONTEND_DIST / path).is_file():
            path = "index.html"
        return send_from_directory(FRONTEND_DIST, path)

    return app


def _database_reachable(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
