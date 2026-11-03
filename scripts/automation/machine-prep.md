# Robot machine prep (Windows 11 Pro)

How to prepare a Windows 11 Pro machine (physical or VM) to run Account Recon
unattended: daily, with no one signed in at the keyboard, and nothing on screen
ever waiting for a click.

The end state:

- A local **admin** account you use for maintenance only.
- A local **non-admin bot user** (`rpa` in this guide) that signs in
  automatically at boot, locks its screen immediately, and runs the job on the
  locked desktop.
- Windows and Edge configured so no prompt, tip, dialog or restart interrupts a run.
- Python and the repo installed for the bot user only, read-only from GitHub.

> **Conventions**
> - **[Admin]** steps run in an elevated PowerShell ("Run as administrator")
>   signed in as the admin account. **[Bot]** steps run in a normal PowerShell
>   signed in as the bot user.
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
sign-in or sync nags, password or autofill offers, promotions, the sidebar or a
"Save as" dialog. They apply to every Edge profile, including the bot's.

```powershell
$p = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"
New-Item -Path $p -Force | Out-Null
$policies = @{
  HideFirstRunExperience = 1; AutoImportAtFirstRun = 4; RestoreOnStartup = 5
  HideRestoreDialogEnabled = 1; StartupBoostEnabled = 0; BackgroundModeEnabled = 0
  BrowserSignin = 0; SyncDisabled = 1; PasswordManagerEnabled = 0
  AutofillAddressEnabled = 0; AutofillCreditCardEnabled = 0
  DefaultBrowserSettingEnabled = 0; DefaultBrowserSettingsCampaignEnabled = 0
  PromotionalTabsEnabled = 0; ShowRecommendationsEnabled = 0
  EdgeShoppingAssistantEnabled = 0; HubsSidebarEnabled = 0; TranslateEnabled = 0
  UserFeedbackAllowed = 0; PromptForDownloadLocation = 0
}
foreach ($k in $policies.Keys) {
  New-ItemProperty -Path $p -Name $k -Value $policies[$k] -PropertyType DWord -Force | Out-Null
}
```

| Policy | Stops |
| --- | --- |
| HideFirstRunExperience, AutoImportAtFirstRun | Welcome screens and import offers |
| RestoreOnStartup = 5, HideRestoreDialogEnabled | Reopened tabs and "Restore pages?" after a crash |
| StartupBoostEnabled, BackgroundModeEnabled | Edge preloading or staying alive in the background |
| BrowserSignin, SyncDisabled | Sign-in and sync prompts |
| PasswordManagerEnabled, Autofill* | Save-password and autofill offers |
| DefaultBrowserSetting*, PromotionalTabsEnabled, ShowRecommendationsEnabled | Default-browser nags, promotions, tips |
| EdgeShoppingAssistantEnabled, HubsSidebarEnabled, TranslateEnabled, UserFeedbackAllowed | Shopping pop-ups, sidebar, translate and feedback prompts |
| PromptForDownloadLocation | "Save as" dialog on downloads |

**Verify:** open `edge://policy`, click **Reload policies**; every row should
show OK. A row marked unknown or error means that policy was renamed in your
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
$pw = Read-Host -AsSecureString "Password for rpa"
New-LocalUser -Name "rpa" -Password $pw -PasswordNeverExpires -AccountNeverExpires `
  -Description "Unattended automation user"
Add-LocalGroupMember -Group "Users" -Member "rpa"
```

Use a long random password and store it in your password manager. The bot
user never needs to know it day to day; Autologon (A10) stores it encrypted.

**Verify:** `Get-LocalGroupMember Administrators` does **not** list `rpa`.

### A9. Working folder

```powershell
New-Item -ItemType Directory -Path "C:\recon" -Force | Out-Null
icacls "C:\recon" /grant "rpa:(OI)(CI)M"
```

The bot user can modify everything under `C:\recon` (repo, `.venv`, `runs\`,
`profiles\`) and nothing else outside its own profile.

### A10. Automatic sign-in

```powershell
winget install --id Microsoft.Sysinternals.Autologon -e
```

Run **Autologon**, enter `rpa`, the machine name as domain, and the password,
then click **Enable**. Autologon stores the password as an encrypted system
secret, not in plain text in the registry.

**Verify:** reboot; the bot user signs in by itself. (Step B6 then makes it
lock immediately.)

---

## Part B: Bot user profile [Bot]

Sign in as `rpa` (it now does so automatically). The first sign-in builds the
profile; wait for it to finish.

### B1. Quiet per-user settings

```powershell
function Set-Reg($Path, $Name, $Value, $Type = "DWord") {
  if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
  New-ItemProperty -Path $Path -Name $Name -Value $Value -PropertyType $Type -Force | Out-Null
}
$cdm = "HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"

# No toast notifications
Set-Reg "HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications" "ToastEnabled" 0

# No tips, suggestions, "fun facts" or suggested apps
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
- System > Notifications: **Do not disturb** on, no exceptions.

**Verify:** sign out and back in; no notifications, tips or setup screens appear.

### B2. Tools

```powershell
winget install --id astral-sh.uv -e
winget install --id Git.Git -e
# close and reopen PowerShell
uv python install 3.13
uv --version; git --version; uv python list --only-installed
```

Python is never put on the global PATH. The project always runs from its own
`.venv`, so other Python installs can't interfere.

### B3. Repo, read-only

The repo is public, so the robot clones it anonymously: no GitHub account,
token or SSH key on this machine. A compromised robot can't change the code.

```powershell
git clone <repo-url> C:\recon\app
cd C:\recon\app
git config --global credential.helper ""      # never store or ask for credentials
git remote set-url --push origin no-push      # any push fails immediately
git fetch --tags
git checkout <vX.Y.Z>                         # robots run tagged releases only
uv sync                                       # exact versions from uv.lock
```

Leave `user.name` and `user.email` unset so an accidental commit fails too.

**Updating later:** `git fetch --tags`, `git checkout <new tag>`, `uv sync`,
then run the environment check (B7). To roll back, check out the previous tag
and `uv sync` again.

### B4. Private configuration and secrets

Nothing private is in the repo. Create or copy these on the robot only:

| Item | Location | How |
| --- | --- | --- |
| Real config | `C:\recon\config.toml` | Copy from your backup, or copy `config.example.toml` and fill in |
| Passwords, API tokens, webhook URL | Windows Credential Manager (bot user) | `uv run recon setup-secrets` |
| Large keys (e.g. Google service account) | Encrypted file in `%USERPROFILE%\.recon\` | `setup-secrets` imports it; delete the original download |
| SFTP key | `%USERPROFILE%\.ssh\` | Generate on this machine with `ssh-keygen -t ed25519`; never copy a key in |

Never copy browser profiles from another machine. Each site asks for
verification once on the first run; approve it and the profile remembers this
machine.

**Verify:** `uv run recon check-config`

### B5. Lock at sign-in

The bot works on a locked desktop, so the console never sits unlocked.

```powershell
$a = New-ScheduledTaskAction -Execute "rundll32.exe" -Argument "user32.dll,LockWorkStation"
$t = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\rpa"
Register-ScheduledTask -TaskName "Lock at sign-in" -Action $a -Trigger $t
```

**Verify:** reboot and walk away. The bot user signs in, then the screen locks.

### B6. Daily job

```powershell
$a = New-ScheduledTaskAction -Execute "C:\recon\app\.venv\Scripts\python.exe" `
  -Argument "-m recon run" -WorkingDirectory "C:\recon\app"
$t = New-ScheduledTaskTrigger -Daily -At 8:00
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
  -MultipleInstances IgnoreNew -RestartCount 1 -RestartInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "Account Recon" -Action $a -Trigger $t -Settings $s
```

- **StartWhenAvailable:** a run missed while the machine was off starts soon after boot.
- **ExecutionTimeLimit:** a hung run is stopped after 2 hours.
- **MultipleInstances IgnoreNew:** never two runs at once.
- Registered by the bot user, the task runs only while that user is signed in,
  which Autologon guarantees.

After any Remote Desktop session on this machine, restart it (see A6) so
the bot user is back on the console before the next run.

### B7. Verify the whole machine

From `C:\recon\app` (or the smoke-test folder before the app exists):

```powershell
uv run python env_check.py          # fake bank site on this machine only
uv run python env_check.py --lock   # same, with Windows locked
```

`env_check.py` confirms Python and Playwright, Edge launches with its sandbox
on, the Edge policies are set, automatic sign-in is on, Credential Manager
works, and a full sign-in, verification, date entry and download flow works,
including on a locked screen. It never contacts a real bank.

Then reboot and walk away. Within a few minutes: bot user signed in, screen
locked, and (at the scheduled time) a run summary arrives.

---

## Part C: Maintenance checklist

| When | Do |
| --- | --- |
| After a major Edge update | Run `env_check.py`; check `edge://policy` |
| After a feature update of Windows | Re-run Part A verify lines; check B1 settings survived |
| Deploying a new release | `git fetch --tags`, `git checkout <tag>`, `uv sync`, `env_check.py` |
| Changing the bot user's password | Update it in Autologon (A10) |
| Changing any site password | `uv run recon setup-secrets` for that entry |
| Rebuilding the machine | Parts A and B in order; restore `config.toml` from backup; new keys and secrets |
