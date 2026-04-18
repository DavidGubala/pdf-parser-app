# Deploying PDF Parse on Oracle Cloud Free Tier

This guide walks you through setting up an always-free Oracle Cloud VM, deploying the PDF Parse application, and pointing a subdomain of your existing domain to it.

---

## Table of Contents

1. [Create an Oracle Cloud Account](#1-create-an-oracle-cloud-account)
2. [Launch an Always-Free VM Instance](#2-launch-an-always-free-vm-instance)
3. [Configure Networking (Open Ports)](#3-configure-networking-open-ports)
4. [Connect to Your VM via SSH](#4-connect-to-your-vm-via-ssh)
5. [Install Dependencies on the VM](#5-install-dependencies-on-the-vm)
6. [Deploy the Application](#6-deploy-the-application)
7. [Set Up Caddy as a Reverse Proxy (HTTPS)](#7-set-up-caddy-as-a-reverse-proxy-https)
8. [Run the App as a System Service](#8-run-the-app-as-a-system-service)
9. [Point a Subdomain to Your VM](#9-point-a-subdomain-to-your-vm)
10. [Seed Your First User](#10-seed-your-first-user)
11. [Maintenance and Updates](#11-maintenance-and-updates)

---

## 1. Create an Oracle Cloud Account

1. Go to [cloud.oracle.com](https://cloud.oracle.com/) and click **Start for free**.
2. Sign up with your email. You will need:
   - A valid email address
   - A phone number for verification
   - A credit/debit card for identity verification (you will **not** be charged)
3. Choose your **Home Region** (pick the one closest to you geographically — this cannot be changed later).
4. Once your account is provisioned (usually instant), sign into the Oracle Cloud Console.

> **Important:** The Always Free tier is permanent and does not expire. Your card will not be charged unless you manually upgrade to a paid account.

---

## 2. Launch an Always-Free VM Instance

### Navigate to Compute

1. In the Oracle Cloud Console, click the hamburger menu (top-left) > **Compute** > **Instances**.
2. Click **Create Instance**.

### Configure the Instance

| Setting | Value |
|---|---|
| **Name** | `pdf-parse` (or any name you like) |
| **Compartment** | Leave as default (root) |
| **Availability Domain** | Any available |
| **Image** | **Canonical Ubuntu 22.04** (click *Change image* if needed) |
| **Shape** | Click *Change shape* > **Ampere** > **VM.Standard.A1.Flex** |
| **OCPUs** | **1** (Always Free allows up to 4, but 1 is plenty) |
| **Memory** | **6 GB** (Always Free allows up to 24 GB total across instances) |

### SSH Key

Under **Add SSH keys**, choose one of:
- **Generate a key pair** — download both the private and public key files. Keep the private key safe.
- **Paste public keys** — if you already have an SSH key (`~/.ssh/id_rsa.pub`), paste it here.

### Networking

Leave the defaults (a new VCN and public subnet will be created).

### Boot Volume

Leave default (47 GB). This is within the Always Free allowance.

### Create

Click **Create**. The instance will provision in 1-2 minutes. Note the **Public IP address** once it appears.

---

## 3. Configure Networking (Open Ports)

By default, Oracle Cloud blocks all inbound traffic except SSH (port 22). You need to open ports 80 (HTTP) and 443 (HTTPS).

### Security List (Oracle Cloud firewall)

1. Go to **Networking** > **Virtual Cloud Networks** > click your VCN.
2. Click the **Public Subnet**.
3. Click the **Default Security List**.
4. Click **Add Ingress Rules** and add two rules:

| Source CIDR | Protocol | Dest Port Range | Description |
|---|---|---|---|
| `0.0.0.0/0` | TCP | `80` | HTTP |
| `0.0.0.0/0` | TCP | `443` | HTTPS |

### OS Firewall (iptables)

Oracle's Ubuntu images also have iptables rules. You will open these ports after SSH-ing in (next step).

---

## 4. Connect to Your VM via SSH

From your local machine (PowerShell, Terminal, or Git Bash):

```bash
ssh -i /path/to/your-private-key ubuntu@<YOUR_VM_PUBLIC_IP>
```

Replace `<YOUR_VM_PUBLIC_IP>` with the IP from step 2.

If you get a permissions error on the key file:

```bash
# Linux/Mac
chmod 600 /path/to/your-private-key

# Windows PowerShell
icacls "C:\path\to\your-private-key" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

### Open OS Firewall Ports

Once connected, run:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

---

## 5. Install Dependencies on the VM

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install Python 3.11, pip, venv, and git
sudo apt install -y python3.11 python3.11-venv python3.11-dev git

# Verify
python3.11 --version
```

> If `python3.11` is not available in the default repos:
> ```bash
> sudo add-apt-repository ppa:deadsnakes/ppa -y
> sudo apt update
> sudo apt install -y python3.11 python3.11-venv python3.11-dev
> ```

### Install Caddy (reverse proxy for HTTPS)

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

---

## 6. Deploy the Application

### Clone the Repository

```bash
cd /home/ubuntu
git clone https://github.com/YOUR_USERNAME/pdf-parse.git
cd pdf-parse
```

### Create Virtual Environment and Install Packages

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> **Note:** Docling can take several minutes to install as it has large dependencies (PyTorch, etc.). On the 1-OCPU ARM instance, this may take 10-15 minutes. Be patient.

### Create Required Directories

```bash
mkdir -p uploads
```

### Set Environment Variables

Create a `.env` file (this stays on the server, never committed):

```bash
cat > .env << 'EOF'
SECRET_KEY=replace-with-a-long-random-string
PORT=8000
FLASK_ENV=production
EOF
```

Generate a good secret key:

```bash
python3.11 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the output and paste it as the `SECRET_KEY` value in `.env`.

### Initialize the Database

```bash
source venv/bin/activate
python3.11 -c "from app import init_db; init_db()"
```

---

## 7. Set Up Caddy as a Reverse Proxy (HTTPS)

Caddy automatically provisions and renews Let's Encrypt SSL certificates.

Edit the Caddy configuration:

```bash
sudo nano /etc/caddy/Caddyfile
```

Replace the entire contents with:

```
pdfparse.yourdomain.com {
    reverse_proxy localhost:8000
    encode gzip
}
```

Replace `pdfparse.yourdomain.com` with your actual subdomain.

Restart Caddy:

```bash
sudo systemctl restart caddy
sudo systemctl enable caddy
```

Caddy will automatically obtain an SSL certificate once the DNS is pointed (step 9).

---

## 8. Run the App as a System Service

Create a systemd service so the app starts automatically and restarts on failure.

```bash
sudo nano /etc/systemd/system/pdfparse.service
```

Paste the following:

```ini
[Unit]
Description=PDF Parse Application
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/pdf-parse
EnvironmentFile=/home/ubuntu/pdf-parse/.env
ExecStart=/home/ubuntu/pdf-parse/venv/bin/gunicorn app:app --bind 127.0.0.1:8000 --workers 2 --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable pdfparse
sudo systemctl start pdfparse
```

### Verify It Is Running

```bash
sudo systemctl status pdfparse
```

You should see `active (running)`. Test locally on the VM:

```bash
curl http://localhost:8000/login
```

You should see the HTML of the login page.

---

## 9. Point a Subdomain to Your VM

Go to your domain registrar or DNS provider (Cloudflare, Namecheap, GoDaddy, etc.) and add a DNS record:

| Type | Name | Value | TTL |
|---|---|---|---|
| **A** | `pdfparse` | `<YOUR_VM_PUBLIC_IP>` | Auto or 300 |

This creates `pdfparse.yourdomain.com` pointing to your Oracle VM.

### Wait for DNS Propagation

DNS changes typically take 1-15 minutes. You can check propagation at [dnschecker.org](https://dnschecker.org/).

### Verify HTTPS

Once DNS propagates, Caddy will automatically obtain an SSL certificate. Visit:

```
https://pdfparse.yourdomain.com
```

You should see the PDF Parse login page with a valid HTTPS lock.

---

## 10. Seed Your First User

```bash
cd /home/ubuntu/pdf-parse
source venv/bin/activate
python3.11 seed_user.py admin YourSecurePassword123
```

Now log in at `https://pdfparse.yourdomain.com` with those credentials.

---

## 11. Maintenance and Updates

### Pull Latest Code and Restart

```bash
cd /home/ubuntu/pdf-parse
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart pdfparse
```

### View Application Logs

```bash
sudo journalctl -u pdfparse -f
```

### View Caddy Logs

```bash
sudo journalctl -u caddy -f
```

### Restart Services

```bash
sudo systemctl restart pdfparse   # restart the app
sudo systemctl restart caddy      # restart the reverse proxy
```

### System Updates

Run periodically to keep the VM secure:

```bash
sudo apt update && sudo apt upgrade -y
```

---

## Quick Reference

| Item | Value |
|---|---|
| App directory | `/home/ubuntu/pdf-parse` |
| Virtual env | `/home/ubuntu/pdf-parse/venv` |
| Environment file | `/home/ubuntu/pdf-parse/.env` |
| Systemd service | `pdfparse.service` |
| Caddy config | `/etc/caddy/Caddyfile` |
| App logs | `journalctl -u pdfparse` |
| Caddy logs | `journalctl -u caddy` |
| Database | `/home/ubuntu/pdf-parse/documents.db` |
| Uploads | `/home/ubuntu/pdf-parse/uploads/` |
| Public URL | `https://pdfparse.yourdomain.com` |

---

## Cost Summary

| Resource | Cost |
|---|---|
| Oracle Cloud VM (A1.Flex, 1 OCPU, 6 GB RAM) | **$0/month** (Always Free) |
| Boot volume (47 GB) | **$0/month** (Always Free) |
| SSL Certificate (Let's Encrypt via Caddy) | **$0** |
| Subdomain DNS record | **$0** (uses your existing domain) |
| **Total** | **$0/month** |
