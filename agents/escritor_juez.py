"""
Agente Escritor: Reescribe la nota del correo siguiendo el prompt editorial.
Agente Juez: Evalúa la nota reescrita y da feedback al escritor.
"""

import logging
from config.prompts import PROMPT_ESCRITOR, PROMPT_JUEZ
from utils.llm_client import llamar_llm_json

logger = logging.getLogger(__name__)


class AgenteEscritor:
    """
    Reescribe noticias del correo siguiendo las reglas editoriales de Gikinx.
    """

    def redactar(self, texto_fuente: str) -> dict:
        """
        Recibe el texto original del correo y retorna la nota reescrita en JSON.
        """
        logger.info("Agente Escritor: redactando nota...")

        prompt = PROMPT_ESCRITOR.format(texto_fuente=texto_fuente)
        resultado = llamar_llm_json(prompt, max_tokens=2500)

        if not resultado:
            logger.error("El escritor no pudo generar la nota")
            return {}

        # Validaciones básicas de campos obligatorios
        campos_requeridos = ["titulo", "meta_descripcion", "cuerpo", "tags"]
        for campo in campos_requeridos:
            if campo not in resultado:
                logger.warning(f"Campo faltante en respuesta del escritor: {campo}")
                resultado[campo] = "" if campo != "tags" else []

        # Truncar si exceden límites
        if len(resultado.get("titulo", "")) > 60:
            logger.warning("Título excede 60 chars, truncando")
            resultado["titulo"] = resultado["titulo"][:60]

        if len(resultado.get("meta_descripcion", "")) > 160:
            logger.warning("Meta descripción excede 160 chars, truncando")
            resultado["meta_descripcion"] = resultado["meta_descripcion"][:160]

        logger.info(f"Nota redactada: '{resultado.get('titulo', 'Sin título')}'")
        return resultado

    def corregir(self, texto_fuente: str, nota_actual: dict, instrucciones: str) -> dict:
        """
        Corrige la nota según las instrucciones del juez o del admin.
        """
        logger.info(f"Agente Escritor: corrigiendo nota con instrucciones del juez...")

        nota_formateada = f"""
TÍTULO ACTUAL: {nota_actual.get('titulo', '')}
META ACTUAL: {nota_actual.get('meta_descripcion', '')}
CUERPO ACTUAL:
{nota_actual.get('cuerpo', '')}
"""

        prompt = f"""
{PROMPT_ESCRITOR.format(texto_fuente=texto_fuente)}

NOTA IMPORTANTE: Ya existe una versión previa de la nota que necesita correcciones.

VERSIÓN PREVIA:
{nota_formateada}

INSTRUCCIONES DE CORRECCIÓN (aplícalas obligatoriamente):
{instrucciones}

Genera una versión corregida manteniendo el mismo formato JSON.
"""
        resultado = llamar_llm_json(prompt, max_tokens=2500)

        if not resultado:
            logger.error("El escritor no pudo corregir la nota")
            return nota_actual  # Devuelve la versión anterior si falla

        return resultado


class AgenteJuez:
    """
    Evalúa la nota reescrita contra el original y los criterios editoriales.
    """

    def evaluar(
        self,
        texto_original: str,
        nota_reescrita: dict,
        feedback_admin: str = ""
    ) -> dict:
        """
        Evalúa la nota y decide si está aprobada o necesita correcciones.
        Retorna dict con: aprobada, puntaje, problemas, instrucciones_escritor.
        """
        logger.info("Agente Juez: evaluando nota...")

        nota_formateada = f"""
TÍTULO: {nota_reescrita.get('titulo', '')}
META DESCRIPCIÓN: {nota_reescrita.get('meta_descripcion', '')}

CUERPO:
{nota_reescrita.get('cuerpo', '')}
"""

        # Si hay feedback del admin, se lo pasamos al juez
        seccion_admin = ""
        if feedback_admin:
            seccion_admin = f"""
INSTRUCCIONES ADICIONALES DEL ADMINISTRADOR (prioritarias):
{feedback_admin}
"""

        prompt = PROMPT_JUEZ.format(
            texto_original=texto_original,
            nota_reescrita=nota_formateada,
            feedback_admin=seccion_admin
        )

        resultado = llamar_llm_json(prompt, max_tokens=2500)

        if not resultado:
            logger.error("El juez no pudo evaluar la nota")
            return {
                "aprobada": False,
                "puntaje": 0,
                "problemas": ["Error interno al evaluar"],
                "instrucciones_escritor": "Revisar el formato general de la nota."
            }

        logger.info(
            f"Evaluación del juez: aprobada={resultado.get('aprobada')}, "
            f"puntaje={resultado.get('puntaje')}"
        )
        return resultado
