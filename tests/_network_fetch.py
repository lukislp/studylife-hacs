"""Shared helper for the two tests that need one real outbound HTTPS fetch against studylife's
GitHub repo: test_openapi_contract.py and test_metrics_golden_fixtures.py.

pytest-homeassistant-custom-component disables real sockets for every test by default (its
plugins.py registers its OWN unconditional `pytest_runtest_setup` hook that always calls
`pytest_socket.socket_allow_hosts(["127.0.0.1"])` then `pytest_socket.disable_socket(...)`,
regardless of any `socket_enabled` fixture/marker - that hook doesn't check for either). The
standard pytest-socket `socket_enabled` fixture only undoes HALF of that
(`pytest_socket.enable_socket()`, which stops `socket.socket()` construction from raising) -
the separate "block connect() to any host but 127.0.0.1" half stays in effect, because its
`guarded_connect` wrapper is attached directly to the socket class and isn't touched by
`enable_socket()`. Confirmed empirically: requesting `socket_enabled` alone still fails with
`SocketConnectBlockedError` here, not the `SocketBlockedError` an un-mitigated call raises.

`allow_network_for` (both public pytest_socket calls, no private API) must be called from
INSIDE a fixture or test body - i.e. after `pytest_runtest_setup` has already fired for this
test, so its adjustment is the last word instead of being immediately re-clamped.
"""

from __future__ import annotations

import socket

import pytest_socket

# pytest-homeassistant-custom-component >= 0.13.36x additionally replaces socket.getaddrinfo /
# gethostbyname with wrappers that refuse every non-IP hostname ("DNS resolution disabled in
# tests"). That happens inside its per-test setup hook, so the originals captured here at
# import (collection) time are the real ones.
_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_GETHOSTBYNAME = socket.gethostbyname


def allow_network_for(host: str) -> None:
    """Re-enables real sockets and DNS for this test and narrows the allow-list to exactly
    `host` (not a blanket allow-everything) - `host` is a bare hostname, e.g.
    "raw.githubusercontent.com". The plugin re-applies its blocks at the next test's setup."""
    # DNS first: socket_allow_hosts itself resolves `host` through socket.getaddrinfo.
    socket.getaddrinfo = _REAL_GETADDRINFO
    socket.gethostbyname = _REAL_GETHOSTBYNAME
    pytest_socket.enable_socket()
    pytest_socket.socket_allow_hosts([host], allow_unix_socket=True)
