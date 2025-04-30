from flask import Flask, jsonify
from dotenv import load_dotenv
import os
import logging

# 환경 변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# API 키 확인
FC_API_KEY = os.getenv('FC_API_KEY')
if not FC_API_KEY:
    logger.error("FC_API_KEY 환경 변수가 설정되지 않았습니다.")
    raise ValueError("FC_API_KEY 환경 변수가 필요합니다.")

@app.route("/")
def home():
    return jsonify({
        "status": "success",
        "message": "FC Support API 서버가 실행 중입니다."
    })

@app.route("/healthz")
def health_check():
    return jsonify({
        "status": "healthy",
        "api_key_configured": bool(FC_API_KEY)
    })

if __name__ == "__main__":
    app.run(debug=True) 