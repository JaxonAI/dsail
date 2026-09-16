"""A running service for the live tests, found or started once per run.

``DSAIL_TEST_URL`` names a running service and wins. Otherwise, if
``dsail/agent_api:latest`` is present, one container is started on a free
loopback port for the whole test process and stopped at exit. Otherwise the
live tests skip, saying why.
"""

import atexit
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

_IMAGE = os.environ.get("AGENT_API_IMAGE", "dsail/agent_api:latest")
_state = {"url": None, "reason": None, "container": None, "resolved": False, "full": None}

# Every test in this process stores credentials under a private directory, so
# the auto-acquired evaluation credential never lands in the developer's own
# ~/.config/dsail. Set at import, before any Client exists.
os.environ["DSAIL_CONFIG_DIR"] = tempfile.mkdtemp(prefix="dsail-test-config-")
os.environ["DSAIL_CREDENTIAL"] = ""


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _healthy(url, attempts=40):
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url + "/health", timeout=2) as response:
                if json.loads(response.read().decode("utf-8")).get("ok"):
                    return True
        except Exception:  # noqa: BLE001 - polling a starting container
            pass
        time.sleep(0.5)
    return False


def _stop():
    if _state["container"]:
        subprocess.run(["docker", "rm", "-f", _state["container"]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def _start_container():
    if shutil.which("docker") is None:
        return None, "docker is not installed"
    probe = subprocess.run(["docker", "image", "inspect", _IMAGE],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if probe.returncode != 0:
        return None, "image %s is not present (make agent_api from the repo root)" % _IMAGE
    port = _free_port()
    name = "dsail-sdk-test-%d" % port
    # Credentials REQUIRED, as a real deployment runs: the suite then proves
    # the no-human-gate on-ramp (auto-issued evaluation credential) end to end,
    # and the storage tests run under an operator-issued full credential.
    run = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1:%d:8710" % port,
         "-e", "AGENT_API_CREDENTIALS=evaluation", _IMAGE],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True,
    )
    if run.returncode != 0:
        return None, "docker run failed: %s" % run.stderr.strip()
    _state["container"] = name
    atexit.register(_stop)
    url = "http://127.0.0.1:%d" % port
    if not _healthy(url):
        return None, "the container did not become healthy"
    return url, None


def live_url():
    """The URL of a running service, or ``None`` (``reason()`` says why)."""
    if _state["resolved"]:
        return _state["url"]
    _state["resolved"] = True
    explicit = os.environ.get("DSAIL_TEST_URL")
    if explicit:
        if _healthy(explicit.rstrip("/"), attempts=3):
            _state["url"] = explicit.rstrip("/")
        else:
            _state["reason"] = "DSAIL_TEST_URL=%s did not answer /health" % explicit
        return _state["url"]
    _state["url"], _state["reason"] = _start_container()
    return _state["url"]


def reason():
    return _state["reason"] or "no live service"


def full_credential():
    """A full-grade credential for the live service, or ``None``.

    Issued by the operator command inside the container this run started —
    the same path an operator uses on the hosted box. Against an external
    ``DSAIL_TEST_URL`` it comes from ``DSAIL_TEST_FULL_CREDENTIAL`` instead.
    """
    if _state["full"] is not None:
        return _state["full"] or None
    if os.environ.get("DSAIL_TEST_FULL_CREDENTIAL"):
        _state["full"] = os.environ["DSAIL_TEST_FULL_CREDENTIAL"]
        return _state["full"]
    if not _state["container"]:
        _state["full"] = ""
        return None
    issued = subprocess.run(
        ["docker", "exec", _state["container"], "python", "-m", "agent_api.entrypoint",
         "issue-credential", "--grade", "full", "--label", "sdk live suite"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True,
    )
    try:
        _state["full"] = json.loads(issued.stdout.strip().splitlines()[-1])["credential"]
    except (ValueError, IndexError, KeyError):
        _state["full"] = ""
    return _state["full"] or None


DEMO_SOURCE = """\
version 1.3;
// @ask amount What is the total amount of this expense claim, in USD?
// @unit amount USD
// @range amount 0..1000000
declare amount as numeric;
// @ask has_receipt Is an itemised receipt attached to the claim?
declare has_receipt as boolean;
// @ask category Which category does the claim fall under?
declare category as enum {"travel","meals","equipment","other"};

assert receipt_over_75 { Implies(amount > 75 "USD", has_receipt) };
assert within_cap { amount <= 5000 "USD" };
assert equipment_needs_receipt { Implies(category == "equipment", has_receipt) };
"""
