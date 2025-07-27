import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
from datetime import datetime, timedelta
import os
import sqlite3
import json
from flask import Flask
import threading

# === Flask app ===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot działa"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# === Discord Bot Setup ===
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# === Stałe (ID roli i kanału logów) ===
MUTED_ROLE_ID = 1396541521003675718
LOG_CHANNEL_ID = 1396875096882417836

# Role uprawnień do komend
PERMISSIONS = {
    "warn": [1393370941811064972, 1393370832071426230, 1393370749661614080,
             1393370358408544328, 1393370252519145493, 1393370458740490351,
             1393370125083607174, 1393369936537194619, 1396460188298641418,
             1393368165567692911],
    "mute": [1393370749661614080, 1393370358408544328, 1393370252519145493,
             1393370458740490351, 1393370125083607174, 1393369936537194619,
             1396460188298641418, 1393368165567692911],
    "kick": [1393370358408544328, 1393370252519145493, 1393370458740490351,
             1393370125083607174, 1393369936537194619, 1396460188298641418,
             1393368165567692911],
    "ban": [1393370458740490351, 1393370125083607174, 1393369936537194619,
            1396460188298641418, 1393368165567692911]
}

def has_permission(interaction: discord.Interaction, command: str) -> bool:
    allowed_roles = PERMISSIONS.get(command, [])
    user_roles_ids = [role.id for role in interaction.user.roles]
    return any(role_id in user_roles_ids for role_id in allowed_roles)

# === Baza SQLite ===
class Database:
    def __init__(self, path="roles.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS muted_roles (
            user_id INTEGER PRIMARY KEY, 
            roles TEXT,
            unmute_time TEXT
        )''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            moderator_id INTEGER,
            reason TEXT,
            timestamp TEXT
        )''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS temp_bans (
            user_id INTEGER PRIMARY KEY,
            unban_time TEXT
        )''')
        self.conn.commit()

    def save_roles(self, user_id: int, roles, unmute_time: datetime):
        roles_ids = [role.id for role in roles]
        roles_json = json.dumps(roles_ids)
        self.cursor.execute('REPLACE INTO muted_roles (user_id, roles, unmute_time) VALUES (?, ?, ?)', 
                            (user_id, roles_json, unmute_time.isoformat()))
        self.conn.commit()

    def load_roles(self, user_id: int):
        self.cursor.execute('SELECT roles, unmute_time FROM muted_roles WHERE user_id = ?', (user_id,))
        result = self.cursor.fetchone()
        if result:
            roles = json.loads(result[0])
            unmute_time = datetime.fromisoformat(result[1])
            return roles, unmute_time
        return [], None

    def delete_roles(self, user_id: int):
        self.cursor.execute('DELETE FROM muted_roles WHERE user_id = ?', (user_id,))
        self.conn.commit()

    def add_warning(self, user_id: int, moderator_id: int, reason: str):
        timestamp = datetime.utcnow().isoformat()
        self.cursor.execute('INSERT INTO warnings (user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?)',
                       (user_id, moderator_id, reason, timestamp))
        self.conn.commit()

    def count_warnings(self, user_id: int) -> int:
        self.cursor.execute('SELECT COUNT(*) FROM warnings WHERE user_id = ?', (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else 0

    def clear_warnings(self, user_id: int):
        self.cursor.execute('DELETE FROM warnings WHERE user_id = ?', (user_id,))
        self.conn.commit()

    def add_temp_ban(self, user_id: int, unban_time: datetime):
        self.cursor.execute('REPLACE INTO temp_bans (user_id, unban_time) VALUES (?, ?)', (user_id, unban_time.isoformat()))
        self.conn.commit()

    def remove_temp_ban(self, user_id: int):
        self.cursor.execute('DELETE FROM temp_bans WHERE user_id = ?', (user_id,))
        self.conn.commit()

    def get_all_temp_bans(self):
        self.cursor.execute('SELECT user_id, unban_time FROM temp_bans')
        return self.cursor.fetchall()

    def get_all_mutes(self):
        self.cursor.execute('SELECT user_id, roles, unmute_time FROM muted_roles')
        return self.cursor.fetchall()

db = Database()

# === Rate limit helper do logów i DM ===
_last_message_times = {}

async def safe_send(destination, content=None, embed=None, delay=1):
    now = asyncio.get_event_loop().time()
    last = _last_message_times.get(destination.id, 0)
    diff = now - last
    if diff < delay:
        await asyncio.sleep(delay - diff)
    try:
        if content and embed:
            await destination.send(content=content, embed=embed)
        elif embed:
            await destination.send(embed=embed)
        elif content:
            await destination.send(content)
        else:
            return
        _last_message_times[destination.id] = asyncio.get_event_loop().time()
    except discord.HTTPException as e:
        if e.status == 429:
            print("429 Rate limit, czekam...")
            await asyncio.sleep(5)
        else:
            print(f"Błąd wysyłania wiadomości: {e}")
    except Exception as e:
        print(f"Nieznany błąd wysyłania wiadomości: {e}")

# === Task do automatycznego odbanowywania ===
@tasks.loop(minutes=1)
async def temp_ban_checker():
    await bot.wait_until_ready()
    for user_id, unban_time_str in db.get_all_temp_bans():
        unban_time = datetime.fromisoformat(unban_time_str)
        if datetime.utcnow() >= unban_time:
            for guild in bot.guilds:
                try:
                    ban_entry = await guild.fetch_ban(discord.Object(id=user_id))
                    if ban_entry:
                        await guild.unban(discord.Object(id=user_id), reason="Koniec tymczasowego bana")
                        db.remove_temp_ban(user_id)
                        log_channel = guild.get_channel(LOG_CHANNEL_ID)
                        if log_channel:
                            await safe_send(log_channel, f"Użytkownik <@{user_id}> został automatycznie odbanowany po wygaśnięciu bana.")
                except discord.NotFound:
                    db.remove_temp_ban(user_id)
                except discord.HTTPException as e:
                    if e.status == 429:
                        print("Rate limit przy odbanowywaniu, czekam...")
                        await asyncio.sleep(5)
                    else:
                        print(f"Błąd HTTP: {e}")
                except Exception as e:
                    print(f"❌ Błąd podczas odbanowywania użytkownika {user_id}: {e}")

# === Task do automatycznego zdejmowania muta ===
@tasks.loop(seconds=30)
async def unmute_checker():
    await bot.wait_until_ready()
    for user_id, roles_json, unmute_time_str in db.get_all_mutes():
        unmute_time = datetime.fromisoformat(unmute_time_str)
        if datetime.utcnow() >= unmute_time:
            for guild in bot.guilds:
                member = guild.get_member(user_id)
                if member:
                    try:
                        muted_role = guild.get_role(MUTED_ROLE_ID)
                        if muted_role in member.roles:
                            await member.remove_roles(muted_role, reason="Koniec muta")
                        saved_roles_ids = json.loads(roles_json)
                        roles_to_add = [guild.get_role(rid) for rid in saved_roles_ids if guild.get_role(rid) is not None]
                        await member.add_roles(*roles_to_add, reason="Przywrócenie ról po muctie")
                        db.delete_roles(user_id)
                        log_channel = guild.get_channel(LOG_CHANNEL_ID)
                        if log_channel:
                            await safe_send(log_channel, f"Użytkownik <@{user_id}> został automatycznie odmutowany.")
                    except Exception as e:
                        print(f"❌ Błąd podczas odmutowywania użytkownika {user_id}: {e}")

# === Check perm decorator ===
def check_perm(command_name):
    def predicate(interaction: discord.Interaction):
        if has_permission(interaction, command_name):
            return True
        else:
            raise app_commands.CheckFailure(f"Brak uprawnień do komendy {command_name}")
    return app_commands.check(predicate)

# === Komendy ===

@bot.event
async def on_ready():
    print(f"Bot zalogowany jako {bot.user}!")
    temp_ban_checker.start()
    unmute_checker.start()
    try:
        synced = await bot.tree.sync()
        print(f"Zsynchronizowano {len(synced)} komend slash.")
    except Exception as e:
        print(f"Błąd synchronizacji komend: {e}")

# -- warn --
@bot.tree.command(name="warn", description="Ostrzeż użytkownika.")
@check_perm("warn")
@app_commands.describe(user="Użytkownik do ostrzeżenia", reason="Powód ostrzeżenia")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    if user == interaction.user:
        await interaction.response.send_message("Nie możesz się ostrzec sam.", ephemeral=True)
        return
    db.add_warning(user.id, interaction.user.id, reason)
    count = db.count_warnings(user.id)
    await interaction.response.send_message(f"Użytkownik {user.mention} został ostrzeżony. Liczba warnów: {count}. Powód: {reason}")
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await safe_send(log_channel, f"**WARN:** {user.mention} otrzymał warn od {interaction.user.mention}. Powód: {reason}. Razem warnów: {count}")

    # Automatyczne bany
    if count == 5:
        try:
            await interaction.guild.ban(user, reason="Automatyczny ban za 5 warnów", delete_message_days=0)
            unban_time = datetime.utcnow() + timedelta(days=3)
            db.add_temp_ban(user.id, unban_time)
            if log_channel:
                await safe_send(log_channel, f"Użytkownik {user.mention} został zbanowany na 3 dni (5 warnów).")
        except Exception as e:
            await interaction.followup.send(f"Nie udało się zbanować użytkownika: {e}", ephemeral=True)
    elif count == 10:
        try:
            await interaction.guild.ban(user, reason="Automatyczny ban za 10 warnów", delete_message_days=0)
            unban_time = datetime.utcnow() + timedelta(days=7)
            db.add_temp_ban(user.id, unban_time)
            if log_channel:
                await safe_send(log_channel, f"Użytkownik {user.mention} został zbanowany na 7 dni (10 warnów).")
        except Exception as e:
            await interaction.followup.send(f"Nie udało się zbanować użytkownika: {e}", ephemeral=True)
    elif count >= 20:
        try:
            await interaction.guild.ban(user, reason="Automatyczny ban permanentny za 20 warnów", delete_message_days=0)
            db.remove_temp_ban(user.id)  # już ban permanentny
            if log_channel:
                await safe_send(log_channel, f"Użytkownik {user.mention} został zbanowany na stałe (20+ warnów).")
        except Exception as e:
            await interaction.followup.send(f"Nie udało się zbanować użytkownika: {e}", ephemeral=True)

# -- warns --
@bot.tree.command(name="warns", description="Wyświetl wszystkie warny użytkownika.")
@check_perm("warn")
@app_commands.describe(user="Użytkownik do sprawdzenia")
async def warns(interaction: discord.Interaction, user: discord.Member):
    db.cursor.execute('SELECT moderator_id, reason, timestamp FROM warnings WHERE user_id = ? ORDER BY timestamp DESC', (user.id,))
    warns = db.cursor.fetchall()
    if not warns:
        await interaction.response.send_message(f"{user.mention} nie ma żadnych warnów.", ephemeral=True)
        return
    embed = discord.Embed(title=f"Warny użytkownika {user}", color=discord.Color.orange())
    for i, (mod_id, reason, timestamp) in enumerate(warns, 1):
        mod = interaction.guild.get_member(mod_id)
        mod_name = mod.display_name if mod else f"ID {mod_id}"
        embed.add_field(name=f"{i}. Ostrzeżenie", value=f"Moderator: {mod_name}\nPowód: {reason}\nData: {timestamp}", inline=False)
    await interaction.response.send_message(embed=embed)

# -- mute --
@bot.tree.command(name="mute", description="Wycisz użytkownika na określony czas.")
@check_perm("mute")
@app_commands.describe(user="Użytkownik do wyciszenia", czas="Czas w minutach", reason="Powód wyciszenia")
async def mute(interaction: discord.Interaction, user: discord.Member, czas: int, reason: str):
    if user == interaction.user:
        await interaction.response.send_message("Nie możesz się wyciszyć sam.", ephemeral=True)
        return
    if MUTED_ROLE_ID in [role.id for role in user.roles]:
        await interaction.response.send_message("Użytkownik jest już wyciszony.", ephemeral=True)
        return
    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("Rola Muted nie istnieje na serwerze.", ephemeral=True)
        return

    # Zapisz obecne role użytkownika (bez @everyone i muted)
    roles_to_save = [r for r in user.roles if r.id != interaction.guild.id and r.id != MUTED_ROLE_ID]
    db.save_roles(user.id, roles_to_save, datetime.utcnow() + timedelta(minutes=czas))

    # Usuń wszystkie inne role i dodaj muted
    try:
        await user.remove_roles(*roles_to_save, reason=f"Mute na {czas} minut: {reason}")
        await user.add_roles(muted_role, reason=f"Muted na {czas} minut: {reason}")
        await interaction.response.send_message(f"Użytkownik {user.mention} został wyciszony na {czas} minut. Powód: {reason}")
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"**MUTE:** {user.mention} wyciszony przez {interaction.user.mention} na {czas} minut. Powód: {reason}")
    except Exception as e:
        await interaction.response.send_message(f"Nie udało się wyciszyć użytkownika: {e}", ephemeral=True)

# -- unmute --
@bot.tree.command(name="unmute", description="Odwołaj wyciszenie użytkownika.")
@check_perm("mute")
@app_commands.describe(user="Użytkownik do odmutowania")
async def unmute(interaction: discord.Interaction, user: discord.Member):
    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("Rola Muted nie istnieje na serwerze.", ephemeral=True)
        return
    if muted_role not in user.roles:
        await interaction.response.send_message("Użytkownik nie jest wyciszony.", ephemeral=True)
        return

    try:
        await user.remove_roles(muted_role, reason=f"Odmutowanie przez {interaction.user}")
        saved_roles, _ = db.load_roles(user.id)
        roles_to_add = [interaction.guild.get_role(rid) for rid in saved_roles if interaction.guild.get_role(rid)]
        await user.add_roles(*roles_to_add, reason=f"Przywrócenie ról po odmutowaniu")
        db.delete_roles(user.id)
        await interaction.response.send_message(f"Użytkownik {user.mention} został odmutowany.")
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"**UNMUTE:** {user.mention} odmutowany przez {interaction.user.mention}.")
    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas odmutowywania: {e}", ephemeral=True)

# -- kick --
@bot.tree.command(name="kick", description="Wyrzuć użytkownika z serwera.")
@check_perm("kick")
@app_commands.describe(user="Użytkownik do wyrzucenia", reason="Powód wyrzucenia")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str):
    if user == interaction.user:
        await interaction.response.send_message("Nie możesz wyrzucić samego siebie.", ephemeral=True)
        return
    try:
        await user.kick(reason=f"Wyrzucony przez {interaction.user}: {reason}")
        await interaction.response.send_message(f"Użytkownik {user.mention} został wyrzucony z serwera. Powód: {reason}")
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"**KICK:** {user.mention} wyrzucony przez {interaction.user.mention}. Powód: {reason}")
    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas wyrzucania: {e}", ephemeral=True)

# -- ban --
@bot.tree.command(name="ban", description="Zbanuj użytkownika na stałe lub tymczasowo.")
@check_perm("ban")
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód bana", czas="Czas bana w dniach (0 = permanentny)")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str, czas: int = 0):
    if user == interaction.user:
        await interaction.response.send_message("Nie możesz zbanować samego siebie.", ephemeral=True)
        return
    try:
        await interaction.guild.ban(user, reason=f"Ban od {interaction.user}: {reason}", delete_message_days=0)
        if czas > 0:
            unban_time = datetime.utcnow() + timedelta(days=czas)
            db.add_temp_ban(user.id, unban_time)
            msg = f"Użytkownik {user.mention} został zbanowany na {czas} dni. Powód: {reason}"
        else:
            db.remove_temp_ban(user.id)
            msg = f"Użytkownik {user.mention} został zbanowany na stałe. Powód: {reason}"
        await interaction.response.send_message(msg)
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"**BAN:** {user.mention} zbanowany przez {interaction.user.mention}. Powód: {reason}. Czas bana: {'permanentny' if czas == 0 else f'{czas} dni'}.")
    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas banowania: {e}", ephemeral=True)

# -- unban --
@bot.tree.command(name="unban", description="Odbanuj użytkownika.")
@check_perm("ban")
@app_commands.describe(user_id="ID użytkownika do odbanowania")
async def unban(interaction: discord.Interaction, user_id: int):
    try:
        user_obj = discord.Object(id=user_id)
        await interaction.guild.unban(user_obj, reason=f"Odbanowanie przez {interaction.user}")
        db.remove_temp_ban(user_id)
        await interaction.response.send_message(f"Użytkownik z ID {user_id} został odbanowany.")
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"Użytkownik z ID {user_id} został odbanowany przez {interaction.user}.")
    except discord.NotFound:
        await interaction.response.send_message("Użytkownik nie jest zbanowany.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("Nie mam uprawnień do odbanowywania tego użytkownika.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Wystąpił błąd: {e}", ephemeral=True)

# === Uruchomienie Flaska w osobnym wątku ===
flask_thread = threading.Thread(target=run_flask)
flask_thread.start()

# === Uruchomienie bota ===
TOKEN = os.getenv("TOKEN")  # Podstaw swój token do zmiennej środowiskowej DISCORD_TOKEN
bot.run(TOKEN)
