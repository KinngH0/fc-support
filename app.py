from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return "Hello from Flask!"

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"}) 