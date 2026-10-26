# Valheim

My Valheim client setup: clean BepInEx configs for the mods I play with, shared between my Mac and Windows machines.

## config_client

`config_client/` is a complete `BepInEx/config` folder. Each file was generated fresh by the current version of its mod, so there are no leftover settings from older versions, and then only the settings I actually change were edited. Every setting keeps the mod's own description, type and default value in the comments above it.

### Installing

Close Valheim first. Plugins only read their configs at startup, and a running game may write over the files when it exits.

Copy the contents of `config_client/` into the `BepInEx/config` folder that the game actually loads. If your mod manager keeps its own copy of each profile, copy into that too, or the manager may put the old files back.

**Mac, with Macheim**
- Game: `~/Library/Application Support/Steam/steamapps/common/Valheim/BepInEx/config/`
- Macheim profile: `~/Library/Application Support/com.macheim/profiles/Default/BepInEx/config/`

**Windows, with Gale**
- Gale profile: `%APPDATA%\com.kesomannen.gale\valheim\profiles\<profile>\BepInEx\config\`

### Things to know

- **Server-synced settings.** Valheim Plus, and some settings in other mods (marked "Synced with Server" in their descriptions), take their values from the server when you join a multiplayer world. On a server, its values win; these files decide what happens in single-player and local worlds.
- **Updating a mod.** When a mod adds settings in a new version, it appends them to its existing file at their defaults. When it removes or renames one, the old entry stays behind as an orphan with no description above it. To keep this folder clean, regenerate the file from the new version and re-apply the changes listed above.
- **Starred Creature Colors** is my own mod. Its source lives in its own folder in this repo.
