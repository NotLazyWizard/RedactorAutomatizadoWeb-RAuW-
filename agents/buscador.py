"""
Agente Buscador: Lee la bandeja de entrada cada 30 minutos.
Estrategia de correos no leídos: usa Message-ID únicos guardados en BD local.
Esto es independiente del flag \Seen del servidor IMAP.
"""

import imaplib
import email
import email.header
import logging
import json
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
from typing import Optional
import os

logger = logging.getLogger(__name__)


def decodificar_header(valor: str) -> str:
    """Decodifica headers de correo que pueden estar en distintas codificaciones."""
    partes = email.header.decode_header(valor)
    resultado = []
    for parte, codificacion in partes:
        if isinstance(parte, bytes):
            resultado.append(parte.decode(codificacion or "utf-8", errors="replace"))
        else:
            resultado.append(str(parte))
    return " ".join(resultado)


def extraer_texto_html(html: str) -> str:
    """Convierte HTML a texto plano limpio."""
    soup = BeautifulSoup(html, "lxml")
    # Eliminar scripts y estilos
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def extraer_youtube_urls(texto: str) -> list[str]:
    """Extrae URLs de YouTube del texto."""
    patron = r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w\-]+(?:&\S*)?)'
    return re.findall(patron, texto)


def extraer_urls_externas(texto: str) -> list[str]:
    """Extrae todas las URLs del texto excluyendo las de YouTube."""
    patron = r'https?://[^\s<>"{}|\\^`\[\]]+'
    todas = re.findall(patron, texto)
    return [u for u in todas if "youtube" not in u and "youtu.be" not in u]


class LectorCorreo:
    def __init__(self):
        self.host = os.getenv("IMAP_HOST", "imap.hostinger.com")
        self.port = int(os.getenv("IMAP_PORT", "993"))
        self.email_address = os.getenv("EMAIL_ADDRESS")
        self.password = os.getenv("EMAIL_PASSWORD")
        self.conexion: Optional[imaplib.IMAP4_SSL] = None

    def conectar(self) -> bool:
        """Establece conexión IMAP SSL con Hostinger."""
        try:
            self.conexion = imaplib.IMAP4_SSL(self.host, self.port)
            self.conexion.login(self.email_address, self.password)
            logger.info(f"Conectado a IMAP: {self.host}")
            return True
        except Exception as e:
            logger.error(f"Error conectando a IMAP: {e}")
            return False

    def desconectar(self):
        if self.conexion:
            try:
                self.conexion.logout()
            except Exception:
                pass

    def obtener_correos_recientes(self, dias: int = 7) -> list[dict]:
        """
        Obtiene todos los correos de los últimos N días.
        NO filtra por leído/no leído — eso lo maneja la BD local.
        """
        correos = []

        if not self.conectar():
            return correos

        try:
            self.conexion.select("INBOX")

            # Buscar correos de los últimos N días
            fecha_limite = (datetime.now() - timedelta(days=dias)).strftime("%d-%b-%Y")
            _, numeros = self.conexion.search(None, f'SINCE {fecha_limite}')

            ids = numeros[0].split()
            logger.info(f"Encontrados {len(ids)} correos en los últimos {dias} días")

            for num in ids:
                try:
                    correo_data = self._parsear_correo(num)
                    if correo_data:
                        correos.append(correo_data)
                except Exception as e:
                    logger.error(f"Error parseando correo {num}: {e}")

        except Exception as e:
            logger.error(f"Error obteniendo correos: {e}")
        finally:
            self.desconectar()

        return correos

    def _parsear_correo(self, num: bytes) -> Optional[dict]:
        """Parsea un correo IMAP y retorna un dict con toda la información relevante."""
        _, data = self.conexion.fetch(num, "(RFC822)")
        raw = data[0][1]
        msg = email.message_from_bytes(raw)

        message_id = msg.get("Message-ID", "").strip()
        if not message_id:
            # Si no tiene Message-ID, generamos uno basado en asunto+fecha
            asunto_raw = msg.get("Subject", "sin-asunto")
            fecha_raw = msg.get("Date", "")
            message_id = f"<generated-{hash(asunto_raw + fecha_raw)}@gikinx>"

        asunto = decodificar_header(msg.get("Subject", "(Sin asunto)"))
        remitente = decodificar_header(msg.get("From", ""))

        # Parsear fecha
        fecha_str = msg.get("Date", "")
        try:
            fecha = parsedate_to_datetime(fecha_str)
        except Exception:
            fecha = datetime.utcnow()

        # Extraer cuerpo y adjuntos
        cuerpo_texto = ""
        cuerpo_html = ""
        imagenes = []

        if msg.is_multipart():
            for parte in msg.walk():
                content_type = parte.get_content_type()
                content_disposition = str(parte.get("Content-Disposition", ""))

                if "attachment" in content_disposition:
                    # Imagen adjunta
                    if content_type.startswith("image/"):
                        nombre = parte.get_filename() or "imagen.jpg"
                        datos = parte.get_payload(decode=True)
                        if datos:
                            imagenes.append({
                                "nombre": nombre,
                                "datos": datos,
                                "tipo": content_type
                            })
                    continue

                if content_type == "text/plain":
                    payload = parte.get_payload(decode=True)
                    if payload:
                        charset = parte.get_content_charset() or "utf-8"
                        cuerpo_texto = payload.decode(charset, errors="replace")
                elif content_type == "text/html":
                    payload = parte.get_payload(decode=True)
                    if payload:
                        charset = parte.get_content_charset() or "utf-8"
                        cuerpo_html = payload.decode(charset, errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                content_type = msg.get_content_type()
                if content_type == "text/html":
                    cuerpo_html = payload.decode(charset, errors="replace")
                else:
                    cuerpo_texto = payload.decode(charset, errors="replace")

        # Preferir texto plano; si no hay, convertir HTML
        if not cuerpo_texto and cuerpo_html:
            cuerpo_texto = extraer_texto_html(cuerpo_html)

        # Extraer URLs del cuerpo completo
        texto_busqueda = cuerpo_texto + " " + cuerpo_html
        youtube_urls = extraer_youtube_urls(texto_busqueda)
        urls_externas = extraer_urls_externas(texto_busqueda)

        return {
            "message_id": message_id,
            "asunto": asunto,
            "remitente": remitente,
            "fecha": fecha,
            "cuerpo": cuerpo_texto[:8000],  # Limitar para no saturar el LLM
            "imagenes": imagenes,
            "youtube_urls": youtube_urls,
            "urls_externas": urls_externas[:5]  # Máximo 5 URLs externas
        }
