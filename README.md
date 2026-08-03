# StudyLife — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

Home Assistant custom integration for [StudyLife](https://github.com/lukislp/studylife), a
self-hosted study organizer (Blazor WASM + ASP.NET Core). Polls the existing REST API (`/api/sessions`, `/api/settings`, `/api/notes`, `/api/coursegoals`, `/api/courses`, `/api/timerstate`, `/api/studyprograms`) and exposes the same metrics as the dashboard and analytics view as entities — including a real course catalog and the live timer phase. Six services also let you create, edit, and delete sessions and course goals, as well as switch the active study programme, directly from Home Assistant — otherwise the integration doesn't change anything in the StudyLife app.

**Multiple study programmes — one device per programme:** Every study programme (the built-in one plus every custom one you've created, completed or not) gets its **own device** in Home Assistant, `StudyLife — <name>`, with a full set of progress sensors — **all visible at the same time**, regardless of which programme is currently active in the app. On top of that there's a "hub" device `StudyLife` for everything app-global (sessions, timer, notes, calendar, course picker, cross-programme study habit). See [Entities](#entities) for details.

- **New study programme** (created in the web app): the corresponding device and its entities appear automatically on the next poll cycle — no restart, no re-setup needed.
- **Deleted study programme:** its entities are **not** automatically removed from the registry — they just go `unavailable`. The orphaned device can then be deleted manually from the HA UI (Settings → Devices & Services → device → Delete). This is deliberate: no automatic cleanup of registry entries that could accidentally break history/automations.
- The data for this comes from one catalog fetch per study programme (`GET /api/courses?program={id}`, `0` = built-in catalog); sessions (`/api/sessions/history`) and course goals (`/api/coursegoals`) are still fetched **once**, globally, and partitioned per programme on the client side — possible because course IDs are globally unique across all study programmes.

## Installation

1. Copy the `custom_components/studylife` folder into the `config/custom_components/` directory of your Home Assistant instance (end result: `config/custom_components/studylife/manifest.json`).
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → "StudyLife"** and enter the base URL of the StudyLife server (e.g. `http://studylife.local:8080`). You'll also need to enter the API key once: generate a key on the **StudyLife app's setup page** (the "Home Assistant" card) and copy the value shown — it's displayed only once. The key is long-lived — it never rotates and never expires, so this really is a one-time step (see [API key](#api-key) below).

The poll interval (default 30s, same as the client's `AppStateService`) can be adjusted via **Configure** on the integration tile. On endpoints with server-side caching (`/api/sessions`, `/api/sessions/history`, `/api/settings`, `/api/courses`), the integration polls using conditional GET (`If-None-Match` against the server's ETags) — unchanged data is answered with an empty `304 Not Modified` instead of being retransmitted in full every time.

## Entities

Two kinds of devices:

- **Hub device `StudyLife`** (one per configured server): all app-global entities from the table below. Metrics tied to sessions (hours/streak/quotas) count here across **all** study programmes — this is the personal study habit, regardless of what was studied.
- **One device `StudyLife — <programme name>` per study programme** (via-device on the hub): the per-programme table further below. Entity IDs are derived from the device name, e.g. `sensor.studylife_applied_artificial_intelligence_ects_fortschritt`.

### Hub device `StudyLife`

| Entity | Type | Meaning |
|---|---|---|
| `sensor.studylife_active_session` | Sensor | Course name of the currently running session (`none` if none), attributes: topic, start/end, color |
| `sensor.studylife_next_session` | Sensor | Course name of the next scheduled session, attributes: topic, start/end, color |
| `sensor.studylife_next_session_start` | Sensor (Timestamp) | Exact start time of the next session (empty if none scheduled) |
| `sensor.studylife_next_session_end` | Sensor (Timestamp) | Exact end time of the next session |
| `sensor.studylife_active_session_end` | Sensor (Timestamp) | Exact end time of the currently running session |
| `sensor.studylife_today_sessions` | Sensor | Number of today's sessions, attribute `sessions` with a detailed list |
| `sensor.studylife_week_sessions` | Sensor | Number of sessions in the current week (Mon–Sun) |
| `sensor.studylife_week_hours` | Sensor | Hours in the current week, attributes: `previous_week_hours`, `delta_hours` and `up` (previous week/difference/trend direction, same metric as the "This week" dashboard tile) |
| `sensor.studylife_streak` | Sensor | Day streak of completed sessions (identical logic to `Index.razor`, based on `/api/sessions/history` rather than the too-narrow `/api/sessions` window) |
| `sensor.studylife_week_quota` | Sensor | Weekly quota in % (target 25–30h), attributes: hours, target, warning, hours missing |
| `sensor.studylife_month_quota` | Sensor | Monthly quota in %, target grows dynamically with the weeks of the month |
| `sensor.studylife_course_hours` | Sensor | Number of courses with logged study time (from completed sessions), attributes: `total_hours`, `total_sessions`, `courses` (hours/sessions per course — mirrors the "Analytics" page) |
| `sensor.studylife_motivational_style` | Sensor | Selected motivational style, attributes: theme, auto-switch settings, selected/completed course IDs **and** names (`selected_courses`/`completed_courses`, resolved via the **active** study programme's catalog, each including the optional `tag` per course — the priority short label set in Setup.razor, e.g. "Exam soon") |
| `sensor.studylife_timer_phase` | Sensor | Current focus timer phase: `focus`, `break`, or `idle`, attributes: `session_id`, `timer_mode_id`, `current_round` |
| `sensor.studylife_timer_phase_ends` | Sensor (Timestamp) | Time at which the current focus/break phase ends (empty when `idle`) |
| `sensor.studylife_notes` | Sensor | Number of notes, attributes of the most recently edited note (`latest_title`, `latest_updated_at`, `latest_course_id`, `latest_excerpt` — first 120 characters of the content —, `latest_session_id` — set if the note originated from the focus timer's post-session reflection prompt, otherwise empty) |
| `sensor.studylife_neglected_course` | Sensor | Name of the active course that hasn't been studied for the longest time (`none` if fewer than 2 active courses), attributes: `course_id`, `last_studied` (empty if never studied in the last 180 days), `days_since` |
| `sensor.studylife_achievements_unlocked` | Sensor | Number of unlocked achievement badges (mirrors the full dashboard Achievements page: 13 categories, 44 badges total — hours, streak, sessions, courses completed, all courses completed, early bird, night owl, weekend warrior, marathon session, perfect weeks, notes written, course diversity, study programmes completed). Attributes: `total` (44), `unlocked` (list of already-unlocked badges: icon/name/status/current value/threshold/category per badge), `locked` (badges not yet unlocked, sorted by `current/threshold` descending — the first element is the one closest to being achieved), `next_up` (this first `locked` element directly, or empty if everything is unlocked) |
| `sensor.studylife_longest_streak` | Sensor | Longest streak of consecutive study days ever reached (persists even if the current streak breaks — unlike `sensor.studylife_streak`) |
| `sensor.studylife_topics_progress` | Sensor | Number of checked-off course topics across all courses, attributes: `topics_total`, `percent`, `courses` (breakdown per course with at least one checked-off topic) |
| `sensor.studylife_month_comparison` | Sensor | Hours in the current calendar month, attributes: `delta_vs_last_month_hours` (difference vs. the previous month) and `delta_vs_last_year_hours` (difference vs. the same month last year — only present if the session history actually reaches back before the entire same month of the previous year, otherwise a misleading "0h" would be shown). Uses the same ~400-day history as `sensor.studylife_streak`/`sensor.studylife_week_hours` (no extra request) — this window always mathematically reaches back to the same month of the previous year |
| `sensor.studylife_weekly_report` | Sensor | ISO week ID of the most recently **completed** Mon–Sun week (e.g. `2026-W28`) — flips to the just-ended week on Monday at the first poll. Attributes: `hours` (hours that week), `delta_vs_previous_week_hours` (difference vs. the week before), `top_course` (course with the most hours, empty if 0 sessions), `sessions_count`. HA counterpart to the server's Sunday 6pm weekly-recap push, which automations otherwise couldn't react to — automations trigger on the state change or on the bus event `studylife_weekly_report` (see below) |
| `sensor.studylife_active_program` | Sensor | Name of the study programme the **app itself** currently treats as active (`/api/studyprograms` + `/api/settings`) — visibility in HA no longer depends on this (every study programme has its own device), only the course picker, course goal calendar, and the hub's resolved course lists follow it. Attributes: `program_id` (empty for the built-in study programme), `is_built_in`, `programs_count`, `completed_programs_count`, and `programs` with the full list of all study programmes (`id`, `name`, `is_built_in`, `is_completed`) |
| `binary_sensor.studylife_studying_now` | Binary Sensor | On while an active session is running (time-window based, independent of the timer) |
| `binary_sensor.studylife_timer_running` | Binary Sensor | On while the focus timer is actually running (not paused/stopped) |
| `binary_sensor.studylife_week_quota_warning` | Binary Sensor | On when the weekly goal is not met |
| `binary_sensor.studylife_month_quota_warning` | Binary Sensor | On when the monthly goal is not met |
| `binary_sensor.studylife_inactivity_warning` | Binary Sensor | On if more days have passed since the last session than configured in setup (default 5), or if no session has ever taken place — mirrors `InactivityReminderService`. Attributes: `days_since_last_session`, `threshold_days` |
| `calendar.studylife_sessions` | Calendar | All sessions in the time window returned by the server (7 days back, 90 days ahead) as calendar entries |
| `calendar.studylife_course_goals` | Calendar | All **open** course goals of the **active** study programme (target date set, not yet completed) as **all-day** entries on the respective target date — title = course name, description = the course's priority tag (e.g. "Exam soon"), if set. Unlike the "Upcoming course goals" dashboard tile, there's no 5-item limit |
| `select.studylife_active_course` | Select | Dropdown of the active (selected in setup, not yet completed) courses of the **active** study programme — a pure picker, it doesn't trigger any API calls itself. State = course name, attributes: `course_id`, `course_code`, `course_color`, `active_courses` (full list). Selection persists across restarts. Meant to conveniently populate `course_id` via dropdown instead of manual entry for the services below (e.g. `{{ state_attr('select.studylife_active_course', 'course_id') }}`). Deliberately **not** duplicated per study programme: "which course am I currently planning for" depends on what's active in the app anyway |

### Per study programme: device `StudyLife — <name>`

Every study programme device carries the same set of entities (entity IDs are derived from the device name, here with `<programme>` as a placeholder). All metrics are scoped to the courses of **this** study programme — including sessions/streaks, which are matched via the globally unique course IDs. The weekly/monthly **target** remains the globally configured one (there are no per-programme targets) — only the studied hours in the counter are scoped to the study programme.

| Entity | Type | Meaning |
|---|---|---|
| `sensor.studylife_<programme>_ects_fortschritt` | Sensor | ECTS earned in this study programme (including elective-group quotas), attributes `ects_total`, `percent` |
| `sensor.studylife_<programme>_o_note` | Sensor | ECTS-weighted average grade across the courses of this study programme that have a grade set, attribute `graded_courses` |
| `sensor.studylife_<programme>_streak` | Sensor | Day streak from this study programme's sessions only |
| `sensor.studylife_<programme>_langste_streak` | Sensor | Longest streak ever reached in this study programme |
| `sensor.studylife_<programme>_wochenkontingent` | Sensor | Hours this week **for this study programme** as a % of the global weekly target, attributes like the hub counterpart |
| `sensor.studylife_<programme>_monatskontingent` | Sensor | Same for the (proportionally growing) monthly target |
| `sensor.studylife_<programme>_lernprognose` | Sensor (Date) | Expected completion date of this study programme — same calculation as the former hub sensor (semester baseline × study rate of the last 8 weeks, but only using this study programme's sessions/courses), attribute `recent_weekly_hours`. Empty at 100% ECTS or without a semester structure |
| `sensor.studylife_<programme>_nachstes_kursziel` | Sensor | Next due course goal of this study programme (`none` if none open), attributes as before including `upcoming_goals` (max. 5) |
| `sensor.studylife_<programme>_nachstes_kursziel_datum` | Sensor (Date) | Corresponding target date |
| `sensor.studylife_<programme>_kurskatalog` | Sensor | Number of courses in this study programme's catalog, attributes: `courses` (full list), `total_hours`/`total_sessions` (study time logged so far for this study programme) |
| `binary_sensor.studylife_<programme>_abgeschlossen` | Binary Sensor | The study programme's completion flag as **manually** set in the app (never automatic, not even at 100% ECTS; always off for the built-in study programme) |
| `binary_sensor.studylife_<programme>_in_der_app_aktiv` | Binary Sensor | On when this study programme is the one active in the app — counterpart to `sensor.studylife_active_program`, attributes `program_id`, `is_built_in` |

Device lifecycle: newly created study programmes appear automatically on the next poll; deleted ones go `unavailable` and their device can be removed manually (see above).

## Events

In addition to the sensor, the coordinator fires the bus event **`studylife_weekly_report`** exactly when the completed week changes between two poll cycles — i.e. once a week, on Monday at the first refresh. Payload = the same fields as the sensor attributes plus `week_id`. On the very first refresh after an HA restart, the week ID is only recorded, not fired — so a restart never triggers the report again. Trigger in automations: `platform: event` with `event_type: studylife_weekly_report`, or alternatively a state trigger on `sensor.studylife_weekly_report`.

## Services

| Service | Purpose | Fields |
|---|---|---|
| `studylife.create_session` | Creates a new session (`POST /api/sessions`) | `course_id` (required), `course_name`, `course_color`, `start_time`, `end_time` (required), `topic`, `notes`, `timer_mode_id` |
| `studylife.update_session` | Changes individual fields of an existing session (`PUT /api/sessions/{id}`) | `session_id` (required), all other fields optional — fields not specified remain unchanged (the server always expects the full DTO, though, so the integration first loads the current state from the coordinator cache and merges in only the fields provided) |
| `studylife.delete_session` | Deletes a session by its ID (`DELETE /api/sessions/{id}`) | `session_id` (required) |
| `studylife.set_course_goal` | Creates or updates the target date/grade/completion note for a course (`PUT /api/coursegoals/{courseId}`) | `course_id` (required), `course_name`, `target_date`, `grade`, `completion_note` — as with `update_session`, fields not specified are preserved (merged against the last-polled state) |
| `studylife.generate_exam_plan` | Automatically distributes a course's still-open topics as study sessions across free calendar slots up to the exam date (`POST /api/planner/exam-plan`) — server-side equivalent of the exam planner on the web app's Planner page, creates the sessions directly (no confirmation step) | `course_id` (required), `exam_date` (required), `session_length_minutes` (default 90), `total_hours` (default: automatically estimated from open topics) |
| `studylife.set_active_program` | Switches the study programme active in the **app** (`PUT /api/settings`, field `activeStudyProgramId`). In HA, all study programme devices remain visible unaffected by this — only the course picker, course goal calendar, and the hub device's resolved course lists follow it | `program_id` (optional; ID of a study programme from `sensor.studylife_active_program`, attribute `programs`; omitted = back to the built-in study programme) |

**`course_name`/`course_color` are now optional:** if only `course_id` is given, the integration automatically resolves the name and color via the course catalog (`/api/courses`) — handy in combination with `select.studylife_active_course` (see above), whose `course_id` attribute can be plugged in directly without having to look up or type the course name yourself. If the `course_id` isn't known in the catalog, `course_name` must still be given explicitly (otherwise an error occurs).

All six services immediately update the sensors/calendar afterward (no waiting for the next poll cycle). The session ID for `update_session`/`delete_session` is available as the `session_id` attribute on `sensor.studylife_active_session`, `sensor.studylife_next_session`, and in the `sessions` list of `sensor.studylife_today_sessions`, as well as `uid` on the respective calendar entry. If only one StudyLife server is configured, `device_id` can be omitted; with multiple servers, the target device must be specified (Developer Tools → Services → select target device).

Example automations (service calls via YAML):

```yaml
action: studylife.create_session
data:
  course_id: 13
  course_name: "Neuronale Netze und Deep Learning"
  start_time: "2026-07-15 18:00:00"
  end_time: "2026-07-15 19:30:00"
  topic: "Backpropagation"
```

```yaml
action: studylife.update_session
data:
  session_id: 42
  end_time: "2026-07-15 20:00:00"   # only extend the end, everything else stays the same
```

```yaml
action: studylife.set_course_goal
data:
  course_id: 13
  course_name: "Neuronale Netze und Deep Learning"
  target_date: "2026-09-30"
```

```yaml
action: studylife.generate_exam_plan
data:
  course_id: 13
  exam_date: "2026-09-30"
```

```yaml
action: studylife.set_active_program
data:
  program_id: 2   # ID from sensor.studylife_active_program, attribute "programs"
```

Example using the course picker instead of a hardcoded `course_id` (select the course beforehand in the `select.studylife_active_course` dropdown, e.g. on a dashboard):

```yaml
action: studylife.create_session
data:
  course_id: "{{ state_attr('select.studylife_active_course', 'course_id') }}"
  start_time: "2026-07-15 18:00:00"
  end_time: "2026-07-15 19:30:00"
  topic: "Backpropagation"
  # course_name/course_color are resolved automatically from the course catalog
```

## Known limitations

- **The study heatmap as well as the weekday/time-of-day/monthly-trend charts (Analytics page) have no equivalent in this integration.** They're based on `GET /api/sessions/history` (up to 371 days of session history) — as a sensor attribute this would be far above Home Assistant's recommended attribute size, so it was deliberately left unmapped. Anyone needing the raw data in HA can query `GET /api/sessions/history?days=N` directly via a RESTful sensor/command-line integration.
- **Recurring (weekly) appointments** are created client-side in the web app as several independent sessions — there's no series concept in the API. `studylife.create_session` still only covers a single session per call; for multiple appointments in a row, the service must be called multiple times (e.g. via an automation loop).
- **Timer status is "best effort," not guaranteed live.** The client only pushes to `/api/timerstate` on state changes (start/pause/phase change/completion), not every second. If the browser crashes or the tab is closed while the timer is running, the last reported state (`IsRunning=true`) persists until the user reopens the app and stops the timer. Automations should therefore check `sensor.studylife_timer_phase_ends` against the current time rather than blindly trusting `binary_sensor.studylife_timer_running`, if this matters.
- **The completion note (free text) isn't mapped for reading.** `set_course_goal` can write it, but there's no sensor that displays the free text of a completion note — only the target date (`next_course_goal`/`next_course_goal_date`) and grade (`average_grade`) are also available for reading. For the full text, the web UI (Setup or "Analytics" page) is still required.
- **Time zone:** session times are treated as naive local time, the same way the server itself does (`DateTime.Now`, no UTC). The integration assumes Home Assistant runs in the same time zone as the StudyLife server. `create_session`/`update_session` convert tz-aware inputs (e.g. from the HA datetime selector) accordingly before sending.

## API key

The StudyLife server protects its entire `/api` with **personal, long-lived API keys** (one per user account; only a SHA-256 hash is stored server-side). The app's own browser client no longer uses an API key at all, but its passkey session instead — the API key exists solely for Home Assistant and similar non-interactive integrations. For this integration, that means:

- **One-time pairing:** generate an API key on the StudyLife app's setup page (the "Home Assistant" card). The plaintext value is shown there **exactly once** — copy it immediately and paste it into the integration's "API key" field. It's attached to every request as an `X-Api-Key` header.
- **No rotation, no expiry:** the key is deliberately long-lived — it stays valid until it's regenerated or revoked in the app. The previous mechanism (a global key, automatic 30-day rotation, `X-Api-Key-Rotated` header) no longer exists; the integration accordingly no longer contains any adoption logic either.
- **Key regenerated or revoked in the app:** the server responds with `401`, the integration marks itself as "Reauthentication required," and HA shows a reauth dialog — enter the newly generated key from the app there. This is the only case where anything ever needs to be entered manually again.

Note: the iCalendar feed (`GET /api/sessions/ics`, for external calendar apps) uses its own permanent `?calendarToken=` query parameter, since calendar clients can't set custom headers — but this affects the web app, not this integration.

Details on the API and data model: [`docs/ARCHITECTURE.md`](https://github.com/lukislp/studylife/blob/main/docs/ARCHITECTURE.md#home-assistant-integration) in the main app repo.

## License

[MIT](LICENSE).
