"""
Cliente Groq centralizado para todos los agentes.
Maneja la conexión, reintentos y parsing de respuestas JSON.
"""

import json
import logging
import os
import re
import time
from groq import Groq

logger = logging.getLogger(__name__)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")


def llamar_llm(prompt: str, max_tokens: int = 2000, intentos: int = 3) -> str:
    """
    Llamada básica al LLM. Retorna el texto de la respuesta.
    """
    for intento in range(intentos):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0.7,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error LLM (intento {intento + 1}/{intentos}): {e}")
            if intento < intentos - 1:
                time.sleep(2 ** intento)  # Backoff exponencial

    return ""


def llamar_llm_json(prompt: str, max_tokens: int = 2000) -> dict:
    """
    Llamada al LLM esperando respuesta en JSON.
    Limpia el markdown y parsea el JSON automáticamente.
    """
    respuesta = llamar_llm(prompt, max_tokens)

    if not respuesta:
        return {}

    # Limpiar posibles bloques de código markdown
    respuesta_limpia = re.sub(r"```json\s*", "", respuesta)
    respuesta_limpia = re.sub(r"```\s*", "", respuesta_limpia)
    respuesta_limpia = respuesta_limpia.strip()

    # Intentar extraer JSON si hay texto extra antes/después
    match = re.search(r'\{.*\}', respuesta_limpia, re.DOTALL)
    if match:
        respuesta_limpia = match.group(0)

    try:
        return json.loads(respuesta_limpia)
    except json.JSONDecodeError as e:
        logger.error(f"Error parseando JSON del LLM: {e}\nRespuesta: {respuesta[:500]}")

        base = respuesta_limpia.rstrip()

        for corte in range(len(base), max(len(base) - 400, 0), -1):
            candidato = base[:corte]

            if candidato.count('"') % 2 != 0:
                ultimo_corte_seguro = max(
                    candidato.rfind(','), candidato.rfind('['), candidato.rfind('{')
                )
                if ultimo_corte_seguro == -1:
                    continue
                candidato = candidato[:ultimo_corte_seguro]

            candidato = candidato.rstrip().rstrip(',')

            faltan_corchetes = candidato.count('[') - candidato.count(']')
            faltan_llaves = candidato.count('{') - candidato.count('}')

            reparado = candidato + (']' * max(faltan_corchetes, 0)) + ('}' * max(faltan_llaves, 0))

            try:
                return json.loads(reparado)
            except json.JSONDecodeError:
                continue

        logger.error("No se pudo reparar el JSON truncado")
        return {}

    except json.JSONDecodeError as e:
        logger.error(f"Error parseando JSON del LLM: {e}\nRespuesta: {respuesta[:500]}")

        # Intento de recuperación: si la cadena quedó cortada (truncada por max_tokens),
        # cerramos la última cadena abierta y las llaves faltantes para rescatar lo posible.
        try:
            reparado = respuesta_limpia
            if reparado.count('"') % 2 != 0:
                reparado += '"'
            faltan_llaves = reparado.count('{') - reparado.count('}')
            faltan_corchetes = reparado.count('[') - reparado.count(']')
            reparado += ']' * max(faltan_corchetes, 0)
            reparado += '}' * max(faltan_llaves, 0)
            return json.loads(reparado)
        except Exception:
            logger.error("No se pudo reparar el JSON truncado")
            return {}
