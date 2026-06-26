import asyncio
import functools
from collections import defaultdict, deque
from dataclasses import dataclass

import discord
import yt_dlp

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


@dataclass
class Track:
    title: str
    webpage_url: str
    stream_url: str
    requester: str


class MusicPlayer:
    def __init__(self, client: discord.Client, data: dict, save_data):
        self.client = client
        self.data = data
        self.save_data = save_data
        self.queues = defaultdict(deque)
        self.now_playing = {}
        self.text_channels = {}
        self.locks = defaultdict(asyncio.Lock)

    def queue_size(self, guild_id: int) -> int:
        return len(self.queues[guild_id])

    def now_playing_title(self, guild_id: int):
        track = self.now_playing.get(guild_id)
        return track.title if track else None

    async def ensure_voice(self, interaction: discord.Interaction):
        if interaction.user.voice is None:
            await interaction.followup.send("先にボイスチャンネルに入ってください。", ephemeral=True)
            return None

        channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client

        if vc and vc.is_connected():
            if vc.channel.id != channel.id:
                await vc.move_to(channel)
        else:
            vc = await channel.connect(self_deaf=True)

        self.data["guild_id"] = interaction.guild.id
        self.data["voice_channel_id"] = channel.id
        self.save_data(self.data)
        return vc

    async def extract_track(self, query: str, requester: str) -> Track:
        loop = asyncio.get_running_loop()
        func = functools.partial(ytdl.extract_info, query, download=False)
        info = await loop.run_in_executor(None, func)

        if "entries" in info:
            info = info["entries"][0]

        return Track(
            title=info.get("title", "タイトル不明"),
            webpage_url=info.get("webpage_url", query),
            stream_url=info["url"],
            requester=requester,
        )

    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()

        vc = await self.ensure_voice(interaction)
        if vc is None:
            return

        try:
            track = await self.extract_track(query, interaction.user.display_name)
        except Exception as e:
            await interaction.followup.send(f"❌ 曲の取得に失敗しました。\n`{e}`")
            return

        guild_id = interaction.guild.id
        self.text_channels[guild_id] = interaction.channel
        self.queues[guild_id].append(track)

        await interaction.followup.send(f"🎵 キューに追加しました: **{track.title}**")

        if not vc.is_playing() and not vc.is_paused():
            await self.play_next(interaction.guild)

    async def play_next(self, guild: discord.Guild):
        guild_id = guild.id
        vc = guild.voice_client

        if vc is None or not vc.is_connected():
            return

        if not self.queues[guild_id]:
            self.now_playing[guild_id] = None
            return

        track = self.queues[guild_id].popleft()
        self.now_playing[guild_id] = track

        source = discord.FFmpegPCMAudio(track.stream_url, **FFMPEG_OPTIONS)

        def after_play(error):
            if error:
                print(f"再生エラー: {error}")
            fut = asyncio.run_coroutine_threadsafe(self.play_next(guild), self.client.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"次曲再生エラー: {e}")

        vc.play(source, after=after_play)

        text_channel = self.text_channels.get(guild_id)
        if text_channel:
            try:
                await text_channel.send(f"▶️ 再生開始: **{track.title}**")
            except Exception:
                pass

    async def pause(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc is None or not vc.is_connected():
            await interaction.response.send_message("VCに接続していません。", ephemeral=True)
            return
        if not vc.is_playing():
            await interaction.response.send_message("現在再生中の曲がありません。", ephemeral=True)
            return
        vc.pause()
        await interaction.response.send_message("⏸️ 一時停止しました。")

    async def resume(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc is None or not vc.is_connected():
            await interaction.response.send_message("VCに接続していません。", ephemeral=True)
            return
        if not vc.is_paused():
            await interaction.response.send_message("一時停止中ではありません。", ephemeral=True)
            return
        vc.resume()
        await interaction.response.send_message("▶️ 再開しました。")

    async def stop(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        vc = interaction.guild.voice_client
        self.queues[guild_id].clear()
        self.now_playing[guild_id] = None

        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

        await interaction.response.send_message("⏹️ 停止してキューを空にしました。")

    async def skip(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc is None or not vc.is_connected():
            await interaction.response.send_message("VCに接続していません。", ephemeral=True)
            return
        if not vc.is_playing() and not vc.is_paused():
            await interaction.response.send_message("スキップする曲がありません。", ephemeral=True)
            return
        vc.stop()
        await interaction.response.send_message("⏭️ スキップしました。")

    async def show_queue(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        current = self.now_playing.get(guild_id)
        queue = list(self.queues[guild_id])

        lines = ["📃 **キュー**"]
        lines.append(f"再生中: {current.title if current else 'なし'}")

        if not queue:
            lines.append("待機中の曲: なし")
        else:
            lines.append("待機中:")
            for i, track in enumerate(queue[:10], start=1):
                lines.append(f"{i}. {track.title} / 追加者: {track.requester}")
            if len(queue) > 10:
                lines.append(f"他 {len(queue) - 10} 曲")

        await interaction.response.send_message("\n".join(lines))
