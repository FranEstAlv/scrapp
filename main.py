import asyncio
import os
import re
import requests
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from defs import getUrl, getcards


API_ID = "35913593"
API_HASH = "3b68bfcc6355ae25c893165a24dfa821"
SESSION_STRING = os.environ.get("SESSION_STRING")
SEND_CHAT = "-1003936831735"

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

# ============================================================
# CONFIGURACIÓN - VARIABLES DE ENTORNO (Railway)
# ======================================================
# ============================================================
# VALIDAR CREDENCIALES
# ============================================================


# ============================================================
# SETUP
# ============================================================

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Cargar base de datos de BINs
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


BIN_DATABASE = load_bin_database()


class SimpleDB:
    def __init__(self):
        self.data = self.load()

    def load(self):
        if os.path.exists(DB_VOLUME):
            try:
                with open(DB_VOLUME, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"⚠️ Error cargando DB: {e}")
        return {
            "last_ids": {},
            "stats": {"total_cards": 0, "total_scans": 0},
            "processed_cards": [],  # Para no repetir tarjetas
        }

    def save(self):
        try:
            # Crear directorio si no existe
            os.makedirs(os.path.dirname(DB_VOLUME), exist_ok=True)
            with open(DB_VOLUME, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.error(f"❌ Error guardando DB: {e}")

    def get_last_id(self, chat_id):
        return self.data["last_ids"].get(str(chat_id), 0)

    def set_last_id(self, chat_id, message_id):
        self.data["last_ids"][str(chat_id)] = message_id
        self.save()

    def is_card_processed(self, card_number):
        """Verifica si la tarjeta ya fue enviada"""
        return card_number in self.data.get("processed_cards", [])

    def mark_card_processed(self, card_number):
        """Marca tarjeta como procesada"""
        if "processed_cards" not in self.data:
            self.data["processed_cards"] = []
        self.data["processed_cards"].append(card_number)
        # Mantener solo últimas 10000 para no crecer infinito
        if len(self.data["processed_cards"]) > 10000:
            self.data["processed_cards"] = self.data["processed_cards"][-10000:]
        self.save()

    def add_cards(self, count):
        self.data["stats"]["total_cards"] += count
        self.data["stats"]["total_scans"] += 1
        self.save()


db = SimpleDB()

user = Client(
    "user_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    workers=100,
)

app = Client(
    "bot_session", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workers=100
)

CARD_PATTERN = re.compile(r"\d{16}\D*\d{2}\D*\d{2,4}\D*\d{3,4}")


def get_bin_info(card_number):
    """Obtiene info del BIN desde la base de datos"""
    # Probar con 6 dígitos primero, luego 5, luego 4
    for length in [6, 5, 4]:
        bin_code = card_number[:length]
        if bin_code in BIN_DATABASE:
            return BIN_DATABASE[bin_code]
    return None


def format_card_message(card_data):
    """
    Formatea el mensaje de la tarjeta con la info del BIN
    card_data: "4207670324511073|02|2030|816"
    """
    parts = card_data.split("|")
    if len(parts) != 4:
        return None

    card_num, month, year, cvv = parts
    bin_info = get_bin_info(card_num)

    # Crear versión censurada
    censored = f"{card_num[:12]}xxxx|{month}|{year}|xxx"

    # Info del BIN
    if bin_info:
        nivel = bin_info.get("nivel", "")
        tipo = bin_info.get("tipo", "Desconocido")
        banco = bin_info.get("banco", "Desconocido")
        pais = bin_info.get("pais", "Desconocido")
        brand = bin_info.get("brand", "Desconocido")
        bin = bin_info.get("bin", "Desconocido")
    else:
        tipo = "Desconocido"
        banco = "Desconocido"
        pais = "Desconocido"
        brand = "Desconocido"
        bin = "Desconocido"

    message = (
        f"Bin: #{bin}\n"
        f"<code>{card_data}</code>\n"
        f"<code>{censored}</code>\n"
        f"Bin: <code>{bin}</code>\n"
        f"Tipo: {tipo} | {brand}\n"
        f"Nivel: {nivel}\n"
        f"Banco: {banco}\n"
        f"País: {pais}\n"
        f"━━━━━━━━━━━━━━━"
    )

    return message


def extract_cards(text):
    """Extrae tarjetas del texto"""
    if not text:
        return []

    matches = CARD_PATTERN.findall(text)
    cards = []

    for match in matches:
        digits = re.findall(r"\d+", match)
        if len(digits) >= 4:
            try:
                card_num = digits[0]
                month = digits[1]
                year = digits[2][-2:] if len(digits[2]) >= 2 else digits[2]
                cvv = digits[3]
                
                if len(card_num) == 16 and len(month) == 2 and len(cvv) >= 3:
                    cards.append(f"{card_num}|{month}|{year}|{cvv}")
            except (IndexError, TypeError):
                logger.warning(f"⚠️ Error procesando tarjeta: {match}")
                continue

    # Eliminar duplicados en el mismo mensaje
    return list(set(cards))


async def resolve_chat(chat_id):
    """Convierte @username a ID numérico"""
    if isinstance(chat_id, str):
        try:
            chat = await user.get_chat(chat_id)
            logger.info(f"  @{chat_id} → ID: {chat.id}")
            return chat.id
        except Exception as e:
            logger.error(f"  ❌ No se pudo resolver {chat_id}: {e}")
            return None
    return chat_id


async def send_card_immediately(card_data, source=""):
    """Envía UNA tarjeta inmediatamente al bot"""
    try:
        # Verificar si ya fue enviada
        card_num = card_data.split("|")[0]
        if db.is_card_processed(card_num):
            return False

        message = format_card_message(card_data)
        if not message:
            return False

        # Agregar encabezado
        full_message = f"💳 <b>OLIMPO SCRAPPER</b>\n{message}"

        await app.send_message(
            DESTINATION_CHAT, full_message, parse_mode=ParseMode.HTML
        )

        # Marcar como procesada
        db.mark_card_processed(card_num)
        db.add_cards(1)

        logger.info(f"✅ Tarjeta enviada: {card_num[:6]}xxxx")
        return True

    except Exception as e:
        logger.error(f"Error enviando tarjeta: {e}")
        return False


async def scrape_chat_realtime(chat_id):
    """
    Scrapea mensajes y envía tarjetas INMEDIATAMENTE una por una
    """
    last_id = db.get_last_id(chat_id)
    max_id = last_id
    new_cards_count = 0

    try:
        async for message in user.get_chat_history(chat_id, limit=1000):
            if message.id <= last_id:
                break

            max_id = max(max_id, message.id)

            text = message.text or message.caption
            if text:
                cards = extract_cards(text)

                # Enviar cada tarjeta inmediatamente
                for card in cards:
                    success = await send_card_immediately(card, f"Chat {chat_id}")
                    if success:
                        new_cards_count += 1
                    await asyncio.sleep(0.5)  # Pequeño delay entre envíos

                # Si encontramos muchas tarjetas, pausar un poco
                if len(cards) > 5:
                    await asyncio.sleep(2)

        return max_id, new_cards_count

    except Exception as e:
        logger.error(f"Error scrapeando {chat_id}: {e}")
        return last_id, 0


async def join_chat_if_needed(chat_id):
    try:
        await user.join_chat(chat_id)
        logger.info(f"Unido a: {chat_id}")
    except:
        pass


# ============================================================
# SCANNER PRINCIPAL
# ============================================================


async def auto_scanner():
    await asyncio.sleep(5)

    logger.info("Resolviendo chats...")
    resolved_chats = []
    for chat in CHATS_TO_SCRAPE:
        chat_id = await resolve_chat(chat)
        if chat_id:
            resolved_chats.append(chat_id)

    if not resolved_chats:
        logger.error("❌ No hay chats válidos!")
        return

    logger.info(f"✅ {len(resolved_chats)} chats listos")

    while True:
        try:
            logger.info(f"🔍 Iniciando scan en tiempo real...")
            total_new = 0

            for chat_id in resolved_chats:
                await join_chat_if_needed(chat_id)

                last_id, new_count = await scrape_chat_realtime(chat_id)
                total_new += new_count

                db.set_last_id(chat_id, last_id)

                if new_count > 0:
                    logger.info(f"  Chat {chat_id}: {new_count} nuevas tarjetas")

                await asyncio.sleep(3)  # Entre chats

            if total_new > 0:
                logger.info(f"✅ Total nuevas tarjetas: {total_new}")
            else:
                logger.info("📭 Sin nuevas tarjetas")

            logger.info(f"⏱️ Esperando {CHECK_INTERVAL}s...")
            await asyncio.sleep(CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"Error scanner: {e}")
            await asyncio.sleep(60)


# ============================================================
# COMANDOS DEL BOT
# ============================================================


@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client, message):
    chats_list = "\n".join([f"<code>{c}</code>" for c in CHATS_TO_SCRAPE])

    await message.reply(
        f"🤖 <b>Auto Scraper Bot - Realtime</b>\n\n"
        f"<b>Chats monitoreados:</b>\n{chats_list}\n\n"
        f"💳 Envío: <b>Inmediato por cada hit</b>\n"
        f"📊 Base BIN: <code>{CSV_FILE}</code>\n"
        f"⏱️ Intervalo: <code>{CHECK_INTERVAL}s</code>\n\n"
        f"<b>Comandos:</b>\n"
        f"/status - Ver estado\n"
        f"/force - Forzar scan\n"
        f"/stats - Estadísticas\n"
        f"/test - Probar formato",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("test"))
async def test_cmd(client, message):
    """Envía una tarjeta de prueba para ver el formato"""
    test_card = "4207670324511073|02|2030|816"
    await send_card_immediately(test_card, "Test")
    await message.reply("✅ Mensaje de prueba enviado")


@app.on_message(filters.command("status"))
async def status_cmd(client, message):
    await message.reply(
        f"📊 <b>Estado</b>\n\n"
        f"💳 Total hits enviados: <code>{db.data['stats']['total_cards']}</code>\n"
        f"🗂️ Tarjetas en memoria: <code>{len(db.data.get('processed_cards', []))}</code>\n"
        f"📈 Scans realizados: <code>{db.data['stats']['total_scans']}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("force"))
async def force_cmd(client, message):
    status = await message.reply("🔄 Forzando scan...")

    total = 0
    for chat in CHATS_TO_SCRAPE:
        chat_id = await resolve_chat(chat)
        if chat_id:
            await join_chat_if_needed(chat_id)
            last_id, new_count = await scrape_chat_realtime(chat_id)
            total += new_count
            db.set_last_id(chat_id, last_id)
            await asyncio.sleep(1)

    await status.edit_text(f"✅ Scan forzado: {total} nuevas tarjetas")


@app.on_message(filters.command("stats"))
async def stats_cmd(client, message):
    await message.reply(
        f"📊 <b>Estadísticas</b>\n\n"
        f"💳 Total hits: <code>{db.data['stats']['total_cards']}</code>\n"
        f"🔍 Scans: <code>{db.data['stats']['total_scans']}</code>\n"
        f"💾 DB: <code>{DB_VOLUME}</code>\n"
        f"📋 CSV: <code>{CSV_FILE}</code> ({len(BIN_DATABASE)} BINs)",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# MAIN
# ============================================================


async def main():
    print("=" * 60)
    print("AUTO SCRAPER BOT - REALTIME HITS")
    print("=" * 60)

    await user.start()
    await app.start()

    me = await user.get_me()
    bot = await app.get_me()

    print(f"✅ User: {me.first_name} (ID: {me.id})")
    print(f"✅ Bot: @{bot.username}")
    print(f"📍 OLIMPO SCRAPP: {len(CHATS_TO_SCRAPE)}")
    print(f"💳 BINs cargados: {len(BIN_DATABASE)}")
    print(f"📤 Destino: {DESTINATION_CHAT}")
    print(f"⏱️ Intervalo: {CHECK_INTERVAL}s")
    print("=" * 60)

    asyncio.create_task(auto_scanner())
    await idle()


if __name__ == "__main__":
    asyncio.run(main())

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
