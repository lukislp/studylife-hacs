"""Constants for the StudyLife integration."""

DOMAIN = "studylife"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30  # seconds, matches AppStateService's own poll interval

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
