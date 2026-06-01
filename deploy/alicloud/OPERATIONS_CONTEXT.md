# Sydney Fishing - ECS & Domain Ops Context

Last updated: 2026-05-30

## 1) Active Production Target
- Cloud: Alibaba Cloud ECS
- Hostname: `iZbp1h520zkf8bhvgqrl4sZ`
- Public IP: `121.40.89.197`
- Private IP (eth0): `172.26.63.164`
- OS: Alibaba Cloud Linux 3
- App repo path on server: `/opt/sydney-fishing`

## 2) Domain & DNS
- Primary domain: `drunkfishing.xyz`
- Secondary domain: `www.drunkfishing.xyz`
- Nameservers:
  - `dns25.hichina.com`
  - `dns26.hichina.com`
- Required A records:
  - `@ -> 121.40.89.197`
  - `www -> 121.40.89.197`

## 3) Runtime Architecture (Current)
- `sydney-fishing` container:
  - Image: `sydney-fishing:latest`
  - Port binding: `0.0.0.0:8501 -> 8501/tcp`
  - Purpose: Streamlit app
- `sydney-web` container:
  - Image: `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nginx:1.27-alpine`
  - Network mode: `host`
  - Purpose: reverse proxy + TLS termination
  - HTTP `80` -> redirects to HTTPS
  - HTTPS `443` -> proxies to `127.0.0.1:8501`

## 4) HTTPS Certificate
- Issuer: Let's Encrypt
- Domains:
  - `drunkfishing.xyz`
  - `www.drunkfishing.xyz`
- Cert path:
  - `/etc/letsencrypt/live/drunkfishing.xyz/fullchain.pem`
  - `/etc/letsencrypt/live/drunkfishing.xyz/privkey.pem`
- Current expiry: `2026-08-28`
- Renewal timer/service installed by certbot

## 5) Nginx Runtime Config Location
- Host file: `/opt/sydney-fishing/runtime-nginx/default.conf`
- Mounted into `sydney-web` as:
  - `/etc/nginx/conf.d/default.conf`

## 6) Access URLs
- Public HTTPS (recommended): `https://drunkfishing.xyz`
- Public HTTP (auto-redirect): `http://drunkfishing.xyz`
- Direct app port (debug only): `http://121.40.89.197:8501`

## 7) Core Ops Commands

### SSH
```bash
ssh root@121.40.89.197
```

### Check running containers
```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

### Rebuild app image and restart app container
```bash
cd /opt/sydney-fishing
docker build -t sydney-fishing:latest .
docker rm -f sydney-fishing || true
docker run -d --name sydney-fishing --restart unless-stopped \
  -p 8501:8501 \
  -e TZ=Australia/Sydney \
  -e APP_DATA_DIR=/app/data \
  -e TIDECHECK_API_KEY="$TIDECHECK_API_KEY" \
  -e TIDECHECK_STATION_ID="$TIDECHECK_STATION_ID" \
  -v /opt/sydney-fishing/data:/app/data \
  sydney-fishing:latest
```

### Restart web proxy container
```bash
docker rm -f sydney-web || true
docker run -d --name sydney-web --restart unless-stopped --network host \
  -v /opt/sydney-fishing/runtime-nginx/default.conf:/etc/nginx/conf.d/default.conf:ro \
  -v /etc/letsencrypt:/etc/letsencrypt:ro \
  swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/nginx:1.27-alpine
```

### Quick health checks
```bash
curl -I http://127.0.0.1:8501
curl -I http://127.0.0.1
curl -I https://drunkfishing.xyz
```

## 8) Security Group Baseline
- Allow inbound:
  - `22/tcp` (SSH)
  - `80/tcp` (HTTP)
  - `443/tcp` (HTTPS)
- Optional (debug only):
  - `8501/tcp`

## 9) Current Performance Optimization Applied
- Reduced first-screen computation scope in `app.py`:
  - `DESKTOP_SPOT_LIMIT = 80`
  - `HERO_SPOT_LIMIT = 80`
- Purpose: reduce first-load external weather/tide calls.

## 10) Tide Data Source
- Preferred source: TideCheck API free tier.
- Required env var: `TIDECHECK_API_KEY`
- Optional env var: `TIDECHECK_STATION_ID`
  - Set this to the Sydney/Fort Denison-compatible TideCheck station once confirmed.
  - If omitted, the app looks up the nearest station to Circular Quay and caches it for 24 hours.
- API responses are cached in `/opt/sydney-fishing/data/tidecheck_cache.json` for 24 hours.
- Fallback order:
  1. TideCheck API
  2. WorldTides API, if configured
  3. Local Circular Quay override table
  4. Astronomical estimate, clearly marked in the UI

## 11) Historical Note
- Previous ECS/IP used earlier in this project: `47.236.73.48` (no longer active production target).
