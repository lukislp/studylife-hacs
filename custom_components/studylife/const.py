"""Constants for the StudyLife integration."""

DOMAIN = "studylife"

# Two kinds of config entry share this one domain: a StudyLife server account (the
# original, default kind - see config_flow.py's async_step_account) and, since the
# studylife-display JSON API (DISPLAY_API_TOKEN) landed, an optional studylife-display
# e-paper panel on the LAN (async_step_display) - each its own device, any number of
# either kind. An entry with no CONF_ENTRY_TYPE at all (created before this existed)
# is treated as ENTRY_TYPE_ACCOUNT throughout - see __init__.py's async_setup_entry.
CONF_ENTRY_TYPE = "entry_type"
ENTRY_TYPE_ACCOUNT = "account"
ENTRY_TYPE_DISPLAY = "display"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30  # seconds, matches AppStateService's own poll interval
# studylife-display's own scheduled refresh is every 5 minutes; polling its JSON API
# faster than that would only ever re-read the same cached state.
DEFAULT_DISPLAY_SCAN_INTERVAL = 60

# studylife-display's pseudo layout key that picks one of the real ones automatically
# on every panel refresh - not itself one of /api/layouts' "options" (see display_select.py).
AUTO_LAYOUT = "auto"

# Weekly study quota target, mirrors Index.razor
WEEK_QUOTA_MIN_HOURS = 25
WEEK_QUOTA_MAX_HOURS = 30

# Monthly study quota target, mirrors Index.razor. Independently configurable from the
# weekly target (MonthlyGoalMinHours/MaxHours server-side) - no longer derived from it.
MONTH_QUOTA_MIN_HOURS = 100
MONTH_QUOTA_MAX_HOURS = 130

# How far back /api/sessions/history is queried for streak/month-quota/trend/neglected-course
# calculations, mirrors Index.razor's HistoryDays
SESSION_HISTORY_DAYS = 400
NEGLECT_HISTORY_DAYS = 180  # mirrors Index.razor's NeglectHistoryDays

# Bus event fired once per week-rollover with the last completed week's summary,
# mirrors the server's Sunday-18:00 weekly web push (RunWeeklyReportAsync in
# BackgroundTaskService.cs), which HA automations can't react to directly
EVENT_WEEKLY_REPORT = f"{DOMAIN}_weekly_report"

REQUEST_TIMEOUT = 10  # seconds
