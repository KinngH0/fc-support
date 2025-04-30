import os
from flask import Flask, render_template
from app.routes.api import api_bp

app = Flask(__name__, 
    template_folder='templates',
    static_folder='static'
)
app.register_blueprint(api_bp)

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port) 