# filmy.py

from flask import Blueprint, request, jsonify, render_template
import os
import requests
import subprocess
from urllib.parse import quote
import re
from datetime import datetime # Nécessaire pour les noms de dossiers Plex

# Importer les composants communs de downloader_core
from downloader_core import (
    add_to_download_queue,
    get_queue_status,
    get_full_queue_data,
    remove_from_queue,
    reorder_queue,
    get_completed_items
)

filmy_bp = Blueprint('filmy', __name__, url_prefix='/filmy')

# --- Configuration pour les films ---
XTREAM_HOST = os.getenv("XTREAM_HOST")
XTREAM_PORT = os.getenv("XTREAM_PORT")
XTREAM_USERNAME = os.getenv("XTREAM_USERNAME")
XTREAM_PASSWORD = os.getenv("XTREAM_PASSWORD")
DOWNLOAD_PATH_MOVIES = os.getenv("DOWNLOAD_PATH_MOVIES", "/downloads/Filmy")
TMDB_API_KEY = "cfdfac787bf2a6e2c521b93a0309ff2c" # Si vous ajoutez des détails de film
BASE_API = f"{XTREAM_HOST}:{XTREAM_PORT}/player_api.php?username={XTREAM_USERNAME}&password={XTREAM_PASSWORD}"

# --- Fonction d'assistance sanitize_filename ---
def sanitize_filename(name):
    """Supprime les caractères non valides du nom de fichier/dossier pour assurer la compatibilité."""
    s = re.sub(r'[^\w\s\-\._()]', '', name)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

# --- Fonctions TMDB pour les films (facultatif, si vous voulez plus de détails) ---
from functools import lru_cache

@lru_cache(maxsize=128)
def search_tmdb_movie_id(title):
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
    url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={quote(cleaned_title)}&language=pl-PL"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        results = data.get("results", [])
        if results:
            return results[0]["id"]
    return None

def get_tmdb_movie_metadata(tmdb_id):
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}&language=pl-PL"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    return None

# --- ROUTE NFO pour les films ---
@filmy_bp.route("/nfo/<int:movie_id>")
def download_movie_nfo(movie_id):
    try:
        response = requests.get(f"{BASE_API}&action=get_vod_info&vod_id={movie_id}")
        response.raise_for_status()
        info = response.json()
    except requests.exceptions.RequestException as e:
        return f"Erreur de communication avec l'API : {e}", 500
    except ValueError:
        return "Erreur : Réponse JSON non valide de l'API.", 500

    movie_info = info.get('info', {})
    movie_name_raw = movie_info.get('name', f"film_{movie_id}")
    
    # === LOGIQUE DE NOMMAGE PLEX POUR LE FILM ===
    prefix_pattern = r"^(?:[pP][lL]|[eE][nN]|[aA]\+|[dD]\+)\s*-\s*"
    movie_name_cleaned = re.sub(prefix_pattern, "", movie_name_raw).strip()
    
    release_date_str = movie_info.get('releaseDate', '') # Pour les films, c'est 'releasedate' (API v2) ou 'added' (API v1)
    year_str = ''
    if release_date_str:
        try:
            year_str = f"({datetime.strptime(release_date_str, '%Y-%m-%d').year})"
        except ValueError:
            if release_date_str.strip()[:4].isdigit():
                 year_str = f"({release_date_str.strip()[:4]})"
    
    movie_folder_name = sanitize_filename(f"{movie_name_cleaned} {year_str}".strip())
    # ==========================================

    tmdb_id = search_tmdb_movie_id(movie_name_cleaned)
    if not tmdb_id:
        return f"ID TMDB introuvable pour : {movie_name_cleaned}", 404
    metadata = get_tmdb_movie_metadata(tmdb_id)
    if not metadata:
        return "Pas de métadonnées de TMDB", 404

    nfo = f"""
<movie>
  <title>{metadata['title']}</title>
  <originaltitle>{metadata['original_title']}</originaltitle>
  <plot>{metadata['overview']}</plot>
  <tagline>{metadata['tagline']}</tagline>
  <runtime>{metadata['runtime']}</runtime>
  <year>{metadata['release_date'].split('-')[0]}</year>
  <rating>{metadata['vote_average']}</rating>
  <country>{', '.join([c['name'] for c in metadata['production_countries']])}</country>
  <director>{', '.join([c['name'] for c in metadata.get('credits', {}).get('crew', []) if c['job'] == 'Director'])}</director>
  <writer>{', '.join([c['name'] for c in metadata.get('credits', {}).get('crew', []) if c['job'] == 'Screenplay'])}</writer>
  <genre>{', '.join([g['name'] for g in metadata['genres']])}</genre>
  <premiered>{metadata['release_date']}</premiered>
  <releasedate>{metadata['release_date']}</releasedate>
  <thumb>{'https://image.tmdb.org/t/p/original' + metadata['poster_path'] if metadata.get('poster_path') else ''}</thumb>
</movie>
"""
    path = os.path.join(DOWNLOAD_PATH_MOVIES, movie_folder_name)
    os.makedirs(path, exist_ok=True)
    file_name_nfo = f"{movie_folder_name}.nfo"
    file_path = os.path.join(path, file_name_nfo)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(nfo.strip())
    return f"📄 Fichier enregistré : {file_path}", 200


# --- Vues de gestion de la file d'attente (utilisent des fonctions communes de downloader_core) ---
# Ces routes sont déjà disponibles sous /series/queue/..., mais peuvent être dupliquées pour /films/queue/
# Si vous voulez qu'elles ne soient disponibles qu'une seule fois pour toute l'application, enregistrez-les une seule fois dans le app.py principal
# Pour les besoins de cette tâche, pour qu'elles soient disponibles directement depuis le blueprint du film, ajoutons-les
# cependant, une centralisation pour toute l'application est recommandée.
@filmy_bp.route("/queue/status")
def queue_status():
    return jsonify(get_queue_status())

@filmy_bp.route("/queue/remove", methods=["POST"])
def queue_remove():
    item_id = request.form.get("id")
    remove_from_queue(item_id)
    return '', 204
    
@filmy_bp.route("/queue/reorder", methods=["POST"])
def queue_reorder():
    order = request.json.get("order", [])
    reorder_queue(order)
    return '', 204

@filmy_bp.route("/completed")
def completed_movies(): # Le nom de la fonction a été modifié pour être spécifique aux films
    return jsonify(get_completed_items())

@filmy_bp.route("/queue/full_data")
def get_full_queue():
    return jsonify(get_full_queue_data())


# --- Vue principale de la liste des films ---
@filmy_bp.route("/")
def filmy_list():
    query = request.args.get('query', '').lower()

    response = requests.get(f"{BASE_API}&action=get_vod_streams")
    if response.status_code != 200:
        return "Erreur lors du téléchargement de la liste de films", 500
    
    all_movies = response.json()
    
    if query:
        filtered_movies = []
        for movie in all_movies:
            if movie.get('name') and query in movie['name'].lower():
                filtered_movies.append(movie)
        movies_to_display = filtered_movies
    else:
        movies_to_display = all_movies

    return render_template("filmy_list.html", movies=movies_to_display, completed_data=get_completed_items())

# --- ROUTE DE TÉLÉCHARGEMENT DE FILM (utilise add_to_download_queue) ---
@filmy_bp.route("/download", methods=["POST"])
def download_movie():
    stream_id = request.form.get('id')
    name_raw = request.form.get('name')
    ext = request.form.get('ext') 

    if not all([stream_id, name_raw]):
        return "Erreur : Données requises manquantes pour télécharger le film.", 400

    # === LOGIQUE DE NOMMAGE PLEX POUR LE FILM ===
    prefix_pattern = r"^(?:[pP][lL]|[eE][nN]|[aA]\+|[dD]\+)\s*-\s*"
    movie_name_cleaned = re.sub(prefix_pattern, "", name_raw).strip()
    
    # Essayez d'obtenir l'année de TMDB pour un nom de dossier plus précis
    tmdb_id = search_tmdb_movie_id(movie_name_cleaned)
    release_year_str = ''
    if tmdb_id:
        tmdb_metadata = get_tmdb_movie_metadata(tmdb_id)
        if tmdb_metadata and tmdb_metadata.get('release_date'):
            release_year_str = tmdb_metadata['release_date'].split('-')[0]
    
    if release_year_str:
        movie_folder_name = sanitize_filename(f"{movie_name_cleaned} ({release_year_str})")
    else:
        movie_folder_name = sanitize_filename(movie_name_cleaned)
    # ==========================================

    path = os.path.join(DOWNLOAD_PATH_MOVIES, movie_folder_name)
    os.makedirs(path, exist_ok=True) # Créer un dossier pour le film

    
    # Le nom du fichier est le nom du dossier + l'extension pour Plex
    file_name = f"{movie_folder_name}.{ext}"
    file_path = os.path.join(path, file_name)

    stream_url = f"{XTREAM_HOST}:{XTREAM_PORT}/movie/{XTREAM_USERNAME}/{XTREAM_PASSWORD}/{stream_id}.{ext}"

    job = {
        "cmd": ["wget", "-O", file_path, stream_url],
        "file": file_name,
        "item_id": stream_id, # Nous utilisons un item_id générique
        "item_type": "movie", # Nous ajoutons le type d'élément
        "title": name_raw # Titre original pour les journaux et l'affichage
    }

    if add_to_download_queue(job):
        return "🕐 Film ajouté à la file d'attente", 202
    else:
        return "Film déjà dans la file d'attente ou téléchargé.", 200