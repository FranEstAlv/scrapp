import re

def getUrl(string):
    """Extrae URLs de una cadena de texto usando una expresión regular."""
    regex = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
    urls = re.findall(regex, string)
    return [x[0] for x in urls] if urls else None

def getcards(text: str):
    """Analiza el texto para extraer y validar la información de la tarjeta de crédito."""
    text = text.replace('\n', ' ').replace('\r', '')
    card_numbers = re.findall(r"\b\d{13,16}\b", text) # Busca números de tarjeta de 13 a 16 dígitos
    if not card_numbers:
        return None

    cc = card_numbers[0]
    other_numbers = re.findall(r"\b\d{2,4}\b", text) # Busca mes, año y cvv

    if len(other_numbers) < 2:
        return None

    # Lógica simplificada para encontrar mes, año y cvv
    try:
        # Asumir que el mes y el año son los dos primeros números de 2 o 4 dígitos
        mes = other_numbers[0]
        ano = other_numbers[1]
        # Asumir que el cvv es el siguiente número de 3 o 4 dígitos
        cvv = next((n for n in other_numbers[2:] if len(n) in [3, 4]), None)

        # Validaciones básicas
        if not (mes and ano and cvv):
            return None
        if not (1 <= int(mes) <= 12 and (21 <= int(ano) <= 29 or 2021 <= int(ano) <= 2029)):
            return None
        if not (len(cvv) == 3 or (len(cvv) == 4 and cc.startswith("3"))):
            return None

        return cc, mes, ano, cvv
    except (ValueError, IndexError):
        return None

