import discord
from discord.ext import commands, tasks
from datetime import datetime, time, timedelta
import asyncio
import os
import json
import random
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


wczytaj_dane()

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

        log_channel_id = 1366403342695141446  # ← ID kanału przypomnień
        channel = bot.get_channel(log_channel_id)

        if not channel:
            print("Nie mogę znaleźć kanału do przypomnienia.")
            return

        if signups:
            mentions = " ".join(user.mention for user in signups)
            await channel.send(f"⏰ **Przypomnienie!** Wydarzenie za 15 minut!\n{mentions}")
            await log_to_discord("📣 Bot wysłał przypomnienie 15 minut przed wydarzeniem.")
        else:
            await channel.send("⏰ Wydarzenie za 15 minut, ale lista główna jest pusta.")



# ---------- SYSTEM ZAPISÓW ---------- #

event_time = None  # dodane globalnie

def generate_embed():
    embed = discord.Embed(title="Panel zapisów", color=discord.Color.green())

    if event_time:
        embed.description = f"🕒 **Czas wydarzenia:** {event_time.strftime('%H:%M')}"
    else:
        embed.description = "🕒 **Czas wydarzenia nie został jeszcze ustawiony.**"

    if signups:
        signup_str = "\n".join(f"{i+1}. {user.mention}" for i, user in enumerate(signups))
    else:
        signup_str = "Brak"

    if waiting_list:
        reserve_str = "\n".join(f"{i+1}. {user.mention}" for i, user in enumerate(waiting_list))
    else:
        reserve_str = "Brak"

    embed.add_field(name=f"Lista główna ({len(signups)}/{MAX_SIGNUPS})", value=signup_str, inline=False)
    embed.add_field(name="Lista rezerwowa", value=reserve_str, inline=False)
    return embed


class SignupPanel(discord.ui.View):
    def __init__(self, *, timeout=None, message=None):
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
        await log_to_discord(f"👤 {interaction.user.mention} zapisał się na listę {'główną' if len(signups) <= MAX_SIGNUPS else 'rezerwową'}.")


    @discord.ui.button(label="Wypisz", style=discord.ButtonStyle.danger)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if user in signups:
            signups.remove(user)
            if waiting_list:
                moved_user = waiting_list.pop(0)
                signups.append(moved_user)
        elif user in waiting_list:
            waiting_list.remove(user)
        else:
            await interaction.response.send_message("Nie jesteś zapisany.", ephemeral=True, delete_after=5)
            return
        await self.update_message(interaction)
        await log_to_discord(f"👤 {interaction.user.mention} wypisał się z listy.")


    @discord.ui.button(label="Zapisz na rezerwę", style=discord.ButtonStyle.secondary)
    async def reserve(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if user in signups or user in waiting_list:
            await interaction.response.send_message("Już jesteś zapisany!", ephemeral=True, delete_after=5)
            return
        waiting_list.append(user)
        await self.update_message(interaction)
        await log_to_discord(f"👤 {interaction.user.mention} zapisał się na listę rezerwową (ręcznie).")


    @discord.ui.button(label="Ustaw czas", style=discord.ButtonStyle.primary)
    async def set_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może ustawić czas wydarzenia.", ephemeral=True, delete_after=5)
            return
        await interaction.response.send_message("Podaj godzinę wydarzenia w formacie `HH:MM` (np. 20:15):", ephemeral=True)
    
        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel
    
        try:
            msg = await bot.wait_for("message", timeout=60.0, check=check)
            hour, minute = map(int, msg.content.strip().split(":"))
            now = datetime.now()
            global event_time
            event_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if event_time < now:
                event_time += timedelta(days=1)
            await msg.delete()
            await self.update_message(interaction)
            await log_to_discord(f"👤 {interaction.user.mention} ustawił czas wydarzenia na {event_time.strftime('%H:%M')}.")
        except asyncio.TimeoutError:
            await interaction.followup.send("Czas na odpowiedź minął.", ephemeral=True, delete_after=5)
            await log_to_discord(f"⚠️ {interaction.user.mention} nie ustawił czasu — przekroczono limit czasu.")
        except ValueError:
            await interaction.followup.send("Niepoprawny format godziny.", ephemeral=True, delete_after=5)
            await log_to_discord(f"⚠️ {interaction.user.mention} podał niepoprawny format godziny.")



    @discord.ui.button(label="🗑️ Usuń gracza", style=discord.ButtonStyle.danger, row=1)
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może usuwać graczy.", ephemeral=True, delete_after=5)
            return
        await interaction.response.send_message("Podaj @użytkownika do usunięcia z listy:", ephemeral=True, delete_after=5)

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check)
            if not msg.mentions:
                await interaction.followup.send("Musisz oznaczyć użytkownika (@).", ephemeral=True)
                return
            user = msg.mentions[0]
            if user in signups:
                signups.remove(user)
                if waiting_list:
                    moved_user = waiting_list.pop(0)
                    signups.append(moved_user)
            elif user in waiting_list:
                waiting_list.remove(user)
            else:
                await interaction.followup.send("Tego użytkownika nie ma na żadnej liście.", ephemeral=True)
                return
            await msg.delete()
            await self.update_message(interaction)
        except asyncio.TimeoutError:
            await interaction.followup.send("Czas na odpowiedź minął.", ephemeral=True)
        await log_to_discord(f"👤 {interaction.user.mention} usunął {user.mention} z listy.")


    @discord.ui.button(label="📤 Przenieś z rezerwy", style=discord.ButtonStyle.success, row=1)
    async def move_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może przenosić graczy.", ephemeral=True, delete_after=5)
            return
        if len(signups) >= MAX_SIGNUPS:
            await interaction.response.send_message("Lista główna jest już pełna.", ephemeral=True, delete_after=5)
            return
        await interaction.response.send_message("Podaj @użytkownika do przeniesienia z rezerwy do głównej:", ephemeral=True, delete_after=5)

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check)
            if not msg.mentions:
                await interaction.followup.send("Musisz oznaczyć użytkownika (@).", ephemeral=True)
                return
            user = msg.mentions[0]
            if user in waiting_list:
                waiting_list.remove(user)
                signups.append(user)
                await msg.delete()
                await self.update_message(interaction)
            else:
                await interaction.followup.send("Tego użytkownika nie ma na liście rezerwowej.", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("Czas na odpowiedź minął.", ephemeral=True)
        await log_to_discord(f"👤 {interaction.user.mention} przeniósł {user.mention} z rezerwy do listy głównej.")


    @discord.ui.button(label="🧹 Wyczyść listy", style=discord.ButtonStyle.danger, row=2)
    async def clear_lists(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może czyścić listy.", ephemeral=True, delete_after=5)
            return
        signups.clear()
        waiting_list.clear()
        await interaction.response.send_message("Listy zostały wyczyszczone.", ephemeral=True, delete_after=5)
        await self.update_message(interaction)
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




@bot.command()
async def panel(ctx):
    """Pokazuje panel zapisów z przyciskami."""
    embed = generate_embed()
    view = SignupPanel()
    message = await ctx.send(embed=embed, view=view)
    view.message = message



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
