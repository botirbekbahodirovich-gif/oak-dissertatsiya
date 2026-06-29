# Deploy Olimlar.uz to Google Compute Engine (native: Gunicorn + systemd + nginx + certbot)

Target VM: **olimlar-uz-server** · zone **europe-west3-c** · IP **34.141.70.133** · domain **olimlar.uz**

`deploy/deploy.sh` is idempotent and performs steps 2–5 + 7 in one run. Secrets are
passed as environment variables and written to `/opt/olimlar-uz/.env` on the VM —
they are **never** committed to git.

---

## 1. Point DNS first (step 6 — do this before TLS)

At your domain registrar / Cloud DNS, create:

| Type | Name        | Value           |
|------|-------------|-----------------|
| A    | `olimlar.uz`     | `34.141.70.133` |
| A    | `www.olimlar.uz` | `34.141.70.133` |

Verify (wait for propagation): `dig +short olimlar.uz` → `34.141.70.133`.

## 2. Open the firewall (once, from your machine)

```bash
gcloud compute firewall-rules create allow-web \
  --allow tcp:80,tcp:443 --target-tags http-server --zone europe-west3-c || true
gcloud compute instances add-tags olimlar-uz-server \
  --tags http-server,https-server --zone europe-west3-c
```

## 3. Connect to the VM (step 1)

```bash
gcloud compute ssh olimlar-uz-server --zone=europe-west3-c
```

## 4. Run the deploy (steps 2–5, 7)

On the VM:

```bash
# Get the script (clone once; the script keeps the repo updated afterwards)
sudo apt-get update -y && sudo apt-get install -y git
git clone --depth 1 https://github.com/botirbekbahodirovich-gif/oak-dissertatsiya.git /tmp/olimlar
cd /tmp/olimlar

# Run it. Pass your real DATABASE_URL; SESSION_SECRET auto-generates if omitted.
sudo -E env \
  DATABASE_URL='postgresql://neondb_owner:YOUR_PASSWORD@ep-silent-tooth-algm2fks-pooler.c-3.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require' \
  DOMAIN='olimlar.uz' \
  CERTBOT_EMAIL='you@example.com' \
  bash deploy/deploy.sh
```

The script will:
- install Python (3.12 on Ubuntu 24.04; system python3 ≥3.10 also runs the app),
  pip, git, nginx, certbot, libpq-dev, build-essential;
- clone the app to `/opt/olimlar-uz`, create a venv, `pip install -r requirements.txt` + gunicorn;
- write `/opt/olimlar-uz/.env` (chmod 600);
- create + start the `olimlar` systemd service (Gunicorn on `127.0.0.1:8000`, auto-restart);
- configure nginx (`:80` → Gunicorn, `client_max_body_size 250M`, static served directly);
- run certbot for HTTPS **if** `olimlar.uz` already resolves to `34.141.70.133` (otherwise it prints the one command to run after DNS propagates);
- smoke-test `/login /journals /universities /` and print status.

## 5. Verify (step 7)

```bash
systemctl status olimlar          # should be active (running)
journalctl -u olimlar -n 50       # app logs
curl -I http://olimlar.uz/login   # 200/302 via nginx
curl -I https://olimlar.uz/       # 200 after certbot
```
Then open in a browser and check: homepage, `/universities` + a university profile,
a researcher page `/olim/<name>`, `/journals`, search on `/dashboard`, `/stats`.

## Common operations

```bash
# Deploy a new version (pull + restart)
cd /opt/olimlar-uz && sudo git pull && sudo systemctl restart olimlar

# Re-run TLS if DNS wasn't ready the first time
sudo certbot --nginx -d olimlar.uz -d www.olimlar.uz --agree-tos -m you@example.com --redirect

# Edit secrets
sudo nano /opt/olimlar-uz/.env && sudo systemctl restart olimlar
```

## Notes
- The app reads `.env` via python-dotenv at startup; `DATABASE_URL` auto-gets
  `sslmode=require` enforced for Neon, and connections use timeouts + keepalives.
- This native setup replaces the Docker path; you do **not** need `Dockerfile`/
  `nginx.conf` (those remain for a container-based deploy if you prefer it).
- Pick the VM region close to your Neon region (eu-central) to keep DB latency low —
  `europe-west3` (Frankfurt) is a good match.
