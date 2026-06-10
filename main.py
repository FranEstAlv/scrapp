import asyncio
import os
import re
import csv
import json
import logging
import hashlib

APP_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(APP_LOOP)

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from typing import Dict, List, Optional, Any


API_ID_STR = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
SESSION_STRING = os.environ.get("SESSION_STRING", "").strip().strip("\"\'")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Validar variables críticas
if not all([API_ID_STR, API_HASH, BOT_TOKEN]):
    raise ValueError("Las variables de entorno API_ID, API_HASH y BOT_TOKEN son obligatorias.")
try:
    API_ID = int(API_ID_STR)
except (TypeError, ValueError):
    raise ValueError("API_ID debe ser un número entero válido.")

# Configurar el chat de destino. Si no se proporciona, se usará un valor por defecto o se lanzará un error.
# Se asume que SEND_CHAT no es una variable de entorno válida y se usa un valor por defecto.
# Se recomienda definir DESTINATION_CHAT explícitamente en el entorno.
DESTINATION_CHAT_STR = os.environ.get("DESTINATION_CHAT")
if DESTINATION_CHAT_STR is None:
    # Si DESTINATION_CHAT no está definido, se podría lanzar un error o usar un valor por defecto seguro.
    # Para este ejemplo, lanzamos un error para forzar la configuración.
    raise ValueError("La variable de entorno DESTINATION_CHAT es obligatoria.")
try:
    DESTINATION_CHAT = int(DESTINATION_CHAT_STR)
except ValueError:
    raise ValueError("DESTINATION_CHAT debe ser un número entero válido.")

CHATS_TO_SCRAPE: List[str] = [
    "https://t.me/+IfbjKNvmKoczYjhh",
    "https://t.me/+iWBtC_JCQ4I0NTFh",
    "@viplunaticscrapper",
]
CHECK_INTERVAL: int = int(os.environ.get("CHECK_INTERVAL", 30))
DB_VOLUME: str = os.environ.get("DB_VOLUME", "/tmp/db.json")
CSV_FILE: str = "tarjetas.csv"

# --- Configuración de Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# --- Clases y Funciones Auxiliares ---

class SimpleDB:
    """
    Clase para manejar la persistencia de datos del bot (últimos IDs, estadísticas, tarjetas procesadas).
    Utiliza un archivo JSON para almacenar los datos.
    """
    def __init__(self, db_path: str = DB_VOLUME):
        self.db_path = db_path
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        """Carga los datos desde el archivo JSON."""
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"⚠️ Error cargando DB desde '{self.db_path}': {e}. Iniciando con datos vacíos.")
        return {
            "last_ids": {},
            "stats": {"total_cards": 0, "total_scans": 0},
            "processed_cards": [], # Almacena huellas SHA-256 para evitar duplicados sin guardar datos sensibles
        }

    def _save(self) -> None:
        """Guarda los datos en el archivo JSON."""
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except OSError as e:
            logger.error(f"❌ Error guardando DB en '{self.db_path}': {e}")

    def get_last_id(self, chat_id: int) -> int:
        """Obtiene el último ID de mensaje procesado para un chat específico."""
        return self.data["last_ids"].get(str(chat_id), 0)

    def set_last_id(self, chat_id: int, message_id: int) -> None:
        """Establece el último ID de mensaje procesado para un chat específico."""
        self.data["last_ids"][str(chat_id)] = message_id
        self._save()

    def is_card_processed(self, card_data: str) -> bool:
        """Verifica si la tarjeta ya fue procesada usando una huella irreversible."""
        processed_cards = self.data.get("processed_cards", [])
        fingerprint = get_card_fingerprint(card_data)
        # Se mantiene compatibilidad con registros antiguos que pudieran estar en texto plano.
        return fingerprint in processed_cards or card_data in processed_cards

    def mark_card_processed(self, card_data: str) -> None:
        """Marca una tarjeta como procesada sin persistir PAN/CVV en texto plano."""
        if "processed_cards" not in self.data:
            self.data["processed_cards"] = []

        fingerprint = get_card_fingerprint(card_data)
        if fingerprint not in self.data["processed_cards"]:
            self.data["processed_cards"].append(fingerprint)
            # Limitar el tamaño de la lista para evitar el consumo excesivo de memoria.
            # Un tamaño de 10000 es un compromiso. Para volúmenes muy altos, considerar una DB real.
            if len(self.data["processed_cards"]) > 10000:
                self.data["processed_cards"] = self.data["processed_cards"][-10000:]
            self._save()

    def add_cards_stats(self, count: int = 1) -> None:
        """Actualiza las estadísticas de tarjetas procesadas y escaneos."""
        self.data.setdefault("stats", {"total_cards": 0, "total_scans": 0})
        self.data["stats"]["total_cards"] += count
        self.data["stats"]["total_scans"] += 1
        self._save()


def load_bin_database(csv_path: str = CSV_FILE) -> Dict[str, Dict[str, str]]:
    """
    Carga la base de datos de BINs desde un archivo CSV.
    El CSV debe tener columnas como 'bin', 'brand', 'tipo', 'nivel', 'Banco', 'país'.
    """
    bin_db: Dict[str, Dict[str, str]] = {}

    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)

            for row in reader:
                bin_code = row.get("bin", "").strip()

                if bin_code:
                    bin_db[bin_code] = {
                        "brand": row.get("brand", "Desconocido").strip(),
                        "tipo": row.get("tipo", "Desconocido").strip(),
                        "nivel": row.get("nivel", "").strip(),
                        "banco": row.get("Banco", "Desconocido").strip(),
                        "pais": row.get("país", "Desconocido").strip(),
                        "bin": bin_code,
                    }

        logger.info(f"✅ Base de datos BIN cargada: {len(bin_db)} entradas")

    except FileNotFoundError:
        logger.warning(
            f"⚠️ Archivo CSV de BINs no encontrado: '{csv_path}'. "
            "El bot funcionará sin información de BIN."
        )

    except csv.Error as e:
        logger.warning(
            f"⚠️ Error al leer el archivo CSV de BINs '{csv_path}': {e}. "
            "El bot funcionará sin información de BIN."
        )

    except Exception:
        logger.exception(
            f"⚠️ Error inesperado al cargar BINs desde '{csv_path}'. "
            "El bot funcionará sin información de BIN."
        )

    return bin_db

def get_card_fingerprint(card_data: str) -> str:
    """Devuelve una huella SHA-256 para deduplicar sin guardar datos sensibles completos."""
    return hashlib.sha256(card_data.encode("utf-8")).hexdigest()


def mask_card_number(card_number: str) -> str:
    """Enmascara una tarjeta mostrando los primeros 12 dígitos y ocultando los últimos 4."""
    if len(card_number) <= 4:
        return "X" * len(card_number)
    visible_digits = min(12, len(card_number) - 4)
    return f"{card_number[:visible_digits]}{'X' * (len(card_number) - visible_digits)}"


def get_bin_info(card_number: str, bin_database: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Obtiene información del BIN desde la base de datos proporcionada."""
    # Intentar con BINs de longitud 6, 5 y 4.
    for length in [6, 5, 4]:
        if len(card_number) >= length:
            bin_code = card_number[:length]
            if bin_code in bin_database:
                return bin_database[bin_code]
    return None

def format_card_message(card_data: str, bin_database: Dict[str, Dict[str, str]]) -> Optional[str]:
    """
    Formatea el mensaje de la tarjeta con la información del BIN.
    Formato de entrada: "4207670324511073|02|2030|816"
    """
    parts = card_data.split("|")
    if len(parts) != 4:
        logger.warning(f"Formato de tarjeta inválido: {card_data}")
        return None

    card_num, month, year, cvv = parts
    
    # Validaciones básicas para asegurar que los datos son razonables
    if not (len(card_num) == 16 and len(month) == 2 and len(cvv) >= 3):
        logger.warning(f"Datos de tarjeta no válidos tras split: {card_data}")
        return None

    bin_info = get_bin_info(card_num, bin_database)

    # Censurar la tarjeta para mostrarla sin exponer PAN/CVV completos.
    censored_card_num = mask_card_number(card_num)
    display_year = f"20{year}" if len(year) == 2 else year
    censored = f"{censored_card_num}|{month}|{display_year}"

    # Extraer información del BIN, con valores por defecto
    tipo = "Desconocido"
    brand = "Desconocido"
    nivel = ""
    banco = "Desconocido"
    pais = "Desconocido"
    bin_code_found = "Desconocido"

    if bin_info:
        tipo = bin_info.get("tipo", "Desconocido")
        brand = bin_info.get("brand", "Desconocido")
        nivel = bin_info.get("nivel", "")
        banco = bin_info.get("banco", "Desconocido")
        pais = bin_info.get("pais", "Desconocido")
        bin_code_found = bin_info.get("bin", "Desconocido")

    message = (
        f"<b>OLIMPO SCRAPP</b>\n"
        f"💳 <b>SCRAPPER CCS</b>\n"
        f"━━━━━━━━\n"
        f"<blockquote>{censored}</blockquote>\n"
        f"BIN: {bin_code_found}\n"
        f"Tipo: {tipo}\n"
        f"Marca: {brand}\n"
        f"Nivel: {nivel}\n"
        f"Banco: {banco}\n"
        f"País: {pais}"
        f"━━━━━━━━\n"
    )

    return message

def extract_cards(text: str) -> List[str]:
    """
    Extrae tarjetas de crédito (formato CC) del texto proporcionado.
    Busca patrones como 16 dígitos seguidos de mes, año y CVV, separados por caracteres no numéricos.
    """
    if not text:
        return []

    # Patrón regex mejorado para capturar el formato común de tarjetas de crédito
    # Busca 16 dígitos, opcionalmente seguidos por separadores y luego mes (2 dígitos),
    # año (2 o 4 dígitos) y CVV (3 o 4 dígitos).
    # Se enfoca en capturar el bloque completo para luego parsearlo.
    CARD_PATTERN = re.compile(r"(\d{16})\D*(\d{2})\D*(\d{2,4})\D*(\d{3,4})")
    matches = CARD_PATTERN.findall(text)
    cards: List[str] = []

    for match in matches:
        try:
            card_num, month, year, cvv = match
            
            # Validaciones adicionales para asegurar la corrección de los datos extraídos
            if len(card_num) == 16 and len(month) == 2 and len(cvv) >= 3:
                # Normalizar el año a 2 dígitos si es de 4
                year_normalized = year[-2:] if len(year) == 4 else year
                
                # Asegurar que el CVV tenga al menos 3 dígitos
                cvv_normalized = cvv[:3] if len(cvv) > 3 else cvv

                cards.append(f"{card_num}|{month}|{year_normalized}|{cvv_normalized}")
        except (IndexError, TypeError, ValueError) as e:
            logger.warning(f"⚠️ Error procesando un posible match de tarjeta: {match} - {e}")
            continue

    # Eliminar duplicados y devolver la lista
    return list(set(cards))

# --- Instancias Globales ---
BIN_DATABASE: Dict[str, Dict[str, str]] = load_bin_database()
db = SimpleDB()
USER_CLIENT_READY = False

# --- Clientes de Pyrogram ---
# El cliente 'user' se utiliza para unirse a chats y leer mensajes.
user = Client(
    "user_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    workers=100, # Aumentar workers para concurrencia en operaciones de red
)

# El cliente 'app' (bot) se utiliza para enviar mensajes al chat de destino.
app = Client(
    "bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=100,
)

# --- Funciones Asincrónicas ---

async def resolve_chat(chat_identifier: str) -> Optional[int]:
    """
    Resuelve un identificador de chat (username o enlace) a su ID numérico.
    """
    if not USER_CLIENT_READY:
        logger.error("❌ Cliente de usuario no está iniciado; no se puede resolver chats.")
        return None

    if isinstance(chat_identifier, int):
        return chat_identifier # Ya es un ID numérico

    try:
        chat = await user.get_chat(chat_identifier)
        logger.info(f"  Resolviendo '{chat_identifier}' → ID: {chat.id}")
        return chat.id
    except Exception as e:
        logger.error(f"  ❌ No se pudo resolver '{chat_identifier}': {e}")
        return None

async def send_card_immediately(card_data: str, source_info: str = "") -> bool:
    """
    Envía una tarjeta detectada inmediatamente al chat de destino.
    Retorna True si la tarjeta fue enviada y marcada como procesada, False en caso contrario.
    """
    try:
        card_num_prefix = card_data.split("|")[0] # Usar prefijo para la verificación rápida
        
        if db.is_card_processed(card_data): # Verificar el número completo de tarjeta
            # logger.debug(f"Tarjeta ya procesada: {card_data[:6]}xxxx") # Log de depuración
            return False

        # No se envían números de tarjeta ni CVV completos al chat de destino.
        message_content = format_card_message(card_data, BIN_DATABASE)
        if not message_content:
            logger.warning(f"No se pudo formatear el mensaje para la tarjeta: {card_data}")
            return False

        await app.send_message(
            DESTINATION_CHAT, message_content, parse_mode=ParseMode.HTML
        )

        db.mark_card_processed(card_data) # Marcar la tarjeta como procesada mediante huella irreversible
        db.add_cards_stats(1) # Actualizar estadísticas

        logger.info(f"✅ Tarjeta enviada: {card_num_prefix[:6]}xxxx ({source_info})")
        return True

    except Exception as e:
        logger.error(f"Error enviando tarjeta '{card_data[:6]}xxxx' a {DESTINATION_CHAT}: {e}")
        return False

async def scrape_chat_realtime(chat_id: int) -> tuple[int, int]:
    """
    Scrapea mensajes de un chat específico y envía las tarjetas detectadas INMEDIATAMENTE.
    Retorna el último ID de mensaje procesado y el número de nuevas tarjetas encontradas.
    """
    last_processed_id = db.get_last_id(chat_id)
    max_message_id_in_chat = last_processed_id # Inicializar con el último procesado
    new_cards_count = 0

    try:
        logger.info(f"Scrapeando chat {chat_id} desde el mensaje ID {last_processed_id}...")
        # Obtener un historial limitado para evitar sobrecargar la memoria.
        # Un límite de 1000 mensajes es un buen punto de partida.
        async for message in user.get_chat_history(chat_id, limit=1000):
            if message.id <= last_processed_id:
                # Ya hemos procesado estos mensajes en ejecuciones anteriores.
                break

            max_message_id_in_chat = max(max_message_id_in_chat, message.id)

            text = message.text or message.caption
            if text:
                cards_found = extract_cards(text)

                for card in cards_found:
                    success = await send_card_immediately(card, f"Chat: {chat_id}")
                    if success:
                        new_cards_count += 1
                    # Pequeña pausa entre el envío de cada tarjeta para no saturar el bot.
                    await asyncio.sleep(0.2) 

                # Si se encontraron muchas tarjetas en un solo mensaje, hacer una pausa más larga.
                if len(cards_found) > 5:
                    await asyncio.sleep(2)

        logger.info(f"Chat {chat_id}: Procesados {max_message_id_in_chat - last_processed_id} mensajes nuevos. Encontradas {new_cards_count} tarjetas.")
        return max_message_id_in_chat, new_cards_count

    except Exception as e:
        logger.error(f"Error scrapeando chat {chat_id}: {e}")
        # Devolver el último ID conocido si ocurre un error para no perder el progreso.
        return last_processed_id, 0

async def join_chat_if_needed(chat_identifier: str) -> bool:
    """
    Intenta unir al cliente 'user' a un chat si no está ya unido.
    Retorna True si se unió o ya estaba unido, False si falló.
    """
    if not USER_CLIENT_READY:
        logger.error("❌ Cliente de usuario no está iniciado; no se puede unir a chats.")
        return False

    try:
        # Intentar obtener información del chat. Si falla, significa que no estamos unidos o el chat no existe.
        await user.get_chat(chat_identifier)
        logger.debug(f"Ya unido a: {chat_identifier}")
        return True
    except Exception:
        try:
            await user.join_chat(chat_identifier)
            logger.info(f"Unido exitosamente a: {chat_identifier}")
            return True
        except Exception as e:
            logger.error(f"No se pudo unir a {chat_identifier}: {e}")
            return False

# --- Scanner Principal ---

async def resolve_configured_chats() -> List[tuple[str, int]]:
    """Resuelve la lista configurada y conserva el identificador original de cada chat."""
    logger.info("Resolviendo identificadores de chats...")
    resolved_chats: List[tuple[str, int]] = []

    for chat_identifier in CHATS_TO_SCRAPE:
        chat_id = await resolve_chat(chat_identifier)
        if chat_id is not None:
            resolved_chats.append((chat_identifier, chat_id))

    return resolved_chats


async def scan_configured_chats_once(resolved_chats: List[tuple[str, int]]) -> int:
    """Ejecuta un único ciclo de escaneo y retorna la cantidad de tarjetas nuevas."""
    total_new_cards_in_cycle = 0

    for chat_identifier, chat_id in resolved_chats:
        if await join_chat_if_needed(chat_identifier):
            last_id, new_count = await scrape_chat_realtime(chat_id)
            total_new_cards_in_cycle += new_count
            db.set_last_id(chat_id, last_id)

            if new_count > 0:
                logger.info(f"  Chat {chat_id}: {new_count} nuevas tarjetas detectadas en este ciclo.")

            await asyncio.sleep(3)
        else:
            logger.warning(f"Saltando chat {chat_id} porque no se pudo unir.")

    return total_new_cards_in_cycle


async def auto_scanner():
    """Scrapea los chats configurados continuamente en tiempo real."""
    await asyncio.sleep(5)

    resolved_chats = await resolve_configured_chats()

    if not resolved_chats:
        logger.error("❌ No hay chats válidos para escanear. Por favor, revise CHATS_TO_SCRAPE y la configuración.")
        return

    logger.info(f"✅ {len(resolved_chats)} chats listos para escanear: {[chat_id for _, chat_id in resolved_chats]}")

    while True:
        logger.info("🔍 Iniciando ciclo de escaneo en tiempo real...")
        total_new_cards_in_cycle = await scan_configured_chats_once(resolved_chats)

        if total_new_cards_in_cycle > 0:
            logger.info(f"✅ Ciclo de escaneo completado. Total de nuevas tarjetas detectadas: {total_new_cards_in_cycle}")
        else:
            logger.info("📭 Sin nuevas tarjetas detectadas en este ciclo.")

        logger.info(f"⏱️ Esperando {CHECK_INTERVAL} segundos hasta el próximo ciclo...")
        await asyncio.sleep(CHECK_INTERVAL)

# --- Comandos del Bot ---

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message):
    """Comando /start - Muestra información y ayuda del bot."""
    chats_list_formatted = "\n".join([f"<code>{c}</code>" for c in CHATS_TO_SCRAPE])

    await message.reply(
        f"🤖 <b>Auto Scraper Bot - Realtime</b>\n\n"
        f"<b>Chats monitoreados:</b>\n{chats_list_formatted}\n\n"
        f"💳 Envío: <b>Inmediato por cada tarjeta detectada</b>\n"
        f"📊 Base BIN: <code>{CSV_FILE}</code> ({len(BIN_DATABASE)} entradas cargadas)\n"
        f"⏱️ Intervalo de escaneo: <code>{CHECK_INTERVAL}s</code>\n\n"
        f"<b>Comandos disponibles:</b>\n"
        f"/status - Ver estado actual del bot.\n"
        f"/force - Forzar un escaneo inmediato de todos los chats.\n"
        f"/stats - Mostrar estadísticas generales.\n"
        f"/test - Enviar una tarjeta de prueba formateada.",
        parse_mode=ParseMode.HTML,
    )

@app.on_message(filters.command("test"))
async def test_cmd(client: Client, message):
    """Comando /test - Envía una tarjeta de prueba formateada al chat de destino."""
    test_card_data = "4207670324511073|02|2030|816" # Ejemplo de tarjeta
    logger.info(f"Ejecutando comando /test. Enviando tarjeta de prueba: {test_card_data}")
    
    success = await send_card_immediately(test_card_data, "Comando /test")
    if success:
        await message.reply("✅ Mensaje de prueba enviado exitosamente.")
    else:
        await message.reply("❌ Falló el envío del mensaje de prueba.")

@app.on_message(filters.command("status"))
async def status_cmd(client: Client, message):
    """Comando /status - Muestra el estado actual del bot."""
    stats = db.data.get("stats", {})
    last_ids = db.data.get("last_ids", {})

    scraper_status = "activo" if USER_CLIENT_READY else "deshabilitado: SESSION_STRING inválido o ausente"

    await message.reply(
        f"📡 <b>Estado del bot</b>\n\n"
        f"<b>Scraper:</b> <code>{scraper_status}</code>\n"
        f"<b>Chats configurados:</b> <code>{len(CHATS_TO_SCRAPE)}</code>\n"
        f"<b>BINs cargados:</b> <code>{len(BIN_DATABASE)}</code>\n"
        f"<b>Intervalo:</b> <code>{CHECK_INTERVAL}s</code>\n"
        f"<b>Tarjetas detectadas:</b> <code>{stats.get('total_cards', 0)}</code>\n"
        f"<b>Escaneos con hallazgos:</b> <code>{stats.get('total_scans', 0)}</code>\n"
        f"<b>Chats con progreso:</b> <code>{len(last_ids)}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("stats"))
async def stats_cmd(client: Client, message):
    """Comando /stats - Muestra estadísticas generales."""
    stats = db.data.get("stats", {})
    processed_count = len(db.data.get("processed_cards", []))

    await message.reply(
        f"📊 <b>Estadísticas</b>\n\n"
        f"<b>Tarjetas nuevas detectadas:</b> <code>{stats.get('total_cards', 0)}</code>\n"
        f"<b>Ciclos con detecciones:</b> <code>{stats.get('total_scans', 0)}</code>\n"
        f"<b>Registros deduplicados:</b> <code>{processed_count}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("force"))
async def force_cmd(client: Client, message):
    """Comando /force - Fuerza un escaneo inmediato de todos los chats configurados."""
    if not USER_CLIENT_READY:
        await message.reply("❌ Scraper deshabilitado: configura un SESSION_STRING válido de Pyrogram y reinicia el servicio.")
        return

    await message.reply("🔍 Iniciando escaneo manual...")
    resolved_chats = await resolve_configured_chats()

    if not resolved_chats:
        await message.reply("❌ No hay chats válidos para escanear.")
        return

    total_new_cards = await scan_configured_chats_once(resolved_chats)
    await message.reply(
        f"✅ Escaneo manual completado. Nuevas tarjetas detectadas: <code>{total_new_cards}</code>",
        parse_mode=ParseMode.HTML,
    )


async def start_user_client() -> bool:
    """Inicia el cliente de usuario; si la sesión es inválida, mantiene vivo el bot."""
    global USER_CLIENT_READY

    if not SESSION_STRING:
        logger.error(
            "❌ SESSION_STRING no está configurado. El bot seguirá activo, "
            "pero el scraper automático queda deshabilitado hasta configurar una sesión Pyrogram válida."
        )
        return False

    try:
        await user.start()
    except Exception:
        USER_CLIENT_READY = False
        logger.exception(
            "❌ No se pudo iniciar el cliente de usuario. Revisa SESSION_STRING: "
            "debe ser una sesión válida generada con Pyrogram, no el BOT_TOKEN ni un archivo .session."
        )
        return False

    USER_CLIENT_READY = True
    logger.info("✅ Cliente de usuario iniciado correctamente.")
    return True


async def main() -> None:
    """Punto de entrada principal: inicia el bot y, si es posible, el scanner en segundo plano."""
    scanner_task: Optional[asyncio.Task] = None

    try:
        logger.info("🚀 Iniciando cliente bot de Telegram...")
        await app.start()
        logger.info("✅ Cliente bot iniciado correctamente.")

        logger.info("🚀 Iniciando cliente de usuario para el scraper...")
        if await start_user_client():
            scanner_task = asyncio.create_task(auto_scanner())
            logger.info("🤖 Scraper en ejecución. Presione Ctrl+C para detenerlo.")
        else:
            logger.error("⚠️ Scraper deshabilitado; el bot queda vivo para comandos mientras corriges SESSION_STRING.")

        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Deteniendo bot...")
    except Exception:
        logger.exception("❌ Error fatal durante el arranque o ejecución del bot.")
        raise
    finally:
        if scanner_task:
            scanner_task.cancel()
            try:
                await scanner_task
            except asyncio.CancelledError:
                pass

        if user.is_connected:
            await user.stop()
        if app.is_connected:
            await app.stop()
        logger.info("Bot detenido correctamente.")


if __name__ == "__main__":
    try:
        APP_LOOP.run_until_complete(main())
    finally:
        APP_LOOP.run_until_complete(APP_LOOP.shutdown_asyncgens())
        APP_LOOP.close()
