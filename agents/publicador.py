"""
Agente Publicador: Publica la nota aprobada en WordPress vía REST API.
Maneja: creación del post, tags dinámicos, categorías, imágenes e imagen destacada.
"""

import json
import logging
import os
import requests
from requests.auth import HTTPBasicAuth
from typing import Optional

logger = logging.getLogger(__name__)

WP_URL = os.getenv("WP_URL", "https://gikinx.mx/wp-json/wp/v2")
WP_USER = os.getenv("WP_USER", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
FEATURED_NEWS_RATIO = int(os.getenv("FEATURED_NEWS_RATIO", "4"))


class AgentPublicador:

    def __init__(self):
        self.auth = HTTPBasicAuth(WP_USER, WP_APP_PASSWORD)
        self.headers = {"Content-Type": "application/json"}
        self._contador_notas = self._cargar_contador()

    def _cargar_contador(self) -> int:
        """Carga el contador de notas publicadas para calcular las destacadas."""
        try:
            with open("/app/data/contador_notas.txt", "r") as f:
                return int(f.read().strip())
        except Exception:
            return 0

    def _guardar_contador(self):
        with open("/app/data/contador_notas.txt", "w") as f:
            f.write(str(self._contador_notas))

    def _es_nota_destacada(self) -> bool:
        """Aproximadamente 1 de cada 4 notas será destacada."""
        self._contador_notas += 1
        self._guardar_contador()
        return self._contador_notas % FEATURED_NEWS_RATIO == 0

    def _obtener_o_crear_tag(self, nombre: str) -> Optional[int]:
        """Busca un tag por nombre; lo crea si no existe. Retorna su ID."""
        try:
            # Buscar tag existente
            resp = requests.get(
                f"{WP_URL}/tags",
                params={"search": nombre, "per_page": 5},
                auth=self.auth,
                timeout=10
            )
            if resp.status_code == 200:
                tags = resp.json()
                for tag in tags:
                    if tag["name"].lower() == nombre.lower():
                        return tag["id"]

            # Crear tag nuevo
            resp = requests.post(
                f"{WP_URL}/tags",
                json={"name": nombre},
                auth=self.auth,
                headers=self.headers,
                timeout=10
            )
            if resp.status_code == 201:
                return resp.json()["id"]

        except Exception as e:
            logger.error(f"Error con tag '{nombre}': {e}")

        return None

    def _obtener_categoria(self, nombre: str) -> Optional[int]:
        """Busca una categoría existente por nombre. Retorna su ID."""
        try:
            resp = requests.get(
                f"{WP_URL}/categories",
                params={"search": nombre, "per_page": 10},
                auth=self.auth,
                timeout=10
            )
            if resp.status_code == 200:
                cats = resp.json()
                for cat in cats:
                    if cat["name"].lower() == nombre.lower():
                        return cat["id"]
                # Si no encontró exactamente, usar la primera que aparezca
                if cats:
                    return cats[0]["id"]
        except Exception as e:
            logger.error(f"Error buscando categoría '{nombre}': {e}")
        return None

    def _subir_imagen(self, imagen_data: bytes, nombre: str, tipo: str) -> Optional[int]:
        """Sube una imagen a la biblioteca de WordPress. Retorna su ID."""
        try:
            resp = requests.post(
                f"{WP_URL}/media",
                headers={
                    "Content-Disposition": f'attachment; filename="{nombre}"',
                    "Content-Type": tipo,
                },
                data=imagen_data,
                auth=self.auth,
                timeout=30
            )
            if resp.status_code == 201:
                media_id = resp.json()["id"]
                logger.info(f"Imagen subida con ID: {media_id}")
                return media_id
            else:
                logger.error(f"Error subiendo imagen: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Error subiendo imagen '{nombre}': {e}")
        return None

    def _preparar_cuerpo_html(self, nota: dict) -> str:
        """
        Convierte el markdown del cuerpo a HTML básico para WordPress.
        También inserta el enlace de YouTube y el enlace interno.
        """
        cuerpo = nota.get("cuerpo", "")

        # Convertir subtítulos ### a H3
        lineas = cuerpo.split("\n")
        html_partes = []
        for linea in lineas:
            linea = linea.strip()
            if not linea:
                continue
            if linea.startswith("### "):
                html_partes.append(f"<h3>{linea[4:]}</h3>")
            elif linea.startswith("## "):
                html_partes.append(f"<h2>{linea[3:]}</h2>")
            else:
                html_partes.append(f"<p>{linea}</p>")

        cuerpo_html = "\n".join(html_partes)

        # Insertar enlace de YouTube si existe
        youtube_url = nota.get("youtube_url")
        if youtube_url:
            cuerpo_html += f'\n<p><a href="{youtube_url}" target="_blank" rel="noopener">Ver video relacionado</a></p>'

        # Insertar enlace externo si existe
        enlace_externo = nota.get("enlace_externo")
        if enlace_externo:
            cuerpo_html += f'\n<p><a href="{enlace_externo}" target="_blank" rel="noopener nofollow">Fuente</a></p>'

        # Enlace interno hacia el sitio (sección de videojuegos como default)
        cuerpo_html += '\n<p>Visita <a href="https://gikinx.mx/videojuegos/">nuestra sección de videojuegos</a> para más noticias.</p>'

        return cuerpo_html

    def publicar(self, nota: dict, imagenes: list = None) -> Optional[dict]:
        """
        Publica la nota en WordPress.
        Retorna dict con post_id y url si fue exitoso, None si falló.
        """
        logger.info(f"Publicador: iniciando publicación de '{nota.get('titulo', '')}'")

        # 1. Preparar tags
        tags_ids = []
        tags_lista = nota.get("tags", [])

        # Asegurar mínimo 3 tags
        if len(tags_lista) < 3:
            logger.warning("Menos de 3 tags, se usarán los disponibles")

        for tag_nombre in tags_lista[:10]:  # Máximo 10 tags
            tag_id = self._obtener_o_crear_tag(tag_nombre)
            if tag_id:
                tags_ids.append(tag_id)

        # Tag de "Noticia destacada" si corresponde
        es_destacada = self._es_nota_destacada()
        if es_destacada:
            tag_destacada = self._obtener_o_crear_tag("Noticia destacada")
            if tag_destacada:
                tags_ids.append(tag_destacada)
            logger.info("Esta nota será marcada como DESTACADA")

        # 2. Obtener categoría
        categoria_ids = []
        categoria_nombre = nota.get("categoria", "Videojuegos")
        cat_id = self._obtener_categoria(categoria_nombre)
        if cat_id:
            categoria_ids.append(cat_id)

        # 3. Subir imágenes del correo (si hay)
        imagen_destacada_id = None
        if imagenes:
            primera_imagen = imagenes[0]
            imagen_destacada_id = self._subir_imagen(
                primera_imagen["datos"],
                primera_imagen["nombre"],
                primera_imagen.get("tipo", "image/jpeg")
            )

        # 4. Preparar cuerpo HTML
        cuerpo_html = self._preparar_cuerpo_html(nota)

        # 5. Construir payload del post
        payload = {
            "title": nota.get("titulo", "Sin título"),
            "content": cuerpo_html,
            "excerpt": nota.get("meta_descripcion", ""),
            "status": "publish",
            "categories": categoria_ids,
            "tags": tags_ids,
        }

        if imagen_destacada_id:
            payload["featured_media"] = imagen_destacada_id

        # 6. Crear el post
        try:
            resp = requests.post(
                f"{WP_URL}/posts",
                json=payload,
                auth=self.auth,
                headers=self.headers,
                timeout=30
            )

            if resp.status_code == 201:
                post = resp.json()
                post_id = post["id"]
                post_url = post["link"]
                logger.info(f"Nota publicada con éxito: ID={post_id}, URL={post_url}")
                return {"post_id": post_id, "url": post_url}
            else:
                logger.error(f"Error publicando: {resp.status_code} {resp.text[:500]}")
                return None

        except Exception as e:
            logger.error(f"Excepción publicando post: {e}")
            return None
