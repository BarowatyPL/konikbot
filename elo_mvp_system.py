
import json
import random
from datetime import datetime
import os

ELO_START = 1000
ELO_MIN_GAIN = 15
ELO_MIN_LOSS = 15
MVP_BONUS_WIN = 5
MVP_BONUS_LOSS = -5

PLIK_GRACZE = "gracze.json"
PLIK_LOGI = "mecze_log.json"
PUNKTY_ELO = {}
LOGI_MECZY = []

def wczytaj_dane():
    global PUNKTY_ELO, LOGI_MECZY
    if os.path.exists(PLIK_GRACZE):
        with open(PLIK_GRACZE, "r", encoding="utf-8") as f:
            try:
                PUNKTY_ELO = json.load(f)
            except json.JSONDecodeError:
                PUNKTY_ELO = {}
    else:
        PUNKTY_ELO = {}
        zapisz_dane()

    if os.path.exists(PLIK_LOGI):
        with open(PLIK_LOGI, "r", encoding="utf-8") as f:
            try:
                LOGI_MECZY = json.load(f)
            except json.JSONDecodeError:
                LOGI_MECZY = []
    else:
        LOGI_MECZY = []
        zapisz_dane()

def zapisz_dane():
    with open(PLIK_GRACZE, "w", encoding="utf-8") as f:
        json.dump(PUNKTY_ELO, f, indent=2, ensure_ascii=False)
    with open(PLIK_LOGI, "w", encoding="utf-8") as f:
        json.dump(LOGI_MECZY, f, indent=2, ensure_ascii=False)

def dodaj_gracza(nick):
    if nick not in PUNKTY_ELO:
        PUNKTY_ELO[nick] = ELO_START

def przewidywana_szansa(elo1, elo2):
    return 1 / (1 + 10 ** ((elo2 - elo1) / 400))

def przetworz_mecz(druzyna_a, druzyna_b, zwyciezca, mvp_a=None, mvp_b=None):
    for g in druzyna_a + druzyna_b:
        dodaj_gracza(g)

    suma_a = sum(PUNKTY_ELO[g] for g in druzyna_a)
    suma_b = sum(PUNKTY_ELO[g] for g in druzyna_b)

    szansa_a = przewidywana_szansa(suma_a, suma_b)
    szansa_b = 1 - szansa_a

    wynik_a, wynik_b = (1, 0) if zwyciezca == "A" else (0, 1)
    zmiany = {}

    for gracz in druzyna_a:
        baza = 32 * (wynik_a - szansa_a)
        baza = max(baza, ELO_MIN_GAIN) if baza > 0 else min(baza, -ELO_MIN_LOSS)
        if mvp_a == gracz:
            baza += MVP_BONUS_WIN if wynik_a == 1 else MVP_BONUS_LOSS
        PUNKTY_ELO[gracz] += round(baza)
        zmiany[gracz] = round(baza)

    for gracz in druzyna_b:
        baza = 32 * (wynik_b - szansa_b)
        baza = max(baza, ELO_MIN_GAIN) if baza > 0 else min(baza, -ELO_MIN_LOSS)
        if mvp_b == gracz:
            baza += MVP_BONUS_WIN if wynik_b == 1 else MVP_BONUS_LOSS
        PUNKTY_ELO[gracz] += round(baza)
        zmiany[gracz] = round(baza)

    log = {
        "czas": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "druzyna_a": druzyna_a,
        "druzyna_b": druzyna_b,
        "zwyciezca": zwyciezca,
        "mvp_a": mvp_a,
        "mvp_b": mvp_b,
        "zmiany": zmiany
    }
    LOGI_MECZY.append(log)
    zapisz_dane()

def ranking():
    return sorted(PUNKTY_ELO.items(), key=lambda x: x[1], reverse=True)

def profil(nick):
    dodaj_gracza(nick)
    mecze = [m for m in LOGI_MECZY if nick in m["zmiany"]]
    win = sum(1 for m in mecze if (m["zwyciezca"] == "A" and nick in m["druzyna_a"]) or (m["zwyciezca"] == "B" and nick in m["druzyna_b"]))
    loss = len(mecze) - win
    mvp_count = sum(1 for m in mecze if m.get("mvp_a") == nick or m.get("mvp_b") == nick)

    return {
        "nick": nick,
        "elo": PUNKTY_ELO[nick],
        "wygrane": win,
        "przegrane": loss,
        "mvp": mvp_count
    }

wczytaj_dane()
