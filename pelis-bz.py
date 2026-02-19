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
SECRET_KEY = "Ak7qrvvH4WKYxV2OgaeHAEg2a5eh16vE"
session = requests.Session()
session.headers.update(HEADERS)
ARCHIVO_JSON = "peliculas_con_reproductores.json"
MAX_WORKERS = 5

# --- Decodificador Universal (AES y JWT) ---
def decrypt_link(encrypted_b64: str, secret_key: str) -> str:
    # 1. Detección y Decodificación de JWT (Nuevo método)
    # Los JWT empiezan con 'eyJ' y tienen 3 partes separadas por puntos
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
        except Exception as e:
            # Si falla el JWT, pasamos al método antiguo o devolvemos error
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

# --- Función para cargar películas existentes ---
def cargar_peliculas_existentes():
    if not os.path.exists(ARCHIVO_JSON):
        return {}
    try:
        with open(ARCHIVO_JSON, "r", encoding="utf-8") as f:
            peliculas_existentes = json.load(f)
        return {peli["url"]: peli for peli in peliculas_existentes}
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

# --- Scrap funciones ---
def obtener_urls_peliculas_pagina(num_pagina):
    print(f"🔍 Obteniendo URLs de películas de la página {num_pagina}...")
    url = PELIS_URL_TEMPLATE.format(num_pagina)
    try:
        r = session.get(url, verify=False, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"  ❌ Error al obtener la página {num_pagina}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    urls_peliculas = []
    for a in soup.select("a.Posters-link"):
        enlace = a.get("href")
        if enlace and not enlace.startswith("http"):
            enlace = urljoin(BASE_URL, enlace)
        urls_peliculas.append(enlace)
    
    print(f"  → Encontradas {len(urls_peliculas)} películas en la página {num_pagina}.")
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
            
            # Usamos la nueva función decrypt_link que maneja JWT y AES
            url = decrypt_link(link_cifrado, SECRET_KEY)
            
            resultados.append({
                "idioma": idioma,
                "servidor": servidor,
                "tipo": tipo,
                "url": url
            })
    return resultados

def extraer_detalles_pelicula(html: str):
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
        
        # Obtener el contenido del iframe (embed69)
        r_iframe = session.get(iframe_url, verify=False, timeout=15)
        r_iframe.raise_for_status()
        
        # Extraer y desencriptar enlaces
        reproductores = extraer_dataLink(r_iframe.text)
        pelicula["reproductores"] = reproductores
        
        return pelicula
    except Exception as e:
        return {
            "url": url_pelicula,
            "titulo": f"ERROR: {str(e)}",
            "reproductores": []
        }

def guardar_en_json(data, archivo=ARCHIVO_JSON):
    with open(archivo, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# --- Main ---
def main(num_paginas=1):
    print(f"🚀 Iniciando scraping de {num_paginas} página(s) con {MAX_WORKERS} hilos...")
    
    peliculas_existentes_dict = cargar_peliculas_existentes()
    print(f"📁 Cargadas {len(peliculas_existentes_dict)} películas existentes.")
    
    # Inicializamos la lista final con las existentes para mantener el orden
    # Nota: Esto podría duplicar si el archivo ya tenía las páginas 1-610 y vuelves a ejecutar desde 1.
    # Para evitar duplicados en este script, trabajaremos con un diccionario en memoria 
    # y guardaremos sus valores al final.
    
    for pagina in range(1, num_paginas + 1):
        print(f"\n📄 Procesando página {pagina}/{num_paginas}...")
        
        urls_peliculas = obtener_urls_peliculas_pagina(pagina)
        if not urls_peliculas:
            continue

        # Lista para guardar resultados de esta página en orden
        peliculas_pagina_actual = []
        urls_nuevas = []
        
        for url_pelicula in urls_peliculas:
            if url_pelicula in peliculas_existentes_dict:
                peliculas_pagina_actual.append(peliculas_existentes_dict[url_pelicula])
            else:
                urls_nuevas.append(url_pelicula)
                # Marcamos placeholder
                peliculas_pagina_actual.append(None)
        
        print(f"  → {len(urls_nuevas)} películas nuevas para procesar")
        
        if urls_nuevas:
            print(f"  ⚡ Procesando en paralelo...")
            
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                future_to_url = {executor.submit(procesar_pelicula, url): url for url in urls_nuevas}
                
                for future in as_completed(future_to_url):
                    url = future_to_url[future]
                    try:
                        pelicula = future.result()
                        # Actualizar memoria
                        peliculas_existentes_dict[url] = pelicula
                        
                        # Colocar en la lista ordenada
                        if url in urls_peliculas:
                            idx = urls_peliculas.index(url)
                            peliculas_pagina_actual[idx] = pelicula
                        
                        print(f"    ✅ {pelicula.get('titulo', url)}")
                    except Exception as e:
                        print(f"    ❌ Error en {url}: {e}")
        
        # Guardar el estado actual del diccionario completo en el JSON
        # Esto asegura que si se corta, no se pierde nada de lo ya procesado
        guardar_en_json(list(peliculas_existentes_dict.values()))
        
        # Pequeña pausa
        if pagina < num_paginas:
            time.sleep(0.5)
    
    print(f"\n✅ Proceso completado! Total películas en archivo: {len(peliculas_existentes_dict)}")

if __name__ == "__main__":
    # Ajusta el número de páginas
    main(num_paginas=632)
