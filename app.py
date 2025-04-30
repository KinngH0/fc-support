import os
from flask import Flask, jsonify
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def root():
    logger.info('Root endpoint accessed')
    return jsonify({"message": "Hello Render"})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    logger.info(f'Starting app on port {port}')
    app.run(host='0.0.0.0', port=port) 