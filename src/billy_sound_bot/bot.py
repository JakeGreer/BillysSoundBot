from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Any

import discord
import yt_dlp
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger("billy_sound_bot")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")
MAX_QUEUE_SIZE = int(os.environ.get("MAX_QUEUE_SIZE", "50"))
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch1",
}
FFMPEG_OPTIONS = {"before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", "options": "-vn"}


@dataclass(slots=True)
class Track:
    title: str
    url: str
    requester: str
    source_url: str


class SpotifyClient:
    def __init__(self) -> None:
        self._access_token: str | None = None

    @property
    def configured(self) -> bool:
        return bool(SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET)

    def _token(self) -> str:
        if self._access_token:
            return self._access_token
        credentials = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        request = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=body,
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
        self._access_token = payload["access_token"]
        return self._access_token

    def search(self, query: str) -> tuple[str, str] | None:
        if not self.configured:
            return None
        track_match = re.search(r"open\.spotify\.com/track/([A-Za-z0-9]+)", query)
        if track_match:
            endpoint = f"https://api.spotify.com/v1/tracks/{track_match.group(1)}"
        else:
            params = urllib.parse.urlencode({"q": query, "type": "track", "limit": 1})
            endpoint = f"https://api.spotify.com/v1/search?{params}"
        for attempt in range(2):
            request = urllib.request.Request(endpoint, headers={"Authorization": f"Bearer {self._token()}"})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                if error.code != 401 or attempt == 1:
                    raise
                LOGGER.warning("Spotify access token was rejected; requesting a new token")
                self._access_token = None
        track = payload if track_match else (payload.get("tracks", {}).get("items") or [None])[0]
        if not track:
            return None
        artists = ", ".join(artist["name"] for artist in track["artists"])
        return track["name"], f"{track['name']} {artists}"


class GuildPlayer:
    def __init__(self, bot: MusicBot, guild_id: int) -> None:
        self.bot = bot
        self.guild_id = guild_id
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.lock = asyncio.Lock()

    async def add(self, track: Track) -> int:
        async with self.lock:
            if len(self.queue) >= MAX_QUEUE_SIZE:
                raise ValueError(f"The queue is full ({MAX_QUEUE_SIZE} tracks maximum).")
            self.queue.append(track)
            return len(self.queue)

    async def start(self, voice_client: discord.VoiceClient) -> None:
        async with self.lock:
            if voice_client.is_playing() or voice_client.is_paused() or not self.queue:
                return
            self.current = self.queue.popleft()
            track = self.current
        try:
            audio_url = await asyncio.to_thread(resolve_audio_url, track.source_url)
            source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS)
            voice_client.play(source, after=self._after_playback)
            LOGGER.info("Playing %s in guild %s", track.title, self.guild_id)
        except Exception:
            LOGGER.exception("Could not play %s", track.title)
            await self.start(voice_client)

    def _after_playback(self, error: Exception | None) -> None:
        if error:
            LOGGER.error("Playback error in guild %s: %s", self.guild_id, error)
        self.bot.loop.call_soon_threadsafe(asyncio.create_task, self._advance())

    async def _advance(self) -> None:
        self.current = None
        guild = self.bot.get_guild(self.guild_id)
        if guild and guild.voice_client:
            await self.start(guild.voice_client)

    def describe(self) -> str:
        lines = []
        if self.current:
            lines.append(f"Now playing: **{self.current.title}**")
        if self.queue:
            lines.extend(f"{index}. {track.title} ({track.requester})" for index, track in enumerate(self.queue, 1))
        return "\n".join(lines) or "The queue is empty."


class MusicBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.players: dict[int, GuildPlayer] = {}
        self.spotify = SpotifyClient()

    async def setup_hook(self) -> None:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            LOGGER.info("Synced commands to guild %s", GUILD_ID)
        else:
            await self.tree.sync()
            LOGGER.info("Synced global commands")

    def player_for(self, guild_id: int) -> GuildPlayer:
        return self.players.setdefault(guild_id, GuildPlayer(self, guild_id))


bot = MusicBot()


def resolve_audio_url(query: str) -> str:
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ytdl:
        info = ytdl.extract_info(query, download=False)
    if "entries" in info:
        info = next((entry for entry in info["entries"] if entry), None)
    if not info or not info.get("url"):
        raise RuntimeError("No playable result found.")
    return info["url"]


def voice_channel_for(interaction: discord.Interaction) -> discord.VoiceChannel | None:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    return member.voice.channel if member and member.voice else None


STICK_FIGURE_FRAMES = (
    "```text\n        O\n       /|\\\n       / \\\n================\n                |\n                |\n________________|____________\n```",
    "```text\n             O\n            /|\\\n            / \\\n================\n                |\n                |\n________________|____________\n```",
    "```text\n                O\n               /|\\\n               / \\\n================\n                |\n                |\n________________|____________\n```",
    "```text\n                 O\n                /|\\\n                / \\\n================\n                |\n                |\n________________|____________\n```",
    "```text\n                 O\n                /|\\\n                / \\\n================\n                |\n                |\n________________|____________\n```",
    "```text\n                 O\n                /|\\\n                / \\\n================\n                |\n                |\n________________|____________\n```",
    "```text\n                 O\n                \\|/\n                / \\\n================\n                |\n                |\n________________|____________\n```",
)


@bot.tree.command(name="play", description="Search for a song and add it to the queue")
@app_commands.describe(query="A song name, Spotify track URL, or YouTube URL")
async def play(interaction: discord.Interaction, query: str) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return
    channel = voice_channel_for(interaction)
    if not channel:
        await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
        return
    await interaction.response.defer()
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.channel != channel:
        await interaction.followup.send("I am already active in another voice channel.")
        return
    try:
        spotify_result = await asyncio.to_thread(bot.spotify.search, query)
        title, source_query = spotify_result or (query, query)
        player = bot.player_for(interaction.guild.id)
        if not voice_client:
            voice_client = await channel.connect()
        position = await player.add(Track(title, query, interaction.user.display_name, source_query))
        await player.start(voice_client)
        await interaction.followup.send(f"Queued **{title}** at position {position}.")
    except Exception as error:
        LOGGER.exception("Play command failed")
        await interaction.followup.send(f"I couldn't queue that track: {error}")


@bot.tree.command(name="stickfigure", description="Show a small animated stick figure")
async def stickfigure(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(STICK_FIGURE_FRAMES[0])
    for frame in STICK_FIGURE_FRAMES[1:]:
        await asyncio.sleep(0.45)
        await interaction.edit_original_response(content=frame)


@bot.tree.command(name="queue", description="Show the current playback queue")
async def queue(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return
    await interaction.response.send_message(bot.player_for(interaction.guild.id).describe())


@bot.tree.command(name="skip", description="Skip the current track")
async def skip(interaction: discord.Interaction) -> None:
    voice_client = interaction.guild.voice_client if interaction.guild else None
    if not voice_client or not voice_client.is_playing():
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        return
    voice_client.stop()
    await interaction.response.send_message("Skipped.")


@bot.tree.command(name="pause", description="Pause playback")
async def pause(interaction: discord.Interaction) -> None:
    voice_client = interaction.guild.voice_client if interaction.guild else None
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await interaction.response.send_message("Paused.")
    else:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)


@bot.tree.command(name="resume", description="Resume playback")
async def resume(interaction: discord.Interaction) -> None:
    voice_client = interaction.guild.voice_client if interaction.guild else None
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await interaction.response.send_message("Resumed.")
    else:
        await interaction.response.send_message("Nothing is paused.", ephemeral=True)


@bot.tree.command(name="stop", description="Stop playback and clear the queue")
async def stop(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return
    player = bot.player_for(interaction.guild.id)
    player.queue.clear()
    if interaction.guild.voice_client:
        interaction.guild.voice_client.stop()
    await interaction.response.send_message("Stopped and cleared the queue.")


@bot.tree.command(name="leave", description="Disconnect the bot from voice")
async def leave(interaction: discord.Interaction) -> None:
    if interaction.guild and interaction.guild.voice_client:
        player = bot.player_for(interaction.guild.id)
        player.queue.clear()
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Disconnected.")
    else:
        await interaction.response.send_message("I am not in a voice channel.", ephemeral=True)


def run() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is required")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    run()
