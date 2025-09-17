from flask import Blueprint, request, jsonify, render_template, send_file
import os
import requests
import subprocess
from urllib.parse import quote # Bien que supprimé de la logique de téléchargement, nous le laissons pour TMDB
import json
import sys
import threading
import queue
import time
from io import BytesIO
import re # Import ajouté pour les expressions régulières
from datetime import datetime # Import ajouté pour la gestion des dates

seriale_bp = Blueprint('seriale', __name__, url_prefix='/seriale')

# --- Configuration (sans changement) ---
XTREAM_HOST = os.getenv("XTREAM_HOST")
XTREAM_PORT = os.getenv("XTREAM_PORT")
XTREAM_USERNAME = os.getenv("XTREAM_USERNAME")
XTREAM_PASSWORD = os.getenv("XTREAM_PASSWORD")
DOWNLOAD_PATH_SERIES = os.getenv("DOWNLOAD_PATH_SERIES", "/downloads/Seriale")
RETRY_COUNT = int(os.getenv("RETRY_COUNT", 3))
QUEUE_FILE = "queue.json"
DOWNLOAD_LOG_FILE = "downloads.log"
COMPLETED_FILE = "completed.json"
TMDB_API_KEY = "cfdfac787bf2a6e2c521b93a0309ff2c"
BASE_API = f"{XTREAM_HOST}:{XTREAM_PORT}/player_api.php?username={XTREAM_USERNAME}&password={XTREAM_PASSWORD}"

# --- Gestion de la file d\'attente (sans changement) ---
if os.path.exists(QUEUE_FILE):
    with open(QUEUE_FILE) as f:
        queue_data = json.load(f)
else:
    queue_data = []

if os.path.exists(COMPLETED_FILE):
    with open(COMPLETED_FILE) as f:
        completed_data = json.load(f)
else:
    completed_data = []

# --- NOUVELLE FONCTION UTILITAIRE ---
def sanitize_filename(name):
    """Supprime les caractères non valides du nom de fichier/dossier pour assurer la compatibilité."""
    s = re.sub(r'[^\w\s\-\._()]', '', name) # Supprime les caractères non valides
    s = re.sub(r'\s+', ' ', s).strip() # Remplace les espaces multiples par un seul et supprime les espaces de début/fin
    return s

# --- Fonctions TMDB (sans changement) ---
from functools import lru_cache

@lru_cache(maxsize=128)
def search_tmdb_series_id(title):
    cleaned_title = (
        title
        .replace("PL -", "")
        .replace("PL-", "")
        .replace("POLSKI", "")
        .replace("LEKTOR", "")
        .replace("DUBBING", "")
        .strip()
        .title()
    )
    url = f"https://api.themoviedb.org/3/search/tv?api_key={TMDB_API_KEY}&query={quote(cleaned_title)}&language=fr-FR"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        results = data.get("results", [])
        if results:
            return results[0]["id"]
    return None

def get_tmdb_episode_metadata(tmdb_id, season, episode):
    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}?api_key={TMDB_API_KEY}&language=fr-FR"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    return None

# --- ROUTE NFO MODIFIÉE ---
@seriale_bp.route("/nfo/<int:series_id>/<int:season>/<int:episode>")
def download_nfo(series_id, season, episode):
    try:
        response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
        response.raise_for_status() # Vérifie si la requête a réussi
        info = response.json()
    except requests.exceptions.RequestException as e:
        return f"Erreur de communication avec l\'API : {e}", 500
    except ValueError: # Erreur de décodage JSON
        return "Erreur : Réponse JSON non valide de l\'API.", 500

    series_info = info.get('info', {})
    series_name_raw = series_info.get('name', f"serie_{series_id}")

    # === LOGIQUE DE NOMMAGE PLEX POUR LE DOSSIER DE LA SÉRIE ===
    # 1. Supprimer le préfixe "PL - " du nom de la série
    series_name_cleaned = re.sub(r"^[pP][lL]\\s*-\\s*", "", series_name_raw).strip()
    
    # 2. Extraire l\'année de 'releaseDate'
    release_date_str = series_info.get('releaseDate', '')
    year_str = ''
    if release_date_str:
        try:
            year_str = f"({datetime.strptime(release_date_str, '%Y-%m-%d').year})"
        except ValueError:
            # Si le format de date est différent, essayez de récupérer les 4 premiers chiffres
            if release_date_str.strip()[:4].isdigit():
                 year_str = f"({release_date_str.strip()[:4]})"
    
    # 3. Construire le nom du dossier de la série : "Nom de la Série (Année)"
    series_folder_name = sanitize_filename(f"{series_name_cleaned} {year_str}".strip())
    # ====================================================

    episodes_raw = info.get('episodes', {})
    if isinstance(episodes_raw, str):
        try:
            episodes_raw = json.loads(episodes_raw)
        except json.JSONDecodeError:
            return "Erreur : Format JSON des épisodes non valide depuis l\'API.", 500
            
    found_ep = None
    for sezon_lista in episodes_raw.values():
        for ep in sezon_lista:
            if int(ep.get('season', 0)) == season and int(ep.get('episode_num', 0)) == episode:
                found_ep = ep
                break
        if found_ep:
            break
    if not found_ep:
        return "❌ Épisode non trouvé", 404

    ep_title = sanitize_filename(found_ep.get('title', f"Épisode {episode}"))
    
    # Nous passons series_name_cleaned à search_tmdb_series_id pour de meilleurs résultats
    tmdb_id = search_tmdb_series_id(series_name_cleaned)
    if not tmdb_id:
        return f"ID TMDB non trouvé pour : {series_name_cleaned}", 404
    metadata = get_tmdb_episode_metadata(tmdb_id, season, episode)
    if not metadata:
        return "Aucune métadonnée", 404

    nfo = f"""
<episodedetails>
  <title>{metadata['name']}</title>
  <season>{season}</season>
  <episode>{episode}</episode>
  <plot>{metadata['overview']}</plot>
  <aired>{metadata['air_date']}</aired>
  <thumb>{'https://image.tmdb.org/t/p/original' + metadata['still_path'] if metadata.get('still_path') else ''}</thumb>
</episodedetails>
"""
    # Création du chemin et du nom de fichier .nfo correspondant au fichier vidéo pour Plex
    path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Saison {season:02d}")
    os.makedirs(path, exist_ok=True)
    # Nom du fichier NFO : "Nom de la Série (Année) - SXXEYY - Titre de l\'Épisode.nfo"
    file_name_nfo = f"{series_folder_name} - S{season:02d}E{episode:02d} - {ep_title}.nfo"
    file_path = os.path.join(path, file_name_nfo)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(nfo.strip())
    return f"📄 Fichier enregistré : {file_path}", 200

# --- Reste du code (worker, file d\'attente, vues) - sans changement de logique, uniquement aux endroits de création de fichiers ---
# --- Worker et gestion de la file d\'attente (sans changement) ---
download_queue = queue.Queue()
download_log = []
download_status = {}

def save_queue():
    with open(QUEUE_FILE, 'w') as f:
        json.dump(queue_data, f, indent=4)

def save_completed():
    with open(COMPLETED_FILE, 'w') as f:
        json.dump(completed_data, f, indent=4)

def download_worker():
    global queue_data
    # Restauration depuis le fichier queue_data
    existing_ids_in_queue = {id(job) for job in list(download_queue.queue)}
    for job in queue_data:
        if id(job) not in existing_ids_in_queue:
            download_queue.put(job)
            
    while True:
        job = download_queue.get()
        if job is None:
            break
        episode_id = job.get("episode_id")
        try:
            download_status[episode_id] = "⏳"
            with open(DOWNLOAD_LOG_FILE, "a") as logf:
                logf.write(f"\n=== Téléchargement : {job['file']} ===\n")
                process = subprocess.Popen(job["cmd"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                for line in process.stdout:
                    logf.write(line)
                process.wait()
                if process.returncode != 0:
                   raise subprocess.CalledProcessError(process.returncode, job["cmd"])

            status = "✅"
            if episode_id not in completed_data:
                completed_data.append(episode_id)
                save_completed()

            # Supprimer la tâche de queue_data après avoir réussi
            #global queue_data
            queue_data = [item for item in queue_data if item['episode_id'] != episode_id]
            save_queue()

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            with open(DOWNLOAD_LOG_FILE, "a") as logf:
                logf.write(f"❌ Erreur de téléchargement : {job['file']} - {e}\n")
            status = "❌"
        finally:
            download_status[episode_id] = status
            download_queue.task_done()

threading.Thread(target=download_worker, daemon=True).start()

# --- Vues et autres routes (sans changement de logique) ---
@seriale_bp.route("/queue/status")
def queue_status():
    return jsonify(download_status)

@seriale_bp.route("/queue/remove", methods=["POST"])
def queue_remove():
    episode_id = request.form.get("id")
    global queue_data
    # Supprimer de la liste principale des données de la file d\'attente
    queue_data = [item for item in queue_data if item.get('episode_id') != episode_id]
    save_queue()
    # Supprimer du statut, s\'il existe
    download_status.pop(episode_id, None)
    # Supprimer de la file d\'attente active (Queue) - c\'est plus difficile car il n\'y a pas de suppression directe
    # Une approche plus simple est de laisser le worker ignorer la tâche si elle n\'est pas dans queue_data
    return '', 204
    
@seriale_bp.route("/queue/reorder", methods=["POST"])
def queue_reorder():
    order = request.json.get("order", [])
    global queue_data
    # Création d\'une carte 'episode_id' -> position
    order_map = {eid: i for i, eid in enumerate(order)}
    # Tri de 'queue_data' sur la base de la carte
    queue_data.sort(key=lambda x: order_map.get(x['episode_id'], len(order)))
    save_queue()
    # Rafraîchir la file d\'attente dans le worker
    while not download_queue.empty():
        download_queue.get()
    for job in queue_data:
        download_queue.put(job)
    return '', 204

@seriale_bp.route("/completed")
def completed_episodes():
    return jsonify(completed_data)

# Nouveau point de terminaison pour récupérer les données complètes de la file d\'attente (noms de fichiers, ID, statuts, etc.)
@seriale_bp.route("/queue/full_data")
def get_full_queue():
    # Nous retournons la liste complète des tâches dans la file d\'attente
    return jsonify(queue_data)

# --- Vues et autres routes (changement dans seriale_list) ---
@seriale_bp.route("/")
def seriale_list():
    # Récupérer le paramètre 'query' de l\'URL, par défaut une chaîne vide
    # Convertir en minuscules pour que la recherche soit insensible à la casse
    query = request.args.get('query', '').lower() 

    response = requests.get(f"{BASE_API}&action=get_series")
    if response.status_code != 200:
        return "Erreur lors du chargement de la liste des séries", 500
    
    all_seriale = response.json()
    
    # Si une requête est fournie, filtrer les séries
    if query:
        filtered_seriale = []
        for serial in all_seriale:
            # Vérifier si le nom de la série existe et s\'il contient la requête (insensible à la casse)
            if serial.get('name') and query in serial['name'].lower():
                filtered_seriale.append(serial)
        seriale_to_display = filtered_seriale
    else:
        # Si aucune requête, afficher toutes les séries
        seriale_to_display = all_seriale

    return render_template("seriale_list.html", seriale=seriale_to_display)

@seriale_bp.route("/<int:series_id>")
def serial_detail(series_id):
    response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
    if response.status_code != 200:
        return "Erreur lors du chargement des détails de la série", 500

    data = response.json()
    serial_info = data.get('info', {})
    episodes_raw = data.get('episodes', {})

    if isinstance(episodes_raw, str):
        episodes_raw = json.loads(episodes_raw)

    # Tri des épisodes par numéro de saison et d\'épisode
    sezony = {}
    for season_num_str, episode_list in episodes_raw.items():
        try:
            season_num = int(season_num_str)
            sorted_episodes = sorted(episode_list, key=lambda x: int(x.get('episode_num', 0)))
            sezony[season_num] = sorted_episodes
        except ValueError:
            # Gestion du cas où season_num_str n\'est pas un nombre (par ex. 'undefined')
            continue

    # Tri des saisons
    sezony = dict(sorted(sezony.items()))
    # ... vue sans changement de logique
    return render_template("serial_detail.html", serial=data, sezony=sezony, series_id=series_id, completed_data=completed_data) # Raccourci pour la concision


# --- ROUTE DE TÉLÉCHARGEMENT D\'ÉPISODE MODIFIÉE ---
@seriale_bp.route("/download/episode", methods=["POST"])
def download_episode():
    episode_id = request.form.get("id")
    series_id = request.form.get("series_id")
    season = request.form.get("season")
    episode_num = request.form.get("episode_num")
    title = request.form.get("title")

    if not all([episode_id, series_id, season, episode_num, title]):
        return "Erreur : Données requises manquantes pour télécharger l\'épisode.", 400

    try:
        response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return f"Erreur de communication avec l\'API : {e}", 500
    except ValueError:
        return "Erreur : Réponse JSON non valide de l\'API.", 500
    
    series_info = data.get('info', {})
    episodes_raw = data.get('episodes', {})

    current_episode_info = None
    if isinstance(episodes_raw, str):
        try:
            episodes_raw = json.loads(episodes_raw)
        except json.JSONDecodeError:
            return "Erreur : Format JSON des épisodes non valide depuis l\'API.", 500
    
    for season_data in episodes_raw.values():
        for ep in season_data:
            if str(ep.get('id')) == str(episode_id):
                current_episode_info = ep
                break
        if current_episode_info:
            break

    if not current_episode_info:
        return f"Erreur : Informations sur l\'épisode {episode_id} non trouvées", 404

    ext = current_episode_info.get("container_extension", "mp4") # Extension par défaut
    
    # === LOGIQUE DE NOMMAGE PLEX POUR LE DOSSIER DE LA SÉRIE ET LE FICHIER DE L\'ÉPISODE ===
    # 1. Supprimer le préfixe "PL - " du nom de la série
    series_name_raw = series_info.get("name", "")
    series_name_cleaned = re.sub(r"^[pP][lL]\\s*-\\s*", "", series_name_raw).strip()

    # 2. Extraire l\'année de 'releaseDate'
    release_year_str = series_info.get("releaseDate", "").split('-')[0] if series_info.get("releaseDate") else ""

    # 3. Construire le nom du dossier de la série : "Nom de la Série (Année)"
    if release_year_str:
        series_folder_name = sanitize_filename(f"{series_name_cleaned} ({release_year_str})")
    else:
        series_folder_name = sanitize_filename(series_name_cleaned)
    # ===================================================================

    if episode_id in completed_data:
        print(f"L\'épisode {title} (ID: {episode_id}) a déjà été téléchargé, je passe.")
        return "Épisode déjà téléchargé", 200

    path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Saison {int(season):02d}")
    os.makedirs(path, exist_ok=True)
    # Nettoyer le titre de l\'épisode des informations répétées sur la série et du préfixe "PL -"
        # Utiliser regex pour supprimer "PL - Nom de la Série - SXXEYY" du début du titre de l\'épisode
        # S\'assurer que nous utilisons series_name_cleaned, et non series_folder_name, car ce dernier contient l\'année
    # === ÉTAPE 1 : Nettoyage initial du titre de l\'épisode ===
        # Supprimer les préfixes populaires des services de streaming (PL, NF, HBO, etc.) du début du titre
        # Nous utiliserons un groupe non capturant (?:...)
    cleaned_episode_title = re.sub(r"^(?:[pP][lL]|[nN][fF]|[hH][bB][oO]|\\[\\s*[pP][lL]\\s*\\]|\\[\\s*[nN][fF]\\s*\\]|\\s*\\[\\s*\\d{4}[pP]\\s*\\])\\s*-\\s*", "", title).strip()

        # === ÉTAPE 2 : Supprimer le nom de la série et le marquage SXXEYY en double ===
        # Cette partie est cruciale pour le problème "NF - Biohackers 4K - S01E06"
        # Nous créons un modèle à supprimer qui peut contenir le nom de la série (series_name_cleaned),
        # éventuellement la qualité (par ex. (4K)), et le marquage SXXEYY.
        # Nous utiliserons re.escape() pour series_name_cleaned afin de protéger les caractères spéciaux de la regex.
        # De plus, nous gérerons d\'autres variantes de marquages de qualité, par ex. " 4K" sans parenthèses.
        
        # Modèle pour correspondre par ex. "Biohackers (4K) - S01E06 - " ou "Biohackers 4K - S01E06 - "
        # Nous commençons par le nom de la série potentiellement nettoyé
    pattern_to_remove_series_info = r"^(?:" + re.escape(series_name_cleaned) + r"(?:\\s*\\(\\d+K\\))?|\\s*\\d+K)?\\s*-\\s*S\\d{2}E\\d{2}\\s*[\\s-]*"
    cleaned_episode_title = re.sub(pattern_to_remove_series_info, "", cleaned_episode_title, flags=re.IGNORECASE).strip()

        # === ÉTAPE 3 : Suppression finale des marquages de qualité restants ===
        # Supprimer tous les marquages de qualité autonomes restants (par ex. " (4K)", " 1080p") de n\'importe où dans le titre
    cleaned_episode_title = re.sub(r"\\s*\\(\\d+K\\)\\s*|\\s*\\d+K\\s*|\\s*\\d{3,4}p\\s*", "", cleaned_episode_title, flags=re.IGNORECASE).strip()

        # === ÉTAPE 4 : Vérification et titre par défaut ===
        # Si après tous les nettoyages le titre est vide, utiliser un titre par défaut
    if not cleaned_episode_title:
            cleaned_episode_title = f"Épisode {int(episode_num):02d}"

        
    episode_title_sanitized = sanitize_filename(cleaned_episode_title)
    #episode_title_sanitized = sanitize_filename(title)
    # 4. Construire le nom de fichier de l\'épisode : "Nom de la Série (Année) - SXXEYY - Titre de l\'Épisode.ext"
    file_name = f"{series_folder_name} - S{int(season):02d}E{int(episode_num):02d} - {episode_title_sanitized}.{ext}"
    file_path = os.path.join(path, file_name)

    url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.{ext}"
    job = {"cmd": ["wget", "-O", file_path, url], "file": file_name, "episode_id": episode_id, "series": series_folder_name, "title": title}
    
    download_queue.put(job)
    download_status[episode_id] = "⏳"
    queue_data.append(job)

    save_queue()
    return "🕐 Épisode ajouté à la file d\'attente", 202

# --- ROUTE DE TÉLÉCHARGEMENT DE SAISON MODIFIÉE ---
@seriale_bp.route("/download/season", methods=["POST"])
def download_season():
    series_id = request.form['series_id'].strip()
    season = int(request.form['season'])

    try:
        response = requests.get(f"{BASE_API}&action=get_series_info&series_id={series_id}")
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        return f"Erreur de communication avec l\'API : {e}", 500
    except ValueError:
        return "Erreur : Réponse JSON non valide de l\'API.", 500

    series_info = data.get('info', {})
    episodes_raw = data.get('episodes', {})

    # === LOGIQUE DE NOMMAGE PLEX POUR LE DOSSIER DE LA SÉRIE ===
    # 1. Supprimer le préfixe "PL - " du nom de la série
    series_name_raw = series_info.get('name', f"serie_{series_id}")
    series_name_cleaned = re.sub(r"^[pP][lL]\\s*-\\s*", "", series_name_raw).strip()

    # 2. Extraire l\'année de 'releaseDate'
    release_date_str = series_info.get('releaseDate', '')
    year_str = ''
    if release_date_str:
        try:
            year_str = f"({datetime.strptime(release_date_str, '%Y-%m-%d').year})"
        except ValueError:
            if release_date_str.strip()[:4].isdigit():
                 year_str = f"({release_date_str.strip()[:4]})"
    
    # 3. Construire le nom du dossier de la série : "Nom de la Série (Année)"
    series_folder_name = sanitize_filename(f"{series_name_cleaned} {year_str}".strip())
    # ====================================================

    if isinstance(episodes_raw, str):
        try:
            episodes_raw = json.loads(episodes_raw)
        except json.JSONDecodeError:
            return "Erreur : Format JSON des épisodes non valide depuis l\'API.", 500
    
    episodes_in_season = [ep for sezon_lista in episodes_raw.values() for ep in sezon_lista if int(ep.get('season', 0)) == season]

    if not episodes_in_season:
        return f"Erreur : Aucun épisode trouvé pour la saison {season}.", 404

    for ep in episodes_in_season:
        episode_id = ep['id']
        title = ep['title']
        episode_num = ep['episode_num']
        ext = ep.get("container_extension", "mp4")

        if not all([episode_id, episode_num, title, ext]):
            print(f"Informations incomplètes pour l\'épisode : {ep}. Je passe.")
            continue

        if episode_id in completed_data:
            print(f"L\'épisode {title} (ID: {episode_id}) a déjà été téléchargé, je passe.")
            continue

        path = os.path.join(DOWNLOAD_PATH_SERIES, series_folder_name, f"Saison {int(season):02d}")
        os.makedirs(path, exist_ok=True)
        # Nettoyer le titre de l\'épisode des informations répétées sur la série et du préfixe "PL -"
        # Utiliser regex pour supprimer "PL - Nom de la Série - SXXEYY" du début du titre de l\'épisode
        # S\'assurer que nous utilisons series_name_cleaned, et non series_folder_name, car ce dernier contient l\'année
        # === ÉTAPE 1 : Nettoyage initial du titre de l\'épisode ===
        # Supprimer les préfixes populaires des services de streaming (PL, NF, HBO, etc.) du début du titre
        # Nous utiliserons un groupe non capturant (?:...)
        cleaned_episode_title = re.sub(r"^(?:[pP][lL]|[nN][fF]|[hH][bB][oO]|\\[\\s*[pP][lL]\\s*\\]|\\[\\s*[nN][fF]\\s*\\]|\\s*\\[\\s*\\d{4}[pP]\\s*\\])\\s*-\\s*", "", title).strip()

        # === ÉTAPE 2 : Supprimer le nom de la série et le marquage SXXEYY en double ===
        # Cette partie est cruciale pour le problème "NF - Biohackers 4K - S01E06"
        # Nous créons un modèle à supprimer qui peut contenir le nom de la série (series_name_cleaned),
        # éventuellement la qualité (par ex. (4K)), et le marquage SXXEYY.
        # Nous utiliserons re.escape() pour series_name_cleaned afin de protéger les caractères spéciaux de la regex.
        # De plus, nous gérerons d\'autres variantes de marquages de qualité, par ex. " 4K" sans parenthèses.
        
        # Modèle pour correspondre par ex. "Biohackers (4K) - S01E06 - " ou "Biohackers 4K - S01E06 - "
        # Nous commençons par le nom de la série potentiellement nettoyé
        pattern_to_remove_series_info = r"^(?:" + re.escape(series_name_cleaned) + r"(?:\\s*\\(\\d+K\\))?|\\s*\\d+K)?\\s*-\\s*S\\d{2}E\\d{2}\\s*[\\s-]*"
        cleaned_episode_title = re.sub(pattern_to_remove_series_info, "", cleaned_episode_title, flags=re.IGNORECASE).strip()

        # === ÉTAPE 3 : Suppression finale des marquages de qualité restants ===
        # Supprimer tous les marquages de qualité autonomes restants (par ex. " (4K)", " 1080p") de n\'importe où dans le titre
        cleaned_episode_title = re.sub(r"\\s*\\(\\d+K\\)\\s*|\\s*\\d+K\\s*|\\s*\\d{3,4}p\\s*", "", cleaned_episode_title, flags=re.IGNORECASE).strip()

        # === ÉTAPE 4 : Vérification et titre par défaut ===
        # Si après tous les nettoyages le titre est vide, utiliser un titre par défaut
        if not cleaned_episode_title:
             cleaned_episode_title = f"Épisode {int(episode_num):02d}"

        

        episode_title_sanitized = sanitize_filename(cleaned_episode_title)
        #episode_title_sanitized = sanitize_filename(title)
        # 4. Construire le nom de fichier de l\'épisode : "Nom de la Série (Année) - SXXEYY - Titre de l\'Épisode.ext"
        file_name = f"{series_folder_name} - S{int(season):02d}E{int(episode_num):02d} - {episode_title_sanitized}.{ext}"
        file_path = os.path.join(path, file_name)
        
        url = f"{XTREAM_HOST}:{XTREAM_PORT}/series/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{episode_id}.{ext}"
        job = {"cmd": ["wget", "-O", file_path, url], "file": file_name, "episode_id": episode_id, "series": series_folder_name, "title": title}
        
        download_queue.put(job)
        download_status[episode_id] = "⏳"
        queue_data.append(job)

    save_queue()
    return "🕐 Saison ajoutée à la file d\'attente", 202

# --- Fonction d\'aide is_episode_already_downloaded (laissée sans changement, bien que sa logique puisse nécessiter une mise à jour) ---
# ATTENTION : Cette fonction n\'est pas utilisée dans le code et sa logique actuelle ne fonctionnera pas correctement avec la nouvelle structure de noms.
# Une meilleure façon de vérifier est d\'utiliser la liste `completed_data`.
def is_episode_already_downloaded(serial_name, season, episode_num, title, ext):
    path = os.path.join(DOWNLOAD_PATH_SERIES, serial_name, f"Saison {season}")
    file_name = f"S{int(season):02d}E{int(episode_num):02d} - {title}.{ext}"
    file_path = os.path.join(path, file_name.replace(' ', '_'))
    return os.path.exists(file_path) and os.path.getsize(file_path) > 1000000