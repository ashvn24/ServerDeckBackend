# ServerDeck

**ServerDeck is a self-hostable Linux server management platform that lets you monitor, control, and automate all your servers from a single dashboard — without SSH, without VPNs, and without opening any inbound ports.**

> 🌐 Hosted version available at [serverdeck.online](https://serverdeck.online) — or self-host the backend on your own infrastructure.

---

## Why ServerDeck?

Managing Linux servers is fragmented. You SSH into each one individually, jump between `htop`, `systemctl`, `nginx`, `certbot`, `ufw`, and a dozen other tools — and you have to do it all over again for every server you manage. There is no central view, no audit trail, and no way to delegate access safely to a teammate without giving them raw SSH.

Existing solutions either cost a fortune (Datadog, New Relic), require complex agents and network exposure (Ansible, Puppet), or are read-only dashboards that cannot actually do anything. ServerDeck takes a different approach:

- **Lightweight agent** — a single Python process installed on your server in under a minute
- **No inbound ports** — the agent connects *outbound* to your backend over WebSocket; your server's firewall never changes
- **Full control** — not just monitoring, but actually managing services, nginx, SSL, files, firewalls, and running commands
- **AI-powered alerting** — when something breaks, an LLM automatically diagnoses the cause and suggests a fix before you even open a terminal

---

## What Problem Does It Solve?

### For solo developers and freelancers
You are managing 3–10 VPS instances across DigitalOcean, Linode, Hetzner, and AWS. Each one requires its own SSH key, its own terminal window, and its own mental model. ServerDeck gives you one place to see all your servers, restart a crashed service, check disk usage, renew an SSL cert, or tail logs — from the browser, without memorizing IPs or keeping terminal sessions open.

### For small DevOps teams
You need to give a new team member access to restart one service on one server — but not SSH access to the whole machine. Or you need an audit trail of every command run on production. Or you need to know the moment a service goes down, with an AI explanation of *why* it went down. ServerDeck handles all of this without standing up a Kubernetes cluster or paying enterprise SaaS prices.

### For agencies and MSPs
You manage servers for multiple clients. Each client is isolated in their own tenant schema — their servers, their users, their alert rules, and their tickets are completely separate. You can give a client's team view-only access while your engineers retain full control.

---

## Who Is This For?

| User | What they use ServerDeck for |
|---|---|
| **Solo developer** | Dashboard for personal VPS fleet — one place for metrics, logs, restarts |
| **Startup team** | Shared server management without shared SSH keys; audit logs for compliance |
| **Agency / MSP** | Multi-tenant: manage multiple client environments from one backend |
| **Support engineer** | Ticket system + limited server access; no SSH required |
| **DevOps engineer** | Alerting, AI diagnosis, and a scriptable API on top of server management |

---

## How It Works

```
+------------------------------------------+
|           ServerDeck Dashboard           |
|      (browser -> your backend API)       |
+--------------------+---------------------+
                     |  HTTPS / WebSocket
          +----------+-----------+
          v                      v
     +---------+           +---------+
     |  Agent  |           |  Agent  |  <- outbound WebSocket only
     | Server A|           | Server B|     no inbound ports opened
     +---------+           +---------+
```

1. **Add a server** in the dashboard — get a one-line install command
2. **Run it** on your Linux server — the agent installs itself as a systemd service
3. The agent connects **outbound** to your backend over a persistent WebSocket
4. Every action you take in the dashboard is relayed to the agent as a signed command
5. Results stream back in real time

**The agent never opens any inbound ports.** No firewall rules need to change on your servers.

---

## Core Features

### Real-Time Monitoring
Live CPU, RAM, disk, and uptime metrics streamed over WebSocket. All data stays in your database — no third-party analytics.

### Service Management
- **systemd** — list, start, stop, restart, enable, disable, and create unit files
- **PM2** — full app lifecycle management for Node.js processes
- **Nginx** — manage virtual hosts, enable/disable sites, edit configs with live validation before applying

### SSL and Security
- List all certificates on a server
- Issue and renew certs via Certbot with one click
- Manage UFW firewall rules (allow, deny, delete) directly from the dashboard

### Files and Logs
- Browse, read, write, and delete files on your server
- Tail logs in real time from journald, nginx, or PM2
- Upload and download files through the dashboard

### Intelligent Alerting
Define rules on any server metric:
- CPU, RAM, or disk above a threshold
- Service down (a specific systemd service not running)
- Server offline for more than 5 minutes
- SSL certificate expiring within N days

When an alert fires, ServerDeck automatically runs an **AI diagnosis** (via Groq / Llama 3.3 70B):
1. Fetches the last 200 lines of relevant logs from the server
2. Fetches current service statuses
3. Reviews the recent audit log of commands run on that server
4. Returns a plain-English explanation, a suggested fix, and a ready-to-run shell command

The diagnosis result is pushed to your dashboard in real time — you often know *why* something broke before you have even opened a new tab.

### Ticket System
Built-in support ticket system scoped to your team:
- Create tickets manually or link them to a fired alert
- Assign to team members, set priority and status
- Supports a `support` role with ticket-only access (no server access)

### Audit Log
Every command sent to every server is recorded: who ran it, when, and what the outcome was. Immutable, per-tenant.

### Multi-Tenancy
The backend is fully multi-tenant using PostgreSQL schema isolation:
- Each organisation gets its own schema (`tenant_acme`, `tenant_globex`, etc.)
- Individual users (personal email addresses) share a pooled `tenant_individual` schema
- No data leakage between tenants by construction — the DB `search_path` is set per-request from the JWT

### Browser Terminal
Open a full interactive PTY session directly in your browser. Useful when you need raw access without leaving the dashboard.

---

## Architecture

| Layer | Technology |
|---|---|
| **Backend API** | Python, FastAPI, async SQLAlchemy |
| **Database** | PostgreSQL (multi-schema, one schema per tenant) |
| **Migrations** | Alembic (scoped public/tenant migrations) |
| **Real-time** | WebSocket (FastAPI native) |
| **Agent** | Python, WebSocket client, systemd service |
| **AI Diagnosis** | Groq API (Llama 3.3 70B) |
| **Auth** | JWT, TOTP two-factor, invite-link team onboarding |

---

## Repository Structure

This repository contains the **backend API** and deployment scripts.

```
ServerDeckBackend/
├── Backend/
│   ├── app/
│   │   ├── api/              # FastAPI route handlers (servers, alerts, tickets, auth, ...)
│   │   ├── models/           # SQLAlchemy ORM models
│   │   ├── schemas/          # Pydantic request/response schemas
│   │   ├── services/
│   │   │   ├── alert_service.py      # Background alerting loop (runs every 60s)
│   │   │   ├── diagnosis_service.py  # AI diagnosis via Groq
│   │   │   ├── tenant.py             # Multi-tenant schema resolution
│   │   │   ├── command_bridge.py     # Routes commands to agent WebSockets
│   │   │   └── email_service.py      # Invite and notification emails
│   │   └── ws/               # WebSocket handlers (agent <-> client)
│   ├── alembic/              # Database migrations
│   │   └── versions/         # Individual migration files
│   ├── migrate_tenants.py    # Apply migrations across all tenant schemas
│   └── gen_migration.py      # Autogenerate scoped public/tenant migrations
```

The **agent** (installed on your Linux servers) lives in a separate repository: [serverdeck-agent](https://github.com/ashvn24/serverdeck-agent).

---

## Getting Started (Self-Hosted)

### Requirements
- Python 3.10+
- PostgreSQL 14+
- A server to host the backend

### 1. Clone and configure

```bash
git clone https://github.com/ashvn24/ServerDeckBackend
cd ServerDeckBackend/Backend

cp .env.example .env
# Edit .env: DATABASE_URL, SECRET_KEY, GROK_API_KEY, CORS origins, etc.
```

### 2. Set up the database

```bash
python -m venv venvsd
source venvsd/bin/activate
pip install -r requirements.txt

# Run public-schema migrations
alembic upgrade head

# Run tenant migrations across all tenant_* schemas
python migrate_tenants.py
```

### 3. Start the API

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 4. Install an agent on a server

Add a server in the dashboard, copy the generated install command, run it on your Linux server. The agent registers itself, connects, and starts reporting immediately.

---

## Security Model

The agent is the most security-sensitive part of the system. Key properties:

| Property | Detail |
|---|---|
| **No inbound ports** | Agent connects outbound only; your firewall is untouched |
| **Token auth** | 256-bit random token per server, `chmod 600`, verified on every connection |
| **Command allowlist** | Agent only accepts an explicit whitelist of action names; unknown commands are rejected |
| **No source on production** | Install script compiles to `.pyc` and removes all `.py` files from the host |
| **Tenant isolation** | Each tenant has a separate PostgreSQL schema; `search_path` is set from the JWT |
| **Schema validation** | Tenant schema names are validated against a strict regex before use in any SQL |
| **Audit trail** | Every command, actor, and result is logged — immutable, per-tenant |

See the [agent repository](https://github.com/ashvn24/serverdeck-agent) for the full security documentation and the complete command allowlist.

---

## Contributing

Found a security issue? **Do not open a public issue.** Email **ashwinvk77@gmail.com** directly.

Found a bug or want to suggest a feature? Open an issue or pull request. The codebase is open source specifically so you can read it, audit it, and improve it.

---

## License

MIT — do whatever you want with it.
