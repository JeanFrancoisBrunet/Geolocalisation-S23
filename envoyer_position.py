#!/data/data/com.termux/files/usr/bin/python3
# =============================================================================
#  Envoi ponctuel (one-shot) de la position GPS + télémétrie du S23 vers le Pi5
#  Déclenché par termux-job-scheduler (voir ConfigS23.txt), pas de boucle interne.
#
#  Auteur : Jean-François BRUNET – JFBConseils – Septembre 2026
# =============================================================================

import subprocess
import json
from datetime import datetime

import requests

URL_SERVEUR = "http://10.221.90.1:5000/position"  # IP WireGuard du Pi5
FICHIER_LOG = "/data/data/com.termux/files/home/geoloc.log"

def executer_termux(commande, timeout=10):
    """Exécute une commande termux-api et renvoie le JSON parsé, ou None en cas d'échec.
    Chaque capteur est optionnel : une panne sur l'un ne doit jamais bloquer les autres."""
    try:
        resultat = subprocess.run(commande, capture_output=True, text=True, timeout=timeout)
        return json.loads(resultat.stdout)
    except Exception:
        return None

def obtenir_position():
    """Position + vitesse (le champ 'speed', en m/s, vient directement de termux-location)."""
    data = executer_termux(["termux-location", "-p", "network", "-r", "once"], timeout=60)
    if data is None:
        raise RuntimeError("termux-location n'a renvoyé aucune donnée exploitable")
    vitesse_ms = data.get("speed") or 0.0
    return {
        "latitude": data["latitude"],
        "longitude": data["longitude"],
        "vitesse_kmh": round(vitesse_ms * 3.6, 1),
    }

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
    (elle lève une exception si elle échoue) ; le reste est du bonus optionnel.
    On note au passage les capteurs optionnels qui n'ont rien renvoyé, pour
    pouvoir le signaler dans le log sans pour autant bloquer l'envoi."""
    donnees = {}
    donnees.update(obtenir_position())

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

    return donnees, manquants

if __name__ == "__main__":
    horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        donnees, manquants = collecter_toutes_les_donnees()
        reponse = requests.post(URL_SERVEUR, json=donnees, timeout=10)
        if manquants:
            ligne = f"[{horodatage}] Envoyé (incomplet, manquants: {', '.join(manquants)}) : {reponse.json()}\n"
        else:
            ligne = f"[{horodatage}] Envoyé : {reponse.json()}\n"
    except Exception as erreur:
        ligne = f"[{horodatage}] Erreur : {erreur}\n"

    with open(FICHIER_LOG, "a") as f:
        f.write(ligne)
