# Backend documentation

This directory contains the CT Monitor backend: an Express API and Socket.IO server that receives cycle data from Field Devices (FDs), persists it in PostgreSQL, serves dashboard data, and proxies FD video and live-stream requests.

## Contents

| File | Responsibility |
|---|---|
| `server.js` | HTTP API, Socket.IO events, FD registration/liveness, station administration, and FD stream/video proxying. This is the application entry point. |
| `db.js` | PostgreSQL connection pool and database queries for stations, cycles, operators, heartbeats, statistics, and video metadata. |
| `engine.js` | In-memory station summaries, shift/day counters, duplicate timestamp reservations, and batched cycle inserts. |
| `auth.js` | Dashboard login, signed session cookies, role checks, and route access rules. |
| `tabel_cycle_times_startup.sql` | PostgreSQL DDL and indexes for the `cycle_times` table. Despite its filename, it is SQL for PostgreSQL. |
| `package.json` / `package-lock.json` | Node.js package metadata, scripts, and locked dependency versions. |

## Architecture and data flow

1. An FD registers its IP address and local API port with `POST /api/fd/register`. The IP must match one active station in the database; station identity is `(line, station_id)`.
2. FDs send completed cycles to `POST /api/cycle`. The engine validates and reserves the timestamp, queues the record, and persists cycles in batches. The FD should retry when it receives a temporary server error.
3. FD status is received through `POST /api/fd/heartbeat` and a separate newline-delimited TCP heartbeat connection (default port `5002`). The TCP connection supports `hello`, `ping`/`pong`, and state messages. Liveness is tracked in memory and checked against the FD health endpoint when needed.
4. The dashboard reads station summaries and history over REST and receives live updates over Socket.IO. Socket.IO clients can subscribe to all stations or selected lines.
5. Videos stay on the owning FD. The backend uses the registered FD address to proxy playback, downloads, deletion, and live MJPEG streams; video metadata and cycle data are stored in PostgreSQL.

The FD HTTP API must be reachable from the backend for proxying (FD default port `5001`). The backend does not store video files.

## Requirements and startup

- Node.js (the project uses CommonJS modules).
- PostgreSQL reachable from the backend host.
- Network access from FDs and browsers to the backend; network access from the backend to FD HTTP APIs and the FD heartbeat TCP port.

From the repository root:

```bash
cd backend
npm install
# Create .env and set database credentials and allowed browser origins.
npm start
```

For development, `npm run dev` starts `nodemon server.js`. `npm start` runs `node server.js`.

The repository does not currently include an `.env.example`. The server loads `.env` via `dotenv`; set configuration in the process environment or in `backend/.env`.

### Environment variables

| Variable | Default / behavior | Purpose |
|---|---|---|
| `PORT` | `3000` | HTTP and Socket.IO listener port. |
| `HB_PORT` | `5002` | FD heartbeat TCP listener port. |
| `PORT_FAMILY` | Unset | Used by the port-selection helper to derive a port when `PORT` or `HB_PORT` is not explicitly provided. Prefer setting `PORT` and `HB_PORT` directly unless the family convention has been verified for your deployment. |
| `CORS_ORIGINS` | Empty list | Comma-separated allowed browser origins. Include the dashboard origin(s); an empty list does not permit normal browser origins. |
| `DBSERVER` | `localhost` | PostgreSQL host. |
| `DBPORT` | `5432` | PostgreSQL port. |
| `DBNAME` | `CTMonitor` | Database name. |
| `DBUSER` | `postgres` | Database user. |
| `DBPASSWORD` | Code fallback is `123` | Database password. Set a strong secret explicitly in deployed environments; do not rely on the code fallback. |
| `DB_POOLMAX` | `30` | Maximum PostgreSQL pool size. |
| `DBENCRYPT` | TLS disabled unless exactly `true` | Enables PostgreSQL SSL. The current SSL configuration accepts the server certificate without CA verification; configure deployment TLS requirements accordingly. |
| `LOG_VERBOSE` | Disabled unless exactly `true` | Enables detailed backend/database logging and periodic performance diagnostics. Avoid enabling verbose logs unnecessarily in production. |
| `AUTH_MODE` | `enforce` | `enforce` applies authentication, `report` logs would-be denials but allows requests, and `off` disables the API guard. |
| `AUTH_SECRET` | Random per process if unset or shorter than 16 characters | HMAC signing secret for session cookies. Set a stable secret of at least 16 characters or sessions will be invalidated at every restart. |
| `AUTH_SESSION_HOURS` | `12` | Dashboard session lifetime in hours. |
| `AUTH_COOKIE_SECURE` | Disabled unless exactly `true` | Adds the `Secure` cookie attribute; enable when serving the dashboard over HTTPS. |
| `MAX_LIVE_STREAMS` | `30` | Maximum concurrent live-stream proxy connections. |
| `MAX_VIDEO_PROXY` | `20` | Maximum concurrent video proxy connections. |

Example `backend/.env` (replace all example values):

```dotenv
PORT=3000
HB_PORT=5002
CORS_ORIGINS=https://dashboard.example.com
DBSERVER=127.0.0.1
DBPORT=5432
DBNAME=CTMonitor
DBUSER=ct_monitor
DBPASSWORD=replace-with-a-secret
AUTH_SECRET=replace-with-a-long-random-secret
AUTH_MODE=enforce
AUTH_COOKIE_SECURE=true
```

## Database

The backend uses PostgreSQL through the `pg` package. `db.js` expects the application tables to exist; it does not create the complete schema automatically. `tabel_cycle_times_startup.sql` creates `cycle_times` and supporting indexes, but station, operator, and heartbeat tables also need to be provisioned by the deployment's database setup.

The `cycle_times` primary key is `(line, station_id, end_timestamp)`. The schema stores timestamps as UTC `timestamp` values (without a timezone); the backend explicitly parses/writes these as UTC. Shift reporting uses WIB (UTC+7): Shift 1 is 19:00–07:00 and Shift 2 is 07:00–19:00. Cycle counts reset at shift boundaries. The SQL file includes indexes for time-range summaries and video lookup.

Database connection settings have short connection and statement timeouts. The server periodically checks database connectivity, and the engine retries its initial summary load every five seconds until it succeeds.

## Authentication and authorization

Dashboard login routes are under `/api/auth`:

- `POST /api/auth/login` — accepts `{ "username": "...", "password": "..." }`; on success returns a user object and sets the HTTP-only `ctm_session` cookie.
- `POST /api/auth/logout` — clears the session cookie.
- `GET /api/auth/me` — returns the current user or `null`.

The current user list and password hashes are hard-coded in `auth.js`; password hashes use Node's `scrypt`. The code defines `admin` and `viewer` roles. Admin can use write operations. Viewer is read-only except the FD stream tile-click route. Login attempts are rate-limited in process memory.

The API guard leaves the FD integration routes and selected read routes public to support devices that do not send dashboard credentials. This includes cycle reporting, FD registration/heartbeat, summary/station/video-list reads, health, and operator lookup/registration. All other guarded routes require a valid session when `AUTH_MODE=enforce`. Treat this public-route list as part of the security boundary and review it when adding endpoints. Socket.IO records session identity but does not reject unauthenticated connections in the current implementation.

## HTTP API reference

All paths below are relative to the backend origin. JSON request bodies should use `Content-Type: application/json`. Most errors use `{ "error": "..." }`; some include a `code`. Authentication requirements are controlled by `auth.js` and `AUTH_MODE` as described above.

### Health, FD registration, and status

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | Checks database connectivity; returns `{ "db": true }` or HTTP 503. |
| `GET` | `/api/summary` | Returns the in-memory summary for active stations. |
| `GET` | `/api/stations` | Lists station configuration and current online/saving state. |
| `POST` | `/api/fd/register` | Registers an FD using its request IP plus body fields `port` and optional `hostname`. Returns assigned line/station and configuration. |
| `POST` | `/api/fd/heartbeat` | Updates FD heartbeat by body `ip` and optional `port`. |
| `GET` | `/api/fd/status` | Returns registered FD status. |
| `GET` | `/api/fd/identity?ip=...` | Looks up station configuration by IP (defaults to the request IP). |
| `POST` | `/api/heartbeat` | Updates station state; requires `line`, `station_id`, and `state`; accepts elapsed/ROI timing fields. |
| `POST` | `/api/cycle` | Accepts a completed cycle from an FD; see payload notes below. |
| `POST` | `/api/video/update-size` | Updates a recorded video's `size_mb`; body requires `filename` and `size_mb`. |

`POST /api/cycle` expects at least `line`, `station_id`, `cycle_time_sec`, and a positive Unix-seconds `end_timestamp`. It may also include `start_timestamp`, `operator_id`, `is_complete`, `label`, `is_genesis`, `buffered`, `anomaly_type`, `anomaly_label`, `skipped_expected`, and `video` metadata (`filename`, `size_mb`, `recorded_ts`/`recorded_at`, `label`). Missing `start_timestamp` is derived from end time minus cycle duration. The backend determines anomaly type where needed, attaches skipped-expected labels, and treats repeated station/end timestamps as duplicates. A future timestamp beyond the configured clock tolerance is rejected. Successful new records return HTTP 201; duplicate records return HTTP 200 with `duplicate: true`.

### Station management and configuration

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/station/register` | Creates a station. Required body fields: `station_id`, `line`, `name`, `ip`, `takt_time`, `roi_timeout_sec`. |
| `PUT` | `/api/station/:line/:station` | Updates optional station fields (`name`, `ip`, `takt_time`, `roi_timeout_sec`, `is_active`). Identity is not editable. |
| `DELETE` | `/api/station/:line/:station` | Permanently deletes a station; may return 409 if related data prevents deletion. |
| `PUT` | `/api/station/:line/:station/takt-time` | Sets `takt_time`. |
| `PUT` | `/api/station/:line/:station/roi-timeout` | Sets `roi_timeout_sec`. |
| `PUT` | `/api/station/:line/:station_id/min-cycle` | Sets `min_cycle_sec`. |
| `PUT` | `/api/station/:line/:station_id/short-anomaly` | Sets `short_anomaly_sec`; `0` disables the short-anomaly threshold. |
| `PUT` | `/api/station/:line/:station/saving-mode` | Enables/disables saving mode with body `{ "enabled": true|false }`. |

Configuration changes are pushed to the relevant station over Socket.IO. A station's identity is always the pair `(line, station_id)`.

### Cycle history and statistics

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/station/:line/:station/history` | Retrieves recent cycles. Optional query parameters: `from`, `to`, `shift`, `limit`. |
| `GET` | `/api/station/:line/:station/stats` | Aggregate CT statistics; accepts `from`, `to`, `shift`. |
| `GET` | `/api/station/:line/:station/hourly` | Time-bucket statistics; accepts `from`, `to`, `shift`. Bucket grain adapts to the date range. |
| `GET` | `/api/station/:line/:station/export` | Downloads CSV cycle history; accepts `from`, `to`, `shift`. |

Time range filters are interpreted as timestamps and shift filters are `1` or `2`. History without a date range returns recent cycles per shift; cycle lists are bounded by server-side limits.

### Videos, streams, and storage

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/videos` | Lists video metadata. Supports line/station, anomaly type, normal/anomaly, genesis, late/buffered, expected/skipped, date range, sort, direction, limit, and offset filters. The response includes `X-Total-Count`. |
| `GET` | `/api/video/:videoId/stream` | Streams the video associated with a database record. Supports HTTP range forwarding. |
| `GET` | `/api/video/:videoId/download` | Downloads the video associated with a database record. |
| `DELETE` | `/api/video/:videoId` | Deletes an anomaly video on its FD and clears its database video metadata; normal videos cannot be deleted here. |
| `GET` | `/api/fd/:line/:station/stream` | Proxies the FD's combined live MJPEG stream. |
| `GET` | `/api/fd/:line/:station/stream/:slot` | Proxies one FD stream slot. |
| `GET` | `/api/fd/:line/:station/layout` | Returns the current FD stream layout. |
| `POST` | `/api/fd/:line/:station/click` | Resolves normalized `{ "x": 0..1, "y": 0..1 }` coordinates to an FD stream slot. |
| `GET` | `/api/fd/:line/:station/video/:type/:filename` | Proxies a video by FD (`type` is `normal` or `anomaly`); `?download=1` requests attachment disposition. |
| `GET` | `/api/fd/:line/:station/video/:type/:filename/layout` | Returns the layout embedded with an FD video. |
| `GET` | `/api/storage/usage` | Collects storage usage from reachable, online, non-saving FDs. |

The server caps concurrent live and video proxy requests using `MAX_LIVE_STREAMS` and `MAX_VIDEO_PROXY`. An FD that is offline or unreachable cannot serve its locally stored video.

### Operators

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/operator/:id` | Checks for an active operator. |
| `POST` | `/api/operator` | Registers an operator; body includes `id` and optional `name`/`shift`. |

## Socket.IO

Socket.IO uses WebSocket transport and credentials-enabled CORS with the same `CORS_ORIGINS` allowlist. Dashboard clients initially join the `all` room. Emit `subscribe` with `{ "lines": [1, 2] }` to receive selected line updates; an empty list returns to all lines. FD configuration events are routed to the dashboard's line room and the corresponding FD room (`fd:<line>-<station>`).

Events emitted by the backend include:

- `summary_update` — summary snapshot, initially and when station data changes.
- `cycle_complete` — a newly accepted cycle.
- `heartbeat` and `heartbeat_batch` — live station state.
- `fd_status` and `fd_status_all` — FD online/offline/saving status.
- `station_update` — station metadata changes.
- `takt_time_update`, `roi_timeout_update`, `min_cycle_update`, `short_anomaly_update` — updated FD parameters.
- `pong` — response to a client `ping`.

## Operational notes

- Keep FD and backend clocks synchronized. Cycle timestamps are UTC Unix seconds; production shift interpretation is WIB.
- `engine.js` keeps summaries and liveness-related state in memory. A restart reloads station summaries and recent shift timestamps from PostgreSQL; FDs must reconnect/register.
- Monitor backend logs for database connection failures, cycle insert errors, FD proxy errors, and queue backpressure. `/api/health` checks PostgreSQL, not FD reachability.
- Expose the backend and FD ports only to the required trusted networks. Use HTTPS for browser access and set `AUTH_COOKIE_SECURE=true` when TLS terminates at the browser-facing endpoint.
- There is no automated full-schema migration in this folder. Back up the database before applying schema changes and provision all tables/indexes required by the deployed version.
