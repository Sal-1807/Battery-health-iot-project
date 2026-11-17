"""ASGI entry point.

Run from the repository root:

    uvicorn iot.backend.main:app --host 0.0.0.0 --port 8000

``--host 0.0.0.0`` is needed for an ESP32 on the LAN to reach it.
Interactive docs: http://localhost:8000/docs
"""

import logging

from .api import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = create_app()
