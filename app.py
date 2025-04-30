from flask import Flask, jsonify, render_template, send_from_directory
import logging
import sys
import os

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # 한글 인코딩을 위한 설정

@app.route("/")
def home():
    return render_template('index.html')

@app.route("/search/pick-rate")
def pick_rate_search():
    return render_template('search/pick_rate.html')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static', 'images'),
                             'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route("/healthz")
def health_check():
    return jsonify({
        "status": "healthy"
    })

if __name__ == "__main__":
    logger.info("Starting Flask application...")
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000))) 