import requests
from bs4 import BeautifulSoup
import re
import base64
import json
import time
import urllib3
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from Crypto.Cipher import AES
from urllib.parse import urljoin

# --- Configuración ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
BASE_URL = "https://pelisplushd.bz"
PELIS_URL_TEMPLATE = BASE_URL + "/peliculas?page={}"
HEADERS = {"User-Agent": "Mozilla/5.0"}
# Clave secreta (mantenida del primer script)
SECRET_KEY = "Ak7qrvvH4WKYxV2OgaeHAEg2a5eh16vE"
session = requests.Session()
session.headers.update(HEADERS)
ARCHIVO_JSON = "peliculas_con_reproductores.json"
MAX_WORKERS = 5
# Para actualizar, solo revisamos las primeras páginas (donde salen los estrenos)
MAX_PAGINAS_BUSQUEDA = 30 

# --- Decodificador Universal (AES y JWT) ---
# Función mejorada del PRIMER script (necesaria para desencriptar los enlaces nuevos)
def decrypt_link(encrypted_b64: str, secret_key: str) -> str:
    # 1. Detección y Decodificación de JWT (Nuevo método)
    if encrypted_b64.startswith("eyJ") and "." in encrypted_b64:
        try:
            parts = encrypted_b64.split('.')
            if len(parts) == 3:
                payload_b64 = parts[1]
                
                # Añadir padding necesario para Base64 si falta
                padding = 4 - len(payload_b64) % 4
                if padding != 4:
                    payload_b64 += '=' * padding
                
                # Decodificar Base64 URL-safe
                decoded_bytes = base64.urlsafe_b64decode(payload_b64)
                decoded_str = decoded_bytes.decode('utf-8')
                
                # Parsear el JSON dentro del JWT
                data = json.loads(decoded_str)
                
                # Extraer el enlace
                if 'link' in data:
                    return data['link']
        except Exception:
            pass

    # 2. Método Antiguo AES (Fallback por si acaso)
    try:
        data = base64.b64decode(encrypted_b64)
        iv = data[:16]
        ciphertext = data[16:]
        key = secret_key.encode("utf-8")
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(ciphertext)
        pad_len = decrypted[-1]
        decrypted = decrypted[:-pad_len]
        return decrypted.decode("utf-8")
    except Exception:
        return "Error: No se pudo descifrar el enlace"

# --- Funciones de Carga ---
def cargar_datos_existentes():
    """
    Carga el JSON existente y retorna la lista y un set de URLs para búsquedas rápidas.
    """
    if not os.path.exists(ARCHIVO_JSON):
        return [], set()
    
    try:
        with open(ARCHIVO_JSON, "r", encoding="utf-8") as f:
            peliculas = json.load(f)
        # Crear un set de URLs para verificar instantáneamente si existe
        urls = {peli["url"] for peli in peliculas}
        return peliculas, urls
    except (json.JSONDecodeError, FileNotFoundError):
        return [], set()

# --- Scrap funciones (Basadas en el primer script) ---
def obtener_urls_peliculas_pagina(num_pagina):
    print(f"🔍 Verificando página {num_pagina}...")
    url = PELIS_URL_TEMPLATE.format(num_pagina)
    try:
        r = session.get(url, verify=False, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"  ❌ Error obteniendo página {num_pagina}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    urls_peliculas = []
    for a in soup.select("a.Posters-link"):
        enlace = a.get("href")
        if enlace and not enlace.startswith("http"):
            enlace = urljoin(BASE_URL, enlace)
        urls_peliculas.append(enlace)
    
    return urls_peliculas

def obtener_iframe_pelicula(html: str):
    soup = BeautifulSoup(html, "html.parser")
    iframe = soup.find("iframe")
    if iframe:
        src = iframe.get("src")
        if src and not src.startswith("http"):
            src = urljoin(BASE_URL, src)
        return src
    return None

def extraer_dataLink(html: str):
    # Regex flexible para buscar dataLink (sea let, const o var)
    scripts = re.findall(r"(?:const|let|var)?\s*dataLink\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not scripts:
        return []
    
    try:
        data = json.loads(scripts[0])
    except json.JSONDecodeError:
        return []
        
    resultados = []
    for entry in data:
        idioma = entry.get("video_language")
        for embed in entry.get("sortedEmbeds", []):
            servidor = embed.get("servername")
            tipo = embed.get("type")
            link_cifrado = embed.get("link")
            
            # Usamos la función mejorada que soporta JWT y AES
            url = decrypt_link(link_cifrado, SECRET_KEY)
            
            resultados.append({
                "idioma": idioma,
                "servidor": servidor,
                "tipo": tipo,
                "url": url
            })
    return resultados

def extraer_detalles_pelicula(html: str):
    """
    Extrae detalles. Usa la lógica robusta del primer script.
    """
    soup = BeautifulSoup(html, "html.parser")
    detalles = {}
    
    h1 = soup.select_one("h1.m-b-5")
    if h1:
        detalles["titulo"] = h1.get_text(strip=True)
    
    if "titulo" in detalles:
        match = re.search(r'\((\d{4})\)', detalles["titulo"])
        if match:
            detalles["anio"] = match.group(1)
    
    # Poster
    poster_img = soup.select_one(".col-sm-3 img.img-fluid")
    if poster_img:
        poster_url = poster_img.get("src")
        if poster_url:
            if not poster_url.startswith("http"):
                poster_url = urljoin(BASE_URL, poster_url)
            detalles["poster"] = poster_url
    if "poster" not in detalles:
        meta_image = soup.select_one("meta[property='og:image']")
        if meta_image:
            detalles["poster"] = meta_image.get("content")

    # Sinopsis
    sinopsis_div = soup.select_one(".text-large")
    if sinopsis_div:
        detalles["sinopsis"] = sinopsis_div.get_text(strip=True)
    
    # País
    pais_div = soup.find("div", class_="sectionDetail", string=re.compile(r"Pais:"))
    if pais_div:
        paises = [link.get_text(strip=True) for link in pais_div.find_all("a")]
        detalles["pais"] = ", ".join(paises)
    
    # Géneros
    generos_container = soup.find("div", class_="p-v-20 p-h-15 text-center")
    if generos_container:
        generos = [link.get_text(strip=True) for link in generos_container.find_all("a", title=re.compile(r"Películas del Genero:"))]
        if generos:
            detalles["genero"] = generos
            
    return detalles

def procesar_pelicula(url_pelicula):
    try:
        r = session.get(url_pelicula, verify=False, timeout=15)
        r.raise_for_status()
        
        detalles = extraer_detalles_pelicula(r.text)
        pelicula = {"url": url_pelicula}
        pelicula.update(detalles)
        
        iframe_url = obtener_iframe_pelicula(r.text)
        if not iframe_url:
            pelicula["reproductores"] = []
            return pelicula
        
        # Obtener el contenido del iframe
        try:
            r_iframe = session.get(iframe_url, verify=False, timeout=15)
            r_iframe.raise_for_status()
            reproductores = extraer_dataLink(r_iframe.text)
        except Exception:
            reproductores = []
            
        pelicula["reproductores"] = reproductores
        return pelicula
    except Exception as e:
        return {
            "url": url_pelicula,
            "titulo": f"ERROR: {str(e)}",
            "reproductores": []
        }

# --- Main ---
def main():
    print(f"🚀 Iniciando actualización de base de datos...")
    
    # 1. Cargar datos existentes
    peliculas_existentes, urls_existentes = cargar_datos_existentes()
    print(f"📁 Archivo cargado: {len(peliculas_existentes)} películas registradas.")
    
    nuevas_peliculas_encontradas = []
    detener = False
    
    # 2. Recorrer páginas desde la 1 hacia adelante (orden de estrenos)
    for pagina in range(1, MAX_PAGINAS_BUSQUEDA + 1):
        if detener:
            break
            
        urls_pagina = obtener_urls_peliculas_pagina(pagina)
        if not urls_pagina:
            continue
            
        # 3. Separar nuevas de existentes
        urls_nuevas = []
        todas_son_existentes = True
        
        for url in urls_pagina:
            if url in urls_existentes:
                # Ya existe, omitimos
                continue
            else:
                # Encontramos una que no tenemos
                todas_son_existentes = False
                urls_nuevas.append(url)
        
        # 4. Lógica de parada inteligente:
        # Si en la página actual TODAS las películas ya las tenemos en el JSON,
        # significa que hemos alcanzado el punto donde nos quedamos la última vez.
        # No tiene sentido seguir a la página 2 si la 1 ya está completa.
        if todas_son_existentes and urls_pagina:
            print(f"  🛑 La página {pagina} ya está completa en tu base de datos. Búsqueda finalizada.")
            detener = True
            continue
            
        # 5. Procesar las nuevas
        if urls_nuevas:
            print(f"  ⚡ Procesando {len(urls_nuevas)} películas nuevas...")
            
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_url = {executor.submit(procesar_pelicula, url): url for url in urls_nuevas}
                
                for future in as_completed(future_to_url):
                    url = future_to_url[future]
                    try:
                        peli = future.result()
                        nuevas_peliculas_encontradas.append(peli)
                        # Agregamos la URL al set en memoria por si aparece duplicada en otra página durante esta ejecución
                        urls_existentes.add(url) 
                        print(f"    ✅ Nuevo: {peli.get('titulo', url)}")
                    except Exception as e:
                        print(f"    ❌ Error procesando {url}: {e}")
        
        time.sleep(0.5) # Pequeña pausa para no saturar
        
    # 6. Guardar resultados (Nuevas al principio)
    if nuevas_peliculas_encontradas:
        # Poner las nuevas primero y luego las antiguas
        lista_final = nuevas_peliculas_encontradas + peliculas_existentes
        
        print(f"💾 Guardando {len(lista_final)} películas en {ARCHIVO_JSON}...")
        with open(ARCHIVO_JSON, "w", encoding="utf-8") as f:
            json.dump(lista_final, f, indent=4, ensure_ascii=False)
        print(f"✅ Actualización completada. Se agregaron {len(nuevas_peliculas_encontradas)} películas nuevas.")
    else:
        print("ℹ️ No se encontraron películas nuevas. Tu base de datos está al día.")

if __name__ == "__main__":
    main()
