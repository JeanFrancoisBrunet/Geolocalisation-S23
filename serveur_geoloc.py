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
    "position_ancienne", "age_position_s",   # dernière position connue (fix ancien) et son âge en secondes
]

FORMAT_HORODATAGE = "%Y-%m-%d %H:%M:%S"
SEUIL_RATTRAPAGE_S = 300  # au-delà de 5 min d'écart avec l'heure du Pi5, l'envoi est affiché comme rattrapage

def horodatage_de_l_envoi(data):
    """Heure à écrire dans les logs. Une position gardée en attente par le S23 (Pi5 injoignable)
    arrive avec son heure réelle dans 'horodatage_s23' : on la conserve pour que le Tracker
    la place au bon moment. Le S23 l'envoie désormais pour toute position ; sans ce champ, on prend l'heure de réception.
    Renvoie (horodatage, True si l'heure du S23 s'écarte de plus de 5 min de celle du Pi5)."""
    brut = data.get("horodatage_s23")
    if brut:
        try:
            heure_s23 = datetime.strptime(str(brut), FORMAT_HORODATAGE)  # on n'écrit jamais un texte qui n'est pas une date
            return str(brut), abs((datetime.now() - heure_s23).total_seconds()) > SEUIL_RATTRAPAGE_S
        except ValueError:
            pass
    return datetime.now().strftime(FORMAT_HORODATAGE), False

@app.route("/position", methods=["POST"])
def recevoir_position():
    data = request.get_json(force=True, silent=True) or {}
    lat, lon = data.get("latitude"), data.get("longitude")
    horodatage, rattrapage = horodatage_de_l_envoi(data)

    # positions.log : format historique inchangé, pour rester compatible
    # avec l'existant (Tracker_S23.py, ancien visualiseur, etc.)
    with open(FICHIER_POSITIONS, "a") as f:
        f.write(f"{horodatage},{lat},{lon}\n")

    # telemetrie.log : toutes les données supplémentaires, une ligne par envoi
    valeurs = [str(data.get(cle, "")) for cle in COLONNES_TELEMETRIE]
    with open(FICHIER_TELEMETRIE, "a") as f:
        f.write(f"{horodatage}," + ",".join(valeurs) + "\n")

    print(f"[{horodatage}] Position reçue{' (rattrapage)' if rattrapage else ''} : {lat}, {lon} | télémétrie : {data}")
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="10.221.90.1", port=5000)  # écoute uniquement sur l'IP WireGuard
