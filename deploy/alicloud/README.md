# AliCloud Minimal-Cost Deployment (Personal Use)

This guide deploys `sydney-fishing` on one Alibaba Cloud ECS instance with persistent local SQLite.

## 1) Recommended Purchase (China Console)

- Product: ECS (or Lightweight Application Server)
- Region: Beijing (start here for lowest complexity)
- Spec: 2 vCPU / 2 GB RAM
- Disk: 40 GB SSD
- Public bandwidth: 3 Mbps is enough for low traffic
- OS: Ubuntu 22.04 LTS

## 2) Server Bootstrap

Run on ECS:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin nginx git
sudo systemctl enable --now docker nginx
```

## 3) Pull Code

```bash
sudo mkdir -p /opt
cd /opt
sudo git clone <YOUR_REPO_URL> sydney-fishing
sudo chown -R $USER:$USER /opt/sydney-fishing
cd /opt/sydney-fishing
```

## 4) Start App (Docker)

```bash
cd /opt/sydney-fishing/deploy/alicloud
docker compose up -d --build
```

App is now listening at `127.0.0.1:8501`.

## 5) Configure Nginx Reverse Proxy

```bash
sudo cp /opt/sydney-fishing/deploy/alicloud/nginx/sydney-fishing.conf /etc/nginx/sites-available/sydney-fishing.conf
sudo ln -sf /etc/nginx/sites-available/sydney-fishing.conf /etc/nginx/sites-enabled/sydney-fishing.conf
sudo nginx -t
sudo systemctl reload nginx
```

Now access with `http://<ECS_PUBLIC_IP>/`.

## 6) Data Persistence

- SQLite DB file path in container: `/app/data/stats.db`
- Host path (mounted): `/opt/sydney-fishing/data/stats.db`
- This survives app restart and container recreation.

## 7) Daily Backup (Local)

```bash
cd /opt/sydney-fishing
./deploy/alicloud/scripts/install_backup_cron.sh
```

Backups are stored in `/opt/sydney-fishing/backups` daily at 03:00 and retained for 14 days.

## 8) Open Firewall/Security Group

Allow inbound:
- TCP 80 (HTTP)
- Optional: TCP 22 (SSH)

## 9) Update Deploy

```bash
cd /opt/sydney-fishing
git pull
cd deploy/alicloud
docker compose up -d --build
```

## 10) Optional: HTTPS

After binding a domain, use certbot:

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx
```

