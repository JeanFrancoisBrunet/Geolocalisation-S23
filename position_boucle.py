#!/usr/bin/env python3
"""
Envoi automatique et périodique de la position GPS du S23 vers le Pi5.
Nécessite : termux-api (paquet), Termux:API (app), et le tunnel WireGuard actif.
"""

import subprocess
import json
import time
from datetime import datetime

import requests

URL_SERVEUR = "http://10.221.90.1:5000/position"  # IP WireGuard du Pi5
INTERVALLE_SECONDES = 3600  # toutes les heures


def envoyer_position():
    resultat = subprocess.run(
        ["termux-location"], capture_output=True, text=True, timeout=60
    )
    data = json.loads(resultat.stdout)
    reponse = requests.post(
        URL_SERVEUR,
        json={"latitude": data["latitude"], "longitude": data["longitude"]},
        timeout=10,
    )
    return reponse.json()


if __name__ == "__main__":
    while True:
        horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            resultat = envoyer_position()
            print(f"[{horodatage}] Envoyé : {resultat}")
        except Exception as erreur:
            print(f"[{horodatage}] Erreur (nouvel essai au prochain cycle) : {erreur}")

        time.sleep(INTERVALLE_SECONDES)
