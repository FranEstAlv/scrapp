#!/usr/bin/env python3
"""Script de prueba de sintaxis para main.py"""

import sys
import py_compile

try:
    print("🔍 Verificando sintaxis de main.py...")
    py_compile.compile('main.py', doraise=True)
    print("✅ La sintaxis es correcta - No hay errores de compilación")
    sys.exit(0)
except py_compile.PyCompileError as e:
    print(f"❌ Error de compilación encontrado:")
    print(f"  Archivo: {e.file}")
    print(f"  Línea: {e.msg}")
    sys.exit(1)
