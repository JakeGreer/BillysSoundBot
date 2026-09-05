# BillyBot

A personal Discord music bot with slash commands, per-server queues, Spotify track search, and queued audio playback.

## How it works

Spotify's Web API can search tracks and return metadata, but it does not provide a stream for a Discord bot to relay. This project uses Spotify Client Credentials when configured to turn a request such as `/play Blinding Lights` into a precise track search, then resolves that title against a playable source with `yt-dlp`. FFmpeg sends the resulting audio to Discord.

Without Spotify credentials, `/play` still works as a direct YouTube search.

## Discord commands

These are slash commands typed directly in Discord. The bot must be installed in the server and you must be connected to a voice channel for `/play`.

| Command | What it does |
| --- | --- |
| `/play <query>` | Searches and queues a song, Spotify track URL, or YouTube URL. The bot joins your voice channel if needed. |
| `/queue` | Shows the current track and waiting tracks. |
| `/skip` | Stops the current track and starts the next queued track. |
| `/pause` | Pauses playback. |
| `/resume` | Resumes paused playback. |
| `/stop` | Stops playback and clears the queue. |
| `/leave` | Clears the queue and disconnects from voice. |

Examples:

```text
/play Never Gonna Give You Up Rick Astley
/play https://open.spotify.com/track/...
/queue
/skip
```

The queue is separate for each Discord server. Requests are played in first-in, first-out order. `MAX_QUEUE_SIZE` limits waiting tracks to 50 by default.

## Install the bot in Discord

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and select the application.
2. Open **Bot**, choose **Reset Token**, and copy the new token into `DISCORD_TOKEN`. Do not send this token to anyone.
3. Open **OAuth2 -> URL Generator**.
4. Select the scopes `bot` and `applications.commands`.
5. Select these bot permissions: `View Channels`, `Send Messages`, `Embed Links`, `Connect`, and `Speak`.
6. Copy the generated URL, open it in a browser, select your server, and authorize the bot.
7. Complete Discord's authorization check. You need the `Manage Server` permission, or equivalent administrator access, to add the bot.

The bot cannot join a voice channel unless it has `Connect` and `Speak` permissions in that channel. Slash commands also require `applications.commands` and may be hidden if the bot was invited without that scope.

## Local development

Prerequisites:

- Python 3.11 or newer
- FFmpeg installed and available on `PATH`
- A Discord application with a bot user
- Optional Spotify Developer credentials for better metadata search

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e .
Copy-Item .env.example .env
```

Fill in `.env`. The template explains where each value comes from:

```dotenv
DISCORD_TOKEN=your-real-discord-token
DISCORD_GUILD_ID=123456789012345678
SPOTIFY_CLIENT_ID=your-spotify-client-id
SPOTIFY_CLIENT_SECRET=your-spotify-client-secret
MAX_QUEUE_SIZE=50
```

`DISCORD_GUILD_ID` is optional. Set it to your test server's ID while developing because commands appear almost immediately there. Leave it blank for global commands; Discord can take up to an hour to publish global command changes.

Run it with:

```powershell
py -m billy_sound_bot.bot
```

## Deploy from GitHub

The easiest GitHub-connected option is a Railway service. Render's background worker is a good alternative. Both can watch the repository, build the included `Dockerfile`, inject environment variables, and restart the process if it exits. A Discord bot is a long-running worker, not a web service.

### Railway

1. Push this repository to GitHub, including `Dockerfile`, `pyproject.toml`, `src/`, and `.env.example`.
2. Confirm `.env` is not shown by `git status`; it is ignored by `.gitignore` and must never be pushed.
3. Create an account at [Railway](https://railway.com/) and choose **New Project -> Deploy from GitHub Repo**.
4. Authorize GitHub, select the BillysSoundBot repository, and select the branch to deploy, usually `main`.
5. Railway will detect the `Dockerfile`. If it asks for a start command, use `billybot`.
6. Open the service's **Variables** section and add `DISCORD_TOKEN`, `SPOTIFY_CLIENT_ID`, and `SPOTIFY_CLIENT_SECRET`. Add `DISCORD_GUILD_ID` while testing and `MAX_QUEUE_SIZE` if you want a non-default limit.
7. Deploy the service and open its logs. Successful startup will include a command-sync message.
8. Enable automatic deploys from the selected GitHub branch. Each push to that branch will build and redeploy the bot.

Railway may require a paid trial or billing method depending on its current plan. Check the current pricing before deploying.

### Render alternative

1. Create a [Render](https://render.com/) account and choose **New -> Background Worker**.
2. Connect GitHub and select this repository and branch.
3. Choose **Docker** as the environment. Render will use the included `Dockerfile`.
4. Add the same environment variables under **Environment**.
5. Create the worker and enable automatic deploys.

Do not create a Render Web Service for this bot. It has no HTTP server and needs to remain connected as a background worker.

Recommended deployment settings:

- Use the included `Dockerfile`; it installs FFmpeg inside the container.
- Add secrets through the hosting provider's variable settings, not through GitHub files.
- Set `DISCORD_GUILD_ID` during initial testing, then remove it when you want global commands.
- Enable automatic deploys and restart-on-failure.
- Use one running replica. Multiple replicas can duplicate playback and Discord connections.
- Keep logs enabled. The bot logs command sync and playback failures.

The container installs FFmpeg, so the host does not need a separate system package. For maximum control and predictable cost, a small always-on VPS running Docker is also a good option; use a systemd or Docker restart policy and pull the GitHub image on deploy.

## Security and limits

- Never commit `.env`; it is ignored by Git.
- Rotate the Discord token immediately if it is exposed.
- Queue length defaults to 50 tracks per server and can be changed with `MAX_QUEUE_SIZE`.
- Playback depends on the availability and terms of the selected audio source. Keep this bot private and use sources you are permitted to play.
