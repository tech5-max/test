# fail2ban setup for api.ozak.dev (nginx) — stop HTTP floods

This bans an IP at the firewall after it floods the server, so the attacker is cut off (connections dropped) while real users keep working. Run the two flood phases from `test/ddos-resilience` before and after this to show the difference.

> ⚠️ **Do step 0 first or you can lock yourself out of SSH.**

---

## 0. Whitelist yourself
Find your own IP (from the machine you SSH from): `curl -s https://ifconfig.me`. Keep it; it goes in `ignoreip` below. Localhost and your office/VPN IP must never be banned.

## 1. Make nginx log the REAL client IP
fail2ban bans the IP nginx writes as `$remote_addr`. Check what that is:
```bash
tail -n 3 /var/log/nginx/api.ozak.dev.access.log
```
- **If the first field is a real client IP** (your test machine when you hit it): nginx is the edge — nothing to do. The nginx block you shared terminates TLS directly, so this is the likely case.
- **If it's a Cloudflare / proxy IP**, nginx is behind a CDN. Then `$remote_addr` is the CDN, and a naive jail would ban the CDN. Fix it so logs show the true client — add inside the `server { }` for api.ozak.dev:
  ```nginx
  # Cloudflare real IP (refresh ranges from https://www.cloudflare.com/ips/)
  set_real_ip_from 173.245.48.0/20;
  set_real_ip_from 103.21.244.0/22;
  # … all Cloudflare ranges …
  real_ip_header CF-Connecting-IP;
  real_ip_recursive on;
  ```
  Behind Cloudflare you should **also** rate-limit at Cloudflare (WAF / rate-limiting rules), because origin fail2ban only helps for traffic that reaches the origin.

## 2. Add nginx rate limiting (first line of defense + gives fail2ban a signal)
In `http { }` (e.g. `/etc/nginx/nginx.conf`):
```nginx
limit_req_zone $binary_remote_addr zone=api_rl:10m rate=30r/s;
limit_req_status 429;
```
In the `location / { }` of the api.ozak.dev server, at the top:
```nginx
limit_req zone=api_rl burst=60 nodelay;
```
Then:
```bash
nginx -t && systemctl reload nginx
```
Now a flood gets `429`s, and each one writes a `limiting requests` line to the nginx **error** log — which fail2ban watches.

## 3. Install fail2ban
```bash
sudo apt update && sudo apt install -y fail2ban
```

## 4. Configure the jail
Create `/etc/fail2ban/jail.local`:
```ini
[DEFAULT]
# NEVER ban these. Add every admin / SSH / office IP here.
ignoreip = 127.0.0.1/8 ::1 YOUR.SSH.IP.HERE
bantime  = 1h          ; how long a banned IP stays blocked
findtime = 1m          ; window to count hits in
maxretry = 100         ; hits within findtime before a ban
backend  = auto

# Bans IPs that trip nginx limit_req (the flood signal from step 2)
[nginx-limit-req]
enabled  = true
port     = http,https
filter   = nginx-limit-req
logpath  = /var/log/nginx/api.ozak.dev.error.log
findtime = 1m
maxretry = 20          ; 20 rate-limit hits in a minute → ban
bantime  = 1h

# Optional: also ban on raw request volume from the access log
[nginx-flood]
enabled  = true
port     = http,https
filter   = nginx-flood
logpath  = /var/log/nginx/api.ozak.dev.access.log
findtime = 10s
maxretry = 300         ; 300 requests in 10s from one IP → ban
bantime  = 1h
```
Create the custom filter `/etc/fail2ban/filter.d/nginx-flood.conf` (matches any access-log line, counted per IP by fail2ban):
```ini
[Definition]
failregex = ^<HOST> -.*"(GET|POST|HEAD).*"
ignoreregex =
```
`nginx-limit-req` already ships with fail2ban, so no filter file is needed for it.

## 5. Start and verify
```bash
sudo systemctl enable --now fail2ban
sudo systemctl restart fail2ban
sudo fail2ban-client status                 # lists active jails
sudo fail2ban-client status nginx-limit-req # shows currently-banned IPs
sudo tail -f /var/log/fail2ban.log          # watch bans happen live
```

## 6. Run the before/after test (from a SEPARATE machine)
```bash
cd test/ddos-resilience
cp .env.example .env     # set TARGET_BASE_URL=https://api.ozak.dev

# Phase 1 — fail2ban OFF (sudo systemctl stop fail2ban on the server):
PHASE_LABEL=without-fail2ban ./run.sh

# Phase 2 — fail2ban ON (sudo systemctl start fail2ban on the server):
PHASE_LABEL=with-fail2ban ./run.sh

# Combine into one before/after report:
./run.sh --combine
```
The report shows, per phase: requests sent, requests the server accepted, and after how many requests / seconds fail2ban blocked us. With fail2ban on you'll see the connection start getting dropped (blocked) after the threshold.

## 7. Operate
```bash
sudo fail2ban-client status nginx-flood                  # who is banned
sudo fail2ban-client set nginx-flood unbanip 1.2.3.4     # manual unban
sudo fail2ban-client set nginx-flood banip 1.2.3.4       # manual ban
```
Tune `maxretry` / `findtime` / `bantime` to your real traffic: too strict bans real users, too loose lets floods through. Start with the values above and adjust from the report.

## Notes
- fail2ban bans at the firewall (nftables/iptables). While banned, the attacker's TCP connections are **dropped** — they get no response at all, which is what the test reports as "blocked".
- It bans by one real IP. A flood from thousands of real IPs (a true botnet) needs upstream/Cloudflare/provider DDoS protection too; fail2ban handles single-source and small-scale abuse, which is the common case.
