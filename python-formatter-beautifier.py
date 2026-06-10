import asyncio
import os
import re
import requests
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from defs import getUrl, getcards


SESSION_STRING = os.environ.get("SESSION_STRING")
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
ccs = []

chats = [
    "https://t.me/+IfbjKNvmKoczYjhh"
    "https://t.me/+iWBtC_JCQ4I0NTFh"
    "@viplunaticscrapper"
]

CSV_FILE = "tarjetas.csv"

# Inclui solamente si ya hay una base para evitar duplicados
try:
    with open("cards.txt", "r") as r:
        temp_cards = r.read().splitlines()
    for x in temp_cards:
        car = getcards(x)
        if car:
            ccs.append(car[0])
except FileNotFoundError:
    print(
        "El archivo 'cards.txt' no se encontró. Se creará uno nuevo si se encuentran tarjetas."
    )


def load_bin_database():
    """Carga el CSV de tarjetas en un diccionario"""
    bin_db = {}
    try:
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                bin_code = row["bin"].strip()
                if bin_code:
                    bin_db[bin_code] = {
                        "brand": row.get("brand", "Desconocido"),
                        "tipo": row.get("tipo", "Desconocido"),
                        "nivel": row.get("nivel", ""),
                        "banco": row.get("Banco", "Desconocido"),
                        "pais": row.get("país", "Desconocido"),
                        "bin": row.get("bin", "Desconocido"),
                    }
        logger.info(f"✅ Base de datos BIN cargada: {len(bin_db)} entradas")
    except Exception as e:
        logger.error(f"❌ Error cargando CSV: {e}")
    return bin_db


@client.on(events.NewMessage(chats=chats, func=lambda x: getattr(x, "text")))
async def new_message_handler(event):
    text = event.text
    if event.reply_markup:
        markup_text = event.reply_markup.stringify()
        urls = getUrl(markup_text)
        if urls:
            try:
                response = requests.get(urls[0])
                response.raise_for_status()
                text = response.text
            except requests.exceptions.RequestException as e:
                print(f"Error al obtener contenido de la URL: {e}")
                return
        else:
            return

    cards = getcards(text)
    if not cards:
        return

    cc, mes, ano, cvv = cards

    if cc in ccs:
        return

    ccs.append(cc)

    try:
        bin_response = requests.get(f"{cc[:6]}")
        bin_response.raise_for_status()
        bin_code = bin_response.row()
    except requests.exceptions.RequestException as e:
        print(f"Error al obtener información BIN: {e}")
        bin_code = {
            "brand": row.get("brand", "Desconocido"),
            "tipo": row.get("tipo", "Desconocido"),
            "nivel": row.get("nivel", "Desconocido"),
            "banco": row.get("banco", "Desconocido"),
            "pais": row.get("pais", "Desconocido"),
            "bin": row.get("bin", "Desconocido"),
        }

    fullinfo = f"{cc}|{mes}|{ano}|{cvv}"
    message_text = f"""
<b>OLIMPO SCRAPPER</b>
BIN: <code>{bin}<code>
CC: <code>{cc}|{mes}|{ano}|{cvv}<code>
MARCA: {bin_code.get('brand', 'Desconocido')}
TIPO: {bin_code.get('tipo', 'Desconocido')}
NIVEL: {bin_code.get('nivel', 'Desconocido')}
BANCO: {bin_code.get('banco', 'Desconocido')}
PAIS: {bin_code.get('pais', 'Desconocido')}
"""
    print(fullinfo)
    with open("tarjetas.csv", "a") as w:
        w.write(fullinfo + "\n", parse_mode="HTML")
    await client.send_message(SEND_CHAT, message_text, link_preview=False)


@client.on(events.NewMessage(outgoing=True, pattern=re.compile(r".lives")))
async def send_cards_file(event):
    await event.reply(file="cards.txt")


client.start()
client.run_until_disconnected()
