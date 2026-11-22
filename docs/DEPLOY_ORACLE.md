# Deploy for free on Oracle Cloud

This puts the whole app (website, API, scanner, database) on one free, always-on Oracle Cloud VM, with free HTTPS.
After that you can open it on your phone, install it to the home screen, and get alerts.

It takes about 30–45 minutes the first time. You need:

- an Oracle Cloud account,
- a free DuckDNS address, and
- a computer with a terminal (macOS, Linux, or Windows PowerShell) for the SSH step.

> Free-tier terms change. Check Oracle's current "Always Free" terms when you sign up. Oracle asks for a card to
> verify your identity; Always Free resources are not charged.

---

## 1. Create the Oracle Cloud account

1. Sign up at <https://www.oracle.com/cloud/free/>.
2. Pick a **home region** close to you. Always Free resources only exist in your home region, and you can't change it later.

## 2. Create the virtual machine

In the Oracle console: **☰ → Compute → Instances → Create instance**.

1. **Name:** `signal-lab`.
2. **Image:** click *Change image* and choose **Canonical Ubuntu 24.04**.
3. **Shape:** click *Change shape*, choose **Ampere**, then **VM.Standard.A1.Flex**. Set **2 OCPUs and 12 GB memory**,
   which is well inside the free allowance.
   - If you get "Out of capacity", try another *availability domain* or try again later.
   - You can also use the AMD **VM.Standard.E2.1.Micro**. It is also free but has only 1 GB of memory; the setup
     script adds swap automatically so it still works, just slower.
4. **Networking:** keep the defaults and make sure **Assign a public IPv4 address** is on.
5. **SSH keys:** choose *Generate a key pair* and **download the private key**. You need it to log in.
6. Click **Create**. When the instance shows *Running*, copy its **Public IP address**.

## 3. Open ports 80 and 443 in Oracle's firewall

On the instance page, click the **Subnet** link, then the **Default Security List**, then **Add Ingress Rules**.
Add two rules:

| Source CIDR | IP protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

The setup script opens the same ports in the VM's own firewall.

## 4. Get a free web address

1. Go to <https://www.duckdns.org> and sign in.
2. Create a subdomain, for example `mysignals`, which gives you `mysignals.duckdns.org`.
3. Set its IP to your VM's **public IP** and click *update ip*.

## 5. Log in to the VM

On your computer, in the folder where you saved the key:

```bash
chmod 600 ssh-key-*.key                    # macOS / Linux only
ssh -i ssh-key-*.key ubuntu@YOUR_PUBLIC_IP
```

## 6. Install the app

On the VM:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Servalo003/blank-app.git signal-lab
cd signal-lab
sudo deploy/setup-oracle.sh mysignals.duckdns.org
```

> If the repository is **private**, `git clone` will ask for a username and password. Use your GitHub username, and
> for the password a [personal access token](https://github.com/settings/tokens?type=beta) with *read-only Contents*
> access to this one repository.

The script:

1. adds swap on small machines,
2. installs Docker,
3. opens ports 80 and 443,
4. creates `.env` with new random secrets,
5. generates the Web Push keys,
6. builds and starts everything with automatic HTTPS (Caddy and Let's Encrypt), and
7. schedules a nightly database backup.

At the end it prints your **access token**. Copy it somewhere safe; it's the password to your app.

## 7. Open it on your phone

1. Open `https://mysignals.duckdns.org` and paste the access token.
2. **Install it:**
   - **Android (Chrome):** menu, then *Add to Home screen* / *Install app*.
   - **iPhone (Safari):** Share, then *Add to Home Screen*. On iPhone, push notifications only work from the
     installed app.
3. Open the installed app, then **Settings → Phone alerts → Enable here**, then **Send test**.

## 8. Telegram alerts (optional)

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the **bot token**.
2. Send any message to your new bot, then open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and copy the `"chat":{"id":…}` number.
3. On the VM, add both values to `.env`:
   ```bash
   cd ~/signal-lab
   sudo nano .env        # set TELEGRAM_BOT_TOKEN=… and TELEGRAM_CHAT_ID=…
   sudo deploy/update.sh
   ```
4. In the app, tap **Settings → Send test**.

## 9. First things to do in the app

1. **Settings → MT5 contracts → Use MT5 specs for all my markets.** First compare a couple of them with the
   *Specification* window in your MT5 terminal.
2. **Research → Test all.** This takes about 5–15 minutes and runs in the background. A strategy has to pass on a market
   before that market can send paper alerts.
3. Add or remove markets under **Markets → Add market**.

---

## Day-to-day

| Task | Command (on the VM, in `~/signal-lab`) |
|---|---|
| Update to the latest version | `sudo deploy/update.sh` |
| See logs | `sudo docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs -f --tail 100 api worker` |
| Restart everything | `sudo docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml restart` |
| Back up the database now | `sudo deploy/backup.sh` (also runs nightly; keeps 14 copies in `backups/`) |
| Show your access token | `sudo grep APP_ACCESS_TOKEN .env` |

## Troubleshooting

- **The site doesn't load and the setup script says it isn't answering.**
  - Check that DuckDNS points to the VM's public IP.
  - Check that step 3 (the ingress rules) is done.
  - Then look at the Caddy logs:
    `sudo docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs caddy`.
- **"Scanner is not running" in the app.** Check the worker logs, using the command in the table above.
- **Out of memory on the 1 GB micro VM.** Use the Ampere A1 shape if you can get it.
- **Change the access token.** Edit `APP_ACCESS_TOKEN` in `.env` (24 characters or more), then run `sudo deploy/update.sh`.

## Security notes

- Only ports 22 (SSH), 80 and 443 are open. The database and Redis are not reachable from the internet.
- All secrets live in `.env` on the VM, readable by root only. Never commit it.
- The app never places trades and holds no Deriv credentials.
