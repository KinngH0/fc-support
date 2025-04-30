import os
from flask import Flask, jsonify
import logging

# 로깅 설정
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route("/")
def home():
    logger.info("Home endpoint accessed")
    return "Hello, Render!"

@app.route("/health")
def health():
    logger.info("Health check endpoint accessed")
    return jsonify({"status": "healthy"})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    logger.info(f"Starting app on port {port}")
    app.run(host="0.0.0.0", port=port) 