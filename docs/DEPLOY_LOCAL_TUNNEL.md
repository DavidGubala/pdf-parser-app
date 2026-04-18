# Deploying PDF Parse from Your Local Linux Machine with Cloudflare Tunnel

Host the app on your own Linux computer and expose it to the internet securely through a Cloudflare Tunnel — no port forwarding, no exposed home IP, no hosting costs.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Move DNS to Cloudflare (Keep GitHub Pages Working)](#2-move-dns-to-cloudflare-keep-github-pages-working)
3. [Install System Dependencies](#3-install-system-dependencies)
4. Deploy the Application — pick one:
   - [4A. Deploy with Docker (recommended)](#4a-deploy-with-docker-recommended)
   - [4B. Deploy without Docker (bare metal)](#4b-deploy-without-docker-bare-metal)
5. [Run the App as a System Service](#5-run-the-app-as-a-system-service) *(bare metal only)*
6. [Install Cloudflare Tunnel](#6-install-cloudflare-tunnel)
7. [Create and Configure the Tunnel](#7-create-and-configure-the-tunnel)
8. [Run the Tunnel as a System Service](#8-run-the-tunnel-as-a-system-service)
9. [Seed Your First User](#9-seed-your-first-user)
10. [Verify Everything Works](#10-verify-everything-works)
11. [Maintenance and Updates](#11-maintenance-and-updates)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Prerequisites

Before starting, make sure you have:

- A Linux machine (Ubuntu/Debian assumed; adapt commands for other distros)
- Python 3.11+ installed or installable
- A domain name you own (e.g. `yourdomain.com`)
- A free Cloudflare account ([cloudflare.com](https://www.cloudflare.com/))
- Git installed (`sudo apt install git`)

This guide assumes your main site is hosted on **GitHub Pages** and your domain is registered with **Squarespace** (or any other registrar). The main site will continue to work — we're only adding a subdomain for the PDF Parse app.

---

## 2. Move DNS to Cloudflare (Keep GitHub Pages Working)

Your registrar (Squarespace) currently handles both domain registration AND DNS. In this step you keep Squarespace as the registrar but hand DNS management over to Cloudflare. Your GitHub Pages site is unaffected — the same DNS records are re-created in Cloudflare.

### 2a. Add your domain to Cloudflare

1. Sign in to [dash.cloudflare.com](https://dash.cloudflare.com/).
2. Click **Add a site** and enter your domain (e.g. `yourdomain.com`).
3. Select the **Free** plan.
4. Cloudflare will auto-scan your existing DNS records and import them.

### 2b. Verify your GitHub Pages records imported correctly

Before changing anything, confirm that Cloudflare's imported records match what you have at Squarespace. You should see records like these (the exact IPs are GitHub's):

| Type | Name | Content | Proxy |
|------|------|---------|-------|
| A | `yourdomain.com` | `185.199.108.153` | Proxied |
| A | `yourdomain.com` | `185.199.109.153` | Proxied |
| A | `yourdomain.com` | `185.199.110.153` | Proxied |
| A | `yourdomain.com` | `185.199.111.153` | Proxied |
| CNAME | `www` | `YOUR_USERNAME.github.io` | Proxied |

> **Important:** If any records are missing, add them manually in Cloudflare's DNS dashboard before proceeding. You can check your current records at Squarespace under **Domains → DNS Settings** to compare.

### 2c. Configure SSL mode in Cloudflare

Go to **SSL/TLS → Overview** in the Cloudflare dashboard and set the mode to **Full**. This ensures HTTPS works correctly with GitHub Pages (which provides its own certificate).

### 2d. Switch nameservers at Squarespace

Cloudflare will show you two nameservers (e.g. `ada.ns.cloudflare.com`, `bob.ns.cloudflare.com`).

1. Log in to [account.squarespace.com](https://account.squarespace.com/).
2. Go to **Domains** → select your domain.
3. Go to **DNS Settings** → **Nameservers**.
4. Switch from Squarespace nameservers to **Custom nameservers**.
5. Enter the two Cloudflare nameservers.
6. Save.

### 2e. Wait for propagation

- Usually 5–30 minutes, can take up to 24 hours.
- Cloudflare will email you when the domain is active.
- Your GitHub Pages site should continue working throughout. If there's a brief blip during propagation, it resolves on its own.

### 2f. Verify your main site still works

Visit `https://yourdomain.com` and `https://www.yourdomain.com` — both should load your GitHub Pages site as before. You can also verify in the terminal:

```bash
nslookup yourdomain.com
```

The IPs should be Cloudflare's (since proxying is on), which is normal and correct.

> **Note:** You do NOT need to add the subdomain DNS record manually. The tunnel setup in step 7 will create it automatically.

---

## 3. Install System Dependencies

```bash
sudo apt update && sudo apt upgrade -y
```

### Choose your deployment path

| | **Option A: Docker (recommended)** | **Option B: Bare metal** |
|---|---|---|
| **Security** | App runs in an isolated container — cannot access host files | App runs directly on host as a Linux user |
| **Setup** | Install Docker, then `docker compose up` | Install Python 3.11, pip install, systemd service |
| **Updates** | `git pull && docker compose up --build` | `git pull && pip install && systemctl restart` |
| **Complexity** | Less manual config | More manual steps |

Pick one path and follow the corresponding sections below. Steps 6-8 (Cloudflare Tunnel) and 10-12 are the same for both.

---

## 4A. Deploy with Docker (recommended)

### Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Log out and back in (or run `newgrp docker`) for the group change to take effect. Verify:

```bash
docker --version
docker compose version
```

### Clone the Repository

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/pdf-parse.git
cd pdf-parse
```

### Create the Data Directory and Environment File

```bash
mkdir -p data
touch data/documents.db
```

```bash
cat > .env << 'EOF'
SECRET_KEY=REPLACE_ME
EOF
```

Generate a proper secret key and update the file:

```bash
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
sed -i "s/REPLACE_ME/$SECRET/" .env
cat .env  # verify it looks right
```

### Build and Start

```bash
docker compose up -d --build
```

> **Note:** The first build downloads PyTorch and Docling dependencies. This may take 10-15 minutes. Subsequent rebuilds are cached and much faster.

### Verify

```bash
docker compose ps                           # should show "running"
curl -s http://localhost:8000/login | head -5   # should show login HTML
```

### View Logs

```bash
docker compose logs -f                      # live stdout
ls data/logs/                               # daily log files
```

---

## 4B. Deploy without Docker (bare metal)

### Install Python 3.11

```bash
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# If python3.11 is not available in default repos:
# sudo add-apt-repository ppa:deadsnakes/ppa -y
# sudo apt update
# sudo apt install -y python3.11 python3.11-venv python3.11-dev

python3.11 --version
```

### Clone the Repository

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/pdf-parse.git
cd pdf-parse
```

### Create Virtual Environment

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> **Note:** Docling has large dependencies (PyTorch). Installation may take 10-15 minutes depending on your hardware and internet speed.

### Create the Uploads Directory

```bash
mkdir -p uploads
```

### Create the Environment File

```bash
cat > .env << 'EOF'
SECRET_KEY=REPLACE_ME
PORT=8000
FLASK_ENV=production
EOF
```

Generate a proper secret key and update the file:

```bash
SECRET=$(python3.11 -c "import secrets; print(secrets.token_hex(32))")
sed -i "s/REPLACE_ME/$SECRET/" .env
cat .env  # verify it looks right
```

### Initialize the Database

```bash
source venv/bin/activate
python3.11 -c "from app import init_db; init_db()"
```

### Quick Smoke Test

```bash
source venv/bin/activate
source .env
gunicorn app:app --bind 127.0.0.1:8000 --workers 2 --timeout 120 &
curl -s http://localhost:8000/login | head -5
kill %1
```

You should see the beginning of the login page HTML. If so, the app is working.

---

## 5. Run the App as a System Service (bare metal only)

> **Docker users:** Skip this step. Docker Compose with `restart: unless-stopped` handles auto-start. To start on boot, enable the Docker service: `sudo systemctl enable docker`.

Create a systemd unit file so the app starts on boot and auto-restarts on failure.

```bash
sudo tee /etc/systemd/system/pdfparse.service > /dev/null << 'EOF'
[Unit]
Description=PDF Parse Application
After=network.target

[Service]
User=$USER
WorkingDirectory=$HOME/pdf-parse
EnvironmentFile=$HOME/pdf-parse/.env
ExecStart=$HOME/pdf-parse/venv/bin/gunicorn app:app --bind 127.0.0.1:8000 --workers 2 --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

> **Important:** The `$USER` and `$HOME` variables above will expand to your current user and home directory. If they don't (e.g. you're running in a non-standard shell), replace them manually with your username and home path.

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable pdfparse
sudo systemctl start pdfparse
```

Verify:

```bash
sudo systemctl status pdfparse
```

You should see `active (running)`.

---

## 6. Install Cloudflare Tunnel

### Install `cloudflared`

For Debian/Ubuntu (x86_64):

```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update
sudo apt install -y cloudflared
```

For ARM (Raspberry Pi, etc.):

```bash
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared-linux-arm64.deb
rm cloudflared-linux-arm64.deb
```

Verify:

```bash
cloudflared --version
```

---

## 7. Create and Configure the Tunnel

### Authenticate with Cloudflare

```bash
cloudflared tunnel login
```

This opens a browser window. Select the domain you want to use and authorize. A certificate is saved to `~/.cloudflared/cert.pem`.

> **If you're on a headless machine** (no browser), `cloudflared` will print a URL. Copy it, open it on any device, authorize, and the process will complete.

### Create the Tunnel

```bash
cloudflared tunnel create pdfparse
```

This outputs a tunnel ID (a UUID like `a1b2c3d4-e5f6-...`). It also creates a credentials file at:

```
~/.cloudflared/<TUNNEL_ID>.json
```

Note the tunnel ID — you need it next.

### Create the Configuration File

```bash
nano ~/.cloudflared/config.yml
```

Paste the following, replacing the placeholders:

```yaml
tunnel: pdfparse
credentials-file: /home/YOUR_USERNAME/.cloudflared/TUNNEL_ID.json

ingress:
  - hostname: pdfparse.yourdomain.com
    service: http://localhost:8000
  - service: http_status:404
```

Replace:
- `YOUR_USERNAME` with your Linux username
- `TUNNEL_ID` with the UUID from the create step
- `pdfparse.yourdomain.com` with your desired subdomain

### Add the DNS Record

This creates a CNAME record in Cloudflare pointing your subdomain to the tunnel:

```bash
cloudflared tunnel route dns pdfparse pdfparse.yourdomain.com
```

You should see: `Successfully routed DNS record pdfparse.yourdomain.com to tunnel pdfparse`.

### Test the Tunnel

```bash
cloudflared tunnel run pdfparse
```

In another terminal (or on your phone), visit `https://pdfparse.yourdomain.com`. You should see the login page with a valid HTTPS certificate.

Press `Ctrl+C` to stop the test — we'll set it up as a service next.

---

## 8. Run the Tunnel as a System Service

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

Verify:

```bash
sudo systemctl status cloudflared
```

You should see `active (running)`.

> **Note:** `cloudflared service install` automatically finds your config at `~/.cloudflared/config.yml` and copies it to `/etc/cloudflared/`. If it can't find the config, copy it manually:
> ```bash
> sudo mkdir -p /etc/cloudflared
> sudo cp ~/.cloudflared/config.yml /etc/cloudflared/
> sudo cp ~/.cloudflared/<TUNNEL_ID>.json /etc/cloudflared/
> ```
> Update the `credentials-file` path in `/etc/cloudflared/config.yml` to point to the new location.

---

## 9. Seed Your First User

**Docker:**

```bash
cd ~/pdf-parse
docker compose exec app python seed_user.py admin YourSecurePassword123
```

**Bare metal:**

```bash
cd ~/pdf-parse
source venv/bin/activate
python3.11 seed_user.py admin YourSecurePassword123
```

---

## 10. Verify Everything Works

1. Visit `https://pdfparse.yourdomain.com` — you should see the login page.
2. The HTTPS lock icon should be present (certificate issued by Cloudflare).
3. Log in with the credentials you just created.
4. Upload a test PDF and verify it processes.

### Confirm Everything Survives a Reboot

```bash
sudo reboot
```

After the machine comes back up (give it a minute), visit your subdomain again. The app (Docker container or systemd service) and the tunnel should have restarted automatically.

---

## 11. Maintenance and Updates

### Pull Latest Code and Restart

**Docker:**

```bash
cd ~/pdf-parse
git pull origin main
docker compose up -d --build
```

**Bare metal:**

```bash
cd ~/pdf-parse
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart pdfparse
```

### View Application Logs

The app writes per-request logs to daily files (one file per day, e.g. `2026-04-04.log`). It also streams to stdout, which Docker or systemd captures.

**Docker:**

```bash
docker compose logs -f                     # live stdout
cat ~/pdf-parse/data/logs/app.log          # today's log file
cat ~/pdf-parse/data/logs/2026-04-04.log   # previous day's log
```

**Bare metal:**

```bash
sudo journalctl -u pdfparse -f            # live stdout
cat ~/pdf-parse/logs/app.log               # today's log file
cat ~/pdf-parse/logs/2026-04-04.log        # previous day's log
```

**Searching logs (same for both):**

```bash
# Search for a specific user's activity
grep "user=kamil" ~/pdf-parse/data/logs/2026-04-04.log

# Find all uploads
grep "POST /api/upload" ~/pdf-parse/data/logs/app.log

# Find slow requests (>1 second)
grep -E "\([0-9]{4,}ms\)" ~/pdf-parse/data/logs/app.log

# Tunnel logs
sudo journalctl -u cloudflared -f
```

> **Note:** For bare metal, replace `data/logs/` with `logs/` in the paths above.

Each request is logged with: timestamp, user, HTTP method, path, status code, and duration:

```
2026-04-04 12:34:56 [INFO] user=kamil GET /api/schedule -> 200 (45ms)
2026-04-04 12:34:57 [INFO] user=kamil POST /api/upload -> 201 (1230ms)
2026-04-04 12:35:03 [WARNING] user=- POST /login -> 401 (8ms)
```

To clean up old logs (e.g. older than 90 days):

```bash
find ~/pdf-parse/data/logs/ -name "*.log" -mtime +90 -delete
```

### Restart Services

**Docker:**

```bash
docker compose restart                     # restart the app
sudo systemctl restart cloudflared         # restart the tunnel
```

**Bare metal:**

```bash
sudo systemctl restart pdfparse            # restart the app
sudo systemctl restart cloudflared         # restart the tunnel
```

### Check Service Health

**Docker:**

```bash
docker compose ps                          # app container status
docker compose logs --tail 20              # recent app output
sudo systemctl status cloudflared          # tunnel status
cloudflared tunnel info pdfparse           # tunnel connection status
```

**Bare metal:**

```bash
sudo systemctl status pdfparse
sudo systemctl status cloudflared
cloudflared tunnel info pdfparse           # tunnel connection status
```

---

## 12. Troubleshooting

### "502 Bad Gateway" in the browser

The tunnel is running but the app isn't responding.

**Docker:**

```bash
docker compose ps                                   # is it running?
curl http://localhost:8000/login                     # does it respond locally?
docker compose logs --tail 50                        # check for errors
```

**Bare metal:**

```bash
sudo systemctl status pdfparse                      # is it running?
curl http://localhost:8000/login                     # does it respond locally?
sudo journalctl -u pdfparse --since "5 min ago"     # check for errors
```

### "This site can't be reached"

The tunnel is not running or DNS hasn't propagated.

```bash
sudo systemctl status cloudflared       # is it running?
cloudflared tunnel info pdfparse        # is it connected?
nslookup pdfparse.yourdomain.com        # does DNS resolve?
```

### Tunnel connects but immediately disconnects

Usually a config issue. Check the config file paths:

```bash
cat /etc/cloudflared/config.yml         # correct paths?
ls -la /etc/cloudflared/*.json          # credentials file exists?
sudo journalctl -u cloudflared --since "5 min ago"
```

### App crashes during PDF processing (OOM)

If your machine has limited RAM, Docling's PyTorch models may exhaust memory. Add swap space:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### Tunnel stops working after IP change

It shouldn't — Cloudflare Tunnel uses an outbound connection, so your public IP doesn't matter. If it does stop, just restart:

```bash
sudo systemctl restart cloudflared
```

---

## Architecture Overview

**With Docker (recommended):**

```
Browser (HTTPS)
    │
    ▼
Cloudflare Edge (SSL termination, DDoS protection)
    │
    ▼ encrypted tunnel (outbound from your machine)
    │
cloudflared daemon (host)
    │
    ▼ http://localhost:8000
    │
Docker container (isolated)
    │
    gunicorn → Flask app (PDF Parse)
    │
    ├── /app/documents.db  ← mounted from host: ~/pdf-parse/data/
    ├── /app/uploads/      ← mounted from host: ~/pdf-parse/data/uploads/
    └── /app/logs/         ← mounted from host: ~/pdf-parse/data/logs/
```

**Without Docker:**

```
Browser (HTTPS)
    │
    ▼
Cloudflare Edge (SSL termination, DDoS protection)
    │
    ▼ encrypted tunnel (outbound from your machine)
    │
cloudflared daemon (host)
    │
    ▼ http://localhost:8000
    │
gunicorn → Flask app (PDF Parse)
    │
    ├── ~/pdf-parse/documents.db
    ├── ~/pdf-parse/uploads/
    └── ~/pdf-parse/logs/
```

---

## Quick Reference

### Docker deployment

| Item | Value |
|---|---|
| App directory | `~/pdf-parse` |
| Environment file | `~/pdf-parse/.env` |
| Persistent data | `~/pdf-parse/data/` |
| Database | `~/pdf-parse/data/documents.db` |
| Uploads | `~/pdf-parse/data/uploads/` |
| App logs (daily files) | `~/pdf-parse/data/logs/` |
| App logs (live) | `docker compose logs -f` |
| Restart app | `docker compose restart` |
| Rebuild app | `docker compose up -d --build` |
| Tunnel service | `cloudflared.service` |
| Tunnel config | `/etc/cloudflared/config.yml` |
| Tunnel logs | `journalctl -u cloudflared` |
| Public URL | `https://pdfparse.yourdomain.com` |

### Bare metal deployment

| Item | Value |
|---|---|
| App directory | `~/pdf-parse` |
| Virtual env | `~/pdf-parse/venv` |
| Environment file | `~/pdf-parse/.env` |
| Database | `~/pdf-parse/documents.db` |
| Uploads | `~/pdf-parse/uploads/` |
| App logs (daily files) | `~/pdf-parse/logs/` |
| App logs (live) | `journalctl -u pdfparse` |
| App service | `pdfparse.service` |
| Tunnel service | `cloudflared.service` |
| Tunnel config | `/etc/cloudflared/config.yml` |
| Tunnel logs | `journalctl -u cloudflared` |
| Public URL | `https://pdfparse.yourdomain.com` |

---

## Cost Summary

| Item | Cost |
|---|---|
| Your Linux machine | $0 (already owned) |
| Cloudflare free plan + tunnel | $0 |
| SSL certificate (Cloudflare) | $0 |
| Domain (already owned) | $0 |
| Electricity | ~$3-5/month (machine would likely be on anyway) |
| **Total** | **$0/month** |
