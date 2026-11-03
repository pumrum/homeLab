# Robot machine prep (Windows 11 Pro)

How to prepare a Windows 11 Pro machine (physical or VM) to run the automations
unattended: daily, with no one signed in at the keyboard, and nothing on screen
ever waiting for a click.

The end state:

- A local **admin** account you use for maintenance only.
- A local **non-admin bot user** (`robot` in this guide) that signs in
  automatically at boot, locks its screen immediately, and runs the job on the
  locked desktop.
- Windows and Edge configured so no prompt, tip, dialog, or restart interrupts a run.
- Python and the repo installed for the bot user only, read-only from GitHub.

> **Conventions**
> - **[Admin]** steps run in an elevated PowerShell ("Run as administrator")
>   signed in as the admin account. **[Bot]** steps run in a normal PowerShell
>   signed in as the bot user.
> - Paths used throughout:
>   - `C:\src\homeLab`: the git repo (code only, read-only on the robot)
>   - `C:\src\homeLab\scripts\automation`: the uv workspace shared by every
>     automation (`pyproject.toml`, `uv.lock`, `.venv`, `tools\env_check.py`)
>   - `C:\src\homeLab\scripts\automation\<process>`: one process's code
>     (examples use `fidelity`, whose command is `fidelity-nav`)
>   - `C:\automation\<process>`: this process's private, writable data
>     (`config.toml`, `data\`, `runs\`, `profiles\`), kept outside the repo.
>     Nothing is built or copied here; the code runs from the workspace and
>     finds this folder itself.
> - Values in `<angle brackets>` are placeholders. Never commit real values
>   (host names, account names, URLs) to this public repo.
> - Policy names change between Windows and Edge releases. After each section,
>   use the **Verify** line. If a setting doesn't take, check its current name
>   in Microsoft's documentation before working around it.

---

## Part A: Machine configuration [Admin]

### A1. Base install

1. Install Windows 11 Pro with a **local** administrator account (not a
   Microsoft account). This account is for maintenance only.
2. VM only: install the guest tools for your hypervisor (for Proxmox: VirtIO
   drivers and the QEMU guest agent), and set the display to at least
   1920x1080. The bot's browser runs on this display even while locked.
3. Rename the machine and set the time zone:

   ```powershell
   Rename-Computer -NewName "<ROBOT-NAME>"
   Set-TimeZone -Id "Eastern Standard Time"      # or your zone
   w32tm /resync
   ```

4. Give the machine a fixed address (DHCP reservation on your router is
   simplest), so remote access and any firewall rules stay stable.
5. Run Windows Update until nothing is left, then reboot.

**Verify:** `Get-ComputerInfo -Property OsName,OsVersion,CsName,TimeZone`

### A2. Windows Update

Goal: updates install automatically, restarts happen only at night, and the
daily run falls inside "active hours" so it is never interrupted.

Using **Local Group Policy Editor** (`gpedit.msc`), under
*Computer Configuration > Administrative Templates > Windows Components >
Windows Update*:

| Setting | Value |
| --- | --- |
| Manage end user experience > Configure Automatic Updates | Enabled, **4 - Auto download and schedule the install**, every day, 03:00 |
| Manage end user experience > Turn off auto-restart for updates during active hours | Enabled, 06:00 to 22:00 (adjust so the daily run is inside this window) |
| Manage updates offered from Windows Update > Select when Preview Builds and Feature Updates are received | Enabled, defer feature updates 180 days |
| Manage updates offered from Windows Update > Select when Quality Updates are received | Enabled, defer 3 days |

Feature updates (yearly Windows versions) are deferred because they are the
updates most likely to change behavior. Security updates still arrive within days.

Also:

- Settings > Windows Update > Advanced options: **Receive updates for other
  Microsoft products** = On (keeps Edge's components current).
- Leave Edge's own automatic updates **on**. A browser that signs into a bank
  must stay patched; the bot starts a fresh Edge every run, so updates apply
  at the next launch.

**Verify:** `gpupdate /force`, then Settings > Windows Update shows
"*Some settings are managed by your organization*" and the active hours you set.

### A3. Power and startup

```powershell
powercfg /setactive SCHEME_MIN                 # High performance plan
powercfg /change standby-timeout-ac 0          # never sleep
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0          # display stays on (VM: harmless)
powercfg /hibernate off                        # also turns off Fast Startup
```

Fast Startup is turned off so every "shut down" is a real cold boot. A
half-hibernated session can leave the previous run's browser and lock files
behind.

**Verify:** `powercfg /getactivescheme` and `powercfg /a` (hibernate not available).

### A4. Quiet system behaviors

These stop system-level pop-ups that would sit on the screen waiting for a click.

```powershell
function Set-Reg($Path, $Name, $Value, $Type = "DWord") {
  if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
  New-ItemProperty -Path $Path -Name $Name -Value $Value -PropertyType $Type -Force | Out-Null
}

# Crash dialogs: log the crash, never show "X has stopped working"
Set-Reg "HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting" "DontShowUI" 1

# Program Compatibility Assistant pop-ups
Set-Reg "HKLM:\SOFTWARE\Policies\Microsoft\Windows\AppCompat" "DisablePCA" 1

# Microsoft consumer features, suggested apps, tips (may be partly ignored on Pro)
Set-Reg "HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent" "DisableWindowsConsumerFeatures" 1
Set-Reg "HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent" "DisableSoftLanding" 1
Set-Reg "HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent" "DisableCloudOptimizedContent" 1

# Widgets
Set-Reg "HKLM:\SOFTWARE\Policies\Microsoft\Dsh" "AllowNewsAndInterests" 0

# OneDrive (nothing on the robot should sync to the cloud)
Set-Reg "HKLM:\SOFTWARE\Policies\Microsoft\Windows\OneDrive" "DisableFileSyncNGSC" 1
```

Uninstall apps the robot will never use (Settings > Apps > Installed apps):
Copilot, Teams (personal), Xbox apps, Clipchamp, News, Weather, Solitaire, and
similar. Removing them removes their notifications and background updates.

**Verify:** reboot; no widgets button on the taskbar; OneDrive does not start.

### A5. Edge policies

Machine-wide policies so Edge never shows first-run screens, restore prompts,
sign-in or sync nags, password or autofill offers, promotions, the sidebar, or a
"Save as" dialog. They apply to every Edge profile, including the bot's.

```powershell
$p = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"

# 1. Close Edge everywhere so the new policies load on next start
Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force

# 2. Create the policy key if it doesn't exist
New-Item -Path $p -Force | Out-Null

# 3. Remove startup/home page policies that standalone Windows blocks ("Error, Ignored")
foreach ($name in "RestoreOnStartup", "HomepageLocation", "HomepageIsNewTabPage", "NewTabPageLocation") {
  Remove-ItemProperty -Path $p -Name $name -ErrorAction SilentlyContinue
}
Remove-Item -Path "$p\RestoreOnStartupURLs" -Recurse -ErrorAction SilentlyContinue

# 4. Set every policy (all DWORD)
$policies = [ordered]@{
  # First run and import
  HideFirstRunExperience                = 1
  AutoImportAtFirstRun                  = 4
  # Crash restore and background running
  HideRestoreDialogEnabled              = 1
  StartupBoostEnabled                   = 0
  BackgroundModeEnabled                 = 0
  # Sign-in and sync
  BrowserSignin                         = 0
  SyncDisabled                          = 1
  # Passwords and autofill
  PasswordManagerEnabled                = 0
  AutofillAddressEnabled                = 0
  AutofillCreditCardEnabled             = 0
  # Default browser nags, promotions, tips, "What's new"
  DefaultBrowserSettingEnabled          = 0
  DefaultBrowserSettingsCampaignEnabled = 0
  PromotionalTabsEnabled                = 0   # deprecated; kept until Edge removes it
  ShowRecommendationsEnabled            = 0   # its replacement
  # Shopping, sidebar, translate, feedback
  EdgeShoppingAssistantEnabled          = 0
  HubsSidebarEnabled                    = 0
  TranslateEnabled                      = 0
  UserFeedbackAllowed                   = 0
  # Downloads go straight to the folder, no "Save as"
  PromptForDownloadLocation             = 0
  # Office files download instead of opening in the browser
  QuickViewOfficeFilesEnabled           = 0
  # New tab page: no feed, quick links, top sites, or background image
  NewTabPageContentEnabled              = 0
  NewTabPageQuickLinksEnabled           = 0
  NewTabPageHideDefaultTopSites         = 1
  NewTabPageAllowedBackgroundTypes      = 3
}
foreach ($k in $policies.Keys) {
  New-ItemProperty -Path $p -Name $k -Value $policies[$k] -PropertyType DWord -Force | Out-Null
}

# 5. Show the result: every expected policy with its value, and anything unexpected
$now = Get-ItemProperty -Path $p
$policies.Keys | ForEach-Object {
  [pscustomobject]@{ Policy = $_; Expected = $policies[$_]; Actual = $now.$_;
                     OK = ($now.$_ -eq $policies[$_]) }
} | Format-Table -AutoSize
$extra = $now.PSObject.Properties.Name | Where-Object { $_ -notlike "PS*" -and -not $policies.Contains($_) }
if ($extra) { "Other values present (not set by this script): $($extra -join ', ')" }
```

The script is safe to run again; it always ends in the same state. The last
step prints an **OK** column for every policy.

| Policy | Stops |
| --- | --- |
| HideFirstRunExperience, AutoImportAtFirstRun | Welcome screens and import offers |
| HideRestoreDialogEnabled | "Restore pages?" after a crash |
| NewTabPageContentEnabled, NewTabPageQuickLinksEnabled, NewTabPageHideDefaultTopSites, NewTabPageAllowedBackgroundTypes = 3 | News feed, quick links, top sites, and background image on the new tab page |
| StartupBoostEnabled, BackgroundModeEnabled | Edge preloading or staying alive in the background |
| BrowserSignin, SyncDisabled | Sign-in and sync prompts |
| PasswordManagerEnabled, Autofill* | Save-password and autofill offers |
| DefaultBrowserSetting*, PromotionalTabsEnabled (deprecated), ShowRecommendationsEnabled (its replacement) | Default-browser nags, full-tab promotions, "What's new", and tips |
| EdgeShoppingAssistantEnabled, HubsSidebarEnabled, TranslateEnabled, UserFeedbackAllowed | Shopping pop-ups, sidebar, translate, and feedback prompts |
| PromptForDownloadLocation | "Save as" dialog on downloads |
| QuickViewOfficeFilesEnabled | Office files (Excel, Word, PowerPoint) opening in the browser instead of downloading |

**Not settable by policy on a standalone machine.** On Windows that isn't
joined to a domain or Microsoft Entra ID, or enrolled in device management,
Edge blocks the startup and home page policies (`RestoreOnStartup`,
`RestoreOnStartupURLs`, `HomepageLocation`, `NewTabPageLocation`, and similar)
and shows them as "Error, Ignored". Don't set them. The bot is unaffected
(Playwright opens `about:blank` itself); for manual use, set the startup page
in Edge's own settings (step B1).

**Verify:** sign in as the bot user, open Edge, open `edge://policy`, click
**Reload policies**; every row should show OK, with no "Error, Ignored". A new
tab should show only a search box, with no feed. A row marked unknown or error means that policy was renamed in your
Edge version. Edge will say it is "managed by your organization"; that is expected.

### A6. Remote access

1. Settings > System > Remote Desktop: **On**, with **Require Network Level
   Authentication**. Allow only the admin account.
2. Limit Remote Desktop to your own network (Windows Firewall rule scope, or
   your router/firewall).
3. Optional, only if a remote trigger needs it later: the built-in OpenSSH
   Server (`Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`),
   key authentication only.

> **Important:** Windows 11 Pro allows one signed-in desktop at a time.
> Connecting over Remote Desktop, as any user, takes over or disconnects the
> bot user's console session, and a disconnected session has no display for
> the bot's browser. For routine checks use the hypervisor console instead.
> If you do use Remote Desktop, **restart the machine when you're done**:
> automatic sign-in and the lock task put the bot user back on a clean,
> locked console.

**Verify:** connect from your workstation with the admin account.

### A7. Security

1. **BitLocker** on the system drive (Settings > Privacy & security > Device
   encryption, or `manage-bde`). VM: add a virtual TPM first. This protects
   the disk image and any backups of it.
2. Keep **Microsoft Defender** on, with no folder exclusions.
3. Keep **UAC** at its default.
4. Leave the bot user out of the Administrators group (A8).

**Verify:** `manage-bde -status C:` shows "Protection On".

### A8. Create the bot user

```powershell
$pw = Read-Host -AsSecureString "Password for robot"
New-LocalUser -Name "robot" -Password $pw -PasswordNeverExpires -AccountNeverExpires `
  -Description "Unattended automation user"
Add-LocalGroupMember -Group "Users" -Member "robot"
```

Use a long random password and store it in your password manager. The bot
user never needs to know it day to day; Autologon (A10) stores it encrypted.

**Verify:** `Get-LocalGroupMember Administrators` does **not** list `robot`.

### A9. Working folders

```powershell
foreach ($d in "C:\src", "C:\automation") {
  New-Item -ItemType Directory -Path $d -Force | Out-Null
  icacls $d /grant "robot:(OI)(CI)M"
}
```

- `C:\src` holds the repo clone (`C:\src\homeLab`). The bot user needs write
  access only so `git checkout` and `uv sync` (which creates `.venv`) work.
- `C:\automation\<process>` holds each process's private config and run data.

The bot user can modify these two folders and its own profile, nothing else.

### A10. Automatic sign-in

```powershell
winget install --id Microsoft.Sysinternals.Autologon -e
```

Run **Autologon**, enter `robot`, the machine name as domain, and the password,
then click **Enable**. Autologon stores the password as an encrypted system
secret, not in plain text in the registry.

**Verify:** reboot; the bot user signs in by itself. (Step B6 then makes it
lock immediately.)

---

## Part B: Bot user profile [Bot]

Sign in as `robot` (it now does so automatically). The first sign-in builds the
profile; wait for it to finish.

The PowerShell blocks in B3 to B7 use these variables. Set them at the start
of each PowerShell window you use for Part B:

```powershell
$repo    = "C:\src\homeLab"                        # repo clone (code only)
$ws      = "$repo\scripts\automation"              # uv workspace: pyproject.toml, uv.lock, .venv
$process = "fidelity"                              # process folder name
$data    = "C:\automation\$process"                 # private data: config.toml, data, runs, profiles
```

### B1. Quiet per-user settings

```powershell
function Set-Reg($Path, $Name, $Value, $Type = "DWord") {
  if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
  New-ItemProperty -Path $Path -Name $Name -Value $Value -PropertyType $Type -Force | Out-Null
}
$cdm = "HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"

# No toast notifications
Set-Reg "HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications" "ToastEnabled" 0

# No tips, suggestions, "fun facts", or suggested apps
foreach ($n in "SoftLandingEnabled","SystemPaneSuggestionsEnabled","SubscribedContent-338387Enabled",
               "SubscribedContent-338388Enabled","SubscribedContent-338389Enabled",
               "SubscribedContent-353694Enabled","SubscribedContent-353696Enabled",
               "RotatingLockScreenOverlayEnabled","SilentInstalledAppsEnabled") {
  Set-Reg $cdm $n 0
}

# No "Let's finish setting up your device" after updates
Set-Reg "HKCU:\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement" "ScoobeSystemSettingEnabled" 0

# Visual effects: best performance (takes effect at next sign-in)
Set-Reg "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" "VisualFXSetting" 2

# No screen saver (the session is locked anyway)
Set-Reg "HKCU:\Control Panel\Desktop" "ScreenSaveActive" "0" "String"
```

Then, in Settings:

- Apps > Advanced app settings > **App execution aliases**: turn off
  **python.exe** and **python3.exe** (stops `python` opening the Microsoft Store).
- Apps > Startup: turn off everything you don't need.
- Edge, for opening it by hand as the bot user (these can't be set by policy
  on a standalone machine; the bot itself always starts on `about:blank` and
  never uses the toolbar):
  - Settings > Start, home, and new tabs > When Edge starts > **Open these
    pages** > add `about:blank`.
  - Turn off the **Copilot** toolbar button (Settings > Copilot, or
    Appearance > toolbar buttons, depending on the Edge version).
  - Settings > Downloads: turn off **Show downloads menu when a download
    starts** (no policy exists for this one).
- System > Notifications: **Do not disturb** on, no exceptions.

**Verify:** sign out and back in; no notifications, tips, or setup screens appear.

### B2. Tools

```powershell
cd $HOME                                  # never work from C:\Windows\System32
winget install --id astral-sh.uv -e
winget install --id Git.Git -e
# close and reopen PowerShell
uv python install 3.13
uv --version; git --version; uv python list --only-installed
```

Python is never put on the global PATH. Every automation runs from the
workspace's own `.venv`, so other Python installs can't interfere.

### B3. Repo, read-only

The repo is public, so the robot clones it anonymously: no GitHub account,
token, or SSH key on this machine. A compromised robot can't change the code.

```powershell
git clone <repo-url> $repo
cd $repo
git config --global credential.helper ""      # never store or ask for credentials
git remote set-url --push origin no-push      # any push fails immediately
git fetch --tags
git checkout "$process/v<X.Y.Z>"              # robots run tagged releases only
cd $ws
uv sync                                       # exact versions from uv.lock
```

`uv sync` creates `$ws\.venv` and installs every automation and the shared
code into it straight from the repo folders. Nothing is copied to
`C:\automation`; that folder only ever holds data (B4).

Tags carry the process name (for example `fidelity/v0.1.0`) because one
repo holds several processes, each versioned on its own.

Leave `user.name` and `user.email` unset so an accidental commit fails too.

**Updating later:**

```powershell
cd $repo; git fetch --tags; git checkout "$process/v<new>"
cd $ws; uv sync
```

Then run the environment check (B7). To roll back, check out the previous tag
and run `uv sync` again. Nothing in `$data` is touched by updates.

### B4. Private configuration and secrets

Nothing private is in the repo. Create the data folder, then create or copy
the items below on the robot only:

```powershell
New-Item -ItemType Directory -Path $data -Force | Out-Null
Copy-Item "$ws\$process\config.example.toml" "$data\config.toml"   # or restore from backup
notepad "$data\config.toml"                                       # replace every placeholder
```

| Item | Location | How |
| --- | --- | --- |
| Real config | `C:\automation\<process>\config.toml` | Copy from your backup, or copy `config.example.toml` and fill in (above) |
| Passwords, API tokens, webhook URL | Windows Credential Manager (bot user) | Only the entries the process's README lists (`fidelity` needs none). Control Panel > Credential Manager > Windows Credentials > **Add a generic credential**: address = entry name (`<process>/<name>`, or `automation/<name>` for shared ones), user name, password = the secret |
| Home Assistant webhook (optional, shared) | Windows Credential Manager (bot user) | Generic credential `automation/home-assistant-webhook`, password = the webhook URL. If it exists, every run POSTs its summary there |
| Large keys (e.g. Google service account) | Encrypted file in `%USERPROFILE%\.<process>\` | Only if a process needs one; its README says how. Delete the original download |
| SFTP key | `%USERPROFILE%\.ssh\` | Only if a process needs one. Generate on this machine with `ssh-keygen -t ed25519`; never copy a key in |

`data\`, `runs\`, and `profiles\` under `$data` are created by the app. Never
copy browser profiles from another machine. A site that asks for verification
does so once on the first run; approve it and the profile remembers this
machine.

**Verify:** run the process once (B7). A missing or wrong config, or one that
still has placeholders, fails at start-up with the reason in the newest
`$data\runs\<timestamp>\report.txt`.

### B5. Lock at sign-in

The bot works on a locked desktop, so the console never sits unlocked.

```powershell
$a = New-ScheduledTaskAction -Execute "rundll32.exe" -Argument "user32.dll,LockWorkStation"
$t = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\robot"
Register-ScheduledTask -TaskName "Lock at sign-in" -Action $a -Trigger $t
```

**Verify:** reboot and walk away. The bot user signs in, then the screen locks.

### B6. Daily job

```powershell
$a = New-ScheduledTaskAction -Execute "$ws\.venv\Scripts\fidelity-nav.exe" `
  -WorkingDirectory $ws                       # each process's command; see its README
$t = New-ScheduledTaskTrigger -Daily -At 19:30   # per process (fidelity: after NAVs publish)
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
  -MultipleInstances IgnoreNew -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName $process -Action $a -Trigger $t -Settings $s
```

- **StartWhenAvailable:** a run missed while the machine was off starts soon after boot.
- **ExecutionTimeLimit:** a hung run is stopped after 2 hours.
- **MultipleInstances IgnoreNew:** never two runs at once.
- Registered by the bot user, the task runs only while that user is signed in,
  which Autologon guarantees.

After any Remote Desktop session on this machine, restart it (see A6) so
the bot user is back on the console before the next run.

### B7. Verify the whole machine

Python packages come from the workspace: its `pyproject.toml` and `uv.lock`
are installed into `$ws\.venv` by `uv sync` (B3). Once that's done:

```powershell
cd $ws
uv run python tools\env_check.py          # fake bank site on this machine only
uv run python tools\env_check.py --lock   # same, with Windows locked
uv run fidelity-nav                       # one real run of the process
Get-Content "$data\data\nav.csv"
```

Before `uv sync` has run (a new machine), let uv bring the packages for just
that run. `env_check.py` finds the shared code in the clone by itself:

```powershell
cd $ws\tools
uv run --no-project --with playwright --with keyring python env_check.py
uv run --no-project --with playwright --with keyring python env_check.py --lock
```

`env_check.py` confirms Python and Playwright, Edge launches with its sandbox
on, the Edge policies are set, automatic sign-in is on, Credential Manager
works, and a full sign-in, verification, date entry, and download flow works,
including on a locked screen. It never contacts a real bank. Its report is in
`C:\automation\env-check\runs\<timestamp>\report.txt`.

Then reboot and walk away. Within a few minutes: bot user signed in, screen
locked, and (at the scheduled time) a run summary arrives.

---

## Part C: Maintenance checklist

| When | Do |
| --- | --- |
| After a major Edge update | Run `tools\env_check.py`; check `edge://policy` |
| After a feature update of Windows | Re-run Part A verify lines; check B1 settings survived |
| Deploying a new release | `git fetch --tags`, `git checkout <process>/v<X.Y.Z>`, `uv sync` in `$ws`, `tools\env_check.py` |
| Changing the bot user's password | Update it in Autologon (A10) |
| Changing any site password | Edit that entry in Credential Manager (B4) |
| Rebuilding the machine | Parts A and B in order; restore `config.toml` from backup; new keys and secrets |

---

## Part D: Developer workstation

Code is written and tested on a developer workstation, then released to the
robot by tag. The workstation should match the robot where it affects how the
code behaves (Python version, locked library versions, Edge, paths), and
differ where the robot's settings exist only for unattended running.

**Keep identical on both machines:** Python 3.13 (minor version), `uv.lock`,
Edge stable channel kept up to date, and the paths `C:\src\homeLab` and
`C:\automation\<process>`. Same paths mean the same config file works on both.

| Step | Workstation | Notes |
| --- | --- | --- |
| A1 Base install | Partly | Keep your existing setup and account; just stay fully patched. No rename, fixed address, or display setting needed |
| A2 Windows Update policy | Skip | Use your normal update settings |
| A3 Power | Skip | |
| A4 Quiet system | Skip | Optional: `DontShowUI` if crash dialogs annoy you |
| A5 Edge policies | Skip | They would change your everyday browser. Test runs use their own profile folder; `env_check.py` will WARN about missing policies, which is expected here |
| A6 Remote access | Skip | |
| A7 Security | Same | BitLocker on: this machine also holds private config and test secrets |
| A8 Bot user | Skip | You develop as yourself |
| A9 Working folders | Same paths | Create `C:\src` and `C:\automation`; no `icacls` needed, your account owns them |
| A10 Automatic sign-in | Skip | |
| B1 Quiet profile | Partly | Only turn off the python.exe and python3.exe app execution aliases |
| B2 Tools | Same, plus more | Also install `gh` (GitHub CLI), Claude Code, and run `pre-commit install` in the repo |
| B3 Repo | Different | Authenticated clone with push rights (`gh auth login`); set `user.name` and your GitHub no-reply `user.email`; work on branches, not tags. Still `uv sync` in `$ws` after switching branches |
| B4 Config and secrets | Same locations | Same `config.toml` path. Secrets in your own Credential Manager. Use separate Gmail tokens and SSH key for this machine, so revoking one never breaks the robot. Never copy browser profiles between machines |
| B5 Lock at sign-in | Skip | |
| B6 Daily job | **Never** | Two machines running the job would both write to the same outputs. The workstation only runs on demand, with you present |
| B7 Verify | Same | Run `tools\env_check.py`; expect WARN for Edge policies and automatic sign-in off. `--lock` is optional here |
| C Maintenance | Partly | Rerun `tools\env_check.py` after major Edge or Windows updates; deployment rows don't apply |
