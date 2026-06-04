import asyncio
import os
import re
import requests
from telethon import TelegramClient, events

from defs import getUrl, getcards

API_ID = 
API_HASH = 
SEND_CHAT =

client = TelegramClient('session', API_ID, API_HASH)
ccs = []

chats = [
    ''
    ''
]

# Inclui solamente si ya hay una base para evitar duplicados
try:
    with open('cards.txt', 'r') as r:
        temp_cards = r.read().splitlines()
    for x in temp_cards:
        car = getcards(x)
        if car:
            ccs.append(car[0])
except FileNotFoundError:
    print("El archivo 'cards.txt' no se encontró. Se creará uno nuevo si se encuentran tarjetas.")

@client.on(events.NewMessage(chats=chats, func=lambda x: getattr(x, 'text')))
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
        bin_response = requests.get(f'{cc[:6]}')
        bin_response.raise_for_status()
        bin_json = bin_response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error al obtener información BIN: {e}")
        bin_json = {'vendor': 'N/A', 'type': 'N/A', 'level': 'N/A', 'bank': 'N/A', 'country_iso': 'N/A', 'flag': 'N/A'}

    fullinfo = f"{cc}|{mes}|{ano}|{cvv}"
    message_text = f"""
CC: {cc}|{mes}|{ano}|{cvv}
INFO: {bin_json.get('vendor', 'N/A')} - {bin_json.get('type', 'N/A')} - {bin_json.get('level', 'N/A')}
BANK: {bin_json.get('bank', 'N/A')}
COUNTRY: {bin_json.get('country_iso', 'N/A')} - {bin_json.get('flag', 'N/A')}
"""    
    print(fullinfo)
    with open('cards.txt', 'a') as w:
        w.write(fullinfo + '\n')
    await client.send_message(SEND_CHAT, message_text, link_preview=False)


@client.on(events.NewMessage(outgoing=True, pattern=re.compile(r'.lives')))
async def send_cards_file(event):
    await event.reply(file='cards.txt')


client.start()
client.run_until_disconnected()

