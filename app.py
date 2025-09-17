# app.py (modifié)

import os
from flask import Flask, render_template, redirect, url_for

# Importer les blueprints
from seriale import seriale_bp
from filmy import filmy_bp

# Vous pouvez également importer downloader_core si vous avez besoin d'accéder à ses fonctions ici,
# mais les blueprints l'importent et l'utilisent déjà.
# from downloader_core import download_worker_thread_start, save_queue, save_completed # etc.

app = Flask(__name__)

# Enregistrement des blueprints
app.register_blueprint(seriale_bp)
app.register_blueprint(filmy_bp)

# La route principale redirige vers la liste des séries
@app.route("/")
def index():
    return redirect(url_for('seriale.seriale_list')) # Rediriger par défaut vers les séries

# Vous pouvez ajouter des liens séparés pour les films et les séries dans le menu de navigation HTML.
# Par exemple, si vous voulez avoir /films comme page d'accueil distincte pour les films.
# @app.route("/films_accueil")
# def films_accueil():
#     return redirect(url_for('filmy.filmy_list'))

if __name__ == '__main__':
    # Assurez-vous que toutes les variables d'environnement sont définies,
    # par exemple dans un fichier .env ou directement dans l'environnement.
    # FLASK_APP=app.py
    # FLASK_ENV=development
    # XTREAM_HOST=votre_hôte
    # XTREAM_PORT=votre_port
    # XTREAM_USERNAME=votre_login
    # XTREAM_PASSWORD=votre_mot_de_passe
    # DOWNLOAD_PATH_SERIES=/chemin/vers/series
    # DOWNLOAD_PATH_MOVIES=/chemin/vers/films

    app.run(host='0.0.0.0', port=5000, debug=True)