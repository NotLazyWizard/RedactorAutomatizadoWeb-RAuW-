"""
Bot de Telegram: maneja todas las interacciones con el admin.
Flujos: aprobación inicial → revisión de nota → publicación → cancelación.
"""

import json
import logging
import os
from telegram import Bot, Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from config.database import SessionLocal, Nota, EstadoNota

logger = logging.getLogger(__name__)

ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", "0"))
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot Gikinx activo*\n\n"
        "Comandos disponibles:\n"
        "/notas — Ver notas en proceso\n"
        "/ayuda — Ver esta ayuda",
        parse_mode="Markdown"
    )


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Comandos del Bot Gikinx*\n\n"
        "Cuando se detecte una nota, recibirás:\n"
        "• `/si_ID` — Aprobar para redacción\n"
        "• `/no_ID` — Descartar nota\n\n"
        "Cuando la nota esté redactada:\n"
        "• `/publicar_ID` — Publicar en WordPress\n"
        "• `/cancelar_ID` — Cancelar y descartar\n"
        "• O escribe sugerencias directamente y el sistema las aplicará\n\n"
        "/notas — Ver notas pendientes",
        parse_mode="Markdown"
    )


async def cmd_notas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista las notas activas en el sistema."""
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return

    db = SessionLocal()
    try:
        estados_activos = [
            EstadoNota.ESPERANDO_ADMIN,
            EstadoNota.EN_REDACCION,
            EstadoNota.EN_REVISION_JUEZ,
            EstadoNota.ESPERANDO_APROBACION_FINAL
        ]
        notas = db.query(Nota).filter(Nota.estado.in_(estados_activos)).all()

        if not notas:
            await update.message.reply_text("✅ No hay notas pendientes en este momento.")
            return

        texto = "📋 *Notas en proceso:*\n\n"
        for nota in notas:
            texto += f"• ID `{nota.id}` — {nota.asunto_original[:50]}\n  Estado: `{nota.estado.value}`\n\n"

        await update.message.reply_text(texto, parse_mode="Markdown")
    finally:
        db.close()


async def manejar_comando_dinamico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja comandos dinámicos como /si_5, /no_5, /publicar_5, /cancelar_5.
    """
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return

    texto = update.message.text.strip()

    # /si_ID
    if texto.startswith("/si_"):
        nota_id = _extraer_id(texto, "/si_")
        await _aprobar_nota(update, nota_id)

    # /no_ID
    elif texto.startswith("/no_"):
        nota_id = _extraer_id(texto, "/no_")
        await _descartar_nota(update, nota_id)

    # /publicar_ID
    elif texto.startswith("/publicar_"):
        nota_id = _extraer_id(texto, "/publicar_")
        await _publicar_nota(update, context, nota_id)

    # /cancelar_ID
    elif texto.startswith("/cancelar_"):
        nota_id = _extraer_id(texto, "/cancelar_")
        await _cancelar_nota(update, nota_id)


async def manejar_texto_libre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Maneja mensajes de texto libre del admin como sugerencias de corrección.
    Si hay una nota esperando aprobación final, interpreta el texto como feedback.
    """
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return

    texto = update.message.text.strip()

    db = SessionLocal()
    try:
        # Buscar nota en estado de aprobación final (la más reciente)
        nota = db.query(Nota).filter(
            Nota.estado == EstadoNota.ESPERANDO_APROBACION_FINAL
        ).order_by(Nota.fecha_actualizacion.desc()).first()

        if not nota:
            await update.message.reply_text(
                "ℹ️ No hay notas esperando revisión en este momento.\n"
                "Usa /ayuda para ver los comandos disponibles."
            )
            return

        # Guardar feedback del admin y devolver la nota al ciclo de redacción.
        # El job programado 'procesar_notas_en_redaccion' (cada 5 min en main.py)
        # se encargará del reintento completo Escritor → Juez, incluyendo
        # los reintentos automáticos hasta MAX_REWRITE_ATTEMPTS.
        nota.feedback_admin = texto
        nota.feedback_juez = texto  # Se usa como instrucción directa para el escritor
        nota.estado = EstadoNota.EN_REDACCION
        db.commit()

        await update.message.reply_text(
            f"📝 Sugerencias recibidas para la nota ID `{nota.id}`.\n"
            "El escritor aplicará los cambios y te enviaré la versión corregida "
            "en unos minutos.",
            parse_mode="Markdown"
        )

    finally:
        db.close()


# ============================================================
# Funciones de acción
# ============================================================

async def _aprobar_nota(update: Update, nota_id: int):
    if nota_id is None:
        await update.message.reply_text("❌ ID de nota inválido.")
        return

    db = SessionLocal()
    try:
        nota = db.get(Nota, nota_id)
        if not nota:
            await update.message.reply_text(f"❌ No existe la nota ID {nota_id}.")
            return
        if nota.estado != EstadoNota.ESPERANDO_ADMIN:
            await update.message.reply_text(f"⚠️ La nota {nota_id} no está esperando aprobación.")
            return

        nota.estado = EstadoNota.EN_REDACCION
        db.commit()

        await update.message.reply_text(
            f"✅ Nota ID `{nota_id}` aprobada.\n"
            "El escritor está redactando la nota, te avisaré cuando esté lista. ⏳",
            parse_mode="Markdown"
        )
    finally:
        db.close()


async def _descartar_nota(update: Update, nota_id: int):
    if nota_id is None:
        await update.message.reply_text("❌ ID de nota inválido.")
        return

    db = SessionLocal()
    try:
        nota = db.get(Nota, nota_id)
        if not nota:
            await update.message.reply_text(f"❌ No existe la nota ID {nota_id}.")
            return

        nota.estado = EstadoNota.DESCARTADA_MANUAL
        db.commit()
        await update.message.reply_text(f"🗑️ Nota ID `{nota_id}` descartada.", parse_mode="Markdown")
    finally:
        db.close()


async def _publicar_nota(update: Update, context: ContextTypes.DEFAULT_TYPE, nota_id: int):
    if nota_id is None:
        await update.message.reply_text("❌ ID de nota inválido.")
        return

    db = SessionLocal()
    try:
        nota = db.get(Nota, nota_id)
        if not nota or nota.estado != EstadoNota.ESPERANDO_APROBACION_FINAL:
            await update.message.reply_text(
                f"⚠️ La nota ID {nota_id} no está lista para publicar."
            )
            return

        await update.message.reply_text(
            f"🚀 Publicando nota ID `{nota_id}` en WordPress...",
            parse_mode="Markdown"
        )

        # Disparar publicación en background
        context.application.create_task(
            _ejecutar_publicacion(nota_id, update, context)
        )
    finally:
        db.close()


async def _cancelar_nota(update: Update, nota_id: int):
    if nota_id is None:
        await update.message.reply_text("❌ ID de nota inválido.")
        return

    db = SessionLocal()
    try:
        nota = db.get(Nota, nota_id)
        if not nota:
            await update.message.reply_text(f"❌ No existe la nota ID {nota_id}.")
            return

        nota.estado = EstadoNota.CANCELADA
        db.commit()
        await update.message.reply_text(
            f"❌ Nota ID `{nota_id}` cancelada. El sistema continuará con otras notas.",
            parse_mode="Markdown"
        )
    finally:
        db.close()


# ============================================================
# Tasks en background (se llaman desde el orquestador también)
# ============================================================

async def _ciclo_correccion(nota_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Ejecuta un ciclo de corrección escritor→juez y notifica al admin."""
    from agents.escritor_juez import AgenteEscritor, AgenteJuez
    from utils.generador_txt import generar_txt
    import json

    db = SessionLocal()
    try:
        nota = db.get(Nota, nota_id)
        if not nota:
            return

        nota_dict = json.loads(nota.nota_actual) if nota.nota_actual else {}
        escritor = AgenteEscritor()
        juez = AgenteJuez()

        # Escritor corrige con el feedback del admin
        nota_corregida = escritor.corregir(
            nota.texto_original,
            nota_dict,
            nota.feedback_admin or ""
        )

        # Juez evalúa la corrección
        evaluacion = juez.evaluar(
            nota.texto_original,
            nota_corregida,
            feedback_admin=nota.feedback_admin or ""
        )

        if evaluacion.get("aprobada"):
            nota.nota_actual = json.dumps(nota_corregida, ensure_ascii=False)
            nota.titulo_nota = nota_corregida.get("titulo", "")
            nota.meta_descripcion = nota_corregida.get("meta_descripcion", "")
            nota.estado = EstadoNota.ESPERANDO_APROBACION_FINAL
            nota.feedback_admin = None
            db.commit()

            # Generar .txt y enviar al admin
            ruta_txt = generar_txt(nota_corregida, nota_id)
            await _enviar_nota_para_aprobacion(nota_id, ruta_txt, context)
        else:
            nota.feedback_juez = evaluacion.get("instrucciones_escritor", "")
            db.commit()
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"⚠️ La corrección de la nota ID `{nota_id}` aún no cumple los estándares.\n"
                    f"Intentando nuevamente...\n\n"
                    f"Problemas: {', '.join(evaluacion.get('problemas', []))}"
                ),
                parse_mode="Markdown"
            )
    finally:
        db.close()


async def _ejecutar_publicacion(nota_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publica la nota en WordPress y notifica al admin."""
    from agents.publicador import AgentPublicador
    import json

    db = SessionLocal()
    try:
        nota = db.get(Nota, nota_id)
        if not nota:
            return

        nota_dict = json.loads(nota.nota_actual) if nota.nota_actual else {}
        imagenes = json.loads(nota.imagenes_urls) if nota.imagenes_urls else []

        publicador = AgentPublicador()
        resultado = publicador.publicar(nota_dict, imagenes)

        if resultado:
            nota.estado = EstadoNota.PUBLICADA
            nota.wp_post_id = resultado["post_id"]
            nota.wp_url = resultado["url"]
            db.commit()

            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"✅ *Nota publicada exitosamente*\n\n"
                    f"🆔 Post ID: `{resultado['post_id']}`\n"
                    f"🔗 URL: {resultado['url']}"
                ),
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"❌ *Error al publicar la nota ID {nota_id}*\n"
                    "Revisa los logs del servidor para más detalles."
                ),
                parse_mode="Markdown"
            )
    finally:
        db.close()


async def _enviar_nota_para_aprobacion(nota_id: int, ruta_txt: str, context):
    """Envía el archivo .txt de la nota al admin para aprobación final."""
    with open(ruta_txt, "rb") as f:
        await context.bot.send_document(
            chat_id=ADMIN_CHAT_ID,
            document=f,
            filename=f"nota_{nota_id}.txt",
            caption=(
                f"📄 *Nota {nota_id} redactada y aprobada por el juez*\n\n"
                f"Para publicar: `/publicar_{nota_id}`\n"
                f"Para cancelar: `/cancelar_{nota_id}`\n"
                f"Para sugerir cambios: escribe tu feedback directamente aquí."
            ),
            parse_mode="Markdown"
        )


# ============================================================
# Helpers
# ============================================================

def _extraer_id(texto: str, prefijo: str) -> int | None:
    try:
        return int(texto.replace(prefijo, "").strip())
    except ValueError:
        return None


def construir_app() -> Application:
    """Construye y configura la aplicación de Telegram."""
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ayuda", cmd_ayuda))
    app.add_handler(CommandHandler("notas", cmd_notas))

    # Comandos dinámicos /si_, /no_, /publicar_, /cancelar_
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"^/(si|no|publicar|cancelar)_\d+"),
        manejar_comando_dinamico
    ))

    # Texto libre = sugerencias del admin
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        manejar_texto_libre
    ))

    return app