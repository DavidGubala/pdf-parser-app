# Deploying PDF Parse from Your Local Linux Machine with Cloudflare Tunnel

Host the app on your own Linux computer and expose it to the internet securely through a Cloudflare Tunnel — no port forwarding, no exposed home IP, no hosting costs.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Set Up Cloudflare DNS for Your Domain](#2-set-up-cloudflare-dns-for-your-domain)
3. [Install System Dependencies](#3-install-system-dependencies)
4. [Deploy the Application](#4-deploy-the-application)
5. [Run the App as a System Service](#5-run-the-app-as-a-system-service)
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

---

## 2. Set Up Cloudflare DNS for Your Domain

If your domain's DNS is not already managed by Cloudflare:

1. Sign in to [dash.cloudflare.com](https://dash.cloudflare.com/).
2. Click **Add a site** and enter your domain.
3. Select the **Free** plan.
4. Cloudflare will scan your existing DNS records and import them.
5. Review the records — make sure your existing site records (A, CNAME, MX) are all there.
6. Cloudflare will give you two nameservers (e.g. `ada.ns.cloudflare.com`, `bob.ns.cloudflare.com`).
7. Go to your domain registrar (Namecheap, GoDaddy, etc.) and **replace the nameservers** with Cloudflare's.
8. Wait for propagation (usually 5-30 minutes, can take up to 24 hours).
9. Cloudflare will confirm the domain is active.

> **Note:** You do NOT need to add the subdomain DNS record manually. The tunnel setup in step 7 will create it automatically.

---

## 3. Install System Dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# If python3.11 is not available in default repos:
# sudo add-apt-repository ppa:deadsnakes/ppa -y
# sudo apt update
# sudo apt install -y python3.11 python3.11-venv python3.11-dev

# Verify
python3.11 --version
```

---

## 4. Deploy the Application

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

## 5. Run the App as a System Service

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

### Confirm Both Services Survive a Reboot

```bash
sudo reboot
```

After the machine comes back up (give it a minute), visit your subdomain again. Both the app and the tunnel should have restarted automatically.

---

## 11. Maintenance and Updates

### Pull Latest Code and Restart

```bash
cd ~/pdf-parse
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart pdfparse
```

### View Application Logs

The app writes per-request logs to daily files in `~/pdf-parse/logs/` (one file per day, e.g. `2026-04-04.log`). It also streams to stdout, which systemd captures.

```bash
# Live tail via systemd (stdout)
sudo journalctl -u pdfparse -f

# Today's log file
cat ~/pdf-parse/logs/app.log

# Previous day's log (rotated at midnight UTC)
cat ~/pdf-parse/logs/2026-04-04.log

# Search for a specific user's activity
grep "user=kamil" ~/pdf-parse/logs/2026-04-04.log

# Find all uploads
grep "POST /api/upload" ~/pdf-parse/logs/app.log

# Find slow requests (>1 second)
grep -E "\([0-9]{4,}ms\)" ~/pdf-parse/logs/app.log

# Tunnel logs
sudo journalctl -u cloudflared -f
```

Each request is logged with: timestamp, user, HTTP method, path, status code, and duration:

```
2026-04-04 12:34:56 [INFO] user=kamil GET /api/schedule -> 200 (45ms)
2026-04-04 12:34:57 [INFO] user=kamil POST /api/upload -> 201 (1230ms)
2026-04-04 12:35:03 [WARNING] user=- POST /login -> 401 (8ms)
```

To clean up old logs (e.g. older than 90 days):

```bash
find ~/pdf-parse/logs/ -name "*.log" -mtime +90 -delete
```

### Restart Services

```bash
sudo systemctl restart pdfparse          # restart the app
sudo systemctl restart cloudflared       # restart the tunnel
```

### Check Service Health

```bash
sudo systemctl status pdfparse
sudo systemctl status cloudflared
cloudflared tunnel info pdfparse         # tunnel connection status
```

---

## 12. Troubleshooting

### "502 Bad Gateway" in the browser

The tunnel is running but the app isn't responding.

```bash
sudo systemctl status pdfparse          # is it running?
curl http://localhost:8000/login         # does it respond locally?
sudo journalctl -u pdfparse --since "5 min ago"   # check for errors
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

```
Browser (HTTPS)
    │
    ▼
Cloudflare Edge (SSL termination, DDoS protection)
    │
    ▼ encrypted tunnel (outbound from your machine)
    │
cloudflared daemon (your Linux box)
    │
    ▼ http://localhost:8000
    │
gunicorn → Flask app (PDF Parse)
    │
    ├── SQLite database (documents.db)
    └── uploads/ directory (PDFs)
```

---

## Quick Reference

| Item | Value |
|---|---|
| App directory | `~/pdf-parse` |
| Virtual env | `~/pdf-parse/venv` |
| Environment file | `~/pdf-parse/.env` |
| App service | `pdfparse.service` |
| Tunnel service | `cloudflared.service` |
| Tunnel config | `/etc/cloudflared/config.yml` |
| App logs (daily files) | `~/pdf-parse/logs/` |
| App logs (live) | `journalctl -u pdfparse` |
| Tunnel logs | `journalctl -u cloudflared` |
| Database | `~/pdf-parse/documents.db` |
| Uploads | `~/pdf-parse/uploads/` |
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
