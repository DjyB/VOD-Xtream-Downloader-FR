# Catalogue VOD Downloader

Cet outil permet de télécharger localement des films et des séries depuis un serveur Xtream Codes.
## Fonctionnalités

- Liste des films VOD depuis l'API Xtream Codes
- Téléchargement de films en un clic
- Réessai automatique en cas d'erreur
- Configuration via des variables d'environnement
- Prêt à être lancé dans un conteneur Docker sur Unraid

## Lancement

1. Configurez `docker-compose.yml` avec vos informations Xtream et les chemins pour Unraid.
2. Lancez :

```bash
docker-compose up --build -d
```

3. Accédez dans votre navigateur : `http://<adresse_unraid>:5000`

## Configuration d'Infuse

- Ajoutez un partage SMB avec `/mnt/user/media` dans Infuse comme nouvelle source.
- Les films seront automatiquement visibles dans le catalogue.

## Variables d'environnement

| Variable               | Description                                  |
|------------------------|----------------------------------------------|
| XTREAM_HOST           | Adresse de l'API Xtream Codes (par ex. http://...)|
| XTREAM_PORT           | Port de l'API Xtream (généralement 8080)         |
| XTREAM_USERNAME       | Identifiant                                 |
| XTREAM_PASSWORD       | Mot de passe                                 |
| DOWNLOAD_PATH_MOVIES  | Chemin de sauvegarde des films                 |
| DOWNLOAD_PATH_SERIES  | Chemin de sauvegarde des séries                |
| RETRY_COUNT           | Nombre de tentatives en cas d'erreur |

## Prérequis

- Docker et Docker Compose
- API Xtream Codes fonctionnelle

---

Projet en cours de développement – seront ajoutés : le support pour les séries, les saisons, les épisodes, les `.nfo`, les statuts de téléchargement.
