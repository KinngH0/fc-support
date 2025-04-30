import os

port = os.getenv('PORT', '10000')
bind = f"0.0.0.0:{port}"
workers = 4
timeout = 120
keepalive = 120
worker_class = "sync"
accesslog = "-"
errorlog = "-" 