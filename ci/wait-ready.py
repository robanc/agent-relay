"""Bounded readiness wait for the disposable CI API."""
import time
from urllib.error import URLError
from urllib.request import urlopen

for _ in range(60):
    try:
        with urlopen("http://127.0.0.1:18000/ready", timeout=1) as response:
            if response.status == 200:
                break
    except (URLError, TimeoutError):
        pass
    time.sleep(1)
else:
    raise SystemExit("Disposable PostgreSQL API did not become ready")
