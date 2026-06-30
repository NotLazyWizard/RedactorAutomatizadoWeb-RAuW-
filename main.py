"""
Orquestador principal del sistema Gikinx Bot.
Coordina: Buscador → Escritor → Juez → Telegram → Publicador.
Corre un scheduler cada 30 minutos y el bot de Telegram en paralelo.
"""

import asyncio
import json
import logging
import os
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

# Configurar logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("/app/logs/gikinx.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("orquestador")

# Importar módulos del sistema
from config.database import init_db, SessionLocal, CorreoProcesado, Nota, EstadoNota
from agents.buscador import LectorCorreo
from agents.escritor_juez import AgenteEscritor, AgenteJuez
from agents.publicador import AgentPublicador
from utils.llm_client import llamar_llm_json
from utils.generador_txt import generar_txt
from utils.telegram_bot import construir_app, ADMIN_CHAT_ID, _enviar_nota_para_aprobacion
from config.prompts import PROMPT_DETECTOR

# Variable global para el bot de Telegram
telegram_app = None


# ============================================================
# CICLO PRINCIPAL: Revisión de correos cada 30 minutos
# ============================================================

async def revisar_correos():
    """
    Tarea programada cada 30 minutos.
    Lee correos nuevos, detecta noticias geek y notifica al admin.
    """
    logger.info("=== Iniciando revisión de correos ===")

    lector = LectorCorreo()
    correos = lector.obtener_correos_recientes(dias=7)

    if not correos:
        logger.info("No se encontraron correos en la bandeja.")
        return

    db = SessionLocal()
    try:
        nuevos = 0
        for correo in correos:
            message_id = correo["message_id"]

            # ESTRATEGIA DE NO-LEÍDOS: verificar en BD local por Message-ID
            ya_procesado = db.query(CorreoProcesado).filter(
                CorreoProcesado.message_id == message_id
            ).first()

            if ya_procesado:
                continue  # Ya fue procesado anteriormente

            # Aislamos cada correo en su propio try/except: si uno falla,
            # los demás correos de este ciclo se siguen procesando con normalidad.
            try:
                nuevos += 1
                logger.info(f"Correo nuevo detectado: {correo['asunto'][:60]}")

                # Pequeña pausa entre clasificaciones para no saturar el rate limit de Groq (30 RPM en free tier)
                if nuevos > 1:
                    await asyncio.sleep(8)

                # Clasificar con LLM
                es_geek, datos_deteccion = await clasificar_correo(correo)

                if es_geek is None:
                    # Fallo de API/JSON: NO registramos como procesado, se reintenta en el próximo ciclo
                    logger.warning(f"Correo pendiente de reintento: {correo['asunto'][:60]}")
                    continue

                # Si ya existe una Nota con este message_id (de un intento previo donde
                # el registro en CorreoProcesado se perdió o fue borrado manualmente),
                # no la duplicamos: solo nos aseguramos de que quede marcada como procesada.
                nota_existente = db.query(Nota).filter(
                    Nota.message_id == message_id
                ).first()

                if nota_existente:
                    logger.info(
                        f"Ya existe una nota para este correo (ID {nota_existente.id}), "
                        f"se omite duplicado: {correo['asunto'][:60]}"
                    )
                    if not db.query(CorreoProcesado).filter(
                        CorreoProcesado.message_id == message_id
                    ).first():
                        db.add(CorreoProcesado(
                            message_id=message_id,
                            asunto=correo["asunto"][:500],
                            remitente=correo["remitente"][:200],
                            fecha_correo=correo["fecha"],
                            es_geek=es_geek,
                        ))
                        db.commit()
                    continue

                # Registrar en BD como procesado (independiente del resultado)
                registro = CorreoProcesado(
                    message_id=message_id,
                    asunto=correo["asunto"][:500],
                    remitente=correo["remitente"][:200],
                    fecha_correo=correo["fecha"],
                    es_geek=es_geek,
                )
                db.add(registro)
                db.commit()

                if not es_geek:
                    logger.info(f"Correo descartado (no es geek): {correo['asunto'][:60]}")
                    continue

                # Crear nota en BD
                nota = Nota(
                    message_id=message_id,
                    asunto_original=correo["asunto"][:500],
                    texto_original=correo["cuerpo"],
                    imagenes_urls=json.dumps(
                        [{"nombre": img["nombre"], "tipo": img.get("tipo", "image/jpeg")}
                         for img in correo.get("imagenes", [])],
                        ensure_ascii=False
                    ),
                    resumen_admin=datos_deteccion.get("resumen", "")[:500],
                    titulo_detectado=datos_deteccion.get("titulo_detectado", correo["asunto"])[:300],
                    youtube_url=correo["youtube_urls"][0] if correo.get("youtube_urls") else None,
                    enlace_externo=correo["urls_externas"][0] if correo.get("urls_externas") else None,
                    estado=EstadoNota.ESPERANDO_ADMIN
                )
                db.add(nota)
                db.commit()
                db.refresh(nota)

                # Guardar datos binarios de imágenes por separado
                if correo.get("imagenes"):
                    await guardar_imagenes_correo(nota.id, correo["imagenes"])

                # Notificar al admin por Telegram
                await notificar_nota_detectada(nota, datos_deteccion)

            except Exception as e:
                # Cualquier error con ESTE correo específico no debe tumbar el resto del ciclo
                db.rollback()
                logger.error(
                    f"Error procesando correo '{correo['asunto'][:60]}': {e}",
                    exc_info=True
                )
                continue

        logger.info(f"Revisión completada. Correos nuevos procesados: {nuevos}")
    except Exception as e:
        logger.error(f"Error en revisión de correos: {e}", exc_info=True)
    finally:
        db.close()


async def clasificar_correo(correo: dict) -> tuple[bool, dict]:
    """Usa el LLM detector para clasificar si el correo es geek o no."""
    prompt = PROMPT_DETECTOR.format(
        asunto=correo["asunto"],
        cuerpo=correo["cuerpo"][:3000]  # Limitar para no gastar tokens
    )

    resultado = llamar_llm_json(prompt, max_tokens=800)

    if not resultado:
        # Fallo de API o JSON irreparable: NO se puede confirmar que no sea geek.
        # Señalamos error explícito para que el correo se reintente en el siguiente ciclo
        # en lugar de marcarlo como "no geek" para siempre.
        logger.warning(
            f"Detector no pudo clasificar '{correo['asunto'][:50]}' "
            f"(fallo de API o JSON inválido). Se reintentará en el próximo ciclo."
        )
        return None, {}

    es_geek = resultado.get("es_geek", False)
    confianza = resultado.get("confianza", 0)

    # Solo consideramos geek si la confianza es >= 6
    if es_geek and confianza < 6:
        logger.info(f"Correo clasificado como geek pero con baja confianza ({confianza}/10), descartando")
        es_geek = False

    return es_geek, resultado


async def guardar_imagenes_correo(nota_id: int, imagenes: list):
    """Guarda los archivos de imagen del correo en disco."""
    directorio = f"/app/data/imagenes/{nota_id}"
    os.makedirs(directorio, exist_ok=True)

    for i, img in enumerate(imagenes):
        nombre = img.get("nombre", f"imagen_{i}.jpg")
        ruta = os.path.join(directorio, nombre)
        try:
            with open(ruta, "wb") as f:
                f.write(img["datos"])
            logger.info(f"Imagen guardada: {ruta}")
        except Exception as e:
            logger.error(f"Error guardando imagen {nombre}: {e}")


async def notificar_nota_detectada(nota: Nota, datos: dict):
    """Envía la notificación de nota detectada al admin por Telegram."""
    global telegram_app

    resumen = datos.get("resumen", "Sin resumen disponible")
    titulo = datos.get("titulo_detectado", nota.asunto_original)[:100]

    mensaje = (
        f"🎮 *Nota detectada*\n\n"
        f"📰 *{titulo}*\n\n"
        f"📝 _{resumen}_\n\n"
        f"¿Deseas subir la nota?\n"
        f"✅ `/si_{nota.id}`\n"
        f"❌ `/no_{nota.id}`"
    )

    try:
        await telegram_app.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=mensaje,
            parse_mode="Markdown"
        )
        logger.info(f"Notificación enviada al admin para nota ID {nota.id}")
    except Exception as e:
        logger.error(f"Error enviando notificación Telegram: {e}")


# ============================================================
# CICLO DE REDACCIÓN: Procesa notas aprobadas por el admin
# ============================================================

async def procesar_notas_en_redaccion():
    """
    Tarea secundaria que revisa si hay notas en estado EN_REDACCION
    y ejecuta el ciclo escritor → juez.
    """
    db = SessionLocal()
    try:
        notas = db.query(Nota).filter(
            Nota.estado == EstadoNota.EN_REDACCION
        ).all()

        for i, nota in enumerate(notas):
            logger.info(f"Procesando nota ID {nota.id} en redacción...")
            if i > 0:
                await asyncio.sleep(10)  # Pausa entre notas para no saturar el rate limit
            await ejecutar_ciclo_escritura(nota.id)

    finally:
        db.close()


async def ejecutar_ciclo_escritura(nota_id: int):
    """
    Ejecuta el ciclo completo: Escritor → Juez → notifica al admin.
    Máximo MAX_REWRITE_ATTEMPTS intentos automáticos.
    """
    MAX_INTENTOS = int(os.getenv("MAX_REWRITE_ATTEMPTS", "3"))

    db = SessionLocal()
    try:
        nota = db.get(Nota, nota_id)
        if not nota:
            return

        escritor = AgenteEscritor()
        juez = AgenteJuez()

        # Primera redacción o continuar desde donde estaba
        if not nota.nota_actual:
            nota_dict = escritor.redactar(nota.texto_original)
        else:
            nota_dict = json.loads(nota.nota_actual)
            if nota.feedback_juez:
                nota_dict = escritor.corregir(
                    nota.texto_original, nota_dict, nota.feedback_juez
                )

        if not nota_dict:
            logger.error(f"Escritor no pudo generar nota para ID {nota_id}")
            return

        nota.intento_escritura += 1
        nota.nota_actual = json.dumps(nota_dict, ensure_ascii=False)
        nota.titulo_nota = nota_dict.get("titulo", "")
        nota.meta_descripcion = nota_dict.get("meta_descripcion", "")
        nota.tags_sugeridos = json.dumps(nota_dict.get("tags", []), ensure_ascii=False)
        nota.categoria = nota_dict.get("categoria", "Videojuegos")

        # Actualizar URLs si el escritor las detectó
        if nota_dict.get("youtube_url") and not nota.youtube_url:
            nota.youtube_url = nota_dict["youtube_url"]
        if nota_dict.get("enlace_externo") and not nota.enlace_externo:
            nota.enlace_externo = nota_dict["enlace_externo"]

        nota.estado = EstadoNota.EN_REVISION_JUEZ
        db.commit()

        # Pausa para evitar choque de rate limit entre la llamada del Escritor y el Juez
        await asyncio.sleep(5)

        # Evaluar con el juez
        evaluacion = juez.evaluar(nota.texto_original, nota_dict)

        if evaluacion.get("aprobada"):
            logger.info(f"Nota ID {nota_id} aprobada por el juez (puntaje: {evaluacion.get('puntaje')})")
            nota.estado = EstadoNota.ESPERANDO_APROBACION_FINAL
            nota.feedback_juez = None
            db.commit()

            # Generar .txt
            ruta_txt = generar_txt(nota_dict, nota_id)

            # Enviar al admin para aprobación final
            await _enviar_nota_para_aprobacion(nota_id, ruta_txt, telegram_app)

        else:
            logger.info(
                f"Nota ID {nota_id} rechazada por el juez "
                f"(intento {nota.intento_escritura}/{MAX_INTENTOS}). "
                f"Problemas: {evaluacion.get('problemas', [])}"
            )

            if nota.intento_escritura >= MAX_INTENTOS:
                # Notificar al admin que no se pudo completar automáticamente
                await telegram_app.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=(
                        f"⚠️ *Nota ID {nota_id} necesita revisión manual*\n\n"
                        f"Después de {MAX_INTENTOS} intentos, el juez no aprobó la nota.\n\n"
                        f"Problemas detectados:\n"
                        + "\n".join(f"• {p}" for p in evaluacion.get("problemas", []))
                        + f"\n\n¿Deseas cancelarla? `/cancelar_{nota_id}`"
                    ),
                    parse_mode="Markdown"
                )
                nota.estado = EstadoNota.ESPERANDO_APROBACION_FINAL  # Enviar de todas formas para revisión
                db.commit()
                ruta_txt = generar_txt(nota_dict, nota_id)
                await _enviar_nota_para_aprobacion(nota_id, ruta_txt, telegram_app)
            else:
                # Reintentar
                nota.feedback_juez = evaluacion.get("instrucciones_escritor", "")
                nota.estado = EstadoNota.EN_REDACCION
                db.commit()
                # Llamar recursivamente en el próximo ciclo (el scheduler lo recogerá)

    except Exception as e:
        logger.error(f"Error en ciclo de escritura para nota {nota_id}: {e}", exc_info=True)
    finally:
        db.close()


# ============================================================
# SERVIDOR DE SALUD (health check para Docker)
# ============================================================

async def health_server():
    """Servidor HTTP mínimo para el health check de Docker y triggers manuales."""
    from aiohttp import web

    async def health(request):
        return web.Response(text="OK")

    async def revisar_ahora(request):
        """
        Endpoint manual para forzar una revisión de correos sin esperar
        el ciclo de 30 minutos. Útil para pruebas:
        curl -X POST http://localhost:8080/revisar-ahora
        """
        logger.info(">>> Revisión manual disparada vía /revisar-ahora <<<")
        asyncio.create_task(revisar_correos())
        return web.Response(text="Revisión de correos disparada. Revisa los logs.")

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/revisar-ahora", revisar_ahora)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Health check server en puerto 8080 (endpoint manual: POST /revisar-ahora)")


# ============================================================
# PUNTO DE ENTRADA PRINCIPAL
# ============================================================

async def main():
    global telegram_app

    logger.info("🚀 Iniciando sistema Gikinx Bot...")

    # Inicializar base de datos
    init_db()
    logger.info("Base de datos inicializada")

    # Construir app de Telegram
    telegram_app = construir_app()
    await telegram_app.initialize()
    await telegram_app.start()
    logger.info("Bot de Telegram iniciado")

    # Iniciar servidor de salud
    await health_server()

    # Configurar scheduler
    scheduler = AsyncIOScheduler()

    # Tarea principal: revisar correos cada 30 minutos
    scheduler.add_job(
        revisar_correos,
        "interval",
        minutes=int(os.getenv("CHECK_INTERVAL_MINUTES", "30")),
        id="revisar_correos",
        next_run_time=datetime.now()  # Ejecutar inmediatamente al arrancar
    )

    # Tarea secundaria: procesar notas en redacción cada 5 minutos
    scheduler.add_job(
        procesar_notas_en_redaccion,
        "interval",
        minutes=5,
        id="procesar_redacciones"
    )

    scheduler.start()
    logger.info("Scheduler iniciado. Revisión de correos cada 30 minutos.")

    # Iniciar polling de Telegram
    await telegram_app.updater.start_polling(
        allowed_updates=["message"],
        drop_pending_updates=True
    )

    logger.info("✅ Sistema Gikinx Bot completamente operativo")

    # Mantener el loop corriendo indefinidamente
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Cerrando sistema...")
    finally:
        scheduler.shutdown()
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
