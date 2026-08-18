#!/usr/bin/env python3
from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path


# Must match the --database-url path in services/comfy/entrypoint.sh.
database_path = Path("/data/user/comfyui.db")
if not database_path.is_file() or not os.access(database_path, os.W_OK):
    raise SystemExit(1)

try:
    # Port 8188 must match the --port in services/comfy/entrypoint.sh and the
    # container port in compose.yaml.
    with urllib.request.urlopen("http://127.0.0.1:8188/", timeout=3) as response:
        if 200 <= response.status < 400:
            raise SystemExit(0)
except (OSError, urllib.error.URLError):
    pass

raise SystemExit(1)
