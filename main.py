import flet as ft
import flet_video as ftv
import json
import os
import urllib.request
import re
import time
import random
import base64

# --- NUEVAS IMPORTACIONES PARA EL EXTRACTOR ---
import requests
from bs4 import BeautifulSoup
import urllib3

# Desactivar advertencias de SSL para el extractor
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURACIÓN ---
ITEMS_PER_PAGE = 24
POSTER_DIR = "posters"

# ==========================================
# LÓGICA DE EXTRACCIÓN DE ENLACES (VOE)
# ==========================================

# List of common user agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

def get_browser_headers(url=None):
    """Generate realistic browser headers with optional referer based on URL"""
    parsed_url = urllib.parse.urlparse(url) if url else None
    referer = f"{parsed_url.scheme}://{parsed_url.netloc}/" if parsed_url else ""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none" if not referer else "same-origin",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "DNT": "1",
        "Priority": "u=1"
    }
    if referer:
        headers["Referer"] = referer
    return headers

def _rot13(text: str) -> str:
    """Apply ROT13 cipher (letters only)."""
    out = []
    for ch in text:
        o = ord(ch)
        if 65 <= o <= 90:
            out.append(chr(((o - 65 + 13) % 26) + 65))
        elif 97 <= o <= 122:
            out.append(chr(((o - 97 + 13) % 26) + 97))
        else:
            out.append(ch)
    return ''.join(out)

def _replace_patterns(txt: str) -> str:
    """Strip marker substrings used as obfuscation separators."""
    for pat in ['@$', '^^', '~@', '%?', '*~', '!!', '#&']:
        txt = txt.replace(pat, '')
    return txt

def _shift_chars(text: str, shift: int) -> str:
    """Shift character code-points by *-shift* (decode)."""
    return ''.join(chr(ord(c) - shift) for c in text)

def _safe_b64_decode(s: str) -> str:
    """Base64 decode with safe padding and utf-8 fallback."""
    pad = len(s) % 4
    if pad:
        s += '=' * (4 - pad)
    return base64.b64decode(s).decode('utf-8', errors='replace')

def deobfuscate_embedded_json(raw_json: str):
    """Return a dict or str extracted from the obfuscated JSON array found in <script type="application/json">."""
    try:
        arr = json.loads(raw_json)
        if not (isinstance(arr, list) and arr and isinstance(arr[0], str)):
            return None
        obf = arr[0]
    except json.JSONDecodeError:
        return None
    try:
        step1 = _rot13(obf)
        step2 = _replace_patterns(step1)
        step3 = _safe_b64_decode(step2)
        step4 = _shift_chars(step3, 3)
        step5 = step4[::-1]
        step6 = _safe_b64_decode(step5)
        try:
            return json.loads(step6)
        except json.JSONDecodeError:
            return step6
    except Exception:
        return None

def is_bait_source(source: str) -> bool:
    """Return True if *source* looks like a known test/bait video."""
    bait_filenames = ["BigBuckBunny", "Big_Buck_Bunny_1080_10s_5MB", "bbb.mp4"]
    bait_domains = ["test-videos.co.uk", "sample-videos.com", "commondatastorage.googleapis.com"]
    if any(fn.lower() in source.lower() for fn in bait_filenames):
        return True
    parsed = urllib.parse.urlparse(source)
    if any(dom in parsed.netloc for dom in bait_domains):
        return True
    return False

def clean_base64(s):
    try:
        s = s.replace('\\', '')
        missing_padding = len(s) % 4
        if missing_padding:
            s += '=' * (4 - missing_padding)
        base64.b64decode(s, validate=True)
        return s
    except (base64.binascii.Error, ValueError) as e:
        print(f"[!] Invalid base64 string: {e}")
        return None

def extract_link_voe(URL):
    """Extract direct link from VOE video URL without downloading"""
    URL = str(URL)
    time.sleep(random.uniform(1, 3))
    
    session = requests.Session()
    headers = get_browser_headers(URL)
    try:
        html_page = session.get(URL, headers=headers, timeout=30, verify=False)
        html_page.raise_for_status()
        
        if html_page.status_code == 403 or "captcha" in html_page.text.lower():
            print(f"[!] Access denied or captcha detected for {URL}. Trying with different headers...")
            time.sleep(random.uniform(3, 5))
            headers = get_browser_headers(URL)
            headers["User-Agent"] = random.choice(USER_AGENTS)
            html_page = session.get(URL, headers=headers, timeout=30, verify=False)
            html_page.raise_for_status()
        
        soup = BeautifulSoup(html_page.content, 'html.parser')
        
        redirect_patterns = [
            "window.location.href = '", "window.location = '", "location.href = '",
            "window.location.replace('", "window.location.assign('", "window.location=\"", "window.location.href=\""
        ]
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string:
                for pattern in redirect_patterns:
                    if pattern in script.string:
                        L = len(pattern)
                        i0 = script.string.find(pattern)
                        closing_quote = "'" if pattern.endswith("'") else "\""
                        i1 = script.string.find(closing_quote, i0 + L)
                        if i1 > i0:
                            url = script.string[i0 + L:i1]
                            print(f"[*] Detected redirect to: {url}")
                            return extract_link_voe(url)
        
        source_json = None
        
        sources_find = soup.find_all(string=re.compile("var sources"))
        if sources_find:
            sources_find = str(sources_find)
            try:
                slice_start = sources_find.index("var sources")
                source = sources_find[slice_start:]
                slice_end = source.index(";")
                source = source[:slice_end]
                source = source.replace("var sources = ", "").replace("\'", "\"").replace("\\n", "").replace("\\", "")
                if not is_bait_source(source):
                    strToReplace = ","
                    replacementStr = ""
                    source = replacementStr.join(source.rsplit(strToReplace, 1))
                    source_json = json.loads(source)
                    print("[+] Found sources using var sources pattern")
            except (ValueError, json.JSONDecodeError): pass
        
        if not source_json:
            scripts = soup.find_all("script")
            for script in scripts:
                if not script.string: continue
                patterns = ["var sources", "sources =", "sources:", "\"sources\":", "'sources':"]
                for pattern in patterns:
                    if pattern in script.string:
                        try:
                            script_text = script.string
                            start_idx = script_text.find(pattern)
                            if start_idx == -1: continue
                            brace_idx = script_text.find("{", start_idx)
                            if brace_idx == -1: continue
                            brace_count = 1
                            end_idx = brace_idx + 1
                            while brace_count > 0 and end_idx < len(script_text):
                                if script_text[end_idx] == "{": brace_count += 1
                                elif script_text[end_idx] == "}": brace_count -= 1
                                end_idx += 1
                            if brace_count == 0:
                                json_str = script_text[brace_idx:end_idx].replace("'", "\"")
                                source_json = json.loads(json_str)
                                print(f"[+] Found sources using pattern: {pattern}")
                                break
                        except Exception: pass
                        if source_json: break
        
        if not source_json:
            video_tags = soup.find_all("video")
            for video in video_tags:
                src = video.get("src")
                if src and not is_bait_source(src):
                    source_json = {"mp4": src}
                    break
                source_tags = video.find_all("source")
                for source_tag in source_tags:
                    src = source_tag.get("src")
                    if src and not is_bait_source(src):
                        type_attr = source_tag.get("type", "")
                        if "mp4" in type_attr: source_json = {"mp4": src}
                        elif "m3u8" in type_attr or "hls" in type_attr: source_json = {"hls": src}
                        else: source_json = {"mp4": src}
                        print(f"[+] Found video source from source tag: {src}")
                        break
                if source_json: break
        
        if not source_json:
            m3u8_pattern = r'(https?://[^"\']+\.m3u8[^"\'\s]*)'
            m3u8_matches = re.findall(m3u8_pattern, html_page.text)
            if m3u8_matches and not is_bait_source(m3u8_matches[0]):
                source_json = {"hls": m3u8_matches[0]}
            if not source_json:
                mp4_pattern = r'(https?://[^"\']+\.mp4[^"\'\s]*)'
                mp4_matches = re.findall(mp4_pattern, html_page.text)
                if mp4_matches and not is_bait_source(mp4_matches[0]):
                    source_json = {"mp4": mp4_matches[0]}
        
        if not source_json:
            base64_pattern = r'base64[,:]([A-Za-z0-9+/=]+)'
            base64_matches = re.findall(base64_pattern, html_page.text)
            for match in base64_matches:
                try:
                    decoded = base64.b64decode(match).decode('utf-8')
                    if '.mp4' in decoded: source_json = {"mp4": decoded}; break
                    elif '.m3u8' in decoded: source_json = {"hls": decoded}; break
                except: continue
        
        if not source_json:
            a168c_script_pattern = r"a168c\s*=\s*'([^']+)'"
            match = re.search(a168c_script_pattern, html_page.text, re.DOTALL)
            if match:
                raw_base64 = match.group(1)
                try:
                    cleaned = clean_base64(raw_base64)
                    if cleaned:
                        decoded = base64.b64decode(cleaned).decode('utf-8')[::-1]
                        try:
                            parsed = json.loads(decoded)
                            if 'direct_access_url' in parsed: source_json = {"mp4": parsed['direct_access_url']}
                            elif 'source' in parsed: source_json = {"hls": parsed['source']}
                        except json.JSONDecodeError:
                            mp4_match = re.search(r'(https?://[^\s"]+\.mp4[^\s"]*)', decoded)
                            m3u8_match = re.search(r'(https?://[^\s"]+\.m3u8[^\s"]*)', decoded)
                            if mp4_match: source_json = {"mp4": mp4_match.group(1)}
                            elif m3u8_match: source_json = {"hls": m3u8_match.group(1)}
                except: pass

        if not source_json:
            MKGMa_pattern = r'MKGMa="(.*?)"'
            match = re.search(MKGMa_pattern, html_page.text, re.DOTALL)
            if match:
                raw_MKGMa = match.group(1)
                def rot13_decode(s: str) -> str:
                    return ''.join(chr((ord(c) - ord('A') + 13) % 26 + ord('A')) if 'A' <= c <= 'Z' else 
                                   chr((ord(c) - ord('a') + 13) % 26 + ord('a')) if 'a' <= c <= 'z' else c for c in s)
                def shift_characters(s: str, offset: int) -> str:
                    return ''.join(chr(ord(c) - offset) for c in s)
                try:
                    step1 = rot13_decode(raw_MKGMa).replace('_', '')
                    step3 = base64.b64decode(step1).decode('utf-8')
                    step4 = shift_characters(step3, 3)
                    decoded = base64.b64decode(step4[::-1]).decode('utf-8')
                    parsed_json = json.loads(decoded)
                    if 'direct_access_url' in parsed_json: source_json = {"mp4": parsed_json['direct_access_url']}
                    elif 'source' in parsed_json: source_json = {"hls": parsed_json['source']}
                except: pass

        if not source_json:
            app_json_scripts = soup.find_all("script", attrs={"type": "application/json"})
            for js in app_json_scripts:
                if not js.string: continue
                result = deobfuscate_embedded_json(js.string.strip())
                if result:
                    try:
                        if isinstance(result, dict):
                            if 'direct_access_url' in result: source_json = {"mp4": result['direct_access_url']}
                            elif 'source' in result: source_json = {"hls": result['source']}
                            elif any(k in result for k in ("mp4", "hls")): source_json = result
                        elif isinstance(result, str):
                            mp4_m = re.search(r'(https?://[^\s"]+\.mp4[^\s"]*)', result)
                            m3u8_m = re.search(r'(https?://[^\s"]+\.m3u8[^\s"]*)', result)
                            if mp4_m: source_json = {"mp4": mp4_m.group(0)}
                            elif m3u8_m: source_json = {"hls": m3u8_m.group(0)}
                    except: pass
                    if source_json: break

        if not source_json:
            iframes = soup.find_all("iframe")
            for iframe in iframes:
                iframe_src = iframe.get("src")
                if iframe_src:
                    if iframe_src.startswith("//"): iframe_src = "https:" + iframe_src
                    elif not iframe_src.startswith(("http://", "https://")):
                        parsed_url = urllib.parse.urlparse(URL)
                        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                        iframe_src = base_url + iframe_src if iframe_src.startswith("/") else base_url + "/" + iframe_src
                    print(f"[*] Found iframe, following to: {iframe_src}")
                    return extract_link_voe(iframe_src)

        if not source_json: return None

        if isinstance(source_json, str): source_json = {"mp4": source_json}
        if not isinstance(source_json, dict): return None

        if "mp4" in source_json:
            link = source_json["mp4"]
            if isinstance(link, str) and (link.startswith("eyJ") or re.match(r'^[A-Za-z0-9+/=]+$', link)):
                try: link = base64.b64decode(link).decode("utf-8")
                except: pass
            if link.startswith("//"): link = "https:" + link
            return link
        elif "hls" in source_json:
            link = source_json["hls"]
            if isinstance(link, str) and (link.startswith("eyJ") or re.match(r'^[A-Za-z0-9+/=]+$', link)):
                try: link = base64.b64decode(link).decode("utf-8")
                except: pass
            if link.startswith("//"): link = "https:" + link
            return link
        return None
    except Exception as e:
        print(f"[!] Unexpected error: {e}")
        return None

# ==========================================
# APLICACIÓN FLET
# ==========================================

class MovieApp:
    def __init__(self, page: ft.Page):
        self.page = page
        if not os.path.exists(POSTER_DIR): os.makedirs(POSTER_DIR)
        self.page_num = 1
        self.total_pages = 1
        self.current_movie_detail = None
        self.current_filter = "Todas"
        self.search_text = ""
        self.filter_chips = [] 
        
        file_name = "peliculas_con_reproductores.json"
        self.movies = []
        if os.path.exists(file_name):
            try:
                with open(file_name, 'r', encoding='utf-8') as f: self.movies = json.load(f)
                print(f"Datos cargados: {len(self.movies)} películas.")
            except Exception as e: print(f"Error JSON: {e}")
        
        self.page.scroll = ft.ScrollMode.AUTO
        self.page.theme = ft.Theme(
            scrollbar_theme=ft.ScrollbarTheme(
                thumb_color=ft.Colors.WHITE, track_color="#1a1a1a",
                thickness=15, radius=10, cross_axis_margin=2
            )
        )
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.bgcolor = "#000000"
        self.page.title = "FletStream Pro"
        self.page.padding = 0
        self.page.window.maximized = True 

        self.movies_grid = ft.Row(wrap=True, spacing=10, run_spacing=10, expand=True)
        self.grid_container = ft.Container(content=self.movies_grid, alignment="center", expand=True)
        self.pagination_controls = ft.Row(spacing=10, alignment="center")
        self.show_home()

    def _download_and_replace(self, url, final_path, container_widget):
        temp_path = final_path + ".tmp"
        try:
            opener = urllib.request.build_opener()
            opener.addheaders = [('User-agent', 'Mozilla/5.0')]
            urllib.request.install_opener(opener)
            urllib.request.urlretrieve(url, temp_path)
            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                if os.path.exists(final_path): os.remove(final_path)
                os.rename(temp_path, final_path)
                if container_widget.page is None: return
                container_widget.content = ft.Image(src=final_path, width=160, height=240, fit="cover", border_radius=ft.border_radius.all(8))
                try: container_widget.update(); self.page.update()
                except: pass
            else:
                if os.path.exists(temp_path): os.remove(temp_path)
        except Exception as e: print(f"Error descarga: {e}")

    def create_movie_card(self, movie):
        card_width = 160
        poster_url = movie.get("poster", "")
        safe_title = re.sub(r'[\\/*?:"<>|]', "", movie["titulo"])
        filename = f"{safe_title}.jpg"
        final_path = os.path.join(POSTER_DIR, filename)
        content_container = ft.Container(width=card_width, height=240, border_radius=ft.border_radius.all(8))

        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            content_container.content = ft.Image(src=final_path, width=card_width, height=240, fit="cover", border_radius=ft.border_radius.all(8))
        else:
            content_container.content = ft.ProgressRing(width=25, height=25, stroke_width=3, color=ft.Colors.WHITE)
            if poster_url: self.page.run_thread(self._download_and_replace, poster_url, final_path, content_container)
            else: content_container.content = ft.Icon(ft.Icons.BROKEN_IMAGE, color="#555", size=30)

        return ft.GestureDetector(content=content_container, on_tap=lambda e: self.open_details(movie))

    def get_unique_genres(self):
        genres = set()
        for m in self.movies:
            for g in m.get("genero", []): genres.add(g)
        return sorted(list(genres))

    def filter_movies(self):
        filtered = []
        search_lower = self.search_text.lower()
        for m in self.movies:
            matches_search = search_lower in m["titulo"].lower()
            matches_genre = self.current_filter == "Todas" or self.current_filter in m.get("genero", [])
            if matches_search and matches_genre: filtered.append(m)
        return filtered

    def update_grid_and_pagination(self):
        filtered = self.filter_movies()
        total_items = len(filtered)
        self.total_pages = (total_items // ITEMS_PER_PAGE) + (1 if total_items % ITEMS_PER_PAGE > 0 else 0)
        if self.page_num > self.total_pages: self.page_num = self.total_pages
        if self.page_num < 1: self.page_num = 1
        start_idx = (self.page_num - 1) * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        page_movies = filtered[start_idx:end_idx]
        self.movies_grid.controls.clear()
        if not page_movies: self.movies_grid.controls.append(ft.Text("No hay películas.", color="grey", size=16))
        else: 
            for m in page_movies: self.movies_grid.controls.append(self.create_movie_card(m))
        self.pagination_controls.controls.clear()
        prev_btn = ft.IconButton(icon=ft.Icons.CHEVRON_LEFT, on_click=self.prev_page, disabled=self.page_num == 1)
        page_text = ft.Text(f"Pág {self.page_num} / {self.total_pages}", color="white")
        next_btn = ft.IconButton(icon=ft.Icons.CHEVRON_RIGHT, on_click=self.next_page, disabled=self.page_num == self.total_pages)
        self.pagination_controls.controls.extend([prev_btn, page_text, next_btn])
        self.page.update()

    def show_home(self):
        self.page.clean()
        self.search_field = ft.TextField(hint_text="Buscar título...", border_color="#E50914", color="white", bgcolor="#141414", expand=True, height=40, text_size=14, on_change=self.on_search_change)
        app_bar = ft.Container(content=ft.Row([ft.Text("FletStream", size=20, weight="bold", color="#E50914"), ft.Container(width=10), self.search_field], alignment="spaceBetween"), padding=10, bgcolor="#141414")
        
        genres = ["Todas"] + self.get_unique_genres()
        self.filter_chips = []
        for g in genres:
            chip = ft.Chip(label=ft.Text(g, size=12), selected_color="#E50914", check_color="white", bgcolor="#141414", selected=self.current_filter == g, on_click=lambda e, genre=g: self.on_genre_click(genre))
            self.filter_chips.append(chip)

        filters_list = ft.ListView(controls=self.filter_chips, horizontal=True, spacing=5, padding=ft.Padding(left=10, right=10, top=10, bottom=10), height=50)
        self.update_grid_and_pagination() 
        grid_container = ft.Container(content=self.movies_grid, padding=ft.Padding(left=10, right=10, top=0, bottom=0), expand=True)
        pagination_container = ft.Container(content=self.pagination_controls, padding=ft.Padding(left=10, right=10, top=10, bottom=10), bgcolor="#141414")

        main_column = ft.Column([app_bar, filters_list, ft.Divider(height=1, color="transparent"), grid_container, pagination_container], expand=True, alignment="center")
        self.page.add(main_column)

    def show_details(self, movie):
        self.page.clean()
        self.current_movie_detail = movie
        poster_url = movie.get("poster", "")
        safe_title = re.sub(r'[\\/*?:"<>|]', "", movie["titulo"])
        local_path = os.path.join(POSTER_DIR, f"{safe_title}.jpg")
        final_src = local_path if (os.path.exists(local_path) and os.path.getsize(local_path) > 0) else (poster_url if poster_url else "https://via.placeholder.com/200x300")

        # --- FILTRAR SOLO SERVIDORES VOE ---
        players = [p for p in movie.get("reproductores", []) if p.get("servidor", "").lower() == "voe"]

        back_btn = ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color="white", icon_size=30, on_click=lambda e: self.show_home())
        top_bar = ft.Container(content=back_btn, padding=10)
        poster_container = ft.Container(content=ft.Image(src=final_src, width=200, height=300, fit="cover", border_radius=ft.border_radius.all(12)), margin=ft.margin.only(top=20))
        title_text = ft.Text(movie["titulo"], size=24, weight="bold", color="white", text_align="center")
        year_text = ft.Text(movie["anio"], size=16, color="#E50914", text_align="center")
        genres_row = ft.Row([ft.Chip(label=ft.Text(g, size=11), bgcolor="#333", selected_color="#E50914") for g in movie.get("genero", [])], wrap=True, spacing=5, alignment="center")
        synopsis_text = ft.Container(content=ft.Text(movie.get("sinopsis", "Sin descripción."), size=14, color="grey", text_align="justify"), padding=ft.padding.symmetric(horizontal=20))
        servers_title = ft.Text("Servidores (VOE):", size=16, weight="bold", color="white", margin=ft.margin.only(left=20, top=20))
        servers_row = ft.Row(wrap=True, spacing=10, alignment="center")

        if players:
            for p in players:
                idioma = p.get("idioma", "UNK")
                servidor = p.get("servidor", "Server")
                btn = ft.ElevatedButton(
                    content=ft.Column([ft.Text(f"{idioma}", size=12, weight="bold", color="white"), ft.Text(f"{servidor}", size=10, color="grey")], tight=True, horizontal_alignment="center"),
                    bgcolor="#333", style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)), width=80, height=50,
                    on_click=lambda e, p=p: self.open_player_with_server(movie, p)
                )
                servers_row.controls.append(btn)
        else:
            servers_row.controls.append(ft.Text("No hay servidores VOE disponibles.", color="grey"))

        main_column = ft.Column([top_bar, poster_container, ft.Container(padding=10), ft.Column([title_text, year_text, genres_row], spacing=5, horizontal_alignment="center"), ft.Divider(height=20, color="transparent"), synopsis_text, servers_title, servers_row, ft.Container(height=50)], scroll="auto", expand=True, alignment="center")
        self.page.add(main_column)

    # --- CORRECCIÓN AQUÍ ---
    def open_player_with_server(self, movie, player_data):
        if player_data.get("servidor", "").lower() != "voe":
            self.show_details(movie)
            return
        self._show_loading_ui(movie["titulo"])
        voe_url = player_data.get("url", "")
        # CORRECCIÓN: Pasar argumentos directamente separados por coma, no como args=(...)
        self.page.run_thread(self._worker_extract_and_play, movie, voe_url)

    def _show_loading_ui(self, movie_title):
        self.page.clean()
        self.page.add(
            ft.Column([
                ft.ProgressRing(color="#E50914", width=50, height=50),
                ft.Text("Extrayendo enlace directo...", color="white", size=20),
                ft.Text("Por favor espere unos segundos.", color="grey", size=14),
                ft.Text(f"Película: {movie_title}", color="grey", size=12, max_lines=1, overflow="ellipsis")
            ], alignment="center", horizontal_alignment="center", expand=True)
        )
        self.page.update()

    def _worker_extract_and_play(self, movie, voe_url):
        try:
            print(f"[*] Iniciando extracción para: {voe_url}")
            direct_link = extract_link_voe(voe_url)
            if direct_link:
                print(f"[+] Enlace obtenido: {direct_link}")
                self._show_video_player_ui(movie, direct_link)
            else:
                print("[!] Falló la extracción.")
                self._show_error_ui("No se pudo extraer el enlace del video.")
        except Exception as e:
            print(f"[!] Error en hilo de reproducción: {e}")
            self._show_error_ui(f"Error: {str(e)}")

    def _show_video_player_ui(self, movie, video_url):
        self.page.clean()
        top_bar = ft.Container(content=ft.Row([ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color="white", icon_size=30, on_click=lambda e: self.show_details(movie)), ft.Text("Reproduciendo", color="white", size=16, expand=True)]), padding=10, bgcolor="#141414")
        self.video_player = ftv.Video(playlist=[ftv.VideoMedia(video_url)], width=self.page.width, aspect_ratio=16 / 9, autoplay=True, show_controls=True, fill_color=ft.Colors.BLACK, fit="contain", volume=100, on_error=lambda e: print("Error video:", e.data))
        info_section = ft.Container(content=ft.Column([ft.Text(movie["titulo"], size=18, weight="bold", color="white"), ft.Text(movie.get("sinopsis", ""), size=13, color="grey")]), padding=20, bgcolor="#141414")
        page_content = ft.Column([top_bar, self.video_player, info_section], scroll="auto", expand=True)
        self.page.add(page_content)

    def _show_error_ui(self, message):
        self.page.clean()
        self.page.add(
            ft.Column([
                ft.Icon(ft.Icons.ERROR_OUTLINE, color="red", size=50),
                ft.Text("Error de Reproducción", size=20, color="white", weight="bold"),
                ft.Text(message, size=14, color="grey"),
                ft.ElevatedButton("Volver", on_click=lambda e: self.show_details(self.current_movie_detail))
            ], alignment="center", horizontal_alignment="center", expand=True)
        )

    def on_search_change(self, e):
        self.search_text = e.control.value
        self.page_num = 1 
        self.update_grid_and_pagination()

    def on_genre_click(self, genre):
        self.current_filter = genre
        self.page_num = 1
        for chip in self.filter_chips: chip.selected = (chip.label.value == genre)
        self.update_grid_and_pagination()

    def prev_page(self, e):
        if self.page_num > 1: self.page_num -= 1; self.update_grid_and_pagination()

    def next_page(self, e):
        if self.page_num < self.total_pages: self.page_num += 1; self.update_grid_and_pagination()

    def open_details(self, movie): self.show_details(movie)

def main(page: ft.Page):
    app = MovieApp(page)

ft.run(main)
