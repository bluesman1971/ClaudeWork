"""
wsgi.py — stub re-export for tooling compatibility.

Gunicorn UvicornWorker is now invoked directly via app:app in the Procfile.
This file is kept so any tool or script that imports wsgi still works.
"""

from app import app  # noqa: F401
