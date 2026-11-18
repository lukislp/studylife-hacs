# Privacy

This integration runs inside your own Home Assistant. It talks to the StudyAtrium instance
you configure, and optionally to a studylife-display panel on your network. The maintainers
of this repository operate no server and receive no data from it. Statements below are based
on the code in `custom_components/studylife`.

## What is stored

- **Config entry (Home Assistant `.storage`)**: the instance URL, your StudyAtrium API key
  (or the display API token), the TLS verification flag, the polling interval and a stable
  instance or display id. Home Assistant stores this in clear text in its own configuration
  directory and includes it in Home Assistant backups, like every integration credential.
- **Entity states and history**: the integration exposes your study data (for example today's
  study time, timer state, next session, course goals, calendar events for sessions and goal
  deadlines) as entities. Home Assistant records these in its own database according to its
  recorder settings (by default ten days), and shows them to every Home Assistant user who
  can see the entities.
- **In-memory cache**: HTTP ETag responses are cached in memory while Home Assistant runs;
  nothing is written to disk by the integration itself.

The integration writes nothing outside Home Assistant and logs no study content (log output at
debug level names entities and errors, not notes or courses).

## Where data goes

Requests go only to the StudyAtrium URL (and, if you add one, the display URL) you entered,
with your API key in the `X-Api-Key` header. Data is read on a schedule (default polling
interval) and when you call a service. The integration has no analytics, telemetry or other
third-party endpoint. What other Home Assistant integrations or automations do with the
entities (for example cloud assistants, notifications) is outside this integration.

## What it can change

Services can create, update or delete sessions, update course goals and settings and request
an exam plan, on the account the API key belongs to. Notes are read-only.

## Retention and deletion

Removing the integration in Home Assistant deletes its config entry (and the stored key). The
key stays valid on StudyAtrium until you revoke or regenerate it on the setup page. Recorded
history is removed by Home Assistant's recorder purge or when you delete the entities'
history. Deleting your StudyAtrium account revokes the key; the integration then reports
"reauthentication required". There is no per-user data held by this integration on any
server, so there is no purge endpoint.

## Contact

Requests about your StudyAtrium data go to the operator of your StudyAtrium instance. Security
issues: see [SECURITY.md](SECURITY.md).