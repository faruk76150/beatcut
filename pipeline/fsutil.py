"""Small file-system helpers."""
from __future__ import annotations

import os
import time


def replace_with_retry(src, dst, tries=10, delay=0.1):
    """os.replace that waits and retries on PermissionError (another process may hold the file
    open for a moment, as antivirus scanners and readers do on Windows). The last failure is raised."""
    for attempt in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(delay)
