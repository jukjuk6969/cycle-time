# CT Monitor — Server Documentation

CT Monitor is a cycle-time monitoring platform. This repository's server-side application is in `backend/`: a Node.js/Express API and Socket.IO service backed by PostgreSQL. It receives cycle and status data from Field Devices (FDs), serves dashboard data, manages stations and users, and proxies FD video and live-stream requests.

> This README focuses on the central server and its integration points—not on installing or operating the workstation mini PCs. For the complete backend API and configuration reference, see [`backend/README.md`](backend/README.md).

## System architecture

```text
Field Devices ── HTTP / heartbeat TCP ──► Backend ──► PostgreSQL
      ▲                                      │
      │             video/stream proxy       │ REST + Socket.IO
      └──────────────────────────────────────┘
                                             ▼
                                       Web dashboard
```

- **Backend (`backend/`)**: central API, authentication, station status, cycle processing, real-time events, and proxying.
- **PostgreSQL**: stores station configuration, cycle records, operator data, and video metadata. Video files remain on their owning FD; the backend proxies access to them.
- **Frontend (`frontend/`)**: browser dashboard for station monitoring, cycle statistics, and video review.
- **Field Devices**: send cycle reports and status to the backend and serve their local videos/streams when requested. Their installation and operation are outside the scope of this README.

A station is identified by the pair `(line, station_id)`; `station_id` alone is not globally unique.

## Backend components

| File | Responsibility |
|---|---|
| `backend/server.js` | Express API, Socket.IO, FD registration and liveness, station administration, and FD video/stream proxying. |
| `backend/db.js` | PostgreSQL pool and database queries. |
| `backend/engine.js` | In-memory station summaries, counters, duplicate timestamp reservations, and batched cycle inserts. |
| `backend/auth.js` | Dashboard login, signed session cookies, roles, and route access rules. |
| `backend/tabel_cycle_times_startup.sql` | PostgreSQL DDL and indexes for `cycle_times`. |

## Requirements

- Node.js (CommonJS application)
- PostgreSQL reachable from the backend host
- Network access from browsers and FDs to the backend
- Network access from the backend to FD HTTP APIs and the FD heartbeat TCP port for proxying and liveness

## Run the backend

From the repository root:

```bash
cd backend
npm install
# Configure backend/.env or the process environment (see below).
npm start
```

For development, run `npm run dev` to start the server with `nodemon`. The HTTP and Socket.IO server listens on port `3000` by default; the FD heartbeat TCP listener uses port `5002` by default.

The repository does not currently include an `.env.example`. The backend loads `.env` using `dotenv`.

## Configuration

Set database credentials and browser origins explicitly in deployed environments. In particular, do not rely on the code's fallback database password or the randomly generated authentication secret.

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

Important environment variables:

| Variable | Default / behavior | Purpose |
|---|---|---|
| `PORT` | `3000` | HTTP and Socket.IO listener. |
| `HB_PORT` | `5002` | FD heartbeat TCP listener. |
| `CORS_ORIGINS` | Empty list | Comma-separated browser origins allowed by CORS. Set the dashboard origin(s). |
| `DBSERVER` / `DBPORT` | `localhost` / `5432` | PostgreSQL host and port. |
| `DBNAME` / `DBUSER` | `CTMonitor` / `postgres` | Database name and user. |
| `DBPASSWORD` | Code fallback: `123` | Database password. Set a strong value explicitly. |
| `DB_POOLMAX` | `30` | Maximum PostgreSQL pool size. |
| `DBENCRYPT` | TLS disabled unless exactly `true` | Enables PostgreSQL SSL. Review the backend README's TLS caveat before relying on this setting. |
| `AUTH_MODE` | `enforce` | `enforce`, `report`, or `off` API authentication-guard behavior. Use `enforce` in production. |
| `AUTH_SECRET` | Random per process if unset or too short | HMAC signing secret for session cookies. Set a stable secret of at least 16 characters. |
| `AUTH_SESSION_HOURS` | `12` | Dashboard session lifetime. |
| `AUTH_COOKIE_SECURE` | Disabled unless exactly `true` | Adds the `Secure` cookie attribute; enable when browser access uses HTTPS. |
| `MAX_LIVE_STREAMS` | `30` | Concurrent live-stream proxy limit. |
| `MAX_VIDEO_PROXY` | `20` | Concurrent video proxy limit. |
| `LOG_VERBOSE` | Disabled unless exactly `true` | Detailed backend/database logging and performance diagnostics. |

See [`backend/README.md`](backend/README.md#environment-variables) for all configuration details and defaults.

## Database

The backend uses PostgreSQL through the `pg` package. The application expects its tables and indexes to be provisioned; it does not automatically create the complete schema. `backend/tabel_cycle_times_startup.sql` provides the `cycle_times` table DDL, but is not a full database migration system. Back up the database before schema changes and provision every table and index required by the deployed version.

Cycle records use `(line, station_id, end_timestamp)` as their primary key. Timestamps are stored as UTC values; production shift interpretation is WIB (UTC+7).

## Main server interfaces

The detailed request formats, parameters, responses, and route behavior are documented in [`backend/README.md`](backend/README.md#http-api-reference). The principal interfaces include:

| Interface | Examples | Purpose |
|---|---|---|
| Health and status | `GET /api/health`, `GET /api/summary`, `GET /api/stations` | Database health, station summaries, and station configuration/status. |
| FD integration | `POST /api/fd/register`, `POST /api/fd/heartbeat`, `POST /api/cycle` | Registration, liveness, and cycle reporting. |
| Station administration | `/api/station/...` | Create/update stations and configure takt time, timeouts, thresholds, and saving mode. |
| Dashboard data | `/api/station/.../history`, `/stats`, `/hourly`, `/export` | Cycle history, aggregates, time buckets, and CSV export. |
| Video and stream proxy | `/api/video/...`, `/api/fd/.../stream` | Access FD-hosted video and live stream through the backend. |
| Authentication | `/api/auth/login`, `/api/auth/logout`, `/api/auth/me` | Dashboard session management. |

Dashboard clients use Socket.IO for live summaries, cycle completion, heartbeat/status changes, station changes, and parameter updates. FDs use Socket.IO for server-pushed configuration updates. See [`backend/README.md`](backend/README.md#socketio) for the event list and subscription behavior.

## Frontend

The dashboard is in `frontend/`. Its stack is React 18, `react-scripts` 5, Chakra UI 3, and Recharts. Typical development commands are:

```bash
cd frontend
npm install
npm start
```

See the frontend package configuration for available scripts and build/deployment settings.

## Operational and security notes

- `/api/health` checks PostgreSQL connectivity; it does not verify that FDs are reachable.
- The backend keeps some summaries and liveness state in memory. On restart it reloads station summaries from PostgreSQL; FDs must reconnect/register.
- Cycle timestamps are UTC Unix seconds, while production shifts are interpreted in WIB. Keep server and FD clocks synchronized.
- Videos are stored on FDs. If the owning FD is offline or unreachable, its videos cannot be streamed by the dashboard.
- Restrict backend and FD ports to trusted networks. Use HTTPS for browser access and set `AUTH_COOKIE_SECURE=true` when TLS is enabled.
- Dashboard credentials and password hashes are currently defined in `backend/auth.js`; review this implementation and deployment access controls before production use.

## Further documentation

- [Backend setup, environment variables, API reference, and Socket.IO events](backend/README.md)
