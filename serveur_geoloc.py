# ~/Projects/GeolocS23/serveur_geoloc.py
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)
FICHIER_LOG = "positions.log"

@app.route("/position", methods=["POST"])
def recevoir_position():
    data = request.get_json()
    lat, lon = data.get("latitude"), data.get("longitude")
    horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(FICHIER_LOG, "a") as f:
        f.write(f"{horodatage},{lat},{lon}\n")
    print(f"[{horodatage}] Position reçue : {lat}, {lon}")
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="10.221.90.1", port=5000)  # écoute uniquement sur l'IP WireGuard
