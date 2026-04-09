"""
app.py - Flask application factory
"""
import logging
from flask import Flask, jsonify
from flask_cors import CORS
from config import Config
from services.session import start_cleanup_thread
from routes.webhook import webhook_bp
from routes.api import api_bp

logging.basicConfig(
    level=logging.DEBUG if Config.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = Config.SECRET_KEY

    # CORS for React dashboard
    CORS(app, resources={
        r"/api/*": {"origins": "*"},
    })

    # Register blueprints
    app.register_blueprint(webhook_bp)
    app.register_blueprint(api_bp)

    # Health check
    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "WhatsApp Khata"})

    # Global error handlers
    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(500)
    def server_error(e):
        logger.exception(e)
        return jsonify({"error": "Internal server error"}), 500

    # Start session GC
    start_cleanup_thread()
    logger.info("✅ WhatsApp Khata backend started")
    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=Config.DEBUG)
