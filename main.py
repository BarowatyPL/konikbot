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

wczytaj_dane()

@bot.event
async def on_ready():
    print(f'Zalogowano jako {bot.user.name}')
    check_event_time.start()

# ---------- SYSTEM ZAPISÓW ---------- #

def generate_embed():
    embed = discord.Embed(title="Panel zapisów", color=discord.Color.green())
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
            await interaction.response.send_message("Już jesteś zapisany!", ephemeral=True)
            return

        if len(signups) < MAX_SIGNUPS:
            signups.append(user)
        else:
            waiting_list.append(user)

        await self.update_message(interaction)

    @discord.ui.button(label="Wypisz", style=discord.ButtonStyle.danger)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user

        if user in signups:
            signups.remove(user)
            # Jeśli ktoś czekał, przesuń z rezerwy do głównej
            if waiting_list:
                moved_user = waiting_list.pop(0)
                signups.append(moved_user)

        elif user in waiting_list:
            waiting_list.remove(user)
        else:
            await interaction.response.send_message("Nie jesteś zapisany.", ephemeral=True)
            return

        await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction):
        embed = generate_embed()
        await self.message.edit(embed=embed, view=self)
        await interaction.response.defer()  # ukrywa "thinking..."


@bot.command()
async def panel(ctx):
    """Pokazuje panel zapisów z przyciskami."""
    embed = generate_embed()
    view = SignupPanel()
    message = await ctx.send(embed=embed, view=view)
    view.message = message  # przypisz wiadomość do edytowania później
bot.run(TOKEN)
