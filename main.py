import json
import os
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import tasks

from music import MusicPlayer

CONFIG_FILE = "config.json"
DATA_FILE = "stay.json"


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "stay": False,
            "guild_id": None,
            "voice_channel_id": None,
        }

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


config = load_config()
TOKEN = config["TOKEN"]
GUILD_ID = config.get("GUILD_ID")  # サーバーIDを入れるとスラッシュコマンド反映が速くなります
data = load_data()

intents = discord.Intents.default()
intents.voice_states = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
music = MusicPlayer(client, data, save_data)


async def connect_to_channel(channel: discord.VoiceChannel):
    guild = channel.guild
    vc = guild.voice_client

    if vc and vc.is_connected():
        await vc.move_to(channel)
    else:
        await channel.connect(self_deaf=True)

    data["guild_id"] = guild.id
    data["voice_channel_id"] = channel.id
    save_data(data)


@client.event
async def on_ready():
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        synced = await tree.sync(guild=guild)
        print(f"ログインしました: {client.user}")
        print("このサーバーに同期したコマンド:", ", ".join('/' + cmd.name for cmd in synced))
    else:
        synced = await tree.sync()
        print(f"ログインしました: {client.user}")
        print("グローバル同期したコマンド:", ", ".join('/' + cmd.name for cmd in synced))
        print("※ グローバルコマンドはDiscord側の反映に時間がかかることがあります。config.jsonにGUILD_IDを追加すると即反映されます。")

    if not keep_connection.is_running():
        keep_connection.start()

    if not time_notice.is_running():
        time_notice.start()


@tree.command(name="join", description="あなたがいるボイスチャンネルに参加します")
async def join(interaction: discord.Interaction):
    if interaction.user.voice is None:
        await interaction.response.send_message("先にボイスチャンネルに入ってください。", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    await connect_to_channel(channel)
    await interaction.response.send_message(f"✅ `{channel.name}` に参加しました。")


@tree.command(name="leave", description="ボイスチャンネルから退出します")
async def leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client

    if vc is None or not vc.is_connected():
        await interaction.response.send_message("現在、ボイスチャンネルに参加していません。", ephemeral=True)
        return

    await vc.disconnect()
    data["voice_channel_id"] = None
    save_data(data)
    await interaction.response.send_message("👋 ボイスチャンネルから退出しました。")


@tree.command(name="stay", description="誰もいなくてもVCに残る設定を切り替えます")
@app_commands.describe(mode="on または off")
async def stay(interaction: discord.Interaction, mode: str):
    mode = mode.lower()

    if mode not in ["on", "off"]:
        await interaction.response.send_message("使い方: `/stay on` または `/stay off`", ephemeral=True)
        return

    data["stay"] = mode == "on"
    save_data(data)

    if data["stay"]:
        await interaction.response.send_message("✅ StayモードをONにしました。誰もいなくてもVCに残ります。")
    else:
        await interaction.response.send_message("✅ StayモードをOFFにしました。")


@tree.command(name="status", description="Botの現在の状態を表示します")
async def status(interaction: discord.Interaction):
    vc = interaction.guild.voice_client

    if vc and vc.is_connected():
        channel_name = vc.channel.name
        connected = "接続中"
    else:
        channel_name = "なし"
        connected = "未接続"

    stay_status = "ON" if data.get("stay") else "OFF"
    queue_count = music.queue_size(interaction.guild.id)
    now_playing = music.now_playing_title(interaction.guild.id) or "なし"

    message = (
        "📊 **現在の状態**\n"
        f"接続状態: {connected}\n"
        f"接続先VC: {channel_name}\n"
        f"Stayモード: {stay_status}\n"
        f"再生中: {now_playing}\n"
        f"待機曲数: {queue_count}"
    )

    await interaction.response.send_message(message)


@tree.command(name="play", description="YouTubeのURLまたは曲名検索で音声を再生します")
@app_commands.describe(query="YouTube URL または 曲名")
async def play(interaction: discord.Interaction, query: str):
    await music.play(interaction, query)


@tree.command(name="pause", description="再生中の音楽を一時停止します")
async def pause(interaction: discord.Interaction):
    await music.pause(interaction)


@tree.command(name="resume", description="一時停止中の音楽を再開します")
async def resume(interaction: discord.Interaction):
    await music.resume(interaction)


@tree.command(name="stop", description="音楽を停止してキューを空にします")
async def stop(interaction: discord.Interaction):
    await music.stop(interaction)


@tree.command(name="skip", description="現在の曲をスキップします")
async def skip(interaction: discord.Interaction):
    await music.skip(interaction)


@tree.command(name="queue", description="再生待ちの曲を表示します")
async def queue(interaction: discord.Interaction):
    await music.show_queue(interaction)


@tasks.loop(seconds=30)
async def keep_connection():
    if not data.get("stay"):
        return

    guild_id = data.get("guild_id")
    channel_id = data.get("voice_channel_id")

    if guild_id is None or channel_id is None:
        return

    guild = client.get_guild(guild_id)
    if guild is None:
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        return

    vc = guild.voice_client

    if vc is None or not vc.is_connected():
        try:
            await channel.connect(self_deaf=True)
            print("VCに自動再接続しました")
        except Exception as e:
            print(f"再接続失敗: {e}")


@tasks.loop(hours=12)
async def time_notice():
    for vc in client.voice_clients:
        if not vc or not vc.is_connected():
            continue

        voice_channel = vc.channel
        text_channel = None

        for ch in voice_channel.guild.text_channels:
            permissions = ch.permissions_for(voice_channel.guild.me)
            if permissions.send_messages:
                text_channel = ch
                break

        if text_channel:
            now = datetime.now().strftime("%Y/%m/%d %H:%M")
            await text_channel.send(
                f"⏰ `{voice_channel.name}` に接続してから12時間経過しました。\n現在時刻: {now}"
            )


@client.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    if not data.get("stay"):
        return

    guild = member.guild
    vc = guild.voice_client

    if vc is None or not vc.is_connected():
        return

    data["guild_id"] = guild.id
    data["voice_channel_id"] = vc.channel.id
    save_data(data)


client.run(TOKEN)
