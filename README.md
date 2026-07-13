#  Guía de Instalación y Despliegue 

## Requisitos previos en la Raspberry Pi

- Docker y Docker Compose instalados
- Puerto 8080 disponible
- Acceso a internet

---

## Paso 1: Obtener tu Chat ID de Telegram

Antes de configurar el bot necesitas saber tu Chat ID personal.

1. Busca en Telegram el bot **@userinfobot**
2. Escribe `/start`
3. Te responderá con tu ID numérico (ej: `123456789`)
4. Guarda ese número, lo necesitarás en el `.env`

---

## Paso 2: Configurar el archivo .env

```bash
cp .env.example .env
nano .env
```

Rellena los siguientes valores:

```env
EMAIL_PASSWORD=contraseña.
GROQ_API_KEY=tu_nueva_clave_gsk_...
TELEGRAM_BOT_TOKEN=tu_nuevo_token_del_bot
TELEGRAM_ADMIN_CHAT_ID=tu_chat_id_numerico
WP_USER=tu_usuario_wordpress
WP_APP_PASSWORD=la_contraseña_de_aplicacion_wp
```

---

## Paso 3: Configurar el Port Forwarding en tu modem

El bot de Telegram usa polling (no webhook), por lo que **NO necesitas** exponer puertos para Telegram.

El puerto 8080 es solo para el health check interno de Docker. No necesitas abrirlo al exterior.

---

## Paso 4: Construir y lanzar el contenedor

```bash
# En la Raspberry Pi, ve al directorio del proyecto
cd /ruta/donde/copiaste/el/proyecto

# Construir la imagen
docker compose build

# Lanzar en segundo plano
docker compose up -d

# Ver logs en tiempo real
docker compose logs -f
```

---

## Paso 5: Verificar que funciona

```bash
# Ver estado del contenedor
docker compose ps

# Ver los últimos logs
docker compose logs --tail=50

# Verificar health check
curl http://localhost:8080/health
```

---

## Paso 6: Obtener la Contraseña de Aplicación de WordPress

1. Entra al panel de WordPress de gikinx.mx
2. Ve a **Usuarios → Tu perfil**
3. Baja hasta **"Contraseñas de aplicación"**
4. En el campo "Nombre de la nueva contraseña de aplicación" escribe: `GikinxBot`
5. Clic en **"Añadir nueva contraseña de aplicación"**
6. Copia la contraseña generada (con los espacios está bien)
7. Pégala en `WP_APP_PASSWORD` del `.env`
8. Reinicia el contenedor: `docker compose restart`

---

## Comandos útiles de mantenimiento

```bash
# Reiniciar el bot
docker compose restart

# Detener el bot
docker compose down

# Ver logs del día de hoy
docker compose logs --since="2024-01-01T00:00:00" --tail=100

# Entrar al contenedor para inspeccionar
docker compose exec gikinx-bot bash

# Ver la base de datos SQLite
docker compose exec gikinx-bot python -c "
from config.database import SessionLocal, Nota
db = SessionLocal()
for n in db.query(Nota).all():
    print(n.id, n.asunto_original[:50], n.estado)
"

# Limpiar notas antiguas publicadas (opcional)
docker compose exec gikinx-bot python -c "
from config.database import SessionLocal, Nota, EstadoNota
from datetime import datetime, timedelta
db = SessionLocal()
hace_30_dias = datetime.utcnow() - timedelta(days=30)
db.query(Nota).filter(
    Nota.estado == EstadoNota.PUBLICADA,
    Nota.fecha_creacion < hace_30_dias
).delete()
db.commit()
print('Limpieza completada')
"
```

---

## Estructura de archivos del proyecto

```
gikinx-bot/
├── main.py                    # Orquestador principal
├── docker-compose.yml         # Configuración Docker
├── Dockerfile
├── requirements.txt
├── .env.example               # Plantilla de variables
├── .env                       # TUS credenciales (no subir a Git)
├── agents/
│   ├── buscador.py            # Agente lector de correos IMAP
│   ├── escritor_juez.py       # Agentes escritor y juez (LLM)
│   └── publicador.py          # Agente publicador en WordPress
├── config/
│   ├── database.py            # Modelos SQLite y BD
│   └── prompts.py             # Prompts para los 3 agentes LLM
├── utils/
│   ├── llm_client.py          # Cliente Groq centralizado
│   ├── telegram_bot.py        # Bot de Telegram y handlers
│   └── generador_txt.py       # Generador de archivos .txt
├── data/                      # Datos persistentes (BD, imágenes, notas)
│   ├── gikinx.db              # Base de datos SQLite
│   ├── notas/                 # Archivos .txt generados
│   └── imagenes/              # Imágenes descargadas de correos
└── logs/
    └── gikinx.log             # Logs del sistema
```

---

## Flujo de trabajo resumido

```
Cada 30 min → Lee correos nuevos (por Message-ID, no por flag leído)
           → LLM clasifica si es geek
           → Telegram admin: "¿Subir nota?" /si_ID /no_ID

/si_ID     → Escritor (Groq) redacta la nota
           → Juez (Groq) evalúa (hasta 3 intentos automáticos)
           → Telegram admin: envía nota.txt para revisión

Admin aprueba (/publicar_ID)
           → Publica en WordPress vía REST API
           → Crea/busca tags dinámicamente
           → Sube imágenes del correo si las hay
           → 1 de cada 4 notas se marca como "Noticia destacada"
           → Telegram admin: "✅ Publicada: URL"

Admin sugiere cambios (texto libre)
           → Juez recibe feedback y le da instrucciones al escritor
           → Vuelve al ciclo de revisión
```

---

## Solución de problemas frecuentes

**El bot no responde en Telegram:**
- Verifica que `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ADMIN_CHAT_ID` están correctos en el `.env`
- Revisa los logs: `docker compose logs --tail=30`

**Error de conexión IMAP:**
- Verifica que el puerto 993 de `imap.hostinger.com` no está bloqueado
- Confirma la contraseña del correo

**Error publicando en WordPress:**
- Verifica que la URL `https://gikinx.mx/wp-json/wp/v2/posts` responde con JSON
- Verifica que la contraseña de aplicación de WP es correcta
- Asegúrate de que el usuario WP tiene permisos de Autor o superior

**Groq API error:**
- Verifica que la API key es válida en console.groq.com
- El modelo por defecto es `llama-3.1-70b-versatile`
"# RedactorAutomatizadoWeb-RAuW-" 
