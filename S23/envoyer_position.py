#!/data/data/com.termux/files/usr/bin/python3
# =============================================================================
#  Envoi ponctuel (one-shot) de la position GPS + télémétrie du S23 vers le Pi5
#  Déclenché par termux-job-scheduler (voir ConfigS23.txt), pas de boucle interne.
#
#  Localisation : plusieurs tentatives à la suite (network, gps, network), et le
#  motif exact de chaque échec est écrit dans le log au lieu d'être avalé.
#  En dernier recours (téléphone immobile en veille profonde, la nuit par exemple),
#  on envoie la dernière position connue du système, marquée "position_ancienne".
#
#  Fiabilité de l'envoi :
#   - l'envoi vers le Pi5 est tenté 3 fois (pause de 15 s) ;
#   - si le Pi5 reste injoignable (tunnel coupé, Pi5 redémarré...), la position est gardée dans
#     ~/geoloc_attente.json avec son heure réelle et renvoyée au passage suivant, avant la nouvelle ;
#   - après un échec (position introuvable ou envoi impossible), un nouvel essai est planifié
#     15 minutes plus tard (job 1002), jusqu'à 8 essais, puis abandon jusqu'au créneau de 8 h ;
#     il est annulé dès qu'un cycle réussit.
#
#  Auteur : Jean-François BRUNET – JFBConseils – Octobre 2026
# =============================================================================

import fcntl
import os
import subprocess
import json
import time
from datetime import datetime

import requests

URL_SERVEUR = "http://10.221.90.1:5000/position"  # IP WireGuard du Pi5
RACINE = os.path.expanduser("~")  # /data/data/com.termux/files/home sous Termux
FICHIER_LOG = os.path.join(RACINE, "geoloc.log")
FICHIER_ATTENTE = os.path.join(RACINE, "geoloc_attente.json")   # positions non envoyées, une par ligne (JSON)
FICHIER_COMPTEUR = os.path.join(RACINE, "geoloc_retry.count")   # échecs consécutifs depuis le dernier succès
FICHIER_VERROU = os.path.join(RACINE, "geoloc.lock")            # évite deux envois simultanés
SCRIPT = os.path.join(RACINE, "envoyer_position.py")
FORMAT_HORODATAGE = "%Y-%m-%d %H:%M:%S"

NB_ESSAIS_ENVOI = 3         # tentatives d'envoi pour la première position de la liste
PAUSE_ENTRE_ESSAIS = 15     # secondes entre deux tentatives d'envoi
MAX_ATTENTE = 100           # positions gardées au maximum en attente (les plus anciennes sont abandonnées)
ID_JOB_RETRY = 1002         # job termux-job-scheduler de nouvel essai (1001 = créneau régulier de 8 h)
DELAI_RETRY_MS = 15 * 60 * 1000   # 15 min : minimum accepté par Android pour un job périodique
MAX_RETRY = 8               # 8 essais x 15 min = 2 h, puis on attend le créneau suivant

# Tentatives de localisation, essayées dans l'ordre jusqu'à la première qui réussit :
# (nom affiché dans le log, fournisseur termux-location, délai maximum en secondes, mode).
# mode "once" = nouveau fix ; mode "last" = dernière position connue du système
# (réponse quasi immédiate, mais peut être ancienne).
# Durée maximale cumulée si tout échoue : 40 + 40 + 30 + 10 = 120 s.
TENTATIVES_POSITION = [
    ("network", "network", 40, "once"),
    ("gps", "gps", 40, "once"),
    ("network (2e essai)", "network", 30, "once"),
    ("dernière position connue", "network", 10, "last"),
]

def lancer_termux(commande, timeout=10):
    """Exécute une commande termux-api.
    Renvoie (json_parsé, None) en cas de succès, ou (None, motif_de_l_échec) :
    le motif (délai dépassé, sortie vide, réponse non JSON...) est conservé pour le log."""
    try:
        resultat = subprocess.run(commande, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"délai dépassé ({timeout} s)"
    except Exception as erreur:
        return None, f"{type(erreur).__name__}: {erreur}"

    sortie = resultat.stdout.strip()
    if not sortie:
        detail = resultat.stderr.strip().replace("\n", " ")[:100]
        motif = f"sortie vide (code {resultat.returncode})"
        return None, motif + (f", stderr : {detail}" if detail else "")
    try:
        return json.loads(sortie), None
    except ValueError:
        return None, f"réponse non JSON : {sortie[:80]!r}"

def executer_termux(commande, timeout=10):
    """Exécute une commande termux-api et renvoie le JSON parsé, ou None en cas d'échec.
    Chaque capteur est optionnel : une panne sur l'un ne doit jamais bloquer les autres."""
    data, _ = lancer_termux(commande, timeout)
    return data

def obtenir_position():
    """Position + vitesse (le champ 'speed', en m/s, vient directement de termux-location).
    Renvoie (position, note). 'note' décrit les tentatives échouées quand la 1re n'a pas suffi
    (chaîne vide si tout s'est bien passé). Lève RuntimeError, avec le motif de chaque échec,
    si aucune tentative ne réussit.
    Si seule la dernière position connue est disponible, 'position' contient en plus
    position_ancienne=True et, si termux le fournit, age_position_s (âge du fix en secondes) ;
    la vitesse est alors forcée à 0 car elle n'est plus d'actualité."""
    echecs = []
    for nom, fournisseur, delai, mode in TENTATIVES_POSITION:
        data, motif = lancer_termux(["termux-location", "-p", fournisseur, "-r", mode], timeout=delai)
        if motif is None and not (isinstance(data, dict) and "latitude" in data and "longitude" in data):
            motif = f"réponse sans coordonnées : {str(data)[:80]}"
        if motif is None:
            ancienne = (mode == "last")
            vitesse_ms = 0.0 if ancienne else (data.get("speed") or 0.0)
            position = {
                "latitude": data["latitude"],
                "longitude": data["longitude"],
                "vitesse_kmh": round(vitesse_ms * 3.6, 1),
            }
            if ancienne:
                position["position_ancienne"] = True
                age_ms = data.get("elapsedMs")
                if isinstance(age_ms, (int, float)):
                    position["age_position_s"] = int(age_ms / 1000)
            note = f"via {nom} après échec de " + " ; ".join(echecs) if echecs else ""
            return position, note
        echecs.append(f"{nom} : {motif}")
    raise RuntimeError("position introuvable — " + " | ".join(echecs))

def sonder_api_termux():
    """Diagnostic, utilisé seulement quand la position est introuvable : une commande termux-api sans rapport
    avec la localisation répond-elle, et en combien de temps ? Distingue « Termux:API ne répond plus du tout »
    (veille profonde) de « seule la localisation échoue »."""
    debut = time.monotonic()
    _, motif = lancer_termux(["termux-battery-status"], timeout=10)
    duree = time.monotonic() - debut
    return f"sonde termux-battery-status : {'répond' if motif is None else motif} en {duree:.1f} s"

def obtenir_batterie():
    data = executer_termux(["termux-battery-status"])
    if data is None:
        return {}
    return {
        "batterie_pct": data.get("percentage"),
        "batterie_statut": data.get("status"),       # CHARGING / DISCHARGING / FULL / ...
        "batterie_temp": data.get("temperature"),
    }

def obtenir_accelerometre():
    """Instantané simple (une seule mesure) — pas de flux continu, pour rester
    compatible avec le fonctionnement one-shot du job-scheduler."""
    data = executer_termux(["termux-sensor", "-s", "accelerometer", "-n", "1"], timeout=15)
    if not data:
        return {}
    # La clé exacte dépend du modèle du capteur (ex. "LSM6DSO Accelerometer Non-wakeup") :
    # on prend le premier capteur renvoyé plutôt qu'un nom fixe.
    premiere_cle = next(iter(data), None)
    if not premiere_cle:
        return {}
    valeurs = data[premiere_cle].get("values", [])
    if len(valeurs) < 3:
        return {}
    return {
        "accel_x": round(valeurs[0], 3),
        "accel_y": round(valeurs[1], 3),
        "accel_z": round(valeurs[2], 3),
    }

def obtenir_luminosite():
    data = executer_termux(["termux-sensor", "-s", "STK33911 Light  Non-wakeup", "-n", "1"], timeout=15)
    if not data:
        return {}
    premiere_cle = next(iter(data), None)
    if not premiere_cle:
        return {}
    valeurs = data[premiere_cle].get("values", [])
    if not valeurs:
        return {}
    return {"luminosite_lux": round(valeurs[0], 1)}

def obtenir_wifi():
    data = executer_termux(["termux-wifi-connectioninfo"])
    if not data:
        return {}
    ssid = data.get("ssid")
    if not ssid or ssid in ("<unknown ssid>", ""):
        return {}  # pas de Wi-Fi connecté
    return {
        "wifi_ssid": ssid.strip('"'),
        "wifi_rssi": data.get("rssi"),
    }

def obtenir_reseau_mobile():
    data = executer_termux(["termux-telephony-deviceinfo"])
    if not data:
        return {}
    return {
        "operateur": data.get("network_operator_name") or None,
        "type_reseau": data.get("network_type") or None,
    }

def collecter_toutes_les_donnees():
    """Assemble tout ce qui a pu être récupéré. La position est indispensable
    (elle lève une exception si toutes les tentatives échouent) ; le reste est du bonus optionnel.
    On note au passage les capteurs optionnels qui n'ont rien renvoyé, pour
    pouvoir le signaler dans le log sans pour autant bloquer l'envoi.
    Renvoie (données, capteurs_manquants, note_sur_la_position)."""
    donnees = {}
    position, note_position = obtenir_position()
    donnees.update(position)

    capteurs_optionnels = {
        "batterie": obtenir_batterie,
        "accel": obtenir_accelerometre,
        "luminosite": obtenir_luminosite,
        "wifi": obtenir_wifi,
        "reseau": obtenir_reseau_mobile,
    }

    manquants = []
    for nom, fonction in capteurs_optionnels.items():
        resultat = fonction()
        if resultat:
            donnees.update(resultat)
        elif nom != "wifi":
            # "wifi" est normalement vide dès qu'on est en 4G/5G : ce n'est pas
            # un échec, donc on ne le compte pas comme un capteur manquant.
            manquants.append(nom)

    return donnees, manquants, note_position

def ecrire_log(ligne):
    with open(FICHIER_LOG, "a") as f:
        f.write(ligne)

def lire_attente():
    """Positions en attente d'envoi, de la plus ancienne à la plus récente."""
    try:
        with open(FICHIER_ATTENTE, encoding="utf-8") as f:
            lignes = f.read().splitlines()
    except FileNotFoundError:
        return []
    elements = []
    for ligne in lignes:
        try:
            donnees = json.loads(ligne)
        except ValueError:
            continue  # ligne abîmée : ignorée plutôt que de bloquer tout le reste
        if isinstance(donnees, dict):
            elements.append(donnees)
    return elements

def ecrire_attente(elements):
    elements = elements[-MAX_ATTENTE:]
    if not elements:
        try:
            os.remove(FICHIER_ATTENTE)
        except FileNotFoundError:
            pass
        return
    temporaire = FICHIER_ATTENTE + ".tmp"
    with open(temporaire, "w", encoding="utf-8") as f:
        for donnees in elements:
            f.write(json.dumps(donnees, ensure_ascii=False) + "\n")
    os.replace(temporaire, FICHIER_ATTENTE)

def poster(donnees, tentatives):
    """Envoie une position au serveur. Renvoie (True, réponse) ou (False, motif de l'échec)."""
    motif = "aucune tentative"
    for essai in range(1, tentatives + 1):
        try:
            reponse = requests.post(URL_SERVEUR, json=donnees, timeout=10)
            if reponse.ok:
                try:
                    return True, reponse.json()
                except ValueError:
                    return True, reponse.text[:60]
            motif = f"HTTP {reponse.status_code}"
        except requests.RequestException as erreur:
            motif = type(erreur).__name__  # ConnectTimeout, ConnectionError...
        if essai < tentatives:
            time.sleep(PAUSE_ENTRE_ESSAIS)
    return False, f"{motif} après {tentatives} essai(s)"

def lire_compteur():
    try:
        with open(FICHIER_COMPTEUR) as f:
            return int(f.read().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0

def ecrire_compteur(valeur):
    with open(FICHIER_COMPTEUR, "w") as f:
        f.write(str(valeur))

def lancer_job(commande):
    """Commande termux-job-scheduler ; renvoie True si elle a répondu sans erreur."""
    try:
        resultat = subprocess.run(commande, capture_output=True, text=True, timeout=15)
    except Exception:
        return False
    return resultat.returncode == 0

def planifier_nouvel_essai():
    return lancer_job([
        "termux-job-scheduler", "--job-id", str(ID_JOB_RETRY), "--script", SCRIPT,
        "--period-ms", str(DELAI_RETRY_MS), "--persisted", "true",
        "--network", "any", "--battery-not-low", "true",
    ])

def annuler_nouvel_essai():
    return lancer_job(["termux-job-scheduler", "--cancel", "--job-id", str(ID_JOB_RETRY)])

def gerer_nouvel_essai(probleme):
    """Planifie (ou annule) le job de nouvel essai selon le résultat du cycle.
    Renvoie un court texte pour le log, ou une chaîne vide s'il n'y a rien à signaler."""
    echecs = lire_compteur()
    if not probleme:
        if echecs:
            ecrire_compteur(0)
            annuler_nouvel_essai()
        return ""
    echecs += 1
    if echecs > MAX_RETRY:
        ecrire_compteur(0)
        annuler_nouvel_essai()
        return f"abandon après {MAX_RETRY} nouveaux essais, prochain essai au créneau de 8 h"
    ecrire_compteur(echecs)
    if planifier_nouvel_essai():
        return f"nouvel essai dans 15 min ({echecs}/{MAX_RETRY})"
    return "nouvel essai NON planifié (termux-job-scheduler n'a pas répondu)"

def main():
    # Horodatage pris au DÉBUT du cycle : il indique l'heure de déclenchement, pas celle de la fin
    # (un cycle qui enchaîne plusieurs tentatives de localisation peut durer 2 minutes).
    horodatage = datetime.now().strftime(FORMAT_HORODATAGE)

    verrou = open(FICHIER_VERROU, "w")
    try:
        fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        ecrire_log(f"[{horodatage}] Ignoré : un autre envoi est déjà en cours\n")
        return

    attente = lire_attente()

    # 1. Collecte. Si la position est introuvable, on tente quand même de renvoyer l'attente.
    donnees = None
    manquants, note_position, echec_position = [], "", None
    try:
        donnees, manquants, note_position = collecter_toutes_les_donnees()
    except Exception as erreur:
        echec_position = str(erreur)
        if isinstance(erreur, RuntimeError):  # position introuvable : on ajoute le diagnostic
            echec_position += f" | {sonder_api_termux()}"
    horodatage_collecte = datetime.now().strftime(FORMAT_HORODATAGE)

    # 2. Envoi dans l'ordre chronologique : d'abord l'attente, puis la nouvelle position.
    restants = attente + ([donnees] if donnees is not None else [])
    nb_envoyes, echec_envoi, derniere_reponse = 0, None, None
    while restants:
        # 3 tentatives pour la première ; les suivantes, si la liaison vient de marcher, une seule.
        ok, retour = poster(restants[0], NB_ESSAIS_ENVOI if nb_envoyes == 0 else 1)
        if not ok:
            echec_envoi = retour
            break
        derniere_reponse = retour
        restants.pop(0)
        nb_envoyes += 1

    # 3. Ce qui n'est pas parti est gardé, avec son heure réelle (la nouvelle position n'en a pas encore).
    for element in restants:
        element.setdefault("horodatage_s23", horodatage_collecte)
    ecrire_attente(restants)

    # 4. Ligne de log
    rattrapees = min(nb_envoyes, len(attente))
    if nb_envoyes and echec_envoi is None:
        details = []
        if manquants:
            details.append(f"incomplet, manquants: {', '.join(manquants)}")
        if note_position:
            details.append(f"position {note_position}")
        if rattrapees:
            details.append(f"{rattrapees} position(s) en attente renvoyée(s)")
        if echec_position:
            details.append(f"position actuelle introuvable : {echec_position}")
        entete = f"Envoyé ({' ; '.join(details)})" if details else "Envoyé"
        ligne = f"[{horodatage}] {entete} : {derniere_reponse}"
    else:
        problemes = []
        if echec_position:
            problemes.append(echec_position)
        if echec_envoi:
            if rattrapees:
                problemes.append(f"{rattrapees} position(s) en attente renvoyée(s) avant l'échec")
            problemes.append(f"envoi impossible : {echec_envoi} — {len(restants)} position(s) en attente")
        ligne = f"[{horodatage}] Erreur : " + " ; ".join(problemes)

    # 5. Nouvel essai dans 15 min si quelque chose a échoué ; annulé dès que tout est passé.
    suite = gerer_nouvel_essai(echec_position is not None or echec_envoi is not None)
    if suite:
        ligne += f" | {suite}"
    ecrire_log(ligne + "\n")

if __name__ == "__main__":
    main()
