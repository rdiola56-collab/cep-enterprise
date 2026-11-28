# Running CEP on your own server

This sets up CEP: ENTERPRISE on one Linux server with Docker. You end up with the web
app on HTTPS at your own address. The database, job queue and API are kept on an
internal network, and only ports 80 and 443 are open to the outside.

## 1. What you need

- A Linux server (Ubuntu 22.04/24.04 or Debian 12 are the easiest) with at least
  2 CPU cores, 4 GB of memory and 20 GB of disk. Drawing imports use more memory,
  so 8 GB is better for large DWG or IFC files.
- A domain name, such as `cep.yourcompany.com`, whose DNS record points to the
  server's public IP address. Without one you can still try CEP on a private network
  over plain HTTP (see step 3).
- Ports 80 and 443 open in the server's firewall or cloud security group.
- **In mainland China:** websites on ports 80/443 need an ICP filing (备案) for
  the domain, and Docker Hub may be slow or blocked. In that case, set a registry
  mirror that your provider offers (Alibaba Cloud, Tencent Cloud and others have
  one) in `/etc/docker/daemon.json` before step 4.

## 2. Install Docker and get the code

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER     # then log out and back in

git clone https://github.com/rdiola56-collab/cep-enterprise.git
cd cep-enterprise
```

If the server can't reach GitHub, copy `cep-enterprise.bundle` to it and run
`git clone cep-enterprise.bundle cep-enterprise` instead.

## 3. Fill in the settings

```bash
cp .env.example .env
openssl rand -hex 32     # run twice: once for CEP_SECRET_KEY, once for CEP_DB_PASSWORD
nano .env
```

Fill in at least the four required lines:

| Setting | What to put |
|---|---|
| `CEP_SITE_ADDRESS` | Your domain, e.g. `cep.yourcompany.com`. For a private-network trial with no domain, use `:80` |
| `CEP_APP_URL` | The address people open, e.g. `https://cep.yourcompany.com` (or `http://<server IP>` with `:80`) |
| `CEP_SECRET_KEY` | A long random value. Changing it later signs everyone out |
| `CEP_DB_PASSWORD` | A long random value. Set it before the first start and don't change it afterwards |

Leave email, SAML and Revit empty for now. Each stays off until you fill it in (step 7).
Keep `.env` private (`chmod 600 .env`), because it holds passwords.

## 4. Start CEP

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

The first build takes 5 to 15 minutes. After that, check that all six services
(`db`, `redis`, `backend`, `worker`, `frontend`, `caddy`) are "Up":

```bash
docker compose -f docker-compose.prod.yml ps
```

With a domain, Caddy requests the HTTPS certificate from Let's Encrypt within a
minute. That only works once DNS points at the server and ports 80 and 443 are open.

## 5. Create the first administrator

```bash
docker compose -f docker-compose.prod.yml exec backend \
  python -m app.services.users create-admin you@yourcompany.com "Your Name"
```

It asks for a password of at least 10 characters. Open your `CEP_APP_URL` and sign in.
Add other people from **Admin → Users**.

CEP starts with the standard formulas only. It has no prices, wages, productivity or
equipment rates: those are entered in the master library or per project.

## 6. Day-to-day

| Task | Command |
|---|---|
| See logs | `docker compose -f docker-compose.prod.yml logs -f backend worker` |
| Restart after changing `.env` | `docker compose -f docker-compose.prod.yml up -d` |
| Update to the latest version | `git pull && docker compose -f docker-compose.prod.yml up -d --build` (database changes are applied automatically on start) |
| Stop | `docker compose -f docker-compose.prod.yml down` (your data stays) |

**Backups.** Everything, including uploaded drawings, is in the PostgreSQL database.
A nightly dump is enough:

```bash
mkdir -p ~/cep-backups
docker compose -f docker-compose.prod.yml exec -T db pg_dump -U cep -Fc cep > ~/cep-backups/cep-$(date +%F).dump
```

To run that every night at 2 AM, add it to `crontab -e` with full paths and
`cd ~/cep-enterprise &&` in front. Copy the dumps off the server too. To restore one
into an empty database:

```bash
docker compose -f docker-compose.prod.yml exec -T db pg_restore -U cep -d cep --clean --if-exists < ~/cep-backups/cep-2026-11-28.dump
```

Never run `docker compose ... down -v`: the `-v` deletes the database.

## 7. Switching on email, sign-in and Revit

Fill in the settings in `.env`, then run `docker compose -f docker-compose.prod.yml up -d`.

**Email** (any SMTP account: Microsoft 365, Gmail/Google Workspace, Alibaba Cloud or
Tencent enterprise mail, SendGrid…): set `CEP_SMTP_HOST`, `CEP_SMTP_PORT`,
`CEP_SMTP_SECURITY`, `CEP_SMTP_USER`, `CEP_SMTP_PASSWORD` and `CEP_MAIL_FROM`. Then go
to **Admin → Users → Email** and press **Send me a test message**. If it fails, the
outbox shows the mail server's error. Gmail and Microsoft 365 usually need an
app password, not your normal password.

**SAML sign-in** (Okta, Entra ID, ADFS, Google Workspace…): set `CEP_SAML_SP_BASE_URL`
to your `CEP_APP_URL`. Give your identity provider administrator these values:

- Metadata: `https://<your CEP address>/api/auth/saml/metadata`
- Reply / ACS URL: `https://<your CEP address>/api/auth/saml/acs`
- Entity ID: `https://<your CEP address>/api/auth/saml/metadata`
- NameID or an `email` attribute holding the person's email address

They give you a metadata URL to put in `CEP_SAML_IDP_METADATA_URL`. People must already
have a CEP account with the same email, unless you set `CEP_SAML_AUTO_CREATE` (for
example to `viewer`).

**Revit import:** create an app at <https://aps.autodesk.com> (Model Derivative and Data
Management APIs) and put its client ID and secret in `CEP_APS_CLIENT_ID` and
`CEP_APS_CLIENT_SECRET`. Revit files are sent to Autodesk for conversion. The server needs
outbound internet access to `developer.api.autodesk.com`.

None of these three has been tried against a real provider yet. The admin pages and
the import screen show whether each one is switched on.

## Troubleshooting

- **The site doesn't open:** run `docker compose -f docker-compose.prod.yml logs caddy`.
  Certificate errors mean DNS doesn't point to the server yet, or ports 80/443 are closed.
- **"set CEP_... in .env" when starting:** a required setting in `.env` is empty.
- **The build fails downloading packages:** the server can't reach Docker Hub, PyPI or
  npm. Check its internet access, or set a mirror (see step 1).
- **The database password was changed after the first start:** PostgreSQL keeps the
  original one. Put the old value back in `.env`.
