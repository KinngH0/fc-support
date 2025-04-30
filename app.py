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

# 환경 변수 로드
load_dotenv()

app = Flask(__name__)

# API 키 확인
FC_API_KEY = os.getenv('FC_API_KEY')
if not FC_API_KEY:
    logger.error("FC_API_KEY 환경 변수가 설정되지 않았습니다.")
    sys.exit(1)

@app.route("/")
def home():
    return jsonify({
        "status": "success",
        "message": "FC Support API 서버가 실행 중입니다."
    })

@app.route("/healthz")
def health_check():
    try:
        return jsonify({
            "status": "healthy",
            "api_key_configured": bool(FC_API_KEY)
        })
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == "__main__":
    logger.info("Starting Flask application...")
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000))) 