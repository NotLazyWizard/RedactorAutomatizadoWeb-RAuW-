"""
Generador de archivo .txt con la nota redactada para enviar al admin.
Simple y liviano, sin consumo extra de tokens.
"""

import os
from datetime import datetime


def generar_txt(nota: dict, nota_id: int) -> str:
    """
    Genera un archivo .txt con la nota redactada y lo guarda en /app/data/notas/.
    Retorna la ruta del archivo generado.
    """
    directorio = "/app/data/notas"
    os.makedirs(directorio, exist_ok=True)

    nombre_archivo = f"nota_{nota_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    ruta = os.path.join(directorio, nombre_archivo)

    tags = nota.get("tags", [])
    tags_str = ", ".join(tags) if tags else "Sin tags"

    contenido = f"""
================================================================================
NOTA GIKINX - ID: {nota_id}
Generada: {datetime.now().strftime('%d/%m/%Y %H:%M')}
================================================================================

TÍTULO (SEO):
{nota.get('titulo', 'Sin título')}

META DESCRIPCIÓN:
{nota.get('meta_descripcion', 'Sin meta descripción')}

CATEGORÍA:
{nota.get('categoria', 'Sin categoría')}

TAGS:
{tags_str}

YOUTUBE URL:
{nota.get('youtube_url') or 'No incluida'}

ENLACE EXTERNO:
{nota.get('enlace_externo') or 'No incluido'}

--------------------------------------------------------------------------------
CUERPO DE LA NOTA:
--------------------------------------------------------------------------------

{nota.get('cuerpo', 'Sin contenido')}

================================================================================
FIN DE LA NOTA
================================================================================
""".strip()

    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)

    return ruta
