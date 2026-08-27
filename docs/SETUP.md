# ASDA setup

## What is stored where

- Source and safe configuration: this repository.
- Leads, drafts, runtime configuration, and local SQLite database: `data/` (never commit or share publicly).
- Credentials: add through the Settings screen or environment variables from `.env.example`.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
asda ui
```

Open `http://localhost:8501`, complete Settings, and leave **live sending** off while configuring or auditing.

Run checks with:

```bash
make test
```

## Persistent local Docker service

The local stack keeps the existing `data/` directory mounted into the container:

```bash
docker-compose -f docker-compose.local.yml up -d --build
docker-compose -f docker-compose.local.yml ps
docker-compose -f docker-compose.local.yml logs -f
```

The container uses `restart: unless-stopped`. It runs on `127.0.0.1:8501`; it is not a cloud deployment.

## Vercel + Cloudflare proxy

This repository includes a minimal Vercel rewrite configuration. It proxies the stable Vercel URL to the local service through a Cloudflare Tunnel.

- The Vercel URL can be stable.
- A free **Quick Tunnel** URL is not stable; after its restart, update `vercel.json` and redeploy the proxy.
- A named Tunnel plus a domain is required for a stable origin hostname.
- The Mac must remain powered on, online, and logged in for a local backend to work.

Do not deploy `data/` or `.env` to Vercel. `.vercelignore` prevents that.
