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
            # możesz tu spróbować retry, ale uważaj żeby nie zapętlić
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
                        # Usuń rolę muta i przywróć stare role
                        muted_role = guild.get_role(MUTED_ROLE_ID)
                        if muted_role in member.roles:
                            await member.remove_roles(muted_role, reason="Koniec muta")
                        saved_roles_ids = json.loads(roles_json)
                        roles_to_add = [guild.get_role(rid) for rid in saved_roles_ids if guild.get_role(rid) is not None]
                        await member.add_roles(*roles_to_add, reason="Przywrócenie ról po muctie")
                        db.delete_roles(user_id)
                        log_channel = guild.get_channel(LOG_CHANNEL_ID)
                        if log_channel:
                            await safe_send(log_channel, f"Użytkownik <@{user_id}> został automatycznie odmutowany po wygaśnięciu muta.")
                    except discord.HTTPException as e:
                        if e.status == 429:
                            print("Rate limit przy odmutowywaniu, czekam...")
                            await asyncio.sleep(5)
                        else:
                            print(f"Błąd HTTP: {e}")
                    except Exception as e:
                        print(f"❌ Błąd podczas odmutowywania użytkownika {user_id}: {e}")

# === Komendy ===

# Sprawdzanie uprawnień w app_commands
def check_perm(command_name):
    async def predicate(interaction: discord.Interaction) -> bool:
        if has_permission(interaction, command_name):
            return True
        await interaction.response.send_message("Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return False
    return app_commands.check(predicate)

@bot.event
async def on_ready():
    print(f'Zalogowano jako {bot.user} ({bot.user.id})')
    temp_ban_checker.start()
    unmute_checker.start()

# Komenda /mute
@bot.tree.command(name="mute", description="Mute użytkownika na określony czas w minutach.")
@check_perm("mute")
@app_commands.describe(user="Użytkownik do mutowania", time="Czas muta w minutach")
async def mute(interaction: discord.Interaction, user: discord.Member, time: int):
    if user.id == interaction.user.id:
        await interaction.response.send_message("Nie możesz zmutować siebie.", ephemeral=True)
        return
    if time <= 0:
        await interaction.response.send_message("Czas muta musi być większy niż 0.", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("Rola muta nie istnieje na serwerze.", ephemeral=True)
        return

    if muted_role in user.roles:
        await interaction.response.send_message("Użytkownik jest już zmutowany.", ephemeral=True)
        return

    # Zapisz aktualne role użytkownika (bez roli @everyone i roli muta)
    roles_to_save = [role for role in user.roles if role != interaction.guild.default_role and role != muted_role]

    unmute_time = datetime.utcnow() + timedelta(minutes=time)

    try:
        # Zapisz role i czas odmutowania do bazy
        db.save_roles(user.id, roles_to_save, unmute_time)
        # Usuń stare role (poza @everyone)
        await user.remove_roles(*roles_to_save, reason="Mute - usunięcie ról")
        # Dodaj rolę muta
        await user.add_roles(muted_role, reason="Mute")
        await interaction.response.send_message(f"Użytkownik {user.mention} został zmutowany na {time} minut.")
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"Użytkownik {user} został zmutowany na {time} minut przez {interaction.user}.")
    except discord.Forbidden:
        await interaction.response.send_message("Bot nie ma uprawnień do zmiany ról tego użytkownika.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Wystąpił błąd: {e}", ephemeral=True)

# Komenda /unmute
@bot.tree.command(name="unmute", description="Odmutuj użytkownika.")
@check_perm("mute")
@app_commands.describe(user="Użytkownik do odmutowania")
async def unmute(interaction: discord.Interaction, user: discord.Member):
    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("Rola muta nie istnieje.", ephemeral=True)
        return
    if muted_role not in user.roles:
        await interaction.response.send_message("Użytkownik nie jest zmutowany.", ephemeral=True)
        return

    saved_roles, _ = db.load_roles(user.id)
    try:
        await user.remove_roles(muted_role, reason="Manualne odmutowanie")
        roles_to_add = [interaction.guild.get_role(rid) for rid in saved_roles if interaction.guild.get_role(rid) is not None]
        await user.add_roles(*roles_to_add, reason="Manualne odmutowanie - przywracanie ról")
        db.delete_roles(user.id)
        await interaction.response.send_message(f"Użytkownik {user.mention} został odmutowany.")
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"Użytkownik {user} został odmutowany przez {interaction.user}.")
    except discord.Forbidden:
        await interaction.response.send_message("Bot nie ma uprawnień do zmiany ról tego użytkownika.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Wystąpił błąd: {e}", ephemeral=True)

# Komenda /warn
@bot.tree.command(name="warn", description="Ostrzeż użytkownika.")
@check_perm("warn")
@app_commands.describe(user="Użytkownik do ostrzeżenia", reason="Powód ostrzeżenia")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    db.add_warning(user.id, interaction.user.id, reason)
    warns_count = db.count_warnings(user.id)

    await interaction.response.send_message(f"Użytkownik {user.mention} został ostrzeżony.\nLiczba ostrzeżeń: {warns_count}")

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await safe_send(log_channel, f"Użytkownik {user} został ostrzeżony przez {interaction.user}. Powód: {reason}. Liczba ostrzeżeń: {warns_count}")

    # Automatyczne bany
    try:
        if warns_count == 5:
            await interaction.guild.ban(user, reason="5 warnów - ban 3 dni", delete_message_days=0)
            unban_time = datetime.utcnow() + timedelta(days=3)
            db.add_temp_ban(user.id, unban_time)
            if log_channel:
                await safe_send(log_channel, f"Użytkownik {user} został zbanowany na 3 dni (5 warnów).")
        elif warns_count == 10:
            await interaction.guild.ban(user, reason="10 warnów - ban 7 dni", delete_message_days=0)
            unban_time = datetime.utcnow() + timedelta(days=7)
            db.add_temp_ban(user.id, unban_time)
            if log_channel:
                await safe_send(log_channel, f"Użytkownik {user} został zbanowany na 7 dni (10 warnów).")
        elif warns_count >= 20:
            await interaction.guild.ban(user, reason="20 warnów - ban permanentny", delete_message_days=0)
            db.remove_temp_ban(user.id)
            if log_channel:
                await safe_send(log_channel, f"Użytkownik {user} został zbanowany permanentnie (20 warnów).")
    except discord.Forbidden:
        await interaction.followup.send("Nie mam uprawnień do banowania tego użytkownika.", ephemeral=True)
    except Exception as e:
        print(f"Błąd podczas automatycznego banowania: {e}")

# Komenda /clearwarns
@bot.tree.command(name="clearwarns", description="Usuń wszystkie ostrzeżenia użytkownika.")
@check_perm("warn")
@app_commands.describe(user="Użytkownik do wyczyszczenia ostrzeżeń")
async def clearwarns(interaction: discord.Interaction, user: discord.Member):
    db.clear_warnings(user.id)
    await interaction.response.send_message(f"Ostrzeżenia użytkownika {user.mention} zostały wyczyszczone.")
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await safe_send(log_channel, f"Ostrzeżenia użytkownika {user} zostały wyczyszczone przez {interaction.user}.")

# Komenda /warns
@bot.tree.command(name="warns", description="Pokaż liczbę ostrzeżeń użytkownika.")
@check_perm("warn")
@app_commands.describe(user="Użytkownik do sprawdzenia")
async def warns(interaction: discord.Interaction, user: discord.Member):
    warns_count = db.count_warnings(user.id)
    await interaction.response.send_message(f"Użytkownik {user.mention} ma {warns_count} ostrzeżeń.")

# Komenda /kick
@bot.tree.command(name="kick", description="Wyrzuć użytkownika z serwera.")
@check_perm("kick")
@app_commands.describe(user="Użytkownik do wyrzucenia", reason="Powód")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    try:
        await user.kick(reason=reason)
        await interaction.response.send_message(f"Użytkownik {user.mention} został wyrzucony.\nPowód: {reason}")
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"Użytkownik {user} został wyrzucony przez {interaction.user}. Powód: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("Nie mam uprawnień do wyrzucania tego użytkownika.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Wystąpił błąd: {e}", ephemeral=True)

# Komenda /ban
@bot.tree.command(name="ban", description="Zbanuj użytkownika.")
@check_perm("ban")
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód", days="Ilość dni usuniętych wiadomości (0-7)")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu", days: int = 0):
    if days < 0 or days > 7:
        await interaction.response.send_message("Dni usuniętych wiadomości musi być w zakresie 0-7.", ephemeral=True)
        return
    try:
        await interaction.guild.ban(user, reason=reason, delete_message_days=days)
        await interaction.response.send_message(f"Użytkownik {user.mention} został zbanowany.\nPowód: {reason}")
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"Użytkownik {user} został zbanowany przez {interaction.user}. Powód: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("Nie mam uprawnień do banowania tego użytkownika.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Wystąpił błąd: {e}", ephemeral=True)

# Komenda /unban
@bot.tree.command(name="unban", description="Odbanuj użytkownika.")
@check_perm("ban")
@app_commands.describe(user_id="ID użytkownika do odbanowania")
async def unban(interaction: discord.Interaction, user_id: int):
    user_obj = discord.Object(id=user_id)
    try:
        await interaction.guild.unban(user_obj)
        await interaction.response.send_message(f"Użytkownik o ID {user_id} został odbanowany.")
        db.remove_temp_ban(user_id)
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"Użytkownik o ID {user_id} został odbanowany przez {interaction.user}.")
    except discord.NotFound:
        await interaction.response.send_message("Ten użytkownik nie jest zbanowany.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("Nie mam uprawnień do odbanowywania.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Wystąpił błąd: {e}", ephemeral=True)

# === Uruchomienie bota i flask w osobnym wątku ===
def run_bot():
    bot.run(os.getenv("TOKEN"))

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    threading.Thread(target=run_bot).start()
