from flask import Flask, jsonify
from dotenv import load_dotenv
import os
import logging
import sys

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "success",
        "message": "FC Support API 서버가 실행 중입니다."
    })

@app.route("/healthz")
def health_check():
    return jsonify({
        "status": "healthy"
    })

if __name__ == "__main__":
    logger.info("Starting Flask application...")
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000))) 