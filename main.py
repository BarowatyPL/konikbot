import discord
from discord.ext import commands, tasks
from datetime import datetime, time
import asyncio
import os
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# Ładowanie tokena z .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Flask - mini serwer do UptimeRobot
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

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

MAX_SIGNUPS = 10
signups = []
waiting_list = []
log_file = 'signup_log.txt'
event_time = time(20, 0)  # Domyślnie 20:00

@bot.event
async def on_ready():
    print(f'Zalogowano jako {bot.user.name}')
    check_event_time.start()

@bot.command()
async def zapisz(ctx):
    user = str(ctx.author)
    if user in signups or user in waiting_list:
        await ctx.send(f'{user}, jesteś już zapisany.')
        return
    if len(signups) < MAX_SIGNUPS:
        signups.append(user)
        log_entry(user, 'Zapisano')
        await ctx.send(f'{user} został zapisany. ({len(signups)}/{MAX_SIGNUPS})')
    else:
        waiting_list.append(user)
        log_entry(user, 'Lista rezerwowa')
        await ctx.send(f'{user}, dodano do listy rezerwowej.')

@bot.command()
async def wypisz(ctx):
    user = str(ctx.author)
    if user in signups:
        signups.remove(user)
        log_entry(user, 'Wypisano')
        if waiting_list:
            moved_user = waiting_list.pop(0)
            signups.append(moved_user)
            log_entry(moved_user, 'Przeniesiono z rezerwowej')
        await ctx.send(f'{user} został wypisany.')
    elif user in waiting_list:
        waiting_list.remove(user)
        log_entry(user, 'Usunięto z rezerwowej')
        await ctx.send(f'{user} usunięty z listy rezerwowej.')
    else:
        await ctx.send(f'{user}, nie jesteś zapisany.')

@bot.command()
async def lista(ctx):
    list_msg = '**Zapisani:**\n' + '\n'.join(f'{i+1}. {name}' for i, name in enumerate(signups))
    if waiting_list:
        list_msg += '\n**Rezerwowi:**\n' + '\n'.join(f'- {name}' for name in waiting_list)
    await ctx.send(list_msg)

@bot.command()
async def ustaw(ctx, godzina: str):
    global event_time
    try:
        godz, minuty = map(int, godzina.split(':'))
        event_time = time(godz, minuty)
        await ctx.send(f'⏰ Ustawiono nową godzinę wydarzenia: {event_time.strftime("%H:%M")}')
    except:
        await ctx.send('❌ Niepoprawny format! Użyj np. `!ustaw 18:30`.')

@bot.command()
async def czas(ctx):
    godzina_str = event_time.strftime("%H:%M")
    await ctx.send(f"⏰ Aktualna godzina wydarzenia: {godzina_str}")

@bot.command()
@commands.has_permissions(administrator=True)
async def reset(ctx):
    global signups, waiting_list
    signups = []
    waiting_list = []
    log_entry(str(ctx.author), 'Zresetowano listy')
    await ctx.send('🗑️ Lista zapisów i rezerwowa zostały wyczyszczone.')

@bot.command()
async def logi(ctx):
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()[-10:]  # ostatnie 10 wpisów
            log_text = ''.join(lines) or 'Brak logów.'
        await ctx.send(f'📝 Ostatnie logi:\n```{log_text}```')
    except FileNotFoundError:
        await ctx.send('❌ Nie znaleziono pliku logów.')

@tasks.loop(seconds=60)
async def check_event_time():
    now = datetime.now().time()
    if now.hour == event_time.hour and now.minute == event_time.minute:
        channel = discord.utils.get(bot.get_all_channels(), name='ogolny')
        if channel:
            await channel.send('📢 Wydarzenie rozpoczyna się teraz!')
        await asyncio.sleep(60)

def log_entry(user, action):
    with open(log_file, 'a', encoding='utf-8') as f:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        f.write(f'[{timestamp}] {action}: {user}\n')

bot.run(TOKEN)
