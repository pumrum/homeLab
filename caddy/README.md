# Caddy Reverse Proxy

> **TODO:**
> - Create git pull and compare scripts
> - Add .bashrc to github
> - Move hostnames to caddy.env

Caddy 2.10.2 running in a Proxmox LXC container, configured with the Dynu DNS provider for ACME TLS certificates, optional mTLS client authentication, and environment-variable-driven site configuration.

---

## Table of Contents

- [Install Caddy 2.10.2](#install-caddy-2102)
- [Configure Shell Aliases](#configure-shell-aliases)
- [Install Client CA for mTLS Authentication](#install-client-ca-for-mtls-authentication)
- [Init Caddy Dynu Provider](#init-caddy-dynu-provider)

---

## Install Caddy 2.10.2 to Proxmox 8

1. Connect to `https://server.domain.com:8006` and log in as an administrator.
2. In the left pane, expand **Datacenter**, click your **\<hostname\>**, then click **Shell** in the middle pane.

> ⚠️ Do not navigate away from the console until the script completes.

3. Run the community install script:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/caddy.sh)"
```

4. When prompted, work through the **Advanced Install** wizard using these settings:

| Prompt | Selection |
|---|---|
| Install type | Advanced Install |
| Container type | Unprivileged (recommended) |
| Root Password | *(leave blank)* → Next |
| Container ID | *(set your ID)* → Next |
| Hostname | *(set your hostname)* → Next |
| Disk size | 6 GB → Next |
| CPU Cores | 1 → Next |
| RAM | 512 MiB → Next |
| Network bridge | vmbr0 → Next |
| IPv4 | dhcp |
| IPv6 | disable |
| MTU size | *(skip)* → Next |
| DNS Search Domain | *(skip)* → Next |
| DNS Server IP | *(skip)* → Next |
| MAC Address | *(skip)* → Next |
| VLAN Tag | *(skip)* → Next |
| Tags | `community-scripts;webserver` → Next |
| SSH Authorized key | **manual** → Paste a single public key → **none** to skip, or paste key → OK |
| Root SSH access | Yes |
| FUSE support | No |
| TUN/TAP device | No |
| Nesting | Yes |
| GPU Passthrough | No |
| Keyctl support | Yes |
| APT Cacher-NG proxy | No |
| Time zone | America/New_York → Next |
| Container Protection | No |
| Device node creation (mknod) | No |
| Filesystem mounts | *(skip)* → Next |
| Verbose mode | Yes |
| Create the LXC | Yes |
| Write selections to config file | No |

5. When prompted, type `y` and press **Enter** to install xCaddy.

---

## Configure Shell Aliases

Add convenience aliases for common Caddy operations:

```bash
vi ~/.bashrc
```

```bash
alias validate='caddy validate --config /etc/caddy/Caddyfile'
alias format='caddy fmt --overwrite /etc/caddy/Caddyfile'
alias reload='caddy reload --config /etc/caddy/Caddyfile'
```

Save and exit.

---

## Install Client CA for mTLS Authentication

```bash
# Copy your CA certificate into place
cp ca.crt /etc/caddy/ca.crt

chown root:caddy /etc/caddy/ca.crt
chmod 640 /etc/caddy/ca.crt
```

---

## Init Caddy Dynu Provider

### 1. Build Caddy with the Dynu DNS plugin

```bash
xcaddy build --with github.com/caddy-dns/dynu
```

### 2. Swap in the new binary

```bash
systemctl stop caddy
mv /usr/bin/caddy /usr/bin/caddy.bak
mv ./caddy /usr/bin/caddy
chmod +x /usr/bin/caddy
systemctl start caddy
caddy list-modules | grep dns
```

### 3. Create the systemd override

Pull the Caddy systemd override to `/etc/systemd/system/caddy.service.d/override.conf`

Or manually:
```bash
systemctl edit caddy
```

Set the override file permissions:
```bash
chmod 600 /etc/systemd/system/caddy.service.d/override.conf
```

### 4. Create the environment file

Pull the Caddy environment file to `/etc/caddy/caddy.env`

Or manually:
```bash
vi /etc/caddy/caddy.env
```

Set the environment file ownership and permissions:
```bash
chown caddy:caddy /etc/caddy/caddy.env
chmod 600 /etc/caddy/caddy.env
```

### 5. Create the Caddyfile

Pull the Caddy configuration file to `/etc/caddy/Caddyfile`

Or manually:
```bash
vi /etc/caddy/Caddyfile
```

Set the environment file ownership and permissions:
```bash
chown root:caddy /etc/caddy/Caddyfile
chmod 640 /etc/caddy/Caddyfile
```


### 6. Apply and restart

Reload the daemon and restart the Caddy process:
```bash
systemctl daemon-reload
systemctl restart caddy
```
