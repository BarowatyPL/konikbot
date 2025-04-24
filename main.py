
import discord
from discord.ext import commands
import random
from elo_mvp_system import przetworz_mecz, zapisz_dane, PUNKTY_ELO, przewidywana_szansa
from collections import Counter

team1 = []
team2 = []
mvp_votes = {}

@bot.command()
@commands.has_permissions(administrator=True)
async def start(ctx):
    if len(signups) < 10:
        await ctx.send("❌ Potrzeba dokładnie 10 zapisanych graczy.")
        return

    random.shuffle(signups)
    global team1, team2
    team1 = signups[:5]
    team2 = signups[5:10]

    suma_a = sum(PUNKTY_ELO.get(g, 1000) for g in team1)
    suma_b = sum(PUNKTY_ELO.get(g, 1000) for g in team2)
    szansa_a = przewidywana_szansa(suma_a, suma_b)
    szansa_b = 1 - szansa_a

    gain_a = max(15, round(32 * (1 - szansa_a)))
    loss_a = max(15, round(32 * (0 - szansa_a)))
    gain_b = max(15, round(32 * (1 - szansa_b)))
    loss_b = max(15, round(32 * (0 - szansa_b)))

    embed = discord.Embed(title="🎮 Drużyny wylosowane!", color=discord.Color.purple())
    embed.add_field(name="Drużyna 1", value="\n".join(
        f"{i+1}. {g} (±{gain_a}/-{loss_a})" for i, g in enumerate(team1)
    ), inline=True)
    embed.add_field(name="Drużyna 2", value="\n".join(
        f"{i+6}. {g} (±{gain_b}/-{loss_b})" for i, g in enumerate(team2)
    ), inline=True)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def wynik(ctx, zwyciezca: int):
    if zwyciezca not in (1, 2):
        await ctx.send("❌ Użyj `!wynik 1` lub `!wynik 2`.")
        return
    if len(team1) != 5 or len(team2) != 5:
        await ctx.send("❌ Najpierw użyj !start.")
        return

    ctx.bot.zwyciezca = zwyciezca

    wygrani = team1 if zwyciezca == 1 else team2
    przegrani = team2 if zwyciezca == 1 else team1

    ctx.bot.last_teams = {"A": team1, "B": team2}

    embed_win = discord.Embed(title="🏆 Głosuj na MVP (Wygrani)", color=discord.Color.green())
    embed_lose = discord.Embed(title="😓 Głosuj na MVP (Przegrani)", color=discord.Color.red())

    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    ctx.bot.mvp_mapping = {}

    for i, g in enumerate(wygrani):
        embed_win.add_field(name=emojis[i], value=g, inline=False)
        ctx.bot.mvp_mapping[emojis[i]] = {"team": "A" if zwyciezca == 1 else "B", "user": g}

    for i, g in enumerate(przegrani):
        embed_lose.add_field(name=emojis[i], value=g, inline=False)
        ctx.bot.mvp_mapping[emojis[i]] = {"team": "B" if zwyciezca == 1 else "A", "user": g}

    ctx.bot.mvp_vote_messages = []
    win_msg = await ctx.send(embed=embed_win)
    lose_msg = await ctx.send(embed=embed_lose)
    ctx.bot.mvp_vote_messages.extend([win_msg.id, lose_msg.id])

    for i in range(5):
        await win_msg.add_reaction(emojis[i])
        await lose_msg.add_reaction(emojis[i])

    await ctx.send("✅ Głosowanie rozpoczęte! Po zakończeniu użyj `!mvp` by zatwierdzić wynik.")

@bot.command()
@commands.has_permissions(administrator=True)
async def mvp(ctx):
    await ctx.send("⏳ Zliczanie głosów na MVP...")

    mvp_counts = {"A": Counter(), "B": Counter()}
    channel = ctx.channel

    for msg_id in ctx.bot.mvp_vote_messages:
        try:
            msg = await channel.fetch_message(msg_id)
            for reaction in msg.reactions:
                if str(reaction.emoji) in ctx.bot.mvp_mapping:
                    async for user in reaction.users():
                        if user == bot.user:
                            continue
                        mapping = ctx.bot.mvp_mapping[str(reaction.emoji)]
                        mvp_counts[mapping["team"]][mapping["user"]] += 1
        except Exception as e:
            await ctx.send(f"❌ Błąd przy zliczaniu głosów: {e}")
            return

    mvp_a = mvp_counts["A"].most_common(1)
    mvp_b = mvp_counts["B"].most_common(1)

    mvp_a_name = mvp_a[0][0] if mvp_a else None
    mvp_b_name = mvp_b[0][0] if mvp_b else None

    przetworz_mecz(ctx.bot.last_teams["A"], ctx.bot.last_teams["B"], "A" if ctx.bot.zwyciezca == 1 else "B", mvp_a_name, mvp_b_name)
    zapisz_dane()

    wynik_embed = discord.Embed(title="📊 MVP zatwierdzeni i punkty przyznane!", color=discord.Color.gold())
    if mvp_a_name:
        wynik_embed.add_field(name="MVP Drużyny A", value=mvp_a_name, inline=True)
    if mvp_b_name:
        wynik_embed.add_field(name="MVP Drużyny B", value=mvp_b_name, inline=True)
    await ctx.send(embed=wynik_embed)
