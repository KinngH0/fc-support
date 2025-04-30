# FC Support

Flask application deployed on Render.com

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the application:
```bash
gunicorn app:app --bind 0.0.0.0:$PORT
```

## Development

To run in development mode:
```bash
flask run
``` 