"""Shared test setup.

agent.py reads its settings when it is imported, so fake values are set here,
before any test imports it. No real bucket, credentials, or API key are used.
"""

import os
import tempfile

os.environ.setdefault("HEALTHY_HINTS_BUCKET", "test-bucket")
os.environ.setdefault("HEALTHY_HINTS_UPLOAD_DIR", tempfile.mkdtemp())
os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-real")
