from flask import Flask, send_from_directory

app = Flask(__name__, static_folder='static', static_url_path='')

@app.route('/')
def serve():
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static/static', path)

@app.route('/<path:path>')
def serve_root(path):
    if path.startswith('static/'):
        return send_from_directory('static', path)
    return send_from_directory('static', 'index.html')

@app.route('/healthz')
def health_check():
    return 'OK', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000) 