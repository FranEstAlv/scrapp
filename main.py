import asyncio
import os
import re
import csv
import json
import logging
import requests
from pyrogram import Client, filters
from pyrogram import idle
from pyrogram.enums import ParseMode
from typing import Dict, List, Optional, Any


API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
SESSION_STRING = os.environ.get("SESSION_STRING")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Validar variables críticas
if not all([API_ID, API_HASH, SESSION_STRING, BOT_TOKEN]):
    raise ValueError("Las variables de entorno API_ID, API_HASH, SESSION_STRING y BOT_TOKEN son obligatorias.")

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
            "processed_cards": [], # Almacena los números de tarjeta completos para evitar duplicados
        }

    def _save(self) -> None:
        """Guarda los datos en el archivo JSON."""
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            with open(self.db_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except IOError as e:
            logger.error(f"❌ Error guardando DB en '{self.db_path}': {e}")

    def get_last_id(self, chat_id: int) -> int:
        """Obtiene el último ID de mensaje procesado para un chat específico."""
        return self.data["last_ids"].get(str(chat_id), 0)

    def set_last_id(self, chat_id: int, message_id: int) -> None:
        """Establece el último ID de mensaje procesado para un chat específico."""
        self.data["last_ids"][str(chat_id)] = message_id
        self._save()

    def is_card_processed(self, card_number: str) -> bool:
        """Verifica si la tarjeta (número completo) ya fue procesada."""
        # Usamos un conjunto para búsquedas más rápidas si la lista crece mucho.
        # Sin embargo, para mantener la compatibilidad con el formato actual y la simplicidad,
        # se mantiene como lista y se limita su tamaño.
        return card_number in self.data.get("processed_cards", [])

    def mark_card_processed(self, card_number: str) -> None:
        """Marca una tarjeta como procesada y mantiene un historial limitado."""
        if "processed_cards" not in self.data:
            self.data["processed_cards"] = []
        
        if card_number not in self.data["processed_cards"]:
            self.data["processed_cards"].append(card_number)
            # Limitar el tamaño de la lista para evitar el consumo excesivo de memoria.
            # Un tamaño de 10000 es un compromiso. Para volúmenes muy altos, considerar una DB real.
            if len(self.data["processed_cards"]) > 10000:
                self.data["processed_cards"] = self.data["processed_cards"][-10000:]
            self._save()

    def add_cards_stats(self, count: int = 1) -> None:
        """Actualiza las estadísticas de tarjetas procesadas y escaneos."""
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
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                bin_code = row.get("bin", "").strip()
                if bin_code:
                    bin_db[bin_code] = {
                        "brand": row.get("brand", "Desconocido"),
                        "tipo": row.get("tipo", "Desconocido"),
                        "nivel": row.get("nivel", ""),  # Nivel puede estar vacío
                        "banco": row.get("Banco", "Desconocido"),
                        "pais": row.get("país", "Desconocido"),
                        "bin": bin_code,  # Guardar el bin normalizado
                    }
        logger.info(f"✅ Base de datos BIN cargada: {len(bin_db)} entradas")
    except FileNotFoundError:
        logger.warning(f"⚠️ Archivo CSV de BINs no encontrado: '{csv_path}'. El bot funcionará sin información de BIN.")
    except csv.Error as e:
        logger.warning(f"⚠️ Error al leer el archivo CSV de BINs '{csv_path}': {e}. El bot funcionará sin información de BIN.")
    except Exception as e:
        logger.warning(f"⚠️ Error inesperado al cargar BINs desde '{csv_path}': {e}. El bot funcionará sin información de BIN.")
    return bin_db

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

    # Censurar la tarjeta para mostrarla
    censored_card_num = f"{card_num[:12]}xxxx"
    censored_cvv = "xxx"
    censored = f"{censored_card_num}|{month}|{year}|{censored_cvv}"

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
        f"💳 <b>Tarjeta Detectada</b>\n"
        f"<code>{card_data}</code>\n"
        f"<code>{censored}</code>\n"
        f"<b>BIN:</b> <code>{bin_code_found}</code>\n"
        f"<b>Tipo:</b> {tipo} | <b>Marca:</b> {brand}\n"
        f"<b>Nivel:</b> {nivel}\n"
        f"<b>Banco:</b> {banco}\n"
        f"<b>País:</b> {pais}\n"
        f"━━━━━━━━━━━━━━━"
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

        message_content = format_card_message(card_data, BIN_DATABASE)
        if not message_content:
            logger.warning(f"No se pudo formatear el mensaje para la tarjeta: {card_data}")
            return False

        # Añadir prefijo de origen si está disponible
        full_message = f"{source_info}\n{message_content}" if source_info else message_content

        await app.send_message(
            DESTINATION_CHAT, full_message, parse_mode=ParseMode.HTML
        )

        db.mark_card_processed(card_data) # Marcar la tarjeta completa como procesada
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

async def auto_scanner():
    """
    Scrapea los chats configurados continuamente en tiempo real.
    """
    await asyncio.sleep(5) # Pequeña espera inicial para asegurar que los clientes estén listos.

    logger.info("Resolviendo identificadores de chats...")
    resolved_chat_ids: List[int] = []
    for chat_id_str in CHATS_TO_SCRAPE:
        chat_id = await resolve_chat(chat_id_str)
        if chat_id:
            resolved_chat_ids.append(chat_id)

    if not resolved_chat_ids:
        logger.error("❌ No hay chats válidos para escanear. Por favor, revise CHATS_TO_SCRAPE y la configuración.")
        return

    logger.info(f"✅ {len(resolved_chat_ids)} chats listos para escanear: {resolved_chat_ids}")

    while True:
        total_new_cards_in_cycle = 0
        logger.info(f"🔍 Iniciando ciclo de escaneo en tiempo real...")

        for chat_id in resolved_chat_ids:
            # Asegurarse de estar unido al chat antes de intentar scrapear.
            # Se usa el identificador original (str) para join_chat_if_needed.
            if await join_chat_if_needed(CHATS_TO_SCRAPE[resolved_chat_ids.index(chat_id)]):
                last_id, new_count = await scrape_chat_realtime(chat_id)
                total_new_cards_in_cycle += new_count
                db.set_last_id(chat_id, last_id) # Guardar el último ID procesado para este chat.
                
                if new_count > 0:
                    logger.info(f"  Chat {chat_id}: {new_count} nuevas tarjetas detectadas en este ciclo.")
                
                # Pausa entre chats para evitar sobrecargar la API de Telegram.
                await asyncio.sleep(3)
            else:
                logger.warning(f"Saltando chat {chat_id} porque no se pudo unir.")

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
    ""
