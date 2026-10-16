"""Test-owned machine identity; production has no hostname/environment override."""

from pathlib import Path
import sys


HELPER = Path(__file__).resolve().parents[1] / "pi-client-link-helper"
LAPTOP_HOST = "conley-Y9000P"
COMPANY_HOST = "conley-company"
HOST_ROLES = {
    LAPTOP_HOST: {"alias": "conley-company", "host": "198.51.100.23", "user": "conley", "port": 22},
    COMPANY_HOST: {"alias": "conley-Y9000p", "host": "203.0.113.64", "user": "laptop-user", "port": 2202},
}


def helper_command(hostname=LAPTOP_HOST, *, browser_grace=None):
    # Patch the actual OS API in a test-only process before executing the exact
    # production source; do not add a permissive production test switch.
    wrapper = ("import runpy,socket,sys; "
               f"socket.gethostname=lambda: {hostname!r}; "
               f"module=runpy.run_path({str(HELPER)!r}); ")
    if browser_grace is not None:
        wrapper += ("module['launch_browser'].__globals__"
                    f"['BROWSER_LAUNCH_GRACE_SECONDS']={browser_grace!r}; ")
    return [sys.executable, "-c", wrapper + "sys.exit(module['main']())"]
