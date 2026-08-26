"""Contract tests against StudyLife's committed OpenAPI spec (`docs/api/openapi.json` in the
main `studylife` repo) - audit finding D2/D3, the same pattern already landed in
studylife-ai's tests/contract/test_openapi_contract.py (read-only reference for this module).
custom_components/studylife/api.py hardcodes every endpoint path/method it calls with zero
drift detection against the server; this module asserts each one still exists (method + path)
in the spec.

Spec source (`STUDYLIFE_OPENAPI_SPEC`): a local file path or an http(s) URL. Defaults to the
studylife repo's own raw GitHub URL - same file the "openapi-contract" CI job in studylife
itself keeps fresh against the built API.

Reachability: in CI, an unreachable/unparsable spec FAILS this module outright - skipping
would defeat the entire point of a contract test. Locally, only the fully-default case (no
env var set, AND the default URL happens to be unreachable - e.g. no internet) is treated as
"offline dev" and skipped with a clear message; an explicitly configured
STUDYLIFE_OPENAPI_SPEC that turns out to be unreachable is still a real error and fails,
since the developer pointed at it on purpose.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ._network_fetch import allow_network_for

DEFAULT_SPEC_URL = "https://raw.githubusercontent.com/lukislp/studylife/main/docs/api/openapi.json"
SPEC_HOST = "raw.githubusercontent.com"

# Every StudyLife endpoint custom_components/studylife/api.py calls, read exhaustively from
# that file's `self._get(...)`/`self._request(...)` call sites (its only HTTP call sites -
# services.py/config_flow.py/coordinator.py all go through this same client, never directly).
CALLED_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/api/sessions"),  # async_get_sessions()
    ("POST", "/api/sessions"),  # async_create_session()
    ("GET", "/api/sessions/history"),  # async_get_session_history()
    ("PUT", "/api/sessions/{id}"),  # async_update_session()
    ("DELETE", "/api/sessions/{id}"),  # async_delete_session()
    ("GET", "/api/settings"),  # async_get_settings() / async_test_connection()
    ("PUT", "/api/settings"),  # async_update_settings()
    ("GET", "/api/notes"),  # async_get_notes()
    ("GET", "/api/coursegoals"),  # async_get_course_goals()
    ("PUT", "/api/coursegoals/{courseId}"),  # async_set_course_goal()
    ("GET", "/api/courses"),  # async_get_courses()
    ("GET", "/api/studyprograms"),  # async_get_study_programs()
    # NOTE: /api/studyprograms/{id} (StudyProgramDetailDto.GroupEctsQuotas) is NO LONGER
    # called - it existed only to feed _calc_ects_progress's group-quota fallback, which is
    # gone now that GET /api/metrics/summary returns ectsEarned/ectsTotal pre-computed (the
    # owner decision behind this whole change: every metric lives in exactly ONE place,
    # StudyLife). Pruned from this inventory rather than left stale - see coordinator.py's
    # module docstring for the removal.
    ("GET", "/api/timerstate"),  # async_get_timer_state()
    ("GET", "/api/metrics/summary"),  # async_get_metrics_summary()
    ("GET", "/api/metrics/achievements"),  # async_get_metrics_achievements()
    ("POST", "/api/planner/exam-plan"),  # async_generate_exam_plan()
]


def _is_ci() -> bool:
    return os.environ.get("CI", "").strip().lower() == "true"


def _fetch_spec(source: str) -> dict[str, Any]:
    if source.startswith(("http://", "https://")):
        allow_network_for(SPEC_HOST)
        with urllib.request.urlopen(source, timeout=15.0) as response:
            data: dict[str, Any] = json.loads(response.read().decode("utf-8"))
            return data
    parsed: dict[str, Any] = json.loads(Path(source).read_text(encoding="utf-8"))
    return parsed


@pytest.fixture
def spec() -> dict[str, Any]:
    """Function-scoped (not session-scoped): `allow_network_for` (see _network_fetch.py) must
    run from inside a fixture/test body, after pytest-homeassistant-custom-component's own
    pytest_runtest_setup hook has already re-clamped sockets to 127.0.0.1-only for this test -
    a session-scoped fixture's body only runs once, before that per-test hook has necessarily
    fired for every subsequent test. _fetch_spec is process-wide lru_cache'd, so only the
    FIRST test that actually needs the spec hits the network - every other one is a cache hit."""
    configured = os.environ.get("STUDYLIFE_OPENAPI_SPEC")
    explicit = configured is not None
    source = configured if explicit else DEFAULT_SPEC_URL
    try:
        return _fetch_spec(source)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if _is_ci():
            pytest.fail(
                f"Could not load the StudyLife OpenAPI spec from {source!r} in CI - contract "
                f"tests MUST fail here, not skip (that's the point of the check): {exc!r}"
            )
        if explicit:
            pytest.fail(
                f"STUDYLIFE_OPENAPI_SPEC={source!r} was set explicitly but could not be "
                f"loaded - this is a real configuration error, not the offline-dev fallback "
                f"below: {exc!r}"
            )
        pytest.skip(
            f"Skipping contract tests: STUDYLIFE_OPENAPI_SPEC is unset and the default spec "
            f"URL ({source!r}) is unreachable - assuming offline local dev. Set "
            f"STUDYLIFE_OPENAPI_SPEC to a local docs/api/openapi.json path to run these tests "
            f"offline. Original error: {exc!r}"
        )


def _path_regex(template: str) -> re.Pattern[str]:
    """Turns an OpenAPI path template like `/api/sessions/{id}` into a regex matching any
    concrete path with that shape, e.g. `/api/sessions/42`. `{param}` segments never contain
    `/`, so `[^/]+` is an exact match for one path segment, not just an approximation."""
    marker = "\x00"
    placeholder_free = re.sub(r"\{[^/}]+\}", marker, template)
    pattern = re.escape(placeholder_free).replace(marker, "[^/]+")
    return re.compile(f"^{pattern}$")


def _find_matching_path_template(spec_paths: dict[str, Any], concrete_path: str) -> str | None:
    """An exact literal match always wins over a templated one (same static-beats-
    parameterized precedence ASP.NET Core's own router uses) - only falls back to templated
    matching once no literal path is present at all. api.py's own path strings are already
    templates here (e.g. "/api/sessions/{id}"), so an exact match is the common case."""
    if concrete_path in spec_paths:
        return concrete_path
    for template in spec_paths:
        if _path_regex(template).match(concrete_path):
            return template
    return None


@pytest.mark.parametrize(
    "method,path",
    CALLED_ENDPOINTS,
    ids=[f"{method}_{path}" for method, path in CALLED_ENDPOINTS],
)
def test_called_endpoint_exists_in_spec(spec: dict[str, Any], method: str, path: str) -> None:
    spec_paths = spec.get("paths", {})
    template = _find_matching_path_template(spec_paths, path)
    assert template is not None, (
        f"{method} {path} is called by custom_components/studylife/api.py but no matching "
        f"path exists in the StudyLife OpenAPI spec's `paths` - the endpoint was removed or "
        f"renamed server-side (or api.py has a typo). Spec paths: {sorted(spec_paths)}"
    )
    methods_at_path = {m.lower() for m in spec_paths[template]}
    assert method.lower() in methods_at_path, (
        f"{method} {path} matched spec path template {template!r}, but that path does not "
        f"support {method} in the spec - only {sorted(methods_at_path)} do. The endpoint's "
        f"HTTP method changed server-side."
    )
