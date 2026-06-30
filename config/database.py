"""
Modelos de base de datos SQLite para el sistema Gikinx Bot.
Estrategia de correos no leídos: guardamos Message-ID de cada correo procesado,
independientemente del estado leído/no leído en el servidor IMAP.
"""

from sqlalchemy import (
    create_engine, Column, Integer, String, Text,
    DateTime, Boolean, Enum as SAEnum
)
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import enum
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/gikinx.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class EstadoNota(str, enum.Enum):
    # Flujo de estados de cada nota
    DETECTADA          = "detectada"           # Correo detectado como geek
    ESPERANDO_ADMIN    = "esperando_admin"      # Esperando si el admin quiere subirla
    DESCARTADA_AUTO    = "descartada_auto"      # El LLM la descartó (no es geek)
    DESCARTADA_MANUAL  = "descartada_manual"    # El admin dijo que no
    CANCELADA          = "cancelada"            # Admin canceló en cualquier punto
    EN_REDACCION       = "en_redaccion"         # Escritor trabajando en ella
    EN_REVISION_JUEZ   = "en_revision_juez"     # El juez la está evaluando
    ESPERANDO_APROBACION_FINAL = "esperando_aprobacion_final"  # Admin revisa el .txt
    PUBLICADA          = "publicada"            # Ya está en WordPress


class CorreoProcesado(Base):
    """
    Registro de TODOS los correos vistos, sin importar si son geek o no.
    La columna message_id es la clave para saber si ya fue procesado,
    completamente independiente del flag \Seen del servidor IMAP.
    """
    __tablename__ = "correos_procesados"

    id         = Column(Integer, primary_key=True)
    message_id = Column(String(500), unique=True, nullable=False, index=True)
    asunto     = Column(String(500))
    remitente  = Column(String(200))
    fecha_correo = Column(DateTime)
    es_geek    = Column(Boolean, default=False)
    fecha_procesado = Column(DateTime, default=datetime.utcnow)


class Nota(Base):
    """
    Nota en proceso de redacción y publicación.
    """
    __tablename__ = "notas"

    id              = Column(Integer, primary_key=True)
    message_id      = Column(String(500), unique=True, nullable=False)
    asunto_original = Column(String(500))
    texto_original  = Column(Text)          # Cuerpo del correo original
    imagenes_urls   = Column(Text)          # JSON con lista de URLs de imágenes del correo
    resumen_admin   = Column(String(500))   # ≤20 palabras enviadas al admin
    titulo_detectado = Column(String(300))

    # Versiones de la nota redactada (el escritor puede iterar)
    nota_actual     = Column(Text)          # Última versión de la nota reescrita
    titulo_nota     = Column(String(200))
    meta_descripcion = Column(String(300))
    tags_sugeridos  = Column(Text)          # JSON con lista de tags
    categoria       = Column(String(200))
    youtube_url     = Column(String(500))   # URL de YouTube detectada
    enlace_externo  = Column(String(500))
    es_destacada    = Column(Boolean, default=False)

    # Control de iteraciones
    intento_escritura = Column(Integer, default=0)
    feedback_juez     = Column(Text)        # Último feedback del juez
    feedback_admin    = Column(Text)        # Último feedback del admin

    estado          = Column(SAEnum(EstadoNota), default=EstadoNota.DETECTADA)
    wp_post_id      = Column(Integer, nullable=True)
    wp_url          = Column(String(500), nullable=True)

    fecha_creacion  = Column(DateTime, default=datetime.utcnow)
    fecha_actualizacion = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db():
    """Crea todas las tablas si no existen."""
    Base.metadata.create_all(engine)


def get_db():
    """Context manager para sesiones de base de datos."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
