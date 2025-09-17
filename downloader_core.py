# downloader_core.py

import os
import json
import threading
import queue
import subprocess
import time

# --- Configuration des fichiers d'état ---
QUEUE_FILE = "queue.json"
COMPLETED_FILE = "completed.json"
DOWNLOAD_LOG_FILE = "downloads.log"

# --- Initialisation des données d'état ---
# Listes globales qui seront modifiées
queue_data = []
completed_data = []

# Chargement de l'état existant depuis les fichiers
if os.path.exists(QUEUE_FILE):
    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            queue_data = json.load(f)
    except json.JSONDecodeError:
        print(f"Erreur : Le fichier {QUEUE_FILE} est corrompu ou vide. Initialisation d'une file d'attente vide.")
        queue_data = []
else:
    print(f"Le fichier {QUEUE_FILE} n'existe pas. Création d'une file d'attente vide.")


if os.path.exists(COMPLETED_FILE):
    try:
        with open(COMPLETED_FILE, 'r', encoding='utf-8') as f:
            completed_data = json.load(f)
    except json.JSONDecodeError:
        print(f"Erreur : Le fichier {COMPLETED_FILE} est corrompu ou vide. Initialisation d'une liste de tâches terminées vide.")
        completed_data = []
else:
    print(f"Le fichier {COMPLETED_FILE} n'existe pas. Création d'une liste de tâches terminées vide.")


# --- Files d'attente et statuts en mémoire ---
download_queue = queue.Queue()
# Statuts de téléchargement en cours {item_id: "⏳"/"✅"/"❌"}
# Stockera un ID de chaîne, car l'API XTream utilise des chaînes pour les séries et les films
download_status = {}

# Remplissage de la file d'attente active à partir du fichier au démarrage (s'il y avait des tâches inachevées)
# Marquer les tâches dans la file d'attente comme "en cours" (⏳) au démarrage de l'application
for job in queue_data:
    item_id = str(job.get("item_id")) # Assurez-vous que l'ID est une chaîne
    if item_id:
        download_queue.put(job)
        download_status[item_id] = "⏳" # Marquer comme en cours au démarrage
    else:
        print(f"Avertissement : Tâche dans queue.json sans item_id, ignorée : {job}")

print(f"Chargé {len(queue_data)} tâches dans la file d'attente depuis {QUEUE_FILE}.")
print(f"Chargé {len(completed_data)} éléments terminés depuis {COMPLETED_FILE}.")


# --- Fonctions de sauvegarde de l'état ---
def save_queue():
    try:
        with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(queue_data, f, indent=4)
    except Exception as e:
        print(f"Erreur d'écriture du fichier {QUEUE_FILE}: {e}")

def save_completed():
    try:
        with open(COMPLETED_FILE, 'w', encoding='utf-8') as f:
            json.dump(completed_data, f, indent=4)
    except Exception as e:
        print(f"Erreur d'écriture du fichier {COMPLETED_FILE}: {e}")

# --- Worker de téléchargement principal ---
def download_worker():
    global queue_data, completed_data # Nous devons modifier les listes globales
    while True:
        job = download_queue.get()
        if job is None: # Signal de fin pour le worker
            break

        # Assurez-vous que item_id est une chaîne pour correspondre aux clés dans completed_data
        item_id = str(job.get("item_id"))
        file_name = job.get("file")
        cmd = job.get("cmd")
        item_title = job.get("title", "Titre inconnu") # Utiliser le 'titre' générique
        item_type = job.get("item_type", "unknown")

        if not item_id or not file_name or not cmd:
            print(f"Erreur : Tâche incomplète dans la file d'attente : {job}")
            download_queue.task_done()
            continue

        try:
            download_status[item_id] = "⏳"
            print(f"Début du téléchargement {item_type}: {item_title} (ID: {item_id})")
            with open(DOWNLOAD_LOG_FILE, "a", encoding='utf-8') as logf:
                logf.write(f"\n=== Téléchargement {item_type}: {item_title} (ID: {item_id}) ===\n")
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                for line in process.stdout:
                    logf.write(line)
                process.wait()
                if process.returncode != 0:
                   raise subprocess.CalledProcessError(process.returncode, cmd)

            status = "✅"
            if item_id not in completed_data:
                completed_data.append(item_id)
                save_completed()

            # Supprimer la tâche de queue_data après avoir réussi
            # Actualiser la variable globale queue_data
            queue_data[:] = [item for item in queue_data if str(item.get('item_id')) != item_id]
            save_queue()
            print(f"Téléchargement terminé pour {item_title} avec l'ID {item_id} avec le statut : {status}")

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            with open(DOWNLOAD_LOG_FILE, "a", encoding='utf-8') as logf:
                logf.write(f"❌ Erreur de téléchargement {item_type}: {item_title} (ID: {item_id}) - {e}\n")
            status = "❌"
            print(f"Erreur de téléchargement pour {item_title} avec l'ID {item_id}: {e}")
        finally:
            download_status[item_id] = status
            download_queue.task_done()


# --- Démarrage du worker dans un thread séparé ---
download_worker_thread = threading.Thread(target=download_worker, daemon=True)
download_worker_thread.start()
print("Le worker de téléchargement a été démarré.")


# Fonction pour ajouter des tâches à la file d'attente de l'extérieur
def add_to_download_queue(job_details):
    # Vérifiez si la tâche existe déjà dans la file d'attente ou si elle est terminée
    item_id = str(job_details.get("item_id")) # Assurez-vous que l'ID est une chaîne
    if not item_id:
        print("Erreur : Tentative d'ajout d'une tâche sans item_id à la file d'attente.")
        return False

    if item_id in completed_data:
        print(f"La tâche avec l'ID {item_id} est déjà terminée. Ne pas ajouter à la file d'attente.")
        return False

    # Vérifiez si la tâche est déjà dans 'queue_data' (par exemple, en attente de téléchargement ou en cours)
    if any(str(q_job.get('item_id')) == item_id for q_job in queue_data):
        print(f"La tâche avec l'ID {item_id} est déjà dans la file d'attente. Ne pas ajouter de doublon.")
        return False

    download_queue.put(job_details)
    download_status[item_id] = "⏳" # Marquer comme en cours
    queue_data.append(job_details)
    save_queue()
    print(f"Tâche avec l'ID {item_id} ajoutée à la file d'attente.")
    return True

# Fonctions de gestion de la file d'attente (déplacées de seriale.py)
def get_queue_status():
    return download_status

def get_full_queue_data():
    return queue_data

def remove_from_queue(item_id):
    global queue_data
    item_id = str(item_id) # Assurez-vous que l'ID est une chaîne
    # Supprimer de la liste principale des données de la file d'attente
    queue_data[:] = [item for item in queue_data if str(item.get('item_id')) != item_id]
    save_queue()
    # Supprimer du statut, s'il existe
    download_status.pop(item_id, None)
    print(f"Tâche avec l'ID {item_id} supprimée de la file d'attente.")

def reorder_queue(order_list):
    global queue_data
    # Assurez-vous que tous les ID dans order_list sont des chaînes
    order_list_str = [str(x) for x in order_list]
    order_map = {item_id: i for i, item_id in enumerate(order_list_str)}
    
    # Tri de 'queue_data' basé sur la carte
    # Assurez-vous que item.get('item_id') est converti en chaîne pour la comparaison
    queue_data.sort(key=lambda x: order_map.get(str(x['item_id']), len(order_list_str)))
    save_queue()
    
    # Actualisation de la file d'attente dans le worker : nous vidons et ajoutons à nouveau
    with download_queue.mutex: # Sécuriser l'accès à la liste interne de la file d'attente
        # Assurez-vous que le worker n'est pas en train de télécharger quelque chose que nous allons supprimer
        # ou qu'il peut gérer une file d'attente vide et un nouvel ajout
        # Il est plus simple de simplement vider et ajouter
        while not download_queue.empty():
            try:
                download_queue.get_nowait() # Ne pas bloquer si vide
            except queue.Empty:
                break
    for job in queue_data:
        download_queue.put(job)
    print("La file d'attente a été réorganisée.")

def get_completed_items():
    return completed_data
