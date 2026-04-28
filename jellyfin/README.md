# Jellyfin

Jellyfin 10.11 running in a Proxmox LXC container, mounted to a remote media server over SSHFS, with TheTVDB and Webhook plugins configured for metadata and Home Assistant playback notifications.

Latest tested version: 10.11

> **TODO:**
> * Update to use media CNAME
> * Confirm mp4 playback in browser without transcoding

---

## Table of Contents

* [Install Jellyfin](#install-jellyfin)
* [Init Jellyfin](#init-jellyfin)
* [Init Jellyfin Admin User](#init-jellyfin-admin-user)
* [Init Media Connection](#init-media-connection)
* [Configure Jellyfin Server](#configure-jellyfin-server)
* [Install Jellyfin Plugins](#install-jellyfin-plugins)
* [Configure Jellyfin Plugins](#configure-jellyfin-plugins)
* [Configure Jellyfin Media Libraries](#configure-jellyfin-media-libraries)

---

## Install Jellyfin

1. Connect to `https://server.domain.com:8006` and log in as an administrator.
2. In the left pane, expand **Datacenter**, click your **\<hostname\>**, then click **Shell** in the middle pane.

> ⚠️ Do not navigate away from the console until the script completes.

3. Run the community install script:

```
bash -c "$(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/ct/jellyfin.sh)"
```

4. When prompted, work through the **Advanced Install** wizard using these settings:

| Prompt | Selection |
| --- | --- |
| Install type | Advanced Install |
| Container type | Unprivileged (recommended) |
| Root password | *(set password)* → Next |
| Confirm root password | *(confirm password)* → Next |
| Container ID | *(accept default)* → Next |
| Hostname | `<hostname>` → Next |
| Disk size | 50 GB → Next |
| CPU Cores | 2 → Next |
| RAM | 8192 MiB → Next |
| Network bridge | vmbr0 → Next |
| IPv4 | dhcp |
| IPv6 | disable |
| MTU size | *(skip)* → Next |
| DNS Search Domain | *(skip)* → Next |
| DNS Server IP | *(skip)* → Next |
| MAC Address | *(skip)* → Next |
| VLAN Tag | *(skip)* → Next |
| Tags | `community-scripts;media` → Next |
| SSH Authorized key | none |
| Root SSH access | Yes |
| FUSE support | Yes |
| TUN/TAP device | No |
| Nesting | Yes |
| GPU Passthrough | Yes |
| Keyctl support | Yes |
| APT Cacher-NG proxy | No |
| Time zone | America/New_York → Next |
| Container Protection | No |
| Device node creation (mknod) | No |
| Filesystem mounts | `fuse` → Next |
| Verbose mode | Yes |
| Create the LXC | Yes |
| Write selections to config file | No |

5. When prompted, type `1` and press **Enter** to configure the embedded GPU.

---

## Init Jellyfin

Navigate to the setup wizard and complete the following:

```
http://server.domain.com:8096/web/index.html#!/wizardstart.html
```

| Prompt | Value |
| --- | --- |
| Server name | `<hostname>` |
| Preferred display language | English |
| Admin username | *(set username)* |
| Admin password | *(set password)* |
| Media library | *(skip)* |
| Metadata language | English |
| Metadata country/region | United States |
| Allow remote connections | ✅ Checked |

Click **Finish** to complete the wizard.

---

## Init Jellyfin Admin User

Navigate to `http://server.domain.com:8096` and log in as the admin user.

### Change the admin profile icon

1. Click the profile icon at the top right → **Profile**.
2. Click **Add Image**, select `profile_admin.png`, then click **Open**.

### Set the background color

1. Click the profile icon at the top right → **Display**.
2. Append the following to the **Custom CSS code** field:

```css
.backgroundContainer {
    background-color: #4a1515 !important;
}
body, html {
    background-color: #4a1515 !important;
}
```

3. Click **Save**.

---

## Init Media Connection

### On the Jellyfin console (as root)

Set up SSH keys for the media connection:

```bash
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
vi /root/.ssh/authorized_keys          # paste in any user keys, save and exit
ssh-keygen -t ed25519 -f /root/.ssh/jelly-<site>_<hostname>
                                       # press Enter twice to skip passphrase
```

### On the media server (as root)

Create the Jellyfin media user and authorize the key:

```bash
useradd -M -N -g media -s /usr/sbin/nologin jelly-<site>
touch /etc/ssh/authorized_keys/jelly-<site>
chmod 644 /etc/ssh/authorized_keys/jelly-<site>
vi /root/.ssh/authorized_keys          # paste in the Jellyfin public key, save and exit
```

### Back on the Jellyfin console (as root)

Mount the remote media share over SSHFS:

```bash
mkdir /mnt/media
chown root:jellyfin /mnt/media
apt install -y sshfs
sshfs jelly-<site>@<hostname>-media.domain.com:/ /mnt/media \
  -o IdentityFile=/root/.ssh/jelly-<site>_jelly<site>
# type 'yes' and press Enter to trust the fingerprint
```

Verify the contents of `/mnt/media`, then persist the mount in `/etc/fstab`:

```bash
vi /etc/fstab
```

Append the following line (update the GID to match the `jellyfin` group):

```
jelly-<site>@<hostname>-media.domain.com:/ /mnt/media fuse.sshfs _netdev,delay_connect,user,identityfile=/root/.ssh/jelly-<site>_<hostname>,allow_other,default_permissions,gid=118 0 0
```

> ⚠️ Update the `gid` value to match the actual GID of the `jellyfin` group on the system.

---

## Configure Jellyfin Server

Navigate to `http://server.domain.com:8096`, log in as the admin user, then click **Menu** > **Dashboard**.

### General

| Setting | Value |
| --- | --- |
| Server name | `<hostname>` |
| Preferred display language | English |
| Cache path | *(blank)* |
| Metadata path | *(blank)* |
| Enable Quick Connect | ❌ Unchecked |
| Login disclaimer | *(blank)* |
| Custom CSS code | *(blank)* |
| Enable the splash screen | ❌ Unchecked |
| Parallel library scan tasks limit | 0 |
| Parallel image encoding limit | 0 |

### Branding

| Setting | Value |
| --- | --- |
| Enable the splash screen image | ❌ Disabled |
| Login disclaimer | *(blank)* |
| Custom CSS code | *(blank)* |

### Users

1. For the admin user, click **...** > **Edit user**.
2. Check **Hide this user from login screen**.
3. Click **Save**.

### Libraries > Display

| Setting | Value |
| --- | --- |
| Date added behavior for new content | Use file creation date |
| Display a folder view | ❌ Unchecked |
| Display specials within seasons they aired in | ❌ Unchecked |
| Group movies into collections | ❌ Unchecked |
| Group shows into collections | ❌ Unchecked |
| Enable external content in suggestions | ✅ Checked |

### Libraries > Metadata

| Setting | Value |
| --- | --- |
| Language | English |
| Country/Region | United States |
| Interval | 0 |
| Resolution | Match Source |

### Libraries > NFO Settings

| Setting | Value |
| --- | --- |
| Save user watch data to NFO files for | None |
| Save image paths within NFO files | ✅ Checked |
| Enable path substitution | ✅ Checked |
| Copy extrafanart to extrathumbs field | ❌ Unchecked |

### Playback > Transcoding

| Setting | Value |
| --- | --- |
| Hardware acceleration | None |
| Allow encoding in HEVC format | ❌ Unchecked |
| Allow encoding in AV1 format | ❌ Unchecked |
| Tone mapping algorithm | BT.2390 |
| Tone mapping range | Auto |
| Tone mapping desat | 0 |
| Tone mapping peak | 100 |
| Tone mapping param | *(blank)* |
| Transcoding thread count | Auto |
| FFmpeg path | `/usr/lib/jellyfin-ffmpeg/ffmpeg` |
| Transcode path | *(blank)* |
| Fallback font folder path | *(blank)* |
| Enable fallback fonts | ❌ Unchecked |
| Enable VBR audio encoding | ❌ Unchecked |
| Audio boost when downmixing | 2 |
| Stereo Downmix Algorithm | None |
| Max muxing queue size | 2048 |
| Encoding preset | *(blank)* |
| H.265 encoding CRF | 28 |
| H.264 encoding CRF | 23 |
| Deinterlacing method | Yet Another DeInterlacing Filter (YADIF) |
| Double the frame rate when deinterlacing | ❌ Unchecked |
| Allow subtitle extraction on the fly | ✅ Checked |
| Throttle Transcodes | ❌ Unchecked |
| Delete segments | ❌ Unchecked |
| Throttle after | 180 |
| Time to keep segments | 720 |

### Playback > Resume

| Setting | Value |
| --- | --- |
| Minimum resume percentage | 5 |
| Maximum resume percentage | 90 |
| Minimum Audiobook resume in minutes | 5 |
| Audiobook remaining minutes to resume | 5 |
| Minimum resume duration | 300 |

### Playback > Streaming

| Setting | Value |
| --- | --- |
| Internet streaming bitrate limit (Mbps) | *(blank)* |

### Playback > Trickplay

| Setting | Value |
| --- | --- |
| Enable hardware decoding | ❌ Unchecked |
| Enable hardware accelerated MJPEG encoding | ❌ Unchecked |
| Only generate images from key frames | ❌ Unchecked |
| Scan Behavior | Non Blocking - queues generation, then returns |
| Process Priority | Below Normal |
| Image Interval | 10000 |
| Width Resolutions | 320 |
| Tile Width | 10 |
| Tile Height | 10 |
| JPEG Quality | 90 |
| Qscale | 4 |
| FFmpeg Threads | 1 |

### Networking

| Setting | Value |
| --- | --- |
| Local HTTP port number | 8096 |
| Enable HTTPS | ❌ Unchecked |
| Local HTTPS port number | 8920 |
| Base URL | *(blank)* |
| Bind to local network address | *(blank)* |
| LAN networks | *(blank)* |
| Known proxies | *(blank)* |
| Require HTTPS | ❌ Unchecked |
| Custom SSL certificate path | *(blank)* |
| Certificate password | *(blank)* |
| Allow remote connections to this server | ✅ Checked |
| Remote IP address filter | *(blank)* |
| Remote IP address filter mode | Whitelist |
| Public HTTP port number | 8096 |
| Public HTTPS port number | 8920 |
| Enable IPv4 | ✅ Checked |
| Enable IPv6 | ❌ Unchecked |
| Enable Auto Discovery | ❌ Unchecked |
| Published Server URIs | *(blank)* |

---

## Install Jellyfin Plugins

Navigate to `http://server.domain.com:8096`, log in as the admin user, then click **Menu** > **Dashboard** > **Plugins**.

1. Set filter to **Available**.
2. Install **TheTVDB** — click the plugin, click **Install**, then click **Back**.
3. Install **Webhook** — click the plugin, click **Install**, then click **Back**.
4. Navigate to **Dashboard** → click **Restart** → confirm by clicking **Restart**.

---

## Configure Jellyfin Plugins

Navigate to `http://server.domain.com:8096`, log in as the admin user, then click **Menu** > **Dashboard** > **Plugins**.

Set filter to **Installed**.

### TheTVDB

Click **TheTVDB** → **Settings**:

| Setting | Value |
| --- | --- |
| TheTvdb Subscriber PIN | *(blank)* |
| Cache time in hours | 1 |
| Cache time in days | 7 |
| Fallback Languages | *(blank)* |
| Import season name from provider | ✅ Checked |
| Fallback to Original Language | ❌ Unchecked |
| Include original country in tags | ❌ Unchecked |
| Include missing specials | ✅ Checked |
| Remove All Missing Episodes On Refresh | ❌ Unchecked |
| Metadata Update In Hours | 2 |
| Update Series | ❌ Unchecked |
| Update Season | ❌ Unchecked |
| Update Episode | ❌ Unchecked |
| Update Movie | ❌ Unchecked |
| Update Person | ❌ Unchecked |

Click **Save**, then **Back** twice.

### Webhook

Click **Webhook** → **Settings**:

1. Set **Server Url** to *(blank)*.
2. Click **Add Generic Destination** and configure as follows:

| Setting | Value |
| --- | --- |
| Webhook Name | `HA JellyPlay` |
| Webhook Url | `https://haserver.domain.com:8123/api/webhook/jellyfin_playback` |
| Status | ✅ Checked |
| Authentication Failure | ❌ |
| Authentication Success | ❌ |
| Item Added | ❌ |
| Item Deleted | ❌ |
| Pending Restart | ❌ |
| Playback Progress | ❌ |
| Playback Start | ✅ Checked |
| Playback Stop | ❌ |
| Plugin Installation events | ❌ (all) |
| Session Start | ❌ |
| Subtitle Download Failure | ❌ |
| Task Completed | ❌ |
| User events | ❌ (all) |
| Movies | ✅ Checked |
| Episodes | ✅ Checked |
| Season | ✅ Checked |
| Series | ✅ Checked |
| Albums | ✅ Checked |
| Songs | ✅ Checked |
| Videos | ✅ Checked |
| Send All Properties | ❌ Unchecked |
| Trim whitespace from message body | ❌ Unchecked |
| Do not send when message body is empty | ❌ Unchecked |

3. Set the **Template** to:

```json
{
  "NotificationUsername": "{{NotificationUsername}}",
  "Name": "{{Name}}",
  "SeriesName": "{{SeriesName}}",
  "SeasonNumber00": "{{SeasonNumber00}}",
  "EpisodeNumber00": "{{EpisodeNumber00}}",
  "ItemType": "{{ItemType}}",
  "DeviceName": "{{DeviceName}}",
  "ClientName": "{{ClientName}}"
}
```

4. Click **Add Request Header** and set:

| Key | Value |
| --- | --- |
| Content-Type | application/json |

5. Click **Save**.

---

## Configure Jellyfin Media Libraries

Navigate to `http://server.domain.com:8096`, log in as the admin user, then click **Menu** > **Dashboard** > **Libraries** > **Libraries**.

### Movies Library

> **TODO:** Set the sort order.

1. Click **Add Media Library** and configure:

| Setting | Value |
| --- | --- |
| Content Type | Movies |
| Display name | Movies |
| Folders | `/mnt/media/movie` |
| Enable the library | ✅ Checked |
| Preferred download language | English |

2. For the **Movies** library, click **...** > **Manage library**:

| Setting | Value |
| --- | --- |
| Country/Region | United States |
| The Open Movie Database (metadata) | ❌ Unchecked |
| TheTVDB (metadata) | ❌ Unchecked |

3. For the **Movies** library, click **...** > **Edit images**:
   - Click **+** next to **Images**.
   - Select `poster_movie.png`.
   - Set **Image type** to `Primary`.
   - Click **Upload**, then **Back**.
