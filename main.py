import asyncio
import os
import re
import csv
import sqlite3
import logging
import html
import tempfile
import aiohttp
from urllib.parse import urlparse
from typing import Dict, List, Optional, Any, Union

APP_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(APP_LOOP)

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, ReplyKeyboardMarkup, Message

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

# Configurar el chat de destino. Acepta ID numérico, @username o enlace t.me.
DESTINATION_CHAT_STR = os.environ.get("DESTINATION_CHAT", "").strip()
if not DESTINATION_CHAT_STR:
    raise ValueError("La variable de entorno DESTINATION_CHAT es obligatoria.")

DESTINATION_CHAT: Union[int, str]
try:
    DESTINATION_CHAT = int(DESTINATION_CHAT_STR)
except ValueError:
    DESTINATION_CHAT = DESTINATION_CHAT_STR

SEND_INTERVAL_SECONDS: int = int(os.environ.get("SEND_INTERVAL_SECONDS", 180))
DESTINATION_CHAT_ID: Optional[int] = DESTINATION_CHAT if isinstance(DESTINATION_CHAT, int) else None
DESTINATION_REFRESH_PENDING: bool = False

CHATS_TO_SCRAPE: List[str] = [
    "@viplunaticscrapper",
    "@AsukaScr",
    "-1003636233013",
    "-1003075577632",
    "-1003658677167",
    "-1002408067156",
    "-1002271492504",
    "-1002328190486"
]
CHECK_INTERVAL: int = int(os.environ.get("CHECK_INTERVAL", 30))
DB_VOLUME: str = os.environ.get("DB_VOLUME", "/data")
DB_FILENAME: str = os.environ.get("DB_FILENAME", "scrapp.sqlite3")
CSV_FILE: str = "tarjetas.csv"

# Dominios que deben ser procesados para extraer tarjetas
PROCESSABLE_LINK_DOMAINS: List[str] = [
    "telegram.ph",
    "telegra.ph",
    "te.legra.ph"
]

# Dominios que deben ser ignorados (si se quiere mantener esta funcionalidad)
IGNORED_LINK_DOMAINS: List[str] = [
    # Aquí se pueden agregar dominios que se quieren ignorar completamente
]

# Configuración de scraping de enlaces
MAX_LINK_CONTENT_SIZE: int = 1024 * 1024  # 1MB máximo para contenido de enlaces
LINK_REQUEST_TIMEOUT: int = 30  # 30 segundos timeout para requests HTTP (aumentado para telegra.ph)
LINK_MAX_RETRIES: int = 3  # Máximo de reintentos para scraping de enlaces

COUNTRY_CODE_BY_NAME = {
    "ARGENTINA": "AR", "AUSTRALIA": "AU", "AUSTRIA": "AT", "BANGLADESH": "BD", "BELGIUM": "BE",
    "BRAZIL": "BR", "BULGARIA": "BG", "CANADA": "CA", "CHILE": "CL", "CHINA": "CN", 
    "COLOMBIA": "CO", "COSTA RICA": "CR", "CROATIA": "HR", "DENMARK": "DK", "DOMINICAN REPUBLIC": "DO",
    "ECUADOR": "EC", "EGYPT": "EG", "FINLAND": "FI", "FRANCE": "FR", "GERMANY": "DE", "GREECE": "GR",
    "GUATEMALA": "GT", "HONG KONG": "HK", "INDIA": "IN", "INDONESIA": "ID", "IRELAND": "IE",
    "ITALY": "IT", "JAPAN": "JP", "KOREA, REPUBLIC OF": "KR", "LEBANON": "LB", "MALAYSIA": "MY",
    "MEXICO": "MX", "NETHERLANDS": "NL", "NIGERIA": "NG", "NORWAY": "NO", "PAKISTAN": "PK",
    "PANAMA": "PA", "PERU": "PE", "PHILIPPINES": "PH", "POLAND": "PL", "PORTUGAL": "PT",
    "ROMANIA": "RO", "RUSSIAN FEDERATION": "RU", "SAUDI ARABIA": "SA", "SERBIA": "RS",
    "SINGAPORE": "SG", "SOUTH AFRICA": "ZA", "SPAIN": "ES", "SWEDEN": "SE", "SWITZERLAND": "CH",
    "TAIWAN, PROVINCE OF CHINA": "TW", "THAILAND": "TH", "TURKEY": "TR", "UKRAINE": "UA",
    "UNITED ARAB EMIRATES": "AE", "UNITED KINGDOM": "GB", "UNITED STATES": "US",
    "VENEZUELA, BOLIVARIAN REPUBLIC OF": "VE", "VIET NAM": "VN",
}


def country_flag(country_name: str) -> str:
    """Devuelve la bandera emoji para países conocidos en la base BIN."""
    country_code = COUNTRY_CODE_BY_NAME.get((country_name or "").strip().upper())
    if not country_code:
        return ""
    return "".join(chr(ord(char) + 127397) for char in country_code)

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
    Maneja la persistencia de datos del bot en una base SQLite ubicada en el
    volumen persistente de Railway (DB_VOLUME, por defecto /data).
    """

    def __init__(self, db_volume: str = DB_VOLUME, db_filename: str = DB_FILENAME):
        self.db_path = self._resolve_db_path(db_volume, db_filename)
        self.data = self._load()

    @staticmethod
    def _resolve_db_path(db_volume: str, db_filename: str) -> str:
        """Resuelve DB_VOLUME como directorio persistente y devuelve el archivo SQLite."""
        if os.path.splitext(db_volume)[1].lower() in {".db", ".sqlite", ".sqlite3"}:
            db_path = db_volume
            db_dir = os.path.dirname(db_path)
        else:
            db_dir = db_volume
            db_path = os.path.join(db_dir, db_filename)

        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        return db_path

    def _connect(self) -> sqlite3.Connection:
        """Abre una conexión SQLite con filas accesibles por nombre."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _load(self) -> Dict[str, Any]:
        """Inicializa la DB SQLite y devuelve una vista cacheada compatible con el bot."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS last_ids (
                        chat_id TEXT PRIMARY KEY,
                        message_id INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS stats (
                        key TEXT PRIMARY KEY,
                        value INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS processed_cards (
                        card_data TEXT PRIMARY KEY,
                        processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        source_info TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXIST card_data (
                        card_data TEXT PRIMARY KEY,
                        value INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute("INSERT OR IGNORE INTO stats (key, value) VALUES ('total_cards', 0)")
                conn.execute("INSERT OR IGNORE INTO stats (key, value) VALUES ('total_scans', 0)")
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"❌ Error inicializando DB SQLite en '{self.db_path}': {e}")

        return self._snapshot()

    def _snapshot(self) -> Dict[str, Any]:
        """Lee los datos actuales de SQLite en el formato usado por los comandos."""
        snapshot: Dict[str, Any] = {
            "last_ids": {},
            "stats": {"total_cards": 0, "total_scans": 0},
            "processed_cards": [],
        }

        try:
            with self._connect() as conn:
                snapshot["last_ids"] = {
                    row["chat_id"]: row["message_id"]
                    for row in conn.execute("SELECT chat_id, message_id FROM last_ids")
                }
                snapshot["stats"] = {
                    row["key"]: row["value"]
                    for row in conn.execute("SELECT key, value FROM stats")
                }
                snapshot["processed_cards"] = [
                    row["card_data"]
                    for row in conn.execute(
                        "SELECT card_data FROM processed_cards ORDER BY processed_at DESC LIMIT 10000"
                    )
                ]
        except sqlite3.Error as e:
            logger.error(f"❌ Error leyendo DB SQLite desde '{self.db_path}': {e}")

        return snapshot

    def _refresh_cache(self) -> None:
        """Sincroniza la vista en memoria después de cada escritura."""
        self.data = self._snapshot()

    def get_last_id(self, chat_id: int) -> int:
        """Obtiene el último ID de mensaje procesado para un chat específico."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT message_id FROM last_ids WHERE chat_id = ?",
                    (str(chat_id),),
                ).fetchone()
            return int(row["message_id"]) if row else 0
        except sqlite3.Error as e:
            logger.error(f"❌ Error consultando last_id para chat {chat_id}: {e}")
            return 0

    def set_last_id(self, chat_id: int, message_id: int) -> None:
        """Establece el último ID de mensaje procesado para un chat específico."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO last_ids (chat_id, message_id)
                    VALUES (?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET message_id = excluded.message_id
                    """,
                    (str(chat_id), message_id),
                )
                conn.commit()
            self._refresh_cache()
        except sqlite3.Error as e:
            logger.error(f"❌ Error guardando last_id para chat {chat_id}: {e}")

    def is_card_processed(self, card_data: str) -> bool:
        """Verifica si la tarjeta ya fue procesada usando los datos originales."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM processed_cards WHERE card_data = ?",
                    (card_data,),
                ).fetchone()
            return row is not None
        except sqlite3.Error as e:
            logger.error(f"❌ Error verificando tarjeta procesada: {e}")
            return card_data in self.data.get("processed_cards", [])

    def mark_card_processed(self, card_data: str, source_info: str = "") -> None:
        """Marca una tarjeta como procesada guardando los datos originales."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO processed_cards (card_data, source_info) VALUES (?, ?)",
                    (card_data, source_info),
                )
                conn.commit()
            self._refresh_cache()
        except sqlite3.Error as e:
            logger.error(f"❌ Error marcando tarjeta procesada: {e}")

    def add_cards_stats(self, count: int = 1) -> None:
        """Actualiza las estadísticas de tarjetas procesadas y escaneos."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO stats (key, value) VALUES ('total_cards', ?)
                    ON CONFLICT(key) DO UPDATE SET value = value + excluded.value
                    """,
                    (count,),
                )
                conn.execute(
                    """
                    INSERT INTO stats (key, value) VALUES ('total_scans', 1)
                    ON CONFLICT(key) DO UPDATE SET value = value + 1
                    """
                )
                conn.commit()
            self._refresh_cache()
        except sqlite3.Error as e:
            logger.error(f"❌ Error actualizando estadísticas: {e}")

    def export_csv(self) -> str:
        """Exporta toda la información persistida a un CSV temporal dentro de DB_VOLUME."""
        export_dir = os.path.join(os.path.dirname(self.db_path), "exports")
        os.makedirs(export_dir, exist_ok=True)

        fd, export_path = tempfile.mkstemp(
            prefix="scrapp_db_",
            suffix=".csv",
            dir=export_dir,
            text=True,
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f, self._connect() as conn:
                writer = csv.writer(f)
                writer.writerow(["table", "key", "value", "created_at"])

                for row in conn.execute("SELECT chat_id, message_id FROM last_ids ORDER BY chat_id"):
                    writer.writerow(["last_ids", row[0], row[1], ""])

                for row in conn.execute("SELECT key, value FROM stats ORDER BY key"):
                    writer.writerow(["stats", row[0], row[1], ""])

                for row in conn.execute("SELECT card_data, processed_at, source_info FROM processed_cards ORDER BY processed_at DESC"):
                    writer.writerow(["processed_cards", row[0], row[2], row[1]])

        except Exception:
            try:
               bos.close(fd)
            except OSError:
                pass
            if os.path.exists(export_path):
               os.remove(export_path)
            raise

        return export_path

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


def mask_card_number(card_number: str) -> str:
    """Enmascara una tarjeta guardando los primeros dígitos visibles."""
    if 'x' in card_number.lower():
        # Ya está enmascarada parcialmente, devolver como está
        return card_number
    
    if len(card_number) <= 4:
        return "X" * len(card_number)
    
    # Mostrar primeros 6-12 dígitos y enmascarar el resto
    show_digits = min(12, len(card_number) - 4)
    masked = card_number[:show_digits] + "X" * (len(card_number) - show_digits)
    return masked


def get_bin_info(card_number: str, bin_database: Dict[str, Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Obtiene información del BIN desde la base de datos proporcionada."""
    # Limpiar 'x' del número de tarjeta para extraer BIN
    clean_number = re.sub(r'[^0-9]', '', card_number.split('|')[0])
    
    # Intentar con BINs de longitud 6, 5 y 4.
    for length in [6, 5, 4]:
        if len(clean_number) >= length:
            bin_code = clean_number[:length]
            if bin_code in bin_database:
                return bin_database[bin_code]
    return None


def format_card_message(card_data: str, bin_database: Dict[str, Dict[str, str]]) -> Optional[str]:
    """
    Formatea el mensaje de la tarjeta con la información del BIN.
    Maneja tanto tarjetas con CVV como sin CVV.
    """
    parts = card_data.split("|")
    
    # Manejar tanto tarjetas con CVV como sin CVV
    if len(parts) == 4:
        card_num, month, year, cvv = parts
        has_cvv = True
    elif len(parts) == 3:
        card_num, month, year = parts
        cvv = "xxx"
        has_cvv = False
    else:
        logger.warning(f"Formato de tarjeta inválido: {card_data}")
        return None
    
    # Validaciones básicas
    if len(card_num) < 12 or len(month) != 2:
        logger.warning(f"Datos de tarjeta no válidos: {card_data}")
        return None

    # Extraer información del BIN (solo si la tarjeta tiene suficientes dígitos)
    bin_info = None
    clean_card_num = re.sub(r'[^0-9]', '', card_num.split('|')[0])
    if len(clean_card_num) >= 4:
        bin_info = get_bin_info(card_num, bin_database)

    # Censurar la tarjeta para mostrarla
    censored_card_num = mask_card_number(card_num)
    display_year = f"20{year}" if len(year) == 2 else year
    
    if has_cvv and cvv != "xxx":
        censored = f"{censored_card_num}|{month}|{display_year}|{cvv[:3]}"
        cvv_display = f"{cvv[:3]}"
    else:
        censored = f"{censored_card_num}|{month}|{display_year}|xxx"
        cvv_display = "No disponible"

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

    country_with_flag = f"{pais} {country_flag(pais)}".strip()

    message = (
        f"<b>OLIMPO SCRAPPER</b>\n\n"
        f"<b>#<code>{html.escape(bin_code_found)}</code></b>\n"
        f"<b>━━━━━━━━</b>\n"
        f"<b>Serie= <code>{html.escape(censored)}</code></b>\n"
        f"<b>Bin= <code>{html.escape(bin_code_found)}</code></b>\n"
        f"<b>Banco= {html.escape(banco)}</b>\n"
        f"<b>Marca= {html.escape(brand)}</b>\n"
        f"<b>Tipo= {html.escape(tipo)}</b>\n"
        f"<b>Nivel= {html.escape(nivel)}</b>\n"
        f"<b>País= {html.escape(country_with_flag)}</b>\n"
        f"<b>━━━━━━━━</b>"\n"
        f"<b>DESARROLLADO POR <code>@MrMxyzptlk04</code> Y <code>@Chack0071</code></b>\n"
           )

    payload = {
        "chat_id": chat_id,
        "text": mensaje,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {
                        "text": "⭐ OLIMPO",
                        "url": BUTTON_URL,
                        "style": "success"
    )

    return message


def extract_urls(text: str) -> List[str]:
    """Extrae URLs HTTP/HTTPS de un texto."""
    if not text:
        return []

    # Patrón mejorado para capturar URLs con diferentes formatos
    url_pattern = r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s<>()\[\]{}"\']*'
    return re.findall(url_pattern, text, flags=re.IGNORECASE)


def normalize_url(url: str) -> str:
    """Normaliza una URL eliminando parámetros de consulta y fragmentos."""
    try:
        parsed = urlparse(url)
        # Mantener solo esquema, netloc y path
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return normalized.rstrip("/")
    except Exception:
        return url


def url_matches_domain(url: str, domains: List[str]) -> bool:
    """Indica si una URL pertenece a alguno de los dominios especificados."""
    try:
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False

    for domain in domains:
        if hostname == domain or hostname.endswith(f".{domain}"):
            return True
    return False


def url_matches_ignored_domain(url: str) -> bool:
    """Indica si una URL pertenece a un dominio que debe saltarse durante el scraping."""
    return url_matches_domain(url, IGNORED_LINK_DOMAINS)


def url_matches_processable_domain(url: str) -> bool:
    """Indica si una URL pertenece a un dominio que debe ser procesado para extraer tarjetas."""
    return url_matches_domain(url, PROCESSABLE_LINK_DOMAINS)


def should_skip_text_for_ignored_links(text: str) -> bool:
    """Determina si un texto debe ser ignorado por contener enlaces a dominios excluidos."""
    urls = extract_urls(text)
    for url in urls:
        if url_matches_ignored_domain(url):
            return True
    return False


async def fetch_url_content(url: str, session: aiohttp.ClientSession) -> Optional[str]:
    """Descarga el contenido de una URL sin guardar registro."""
    if not url_matches_processable_domain(url):
        logger.debug(f"URL no procesable (dominio no incluido): {url}")
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "DNT": "1",
        "Sec-GPC": "1"
    }

    for attempt in range(LINK_MAX_RETRIES + 1):
        try:
            async with session.get(
                url, 
                headers=headers, 
                timeout=aiohttp.ClientTimeout(total=LINK_REQUEST_TIMEOUT),
                allow_redirects=True
            ) as response:
                if response.status != 200:
                    logger.warning(f"URL respondió con código {response.status}: {url}")
                    continue

                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" not in content_type and "text/plain" not in content_type:
                    logger.debug(f"URL no es texto (Content-Type: {content_type}): {url}")
                    continue

                content = await response.text(encoding="utf-8", errors="ignore")
                
                # Limitar el tamaño del contenido procesado
                if len(content) > MAX_LINK_CONTENT_SIZE:
                    content = content[:MAX_LINK_CONTENT_SIZE]
                    logger.debug(f"Contenido truncado a {MAX_LINK_CONTENT_SIZE} bytes: {url}")

                logger.info(f"✅ Contenido descargado exitosamente: {url} ({len(content)} bytes)")
                return content

        except asyncio.TimeoutError:
            logger.warning(f"Timeout al descargar URL (intento {attempt + 1}/{LINK_MAX_RETRIES + 1}): {url}")
            if attempt < LINK_MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
            else:
                logger.error(f"❌ Falló después de {LINK_MAX_RETRIES + 1} intentos: {url}")
                break
        except aiohttp.ClientError as e:
            logger.warning(f"Error HTTP al descargar URL (intento {attempt + 1}/{LINK_MAX_RETRIES + 1}): {url} - {e}")
            if attempt < LINK_MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
            else:
                logger.error(f"❌ Falló después de {LINK_MAX_RETRIES + 1} intentos: {url}")
                break
        except Exception as e:
            logger.error(f"Error inesperado al descargar URL: {url} - {e}")
            break

    return None


def extract_telegram_buttons_urls(message: Message) -> List[str]:
    """Extrae URLs de botones inline de Telegram."""
    urls = []
    
    # Verificar botones inline (reply_markup)
    if message.reply_markup:
        for row in message.reply_markup.inline_keyboard:
            for button in row:
                if hasattr(button, 'url') and button.url:
                    urls.append(button.url)
    
    # Verificar botones de reply keyboard (menos común en este contexto)
    if message.reply_markup and hasattr(message.reply_markup, 'keyboard'):
        for row in message.reply_markup.keyboard:
            for button in row:
                if isinstance(button, dict) and 'url' in button:
                    urls.append(button['url'])
    
    return urls


def extract_cards_from_html(html_content: str) -> List[str]:
    """Extrae tarjetas de crédito del contenido HTML, enfocándose en telegra.ph."""
    cards = []
    
    # Patrones específicos para telegra.ph (que usa Markdown/HTML simple)
    # Telegra.ph generalmente muestra el contenido en texto plano dentro de <article> o <p>
    
    # Primero, extraer todo el texto del HTML
    text_content = re.sub(r'<[^>]+>', ' ', html_content)
    text_content = re.sub(r'\s+', ' ', text_content).strip()
    
    # Buscar tarjetas en el texto extraído
    cards.extend(extract_cards(text_content))
    
    # Buscar en elementos <pre> y <code> que pueden contener datos estructurados
    pre_pattern = r'<pre[^>]*>(.*?)</pre>'
    code_pattern = r'<code[^>]*>(.*?)</code>'
    
    for pattern in [pre_pattern, code_pattern]:
        matches = re.findall(pattern, html_content, re.IGNORECASE | re.DOTALL)
        for match in matches:
            # Limpiar HTML dentro del match
            clean_text = re.sub(r'<[^>]+>', ' ', match)
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
            cards.extend(extract_cards(clean_text))
    
    # Buscar patrones específicos de telegra.ph (listas con tarjetas)
    li_pattern = r'<li[^>]*>(.*?)</li>'
    li_matches = re.findall(li_pattern, html_content, re.IGNORECASE | re.DOTALL)
    for li_content in li_matches:
        clean_text = re.sub(r'<[^>]+>', ' ', li_content)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        cards.extend(extract_cards(clean_text))
    
    # Buscar en tablas (si las hay)
    td_pattern = r'<td[^>]*>(.*?)</td>'
    td_matches = re.findall(td_pattern, html_content, re.IGNORECASE | re.DOTALL)
    for td_content in td_matches:
        clean_text = re.sub(r'<[^>]+>', ' ', td_content)
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        cards.extend(extract_cards(clean_text))
    
    return list(set(cards))


async def extract_cards_from_url(url: str, session: aiohttp.ClientSession) -> List[str]:
    """Extrae tarjetas de crédito del contenido de una URL, especializado para telegra.ph."""
    content = await fetch_url_content(url, session)
    if not content:
        return []

    logger.info(f"🔍 Analizando contenido de {url} ({len(content)} bytes)")
    
    # Usar el extractor especializado para HTML
    cards = extract_cards_from_html(content)
    
    if cards:
        logger.info(f"✅ Encontradas {len(cards)} tarjetas en {url}")
    else:
        logger.info(f"📭 No se encontraron tarjetas en {url}")
    
    return cards


def extract_cards(text: str) -> List[str]:
    """
    Extrae tarjetas de crédito (formato CC) del texto proporcionado.
    Maneja tanto tarjetas completas como parciales con 'xxxx'.
    """
    if not text:
        return []

    cards: List[str] = []
    
    # Patrones mejorados que incluyen 'xxxx' y manejan diferentes formatos
    patterns = [
        # Formato con xxxx: 528182124899xxxx|05|2031 o 528182124899xxxx|05|31
        r'(\d{12,15}x{1,4})[|/\s]+(\d{2})[|/\s]+(\d{2,4})',
        # Formato completo con CVV: 1234567890123456|02|2030|816
        r'(\d{16})[|/\s]+(\d{2})[|/\s]+(\d{2,4})[|/\s]+(\d{3,4})',
        # Formato con espacios: 1234 5678 9012 3456 02 2030 816
        r'(\d{4}\s?\d{4}\s?\d{4}\s?\d{4})[|/\s]+(\d{2})[|/\s]+(\d{2,4})[|/\s]+(\d{3,4})',
        # Formato con guiones: 1234-5678-9012-3456-02-2030-816
        r'(\d{4}-\d{4}-\d{4}-\d{4})[|/\s]+(\d{2})[|/\s]+(\d{2,4})[|/\s]+(\d{3,4})',
        # Formato completo sin CVV: 1234567890123456|02|2030
        r'(\d{16})[|/\s]+(\d{2})[|/\s]+(\d{2,4})',
        # Formato con xxxx y CVV: 528182124899xxxx|05|2031|123
        r'(\d{12,15}x{1,4})[|/\s]+(\d{2})[|/\s]+(\d{2,4})[|/\s]+(\d{3,4})',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                if len(match) in [3, 4]:  # Aceptar 3 o 4 partes
                    if len(match) == 4:
                        card_num, month, year, cvv = match
                        has_cvv = True
                    else:  # len(match) == 3
                        card_num, month, year = match
                        cvv = "xxx"
                        has_cvv = False
                    
                    # Limpiar caracteres no numéricos del número de tarjeta solo si no tiene 'x'
                    if 'x' not in card_num.lower():
                        card_num = re.sub(r'\D', '', card_num)
                    
                    # Validar mes (01-12)
                    try:
                        month_num = int(month)
                        if not (1 <= month_num <= 12):
                            continue
                    except ValueError:
                        continue
                    
                    # Normalizar el año
                    if len(year) == 4:
                        year_normalized = year[2:]  # Tomar últimos 2 dígitos
                    elif len(year) == 2:
                        year_normalized = year
                    else:
                        continue  # Año inválido
                    
                    # Construir el formato de tarjeta
                    if has_cvv and cvv != "xxx":
                        # Limpiar CVV si es numérico
                        cvv = re.sub(r'\D', '', cvv)
                        if len(cvv) >= 3:
                            cvv_normalized = cvv[:3]
                            card_data = f"{card_num}|{month}|{year_normalized}|{cvv_normalized}"
                        else:
                            card_data = f"{card_num}|{month}|{year_normalized}|xxx"
                    else:
                        card_data = f"{card_num}|{month}|{year_normalized}|xxx"
                    
                    # Solo añadir si el número de tarjeta tiene al menos 12 caracteres
                    if len(card_num) >= 12:
                        cards.append(card_data)
                        
            except (IndexError, TypeError, ValueError) as e:
                logger.debug(f"⚠️ Error procesando tarjeta: {match} - {e}")
                continue

    # También buscar patrones con prefijos comunes
    prefix_patterns = [
        r'CC[:=\s]+([\d\s|/-x]{12,})[|/\s]+(\d{2})[|/\s]+(\d{2,4})(?:[|/\s]+(\d{3,4}))?',
        r'Card[:=\s]+([\d\s|/-x]{12,})[|/\s]+(\d{2})[|/\s]+(\d{2,4})(?:[|/\s]+(\d{3,4}))?',
        r'Credit[:=\s]+([\d\s|/-x]{12,})[|/\s]+(\d{2})[|/\s]+(\d{2,4})(?:[|/\s]+(\d{3,4}))?',
        r'#[:=\s]+([\d\s|/-x]{12,})[|/\s]+(\d{2})[|/\s]+(\d{2,4})(?:[|/\s]+(\d{3,4}))?',
    ]
    
    for pattern in prefix_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            try:
                if len(match) == 4:
                    card_num_raw, month, year, cvv = match
                    has_cvv = cvv != ''
                elif len(match) == 3:
                    card_num_raw, month, year = match
                    cvv = "xxx"
                    has_cvv = False
                else:
                    continue
                
                # Limpiar caracteres no numéricos solo si no tiene 'x'
                if 'x' not in card_num_raw.lower():
                    card_num = re.sub(r'\D', '', card_num_raw)
                else:
                    card_num = card_num_raw
                
                # Validar mes
                try:
                    if not (1 <= int(month) <= 12):
                        continue
                except ValueError:
                    continue
                
                # Normalizar año
                year_normalized = year[-2:] if len(year) == 4 else year
                
                # Construir el formato
                if has_cvv and cvv and cvv != "xxx":
                    cvv = re.sub(r'\D', '', cvv)
                    if len(cvv) >= 3:
                        card_data = f"{card_num}|{month}|{year_normalized}|{cvv[:3]}"
                    else:
                        card_data = f"{card_num}|{month}|{year_normalized}|xxx"
                else:
                    card_data = f"{card_num}|{month}|{year_normalized}|xxx"
                
                if len(card_num) >= 12:
                    cards.append(card_data)
                    
            except (IndexError, TypeError, ValueError) as e:
                logger.debug(f"⚠️ Error procesando tarjeta con prefijo: {match} - {e}")
                continue

    # Eliminar duplicados y devolver la lista
    return list(set(cards))


# --- Instancias Globales ---
BIN_DATABASE: Dict[str, Dict[str, str]] = load_bin_database()
db = SimpleDB()
USER_CLIENT_READY = False
OUTGOING_CARD_QUEUE: asyncio.Queue = asyncio.Queue()

# --- Clientes de Pyrogram ---
# El cliente 'user' se utiliza para unirse a chats y leer mensajes.
user = Client(
    "user_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    workers=100,
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
        return chat_identifier  # Ya es un ID numérico

    try:
        chat = await user.get_chat(chat_identifier)
        logger.info(f"  Resolviendo '{chat_identifier}' → ID: {chat.id}")
        return chat.id
    except Exception as e:
        logger.error(f"  ❌ No se pudo resolver '{chat_identifier}': {e}")
        return None


async def resolve_destination_chat(force_refresh: bool = False) -> Optional[int]:
    """Resuelve y cachea el chat de destino para tolerar cambios de ID o username."""
    global DESTINATION_CHAT_ID, DESTINATION_REFRESH_PENDING

    if DESTINATION_CHAT_ID is not None and not force_refresh:
        return DESTINATION_CHAT_ID

    try:
        chat = await app.get_chat(DESTINATION_CHAT)
        DESTINATION_CHAT_ID = chat.id
        DESTINATION_REFRESH_PENDING = False
        logger.info(f"✅ Destination chat resuelto: {DESTINATION_CHAT} → ID {DESTINATION_CHAT_ID}")
        return DESTINATION_CHAT_ID
    except Exception as e:
        DESTINATION_REFRESH_PENDING = True
        logger.error(
            f"❌ No se pudo resolver destination chat '{DESTINATION_CHAT}'. "
            f"Esperando un evento del canal/grupo para actualizararlo: {e}"
        )
        return None


def destination_identifier_matches_chat(chat) -> bool:
    """Indica si un evento recibido corresponde al destination chat configurado."""
    identifier = str(DESTINATION_CHAT).strip()
    chat_id = getattr(chat, "id", None)

    if DESTINATION_CHAT_ID is not None and chat_id == DESTINATION_CHAT_ID:
        return True

    if identifier.lstrip("-").isdigit():
        return chat_id == int(identifier)

    username = (getattr(chat, "username", None) or "").lower()
    normalized_identifier = identifier.lower().rstrip("/")

    if normalized_identifier.startswith("@"):
        return username == normalized_identifier[1:]

    if "t.me/" in normalized_identifier:
        return bool(username) and normalized_identifier.endswith(f"/{username}")

    return bool(username) and username == normalized_identifier


async def register_destination_chat_event(chat) -> None:
    """Actualiza el cache del destino cuando Telegram entrega un evento del canal/grupo."""
    global DESTINATION_CHAT_ID, DESTINATION_REFRESH_PENDING

    if not chat:
        return

    if DESTINATION_REFRESH_PENDING or destination_identifier_matches_chat(chat):
        previous_id = DESTINATION_CHAT_ID
        DESTINATION_CHAT_ID = chat.id
        DESTINATION_REFRESH_PENDING = False

        if previous_id != DESTINATION_CHAT_ID:
            logger.info(
                f"🔄 Destination chat actualizado por evento recibido: "
                f"{previous_id} → {DESTINATION_CHAT_ID} ({getattr(chat, 'title', '')})"
            )
        else:
            logger.debug(f"Destination chat confirmado por evento: {DESTINATION_CHAT_ID}")


async def deliver_card_message(message_content: str) -> bool:
    """Envía un mensaje al destination chat, refrescando el ID si Telegram rechaza el envío."""
    global DESTINATION_REFRESH_PENDING
    destination_chat_id = await resolve_destination_chat()
    if destination_chat_id is None:
        return False

    try:
        await app.send_message(destination_chat_id, message_content, parse_mode=ParseMode.HTML)
        return True
    except Exception as e:
        logger.warning(
            f"⚠️ Falló el envío a destination chat {destination_chat_id}; "
            f"se intentará refrescar el ID: {e}"
        )

    destination_chat_id = await resolve_destination_chat(force_refresh=True)
    if destination_chat_id is None:
        return False

    try:
        await app.send_message(destination_chat_id, message_content, parse_mode=ParseMode.HTML)
        return True
    except Exception as e:
        DESTINATION_REFRESH_PENDING = True
        logger.error(
            f"❌ Error enviando al destination chat {destination_chat_id}. "
            f"Queda pendiente registrar un evento del canal/grupo para actualizar el ID: {e}"
        )
        return False


async def send_card_immediately(card_data: str, source_info: str = "") -> bool:
    """
    Encola una tarjeta detectada para enviarla al chat de destino con pausa entre mensajes.
    Retorna True si la tarjeta fue aceptada en la cola, False si ya estaba procesada.
    """
    if db.is_card_processed(card_data):
        return False

    message_content = format_card_message(card_data, BIN_DATABASE)
    if not message_content:
        logger.warning(f"No se pudo formatear el mensaje para la tarjeta: {card_data}")
        return False

    await OUTGOING_CARD_QUEUE.put((card_data, message_content, source_info, 0))
    logger.info(
        f"📥 Tarjeta encolada: {card_data} ({source_info}). "
        f"Tamaño de cola: {OUTGOING_CARD_QUEUE.qsize()}"
    )
    return True


async def outgoing_card_sender() -> None:
    """Consume la cola de tarjetas enviando como máximo un mensaje cada SEND_INTERVAL_SECONDS."""
    logger.info(f"📨 Cola de envío iniciada: 1 mensaje cada {SEND_INTERVAL_SECONDS} segundos.")

    while True:
        card_data, message_content, source_info, attempts = await OUTGOING_CARD_QUEUE.get()
        sent = False
        already_processed = False

        try:
            already_processed = db.is_card_processed(card_data)
            if already_processed:
                logger.debug(f"Tarjeta ya procesada antes de enviar: {card_data}")
                sent = True
            else:
                sent = await deliver_card_message(message_content)

            if already_processed:
                pass
            elif sent:
                db.mark_card_processed(card_data, source_info)
                db.add_cards_stats(1)
                logger.info(f"✅ Tarjeta enviada: {card_data} ({source_info})")
            elif attempts < 3:
                await OUTGOING_CARD_QUEUE.put((card_data, message_content, source_info, attempts + 1))
                logger.warning(
                    f"🔁 Reintentando tarjeta {card_data} más tarde "
                    f"(intento {attempts + 1}/3)."
                )
            else:
                logger.error(f"❌ Tarjeta descartada tras 3 reintentos: {card_data}")
        finally:
            OUTGOING_CARD_QUEUE.task_done()

        await asyncio.sleep(SEND_INTERVAL_SECONDS)


async def process_message_links(message: Message, chat_id: int) -> int:
    """Procesa los enlaces de un mensaje (texto y botones) sin guardar registro de enlaces."""
    new_cards_count = 0
    
    # Extraer URLs del texto del mensaje
    text = message.text or message.caption or ""
    urls_from_text = extract_urls(text)
    
    # Extraer URLs de botones inline
    urls_from_buttons = extract_telegram_buttons_urls(message)
    
    # Combinar todas las URLs
    all_urls = list(set(urls_from_text + urls_from_buttons))
    
    # Filtrar solo URLs procesables (telegra.ph)
    processable_urls = [url for url in all_urls if url_matches_processable_domain(url)]
    
    if processable_urls:
        logger.info(f"🔗 Encontrados {len(processable_urls)} enlaces procesables en mensaje {message.id}")
        
        # Crear sesión HTTP para procesar enlaces
        async with aiohttp.ClientSession() as session:
            for url in processable_urls:
                try:
                    logger.info(f"📥 Descargando contenido de: {url}")
                    cards_from_url = await extract_cards_from_url(url, session)
                    
                    if cards_from_url:
                        logger.info(f"✅ Encontradas {len(cards_from_url)} tarjetas en {url}")
                        
                        for card in cards_from_url:
                            success = await send_card_immediately(card, f"URL: {url}, Chat: {chat_id}, Msg: {message.id}")
                            if success:
                                new_cards_count += 1
                    else:
                        logger.info(f"📭 No se encontraron tarjetas en {url}")
                        
                except Exception as e:
                    logger.error(f"❌ Error procesando enlace {url}: {e}")
    
    return new_cards_count


async def process_message_text_only(text: str, chat_id: int, message_id: int = 0) -> int:
    """Procesa solo el texto de un mensaje y retorna el número de tarjetas encontradas."""
    if not text:
        return 0

    new_cards_count = 0
    
    # Buscar tarjetas directamente en el texto del mensaje
    direct_cards = extract_cards(text)
    for card in direct_cards:
        success = await send_card_immediately(card, f"Chat: {chat_id}, Msg: {message_id}")
        if success:
            new_cards_count += 1
    
    return new_cards_count


async def scrape_chat_realtime(chat_id: int) -> tuple[int, int]:
    """
    Scrapea mensajes de un chat específico y encola las tarjetas detectadas para envío pausado.
    Retorna el último ID de mensaje procesado y el número de nuevas tarjetas encontradas.
    """
    last_processed_id = db.get_last_id(chat_id)
    max_message_id_in_chat = last_processed_id  # Inicializar con el último procesado
    new_cards_count = 0

    try:
        logger.info(f"Scrapeando chat {chat_id} desde el mensaje ID {last_processed_id}...")
        # Obtener un historial limitado para evitar sobrecargar la memoria.
        async for message in user.get_chat_history(chat_id, limit=1000):
            if message.id <= last_processed_id:
                # Ya hemos procesado estos mensajes en ejecuciones anteriores.
                break

            max_message_id_in_chat = max(max_message_id_in_chat, message.id)

            text = message.text or message.caption
            if text:
                if should_skip_text_for_ignored_links(text):
                    logger.info(
                        f"Saltando mensaje {message.id} de chat {chat_id}: "
                        "contiene enlace de dominio excluido."
                    )
                    continue

                # Procesar texto del mensaje
                cards_from_text = await process_message_text_only(text, chat_id, message.id)
                new_cards_count += cards_from_text
                
                # Procesar enlaces del mensaje (incluyendo botones)
                cards_from_links = await process_message_links(message, chat_id)
                new_cards_count += cards_from_links
                
                if cards_from_text > 0 or cards_from_links > 0:
                    logger.info(f"📨 Mensaje {message.id}: {cards_from_text} tarjetas en texto, {cards_from_links} tarjetas en enlaces")

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

@app.on_message(~filters.private, group=1)
async def destination_event_logger(client: Client, message):
    """Registra eventos de canales/grupos para mantener actualizado el ID del destination chat."""
    await register_destination_chat_event(message.chat)


@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message):
    """Comando /start - Muestra información y ayuda del bot."""
    chats_list_formatted = "\n".join([f"<code>{c}</code>" for c in CHATS_TO_SCRAPE])

    await message.reply(
        f"🤖 <b>Auto Scraper Bot - Realtime</b>\n\n"
        f"<b>Chats monitoreados:</b>\n{chats_list_formatted}\n\n"
        f"💳 Envío: <b>Cola pausada, 1 mensaje cada {SEND_INTERVAL_SECONDS}s</b>\n"
        f"📊 Base BIN: <code>{CSV_FILE}</code> ({len(BIN_DATABASE)} entradas cargadas)\n"
        f"🔗 Dominios procesables: <code>{html.escape(', '.join(sorted(PROCESSABLE_LINK_DOMAINS)))}</code>\n"
        f"🔕 Dominios saltados: <code>{html.escape(', '.join(sorted(IGNORED_LINK_DOMAINS)))}</code>\n"
        f"💾 DB persistente: <code>{html.escape(db.db_path)}</code>\n"
        f"⏱️ Intervalo de escaneo: <code>{CHECK_INTERVAL}s</code>\n\n"
        f"<b>Comandos disponibles:</b>\n"
        f"/status - Ver estado actual del bot.\n"
        f"/force - Forzar un escaneo inmediato de todos los chats.\n"
        f"/stats - Mostrar estadísticas generales.\n"
        f"/export_db - Exportar la base de datos en CSV.\n"
        f"/refresh_destination - Refrescar el destination chat.\n"
        f"/test - Enviar una tarjeta de prueba formateada.",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("test"))
async def test_cmd(client: Client, message):
    """Comando /test - Envía una tarjeta de prueba formateada al chat de destino."""
    test_card_data = "4207670324511073|02|2030|816"  # Ejemplo de tarjeta
    logger.info(f"Ejecutando comando /test. Enviando tarjeta de prueba: {test_card_data}")
    
    success = await send_card_immediately(test_card_data, "Comando /test")
    if success:
        await message.reply("✅ Mensaje de prueba encolado exitosamente.")
    else:
        await message.reply("❌ Falló el encolado del mensaje de prueba.")

@app.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message):
    """Comando /start - Muestra información y ayuda del bot."""
    chats_list_formatted = "\n".join([f"<code>{c}</code>" for c in CHATS_TO_SCRAPE])

    await message.reply(
        f"🤖 <b>Auto Scraper Bot - Realtime</b>\n\n"
        f"<b>Chats monitoreados:</b>\n{chats_list_formatted}\n\n"
        f"💳 Envío: <b>Cola pausada, 1 mensaje cada {SEND_INTERVAL_SECONDS}s</b>\n"
        f"📊 Base BIN: <code>{CSV_FILE}</code> ({len(BIN_DATABASE)} entradas cargadas)\n"
        f"🔗 Dominios procesables: <code>{html.escape(', '.join(sorted(PROCESSABLE_LINK_DOMAINS)))}</code>\n"
        f"🔕 Dominios saltados: <code>{html.escape(', '.join(sorted(IGNORED_LINK_DOMAINS)))}</code>\n"
        f"💾 DB persistente: <code>{html.escape(db.db_path)}</code>\n"
        f"⏱️ Intervalo de escaneo: <code>{CHECK_INTERVAL}s</code>\n\n"
        f"<b>Comandos disponibles:</b>\n"
        f"/status - Ver estado actual del bot.\n"
        f"/force - Forzar un escaneo inmediato de todos los chats.\n"
        f"/stats - Mostrar estadísticas generales.\n"
        f"/export_db - Exportar la base de datos en CSV.\n"
        f"/refresh_destination - Refrescar el destination chat.\n"
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
        await message.reply("✅ Mensaje de prueba encolado exitosamente.")
    else:
        await message.reply("❌ Falló el encolado del mensaje de prueba.")

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
        f"<b>Dominios procesables:</b> <code>{html.escape(', '.join(sorted(PROCESSABLE_LINK_DOMAINS)))}</code>\n"
        f"<b>Dominios saltados:</b> <code>{html.escape(', '.join(sorted(IGNORED_LINK_DOMAINS)))}</code>\n"
        f"<b>BINs cargados:</b> <code>{len(BIN_DATABASE)}</code>\n"
        f"<b>Intervalo de escaneo:</b> <code>{CHECK_INTERVAL}s</code>\n"
        f"<b>Intervalo de envío:</b> <code>{SEND_INTERVAL_SECONDS}s</code>\n"
        f"<b>Cola pendiente:</b> <code>{OUTGOING_CARD_QUEUE.qsize()}</code>\n"
        f"<b>Destination chat:</b> <code>{DESTINATION_CHAT_ID or DESTINATION_CHAT}</code>\n"
        f"<b>DB persistente:</b> <code>{html.escape(db.db_path)}</code>\n"
        f"<b>Tarjetas detectadas:</b> <code>{stats.get('total_cards', 0)}</code>\n"
        f"<b>Enlaces procesados:</b> <code>{stats.get('links_processed', 0)}</code>\n"
        f"<b>Escaneos con hallazgos:</b> <code>{stats.get('total_scans', 0)}</code>\n"
        f"<b>Chats con progreso:</b> <code>{len(last_ids)}</code>",
        parse_mode=ParseMode.HTML,
    )



@app.on_message(filters.command("refresh_destination"))
async def refresh_destination_cmd(client: Client, message):
    """Comando /refresh_destination - Fuerza la resolución del destination chat configurado."""
    destination_chat_id = await resolve_destination_chat(force_refresh=True)

    if destination_chat_id is None:
        await message.reply(
            "❌ No se pudo resolver el destination chat. "
            "Envía o reenvía un evento/mensaje en el canal destino para que el bot registre el ID actualizado."
        )
        return

    await message.reply(
        f"✅ Destination chat actualizado: <code>{destination_chat_id}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("stats"))
async def stats_cmd(client: Client, message):
    """Comando /stats - Muestra estadísticas generales."""
    stats = db.data.get("stats", {})
    processed_count = len(db.data.get("processed_cards", []))
    processed_links_count = len(db.data.get("processed_links", []))

    await message.reply(
        f"📊 <b>Estadísticas</b>\n\n"
        f"<b>Tarjetas nuevas detectadas:</b> <code>{stats.get('total_cards', 0)}</code>\n"
        f"<b>Ciclos con detecciones:</b> <code>{stats.get('total_scans', 0)}</code>\n"
        f"<b>Enlaces procesados:</b> <code>{stats.get('links_processed', 0)}</code>\n"
        f"<b>Registros deduplicados:</b> <code>{processed_count}</code>\n"
        f"<b>Enlaces únicos procesados:</b> <code>{processed_links_count}</code>\n"
        f"<b>Mensajes pendientes en cola:</b> <code>{OUTGOING_CARD_QUEUE.qsize()}</code>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command(["export_db", "exportdb"]))
async def export_db_cmd(client: Client, message):
    """Comando /export_db - Exporta la base de datos persistente en formato CSV."""
    export_path: Optional[str] = None

    try:
        export_path = db.export_csv()
        await client.send_document(
            chat_id=message.chat.id,
            document=export_path,
            caption="✅ Exportación de la base de datos en formato CSV.",
        )
    except Exception as e:
        logger.exception(f"❌ Error exportando la DB a CSV: {e}")
        await message.reply("❌ No se pudo exportar la base de datos en CSV. Revisa los logs del servicio.")
    finally:
        if export_path and os.path.exists(export_path):
            try:
                os.remove(export_path)
            except OSError as e:
                logger.warning(f"⚠️ No se pudo eliminar el CSV temporal '{export_path}': {e}")


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
    sender_task: Optional[asyncio.Task] = None

    try:
        logger.info("🚀 Iniciando cliente bot de Telegram...")
        await app.start()
        logger.info("✅ Cliente bot iniciado correctamente.")
        await resolve_destination_chat(force_refresh=True)
        sender_task = asyncio.create_task(outgoing_card_sender())

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
        for task in (scanner_task, sender_task):
            if task:
                task.cancel()
                try:
                    await task
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
