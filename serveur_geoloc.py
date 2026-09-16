# ~/Projects/GeolocS23/serveur_geoloc.py
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)
FICHIER_POSITIONS = "positions.log"
FICHIER_TELEMETRIE = "telemetrie.log"

# Colonnes du fichier telemetrie.log, dans l'ordre d'écriture.
# Toute valeur absente (capteur en échec côté S23) est enregistrée comme chaîne vide,
# jamais omise, pour garder un nombre de colonnes constant.
COLONNES_TELEMETRIE = [
    "vitesse_kmh",
    "batterie_pct", "batterie_statut", "batterie_temp",
    "accel_x", "accel_y", "accel_z",
    "luminosite_lux",
    "wifi_ssid", "wifi_rssi",
    "operateur", "type_reseau",
]

@app.route("/position", methods=["POST"])
def recevoir_position():
    data = request.get_json(force=True, silent=True) or {}
    lat, lon = data.get("latitude"), data.get("longitude")
    horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # positions.log : format historique inchangé, pour rester compatible
    # avec l'existant (Tracker_S23.py, ancien visualiseur, etc.)
    with open(FICHIER_POSITIONS, "a") as f:
        f.write(f"{horodatage},{lat},{lon}\n")

    # telemetrie.log : toutes les données supplémentaires, une ligne par envoi
    valeurs = [str(data.get(cle, "")) for cle in COLONNES_TELEMETRIE]
    with open(FICHIER_TELEMETRIE, "a") as f:
        f.write(f"{horodatage}," + ",".join(valeurs) + "\n")

    print(f"[{horodatage}] Position reçue : {lat}, {lon} | télémétrie : {data}")
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="10.221.90.1", port=5000)  # écoute uniquement sur l'IP WireGuard
