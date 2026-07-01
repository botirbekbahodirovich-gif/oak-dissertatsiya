# Deploy Olimlar.uz to a Google Compute Engine VM

Native production stack: **Gunicorn + systemd + nginx + certbot (Let's Encrypt)**.

This folder has three files — copy the whole folder to the VM:

| File              | Purpose                                                        |
|-------------------|---------------------------------------------------------------|
| `deploy.sh`       | One-shot installer. Edit the CONFIG block, then run it.        |
| `olimlar.service` | systemd unit for Gunicorn (installed automatically).          |
| `olimlar.uz.conf` | nginx reverse-proxy config (installed automatically).         |

`deploy.sh` reads the other two files from the same folder, so keep them together.

---

## 1. Point DNS first (needed for HTTPS)

At your DNS provider create A records for `olimlar.uz` and `www.olimlar.uz`
pointing at your VM's external IP. Verify: `dig +short olimlar.uz`.

(If DNS isn't ready yet the script still finishes — it just skips TLS and prints
the one certbot command to run later.)

## 2. Open the firewall (once, from your machine)

```bash
gcloud compute firewall-rules create allow-web \
  --allow tcp:80,tcp:443 --target-tags http-server,https-server || true
gcloud compute instances add-tags YOUR_VM_NAME \
  --tags http-server,https-server --zone YOUR_ZONE
```

## 3. SSH in and copy the deploy folder

```bash
gcloud compute ssh YOUR_VM_NAME --zone=YOUR_ZONE
# then, from your laptop, copy the folder up:
gcloud compute scp --recurse deploy YOUR_VM_NAME:~/ --zone=YOUR_ZONE
```

## 4. Edit config and run

On the VM:

```bash
cd ~/deploy
nano deploy.sh        # fill in REPO_URL, DATABASE_URL, SESSION_SECRET, DOMAIN, CERTBOT_EMAIL
sudo bash deploy.sh
```

The script will:
- install Python 3.11 (via deadsnakes if needed), pip, git, nginx, certbot, libpq-dev, build-essential;
- clone your repo to `/opt/olimlar-uz`, create a venv, install `requirements.txt` + gunicorn;
- write `/opt/olimlar-uz/.env` (chmod 600) with your `DATABASE_URL` and `SESSION_SECRET`;
- install + start the `olimlar` systemd service (Gunicorn on `127.0.0.1:8000`, auto-restart);
- install the nginx site (`:80` → Gunicorn, static served directly, 250M uploads);
- run certbot for HTTPS (with HTTP→HTTPS redirect);
- smoke-test `/` and `/login`.

It is **idempotent** — re-run any time to pull the latest code and restart.

## 5. Verify

```bash
systemctl status olimlar          # active (running)
journalctl -u olimlar -n 50       # app logs
curl -I http://127.0.0.1:8000/    # backend up
curl -I https://olimlar.uz/       # 200 after certbot
```

## Common operations

```bash
# Deploy a new version
sudo bash ~/deploy/deploy.sh

# Re-run TLS if DNS wasn't ready the first time
sudo certbot --nginx -d olimlar.uz -d www.olimlar.uz --agree-tos -m you@example.com --redirect

# Edit secrets, then restart
sudo nano /opt/olimlar-uz/.env && sudo systemctl restart olimlar
```
