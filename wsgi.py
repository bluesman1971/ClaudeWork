"""
wsgi.py — WSGI entry point for production servers (Gunicorn, uWSGI, etc.)

Usage:
  gunicorn wsgi:application
  gunicorn --config gunicorn.conf.py wsgi:application

The app object is imported here so that:
  1. The module name is stable regardless of how the server is invoked.
  2. app.py can still be run directly during development (`python app.py`).
"""

from app import app as application  # noqa: F401  (Gunicorn looks for 'application')

if __name__ == '__main__':
    application.run()
