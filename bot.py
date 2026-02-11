import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import os
import sqlite3
from dotenv import load_dotenv
import asyncio

# 1. 環境設定
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# --- 修正後（環境変数から読み込む） ---
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', '0'))
OWNER_ID       = int(os.getenv('OWNER_ID', '0'))

# 2. データベース準備
db = sqlite3.connect('serina_beta.db') 
cursor = db.cursor()

# テーブル作成
cursor.execute('''CREATE TABLE IF NOT EXISTS reminders 
                  (user_id INTEGER PRIMARY KEY, 
                   next_time TEXT, 
                   channel_id INTEGER, 
                   mention_enabled INTEGER DEFAULT 1,
                   reset_mention_enabled INTEGER DEFAULT 1)''')

# 既存DBへのカラム追加救護策
try:
    cursor.execute("ALTER TABLE reminders ADD COLUMN reset_mention_enabled INTEGER DEFAULT 1")
    db.commit()
except sqlite3.OperationalError:
    pass

# --- DB操作用関数 ---
def db_get_reminder(user_id):
    cursor.execute("SELECT next_time, channel_id, mention_enabled, reset_mention_enabled FROM reminders WHERE user_id = ?", (user_id,))
    return cursor.fetchone()

def db_add_reminder(user_id, channel_id, start_dt=None):
    base_time = start_dt if start_dt else datetime.datetime.now()
    next_time = (base_time + datetime.timedelta(hours=3)).isoformat()
    cursor.execute(
        "INSERT OR REPLACE INTO reminders (user_id, next_time, channel_id, mention_enabled, reset_mention_enabled) VALUES (?, ?, ?, 1, 1)", 
        (user_id, next_time, channel_id)
    )
    db.commit()
    return next_time

def db_remove_reminder(user_id):
    if db_get_reminder(user_id):
        cursor.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
        db.commit()
        return True
    return False

# 3. ボットクラス定義（ハイブリッド対応）
class SerinaHybridBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True 
        intents.members = True 
        super().__init__(command_prefix=['!!','??'], intents=intents, help_command=None)

    async def setup_hook(self):
        # スラッシュコマンドを同期
        await self.tree.sync()

bot = SerinaHybridBot()

# --- ⏰ 定期タスク ---

@tasks.loop(seconds=30)
async def check_reminders():
    now = datetime.datetime.now()
    cursor.execute("SELECT user_id, next_time, channel_id, mention_enabled FROM reminders")
    for user_id, next_time_str, channel_id, mention_enabled in cursor.fetchall():
        next_time = datetime.datetime.fromisoformat(next_time_str)
        if now >= next_time:
            channel = bot.get_channel(channel_id)
            if channel:
                prefix = f"<@{user_id}> " if mention_enabled == 1 else ""
                await channel.send(f"{prefix}先生、カフェ更新から3時間です！生徒さんに会いに行きましょう。")
                
                new_time = (next_time + datetime.timedelta(hours=3)).isoformat()
                cursor.execute("UPDATE reminders SET next_time = ? WHERE user_id = ?", (new_time, user_id))
    db.commit()

@tasks.loop(minutes=5)
async def update_status_task():
    count = len(bot.guilds)
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.competing, 
        name=f"{count}箇所の救護活動 | !!help"
    ))

@tasks.loop(seconds=60)
async def daily_reset_task():
    now = datetime.datetime.now()
    if (now.hour == 4 or now.hour == 16) and now.minute == 0:
        cursor.execute("SELECT user_id, channel_id, reset_mention_enabled FROM reminders")
        all_data = cursor.fetchall()
        if not all_data: return

        msg = "先生、4時になりました。夜更かしは禁物ですよ？リマインダーを整理しますね。" if now.hour == 4 \
              else "先生、16時です。午後のリマインダーを整理しておきました。また呼んでくださいね。"

        channel_map = {}
        for user_id, ch_id, r_mention in all_data:
            if ch_id not in channel_map: channel_map[ch_id] = []
            if r_mention == 1: channel_map[ch_id].append(f"<@{user_id}>")

        for ch_id, mentions in channel_map.items():
            ch = bot.get_channel(ch_id)
            if ch:
                m_prefix = " ".join(mentions) + " " if mentions else ""
                await ch.send(f"{m_prefix}{msg}")
        
        cursor.execute("DELETE FROM reminders")
        db.commit()

# --- 🛡️ イベント ---
@bot.event
async def on_ready():
    if not check_reminders.is_running(): check_reminders.start()
    if not update_status_task.is_running(): update_status_task.start()
    if not daily_reset_task.is_running(): daily_reset_task.start()
    print(f'Logged in as {bot.user} (Complete Hybrid Mode)')

# --- 🚀 ハイブリッドコマンド (!! と / 両対応) ---

@bot.hybrid_command(name="help", description="セリナがお手伝いできる内容を表示します")
async def help_command(ctx):
    embed = discord.Embed(
        title="🍀 救護騎士団セリナ・活動のご案内", 
        color=0xffc0cb,
        description="先生、お疲れ様です！私がお手伝いできる内容をまとめました。"
    )
    embed.add_field(
        name="🏥 メイン機能", 
        value="**!!カフェ [時間]**\n3時間おきに通知します。`06:30` のように時間指定も可能です。", 
        inline=False
    )
    embed.add_field(name="🔔 通知設定", value="**!!メンション ON/OFF**\nカフェ通知のメンション切り替え\n**!!リセットメンション ON/OFF**\n4時/16時整理時のメンション切り替え", inline=False)
    embed.add_field(name="🔍 状態確認", value="**!!確認**: 次回の予定を表示\n**!!解除**: 救護活動を停止", inline=True)
    embed.add_field(name="⚙️ その他", value="**!!ping**: 応答速度の確認\n**!!要望 [内容]**: 開発者さんへ送信", inline=True)
    embed.add_field(name="📊 統計", value="**!!status**: ボットの活動状況を表示", inline=False)
    embed.set_footer(text="いつでも先生をお呼びしますので、安心してくださいね。")
    await ctx.send(embed=embed)

@bot.hybrid_command(name="カフェ", description="3時間おきのリマインダーを開始します")
@app_commands.describe(time_str="開始時間を指定（例：06:30）※任意")
async def cafe(ctx, time_str: str = None):
    start_dt = None
    if time_str:
        try:
            parsed_time = datetime.datetime.strptime(time_str, "%H:%M")
            now = datetime.datetime.now()
            start_dt = now.replace(hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0)
            if start_dt < now: start_dt += datetime.timedelta(days=1)
        except ValueError:
            return await ctx.send("先生、時間は `06:30` のような形式で教えていただけますか？")

    next_time_iso = db_add_reminder(ctx.author.id, ctx.channel.id, start_dt)
    next_dt = datetime.datetime.fromisoformat(next_time_iso)
    next_display = next_dt.strftime("%H:%M")

    msg = f"{time_str}を基準に設定しました。" if time_str else "了解しました。"
    await ctx.send(f"{ctx.author.display_name}先生、{msg}次は **{next_display}** 頃にお呼びしますね。")

@bot.hybrid_command(name="確認", description="次回の通知予定時間を確認します")
async def status_check(ctx):
    data = db_get_reminder(ctx.author.id)
    if data:
        next_t = datetime.datetime.fromisoformat(data[0]).strftime('%H時%M分')
        await ctx.send(f"先生、次は **{next_t}頃** に通知予定ですよ！")
    else:
        await ctx.send("リマインダーを設定していません！`!!カフェ` で開始できますよ？")

@bot.hybrid_command(name="解除", description="実行中のリマインダーを停止します")
async def stop_reminder(ctx):
    if db_remove_reminder(ctx.author.id):
        await ctx.send("リマインダーを解除しました。また必要になったら呼んでくださいね。")
    else:
        await ctx.send("現在実行中のリマインダーはありませんよ？")

# --- 1. カフェ通知のメンション設定 ---
@bot.hybrid_command(name="メンション", description="カフェ通知時のメンションを切り替えます")
@app_commands.describe(setting="ON または OFF")
async def toggle_mention(ctx, setting: str = None):
    data = db_get_reminder(ctx.author.id)
    if not data: 
        return await ctx.send("先に `!!カフェ` でリマインダーを開始してくださいね。")
    
    # 引数なし：現在の状態を表示
    if setting is None:
        current = "ON" if data[2] == 1 else "OFF"
        return await ctx.send(f"現在は **{current}** になっています。`!!メンション ON/OFF` で変えられますよ。")

    # バリデーション：ON/OFF 以外を弾く
    if setting.upper() not in ["ON", "OFF"]:
        return await ctx.send("先生、設定は `ON` か `OFF` で教えてくださいね？")

    val = 1 if setting.upper() == "ON" else 0
    cursor.execute("UPDATE reminders SET mention_enabled = ? WHERE user_id = ?", (val, ctx.author.id))
    db.commit()
    await ctx.send(f"了解しました！メンションを **{setting.upper()}** に設定しました。")

# --- 2. 4時/16時リセットのメンション設定 ---
@bot.hybrid_command(name="リセットメンション", description="4時/16時の整理時のメンションを切り替えます")
@app_commands.describe(setting="ON または OFF")
async def toggle_reset_mention(ctx, setting: str = None):
    data = db_get_reminder(ctx.author.id)
    if not data: 
        return await ctx.send("先に `!!カフェ` を使ってくださいね。")
    
    # 引数なし：現在の状態を表示（ここを修正！）
    if setting is None:
        current = "ON" if data[3] == 1 else "OFF" # インデックス[3]を参照
        return await ctx.send(f"現在は **{current}** になっています。`!!リセットメンション ON/OFF` で変えられますよ。")

    # バリデーション：ON/OFF 以外を弾く
    if setting.upper() not in ["ON", "OFF"]:
        return await ctx.send("先生、設定は `ON` か `OFF` で教えてくださいね？")

    val = 1 if setting.upper() == "ON" else 0
    cursor.execute("UPDATE reminders SET reset_mention_enabled = ? WHERE user_id = ?", (val, ctx.author.id))
    db.commit()
    await ctx.send(f"了解しました！リセット時のメンションを **{setting.upper()}** に設定しました。")

@bot.hybrid_command(name="要望", description="開発者に要望を送信します")
@app_commands.describe(message="要望の内容")
async def feedback(ctx, *, message: str):
    log_ch = bot.get_channel(LOG_CHANNEL_ID)
    if log_ch:
        embed = discord.Embed(title="💌 要望届", color=discord.Color.gold())
        embed.add_field(name="送信者", value=f"{ctx.author.name} ({ctx.author.id})", inline=False)
        embed.add_field(name="内容", value=message, inline=False)
        await log_ch.send(embed=embed)
        await ctx.send("救護の参考にさせていただきますね。ご協力ありがとうございます！")

@bot.hybrid_command(name="ping", description="私の応答速度を確認します")
async def ping(ctx):
    # bot.latency は秒単位なので、1000倍してミリ秒(ms)に変換します
    await ctx.send(f"ぽん！ですね ({round(bot.latency * 1000)}ms)")

@bot.hybrid_command(name="status", description="ボットの稼働状況を表示します")
async def bot_status(ctx):
    guild_count = len(bot.guilds)
    total_members = sum(g.member_count for g in bot.guilds)
    if ctx.author.id == OWNER_ID:
        msg = "🏥 **管理者用：導入サーバー一覧**\n"
        for guild in bot.guilds: msg += f"・{guild.name} ({guild.member_count}名)\n"
    else:
        msg = f"🏥 **現在の活動規模**\n現在、**{guild_count}箇所**のサーバーで合計 **{total_members}名** の先生を見守っていますよ。"
    await ctx.send(msg)

# --- 🛠️ 管理者専用 (!!コマンドのみ) ---
@bot.command(name="一斉送信")
async def broadcast(ctx, *, message: str):
    if ctx.author.id != OWNER_ID: return
    
    cursor.execute("SELECT DISTINCT channel_id FROM reminders")
    channels = cursor.fetchall()
    target_count = len(channels)
    
    if target_count == 0:
        return await ctx.send("送信対象のチャンネルが見つかりませんでした。")

    # 確認メッセージを表示
    await ctx.send(
        f"📢 **一斉送信の確認**\n"
        f"この内容を **{target_count}箇所のサーバー** に送信します。よろしいですか？\n"
        f"実行する場合は、30秒以内に **「はい」** と入力してください。"
    )

    # 先生（OWNER）からの「はい」を待つ
    def check(m):
        return m.author.id == OWNER_ID and m.channel == ctx.channel and m.content == "はい"

    try:
        await bot.wait_for('message', check=check, timeout=30.0)
    except asyncio.TimeoutError:
        return await ctx.send("⌛ 時間切れです。送信を中止しました。")

    # 送信開始
    sent_count = 0
    status_msg = await ctx.send("🚀 送信を開始します...")
    
    for (ch_id,) in channels:
        ch = bot.get_channel(ch_id)
        if ch:
            try:
                await ch.send(f"📢 **先生へのお知らせ**\n\n{message}")
                sent_count += 1
            except Exception:
                pass 
                
    await status_msg.edit(content=f"✅ **送信完了！**\n{sent_count}箇所のチャンネルへ届けました。")


bot.run(TOKEN)
