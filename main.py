import discord
from discord.ext import commands, tasks
from datetime import datetime, time, timedelta
import asyncio
import os
import json
import random
import asyncpg
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
from elo_mvp_system import przetworz_mecz, ranking, profil, wczytaj_dane, zapisz_dane, PUNKTY_ELO, przewidywana_szansa
from collections import Counter


# Flask do keep-alive
app = Flask('')

@app.route('/')
def home():
    return "Bot działa :)"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()

# Intents i bot
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # <- DODAJ TO TUTAJ

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Dane
MAX_SIGNUPS = 10
signups = []
waiting_list = []
log_file = 'signup_log.txt'
event_time = None
team1 = []
team2 = []
mvp_votes = {}
bot.mvp_mapping = {}
bot.mvp_vote_messages = []
bot.last_teams = {}
bot.zwyciezca = None
signup_ids = []
reminder_sent = False
panel_channel = None
ranking_mode = False




wczytaj_dane()

# ---------- BAZA DANYCH ---------- #

db = None

async def connect_to_db():
    global db
    db = await asyncpg.connect(os.getenv("DATABASE_URL"))

@bot.event
async def on_ready():
    print(f'Zalogowano jako {bot.user.name}')
    check_event_time.start()

@tasks.loop(seconds=60)
async def check_event_time():
    global event_time, reminder_sent

    if event_time is None or reminder_sent:
        return

    now = datetime.now() + timedelta(hours=2)  # ← kompensacja UTC → CEST

    diff = event_time - now

    if timedelta(minutes=14) < diff <= timedelta(minutes=15):
        reminder_sent = True

        channel = panel_channel
        if not channel:
            print("❌ Nie znaleziono kanału panelu do przypomnienia.")
            return
        
        if signups:
            mentions = " ".join(user.mention for user in signups)
            await channel.send(f"⏰ **Przypomnienie!** Customy za 15 minut!\n{mentions}")
        else:
            await channel.send("⏰ Customy za 15 minut, ale lista główna jest pusta.")


# ---------- SYSTEM ZAPISÓW I WYŚWIETLANIA ---------- #

event_time = None  # dodane globalnie

def generate_embed():
    embed = discord.Embed(title="Panel zapisów", color=discord.Color.green())

    if event_time:
        czas_wydarzenia = f"🕒 **Czas wydarzenia:** {event_time.strftime('%H:%M')}"
    else:
        czas_wydarzenia = "🕒 **Czas wydarzenia nie został jeszcze ustawiony.**"

    ranking_info = "🏆 **Rankingowa**" if ranking_mode else "🎮 **Nierankingowa**"

    embed.description = f"{czas_wydarzenia}\n{ranking_info}"

    signup_str = "\n".join(f"{i+1}. {user.mention}" for i, user in enumerate(signups)) if signups else "Brak"
    reserve_str = "\n".join(f"{i+1}. {user.mention}" for i, user in enumerate(waiting_list)) if waiting_list else "Brak"

    embed.add_field(name=f"Lista główna ({len(signups)}/{MAX_SIGNUPS})", value=signup_str, inline=False)
    embed.add_field(name="Lista rezerwowa", value=reserve_str, inline=False)
    return embed



class SignupPanel(discord.ui.View):
    def __init__(self, *, timeout=None, message):
        super().__init__(timeout=timeout)
        self.message = message

    @discord.ui.button(label="Zapisz", style=discord.ButtonStyle.success)
    async def signup(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if user in signups or user in waiting_list:
            await interaction.response.send_message("Już jesteś zapisany!", ephemeral=True, delete_after=5)
            return
        if len(signups) < MAX_SIGNUPS:
            signups.append(user)
        else:
            waiting_list.append(user)
        await self.update_message(interaction)
        await log_to_discord(f"👤 {user.mention} zapisał się na listę {'główną' if user in signups else 'rezerwową'}.")

    @discord.ui.button(label="Wypisz", style=discord.ButtonStyle.danger)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if user in signups:
            signups.remove(user)
            if waiting_list:
                moved = waiting_list.pop(0)
                signups.append(moved)
        elif user in waiting_list:
            waiting_list.remove(user)
        else:
            await interaction.response.send_message("Nie jesteś zapisany.", ephemeral=True, delete_after=5)
            return
        await self.update_message(interaction)
        await log_to_discord(f"👤 {user.mention} wypisał się z listy.")

    @discord.ui.button(label="Zapisz na rezerwę", style=discord.ButtonStyle.secondary)
    async def reserve(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if user in signups or user in waiting_list:
            await interaction.response.send_message("Już jesteś zapisany!", ephemeral=True, delete_after=5)
            return
        waiting_list.append(user)
        await self.update_message(interaction)
        await log_to_discord(f"👤 {user.mention} zapisał się na listę rezerwową (ręcznie).")

    @discord.ui.button(label="Ustaw czas", style=discord.ButtonStyle.primary)
    async def set_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może ustawić czas wydarzenia.", ephemeral=True, delete_after=5)
            return
        await interaction.response.send_message("Podaj godzinę wydarzenia w formacie `HH:MM`:", ephemeral=True, delete_after=5)

        def check(msg): return msg.author == interaction.user and msg.channel == interaction.channel
        try:
            msg = await bot.wait_for("message", timeout=60.0, check=check)
            hour, minute = map(int, msg.content.strip().split(":"))
            global event_time, reminder_sent
            now = datetime.now()
            event_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if event_time < now:
                event_time += timedelta(days=1)
            reminder_sent = False
            await msg.delete()
            await self.update_message(interaction)
            await log_to_discord(f"👤 {interaction.user.mention} ustawił czas wydarzenia na {event_time.strftime('%H:%M')}.")
        except asyncio.TimeoutError:
            await interaction.followup.send("Czas na odpowiedź minął.", ephemeral=True, delete_after=5)
        except ValueError:
            await interaction.followup.send("Niepoprawny format godziny.", ephemeral=True, delete_after=5)

    @discord.ui.button(label="🗑️ Usuń gracza", style=discord.ButtonStyle.danger, row=1)
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może usuwać graczy.", ephemeral=True, delete_after=5)
            return
        await interaction.response.send_message("Podaj @użytkownika do usunięcia:", ephemeral=True, delete_after=10)

        def check(msg): return msg.author == interaction.user and msg.channel == interaction.channel
        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check)
            if not msg.mentions:
                await interaction.followup.send("Musisz oznaczyć użytkownika.", ephemeral=True, delete_after=5)
                return
            user = msg.mentions[0]
            if user in signups:
                signups.remove(user)
                if waiting_list:
                    signups.append(waiting_list.pop(0))
            elif user in waiting_list:
                waiting_list.remove(user)
            else:
                await interaction.followup.send("Użytkownik nie znajduje się na żadnej liście.", ephemeral=True, delete_after=5)
                return
            await msg.delete()
            await self.update_message(interaction)
            await log_to_discord(f"👤 {interaction.user.mention} usunął {user.mention} z listy.")
        except asyncio.TimeoutError:
            await interaction.followup.send("Czas na odpowiedź minął.", ephemeral=True, delete_after=5)

    @discord.ui.button(label="➕ Dodaj gracza", style=discord.ButtonStyle.success, row=1)
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może dodawać graczy.", ephemeral=True, delete_after=5)
            return
        await interaction.response.send_message("Podaj @użytkownika do dodania:", ephemeral=True, delete_after=10)

        def check(msg): return msg.author == interaction.user and msg.channel == interaction.channel
        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check)
            if not msg.mentions:
                await interaction.followup.send("Musisz oznaczyć użytkownika.", ephemeral=True, delete_after=5)
                return
            user = msg.mentions[0]
            if user in signups or user in waiting_list:
                await interaction.followup.send("Ten użytkownik już jest zapisany.", ephemeral=True, delete_after=5)
                await msg.delete()
                return
            if len(signups) < MAX_SIGNUPS:
                signups.append(user)
                await log_to_discord(f"👤 {interaction.user.mention} dodał {user.mention} do listy głównej.")
            else:
                waiting_list.append(user)
                await log_to_discord(f"👤 {interaction.user.mention} dodał {user.mention} do listy rezerwowej.")
            await msg.delete()
            await self.update_message(interaction)
        except asyncio.TimeoutError:
            await interaction.followup.send("Czas na odpowiedź minął.", ephemeral=True, delete_after=5)

    @discord.ui.button(label="📤 Przenieś z rezerwy", style=discord.ButtonStyle.success, row=1)
    async def move_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może przenosić graczy.", ephemeral=True, delete_after=5)
            return
        if len(signups) >= MAX_SIGNUPS:
            await interaction.response.send_message("Lista główna jest już pełna.", ephemeral=True, delete_after=5)
            return
        await interaction.response.send_message("Podaj @użytkownika do przeniesienia z rezerwy:", ephemeral=True, delete_after=10)

        def check(msg): return msg.author == interaction.user and msg.channel == interaction.channel
        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check)
            if not msg.mentions:
                await interaction.followup.send("Musisz oznaczyć użytkownika.", ephemeral=True, delete_after=5)
                return
            user = msg.mentions[0]
            if user in waiting_list:
                waiting_list.remove(user)
                signups.append(user)
                await log_to_discord(f"👤 {interaction.user.mention} przeniósł {user.mention} z rezerwy do listy głównej.")
                await msg.delete()
                await self.update_message(interaction)
            else:
                await interaction.followup.send("Użytkownik nie znajduje się na liście rezerwowej.", ephemeral=True, delete_after=5)
        except asyncio.TimeoutError:
            await interaction.followup.send("Czas na odpowiedź minął.", ephemeral=True, delete_after=5)

    @discord.ui.button(label="🧹 Wyczyść listy", style=discord.ButtonStyle.danger, row=2)
    async def clear_lists(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może czyścić listy.", ephemeral=True, delete_after=5)
            return
        signups.clear()
        waiting_list.clear()
        await self.update_message(interaction)
        await interaction.response.send_message("Listy zostały wyczyszczone.", ephemeral=True, delete_after=5)
        await log_to_discord(f"👤 {interaction.user.mention} wyczyścił obie listy.")

    @discord.ui.button(label="📢 Ping lista główna", style=discord.ButtonStyle.primary, row=2)
    async def ping_main(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może pingować.", ephemeral=True, delete_after=5)
            return
        if not signups:
            await interaction.response.send_message("Lista główna jest pusta.", ephemeral=True, delete_after=5)
            return
        mentions = " ".join(user.mention for user in signups)
        await interaction.response.send_message(f"Pinguję listę główną:\n{mentions}", delete_after=300)
        await log_to_discord(f"👤 {interaction.user.mention} pingnął listę główną.")

    
    @discord.ui.button(label="📢 Ping rezerwa", style=discord.ButtonStyle.secondary, row=2)
    async def ping_reserve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może pingować.", ephemeral=True, delete_after=5)
            return
        if not waiting_list:
            await interaction.response.send_message("Lista rezerwowa jest pusta.", ephemeral=True, delete_after=5)
            return
        mentions = " ".join(user.mention for user in waiting_list)
        await interaction.response.send_message(f"Pinguję listę rezerwową:\n{mentions}", delete_after=300)
        await log_to_discord(f"👤 {interaction.user.mention} pingnął listę rezerwową.")

    async def update_message(self, interaction: discord.Interaction):
        embed = generate_embed()
        await self.message.edit(embed=embed, view=self)
        await interaction.response.defer()

    @discord.ui.button(label="🎯 Zmień tryb", style=discord.ButtonStyle.primary, row=2)
    async def toggle_ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może zmieniać tryb gry.", ephemeral=True, delete_after=5)
            return
    
        global ranking_mode
        ranking_mode = not ranking_mode
    
        await self.update_message(interaction)
    
        await interaction.response.send_message(
            f"✅ Tryb gry zmieniony na: {'🏆 Rankingowa' if ranking_mode else '🎮 Nierankingowa'}", ephemeral=True, delete_after=5
        )
    
        await log_to_discord(f"👤 {interaction.user.mention} zmienił tryb gry na {'🏆 Rankingowa' if ranking_mode else '🎮 Nierankingowa'}.")



@bot.command()
@commands.has_permissions(administrator=True)
async def panel(ctx):
    """Pokazuje panel zapisów z przyciskami."""
    global panel_channel
    panel_channel = ctx.channel

    embed = generate_embed()
    message = await ctx.send(embed=embed)        
    view = SignupPanel(message=message)           
    await message.edit(view=view)                 



@bot.command(name="lista")
@commands.has_permissions(administrator=True)
async def lista(ctx):
    """Wyświetla listę zapisanych bez przycisków (tylko dla admina)."""
    embed = generate_embed()
    await ctx.send(embed=embed)

# ---------- KOMENDY DLA BEKI ---------- #

@bot.command(name="ksante")
async def ksante(ctx):
    tekst = ("K'Sante👤 4,700 HP 💪 329 Armor 🤷‍♂️ 201 MR 💦 Unstoppable 🚫 "
             "A Shield 🛡 Goes over walls 🧱 Has Airborne 🌪 "
             "Cooldown is only ☝ second too 🕐 It costs 15 Mana 🧙‍♂️")
    
    await ctx.send(tekst, delete_after=300)


# ---------- LOGI ---------- #

async def log_to_discord(message: str):
    log_channel_id = 1366403342695141446
    channel = bot.get_channel(log_channel_id)
    if channel:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        await channel.send(f"[{timestamp}] {message}")

@bot.command(name="logi")
@commands.has_permissions(administrator=True)
async def logi(ctx, liczba: int = 10):
    """Pokazuje ostatnie X logów z kanału logów (domyślnie 10)."""
    log_channel_id = 1366403342695141446
    channel = bot.get_channel(log_channel_id)

    if not channel:
        await ctx.send("❌ Nie mogę znaleźć kanału logów.")
        return

    messages = [msg async for msg in channel.history(limit=liczba)]
    messages.reverse()

    formatted = "\n".join(msg.content for msg in messages)
    if not formatted:
        formatted = "Brak logów do wyświetlenia."

    await ctx.send(f"📄 **Ostatnie {liczba} logów:**\n```{formatted}```")





bot.run(TOKEN)
