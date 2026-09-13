# GeolocS23 — Géolocalisation Samsung S23 → Raspberry Pi 5

Suivi de position d'un Samsung S23 depuis un Raspberry Pi 5, via un tunnel
WireGuard chiffré. Le téléphone envoie périodiquement sa position GPS à un
petit serveur Flask hébergé sur le Pi5, qui l'enregistre et permet de la
visualiser sur une carte interactive.

## Fonctionnement général

```
[Samsung S23]                    [Raspberry Pi 5]
  Termux + Termux:API      --->    serveur_geoloc.py (Flask, service systemd)
  position_boucle.py               positions.log
  (via tunnel WireGuard)           visualiseur_geoloc.py (Tkinter + carte)
```

Le téléphone n'a besoin d'aucune connexion Wi-Fi partagée avec le Pi5 : le
tunnel WireGuard fonctionne aussi bien à la maison qu'en 4G/5G en mobilité.

## Prérequis

### Sur le Raspberry Pi 5

- Python 3 avec Flask (souvent déjà présent sur Raspberry Pi OS)
- `pip install pillow tkintermapview --break-system-packages`
- Un tunnel WireGuard fonctionnel entre le Pi5 (serveur) et le S23 (client)

### Sur le Samsung S23

- **Termux**, installé **depuis F-Droid** (`f-droid.org/packages/com.termux`),
  pas depuis le Play Store — la version Play Store n'est plus maintenue par l'éditeur 
  et a divergé du code source officiel.
- **Termux:API** (même source, F-Droid), plugin nécessaire pour accéder au GPS
  depuis Termux.
- **Termux:Boot** (même source), pour lancer la collecte automatiquement au
  démarrage du téléphone.
- L'application WireGuard officielle, avec un tunnel déjà configuré vers le Pi5.

## Installation

### 1. Serveur sur le Pi5

```bash
mkdir -p ~/Projects/GeolocS23
cd ~/Projects/GeolocS23
# copier serveur_geoloc.py, visualiseur_geoloc.py, et le dossier icons/ ici
```

Dans `serveur_geoloc.py`, l'adresse d'écoute doit correspondre à l'IP
WireGuard du Pi5 (visible avec `ip a`, interface `wg0`) :

```python
app.run(host="10.221.90.1", port=5000)  # à adapter à votre IP wg0
```

**Faire tourner le serveur en permanence (service systemd) :**

Créer `/etc/systemd/system/geoloc-serveur.service` :

```ini
[Unit]
Description=Serveur de reception des positions GPS (GeolocS23)
After=network-online.target wg-quick@wg0.service
Wants=network-online.target

[Service]
Type=simple
User=<votre_utilisateur>
WorkingDirectory=/home/<votre_utilisateur>/Projects/GeolocS23
ExecStart=/usr/bin/python3 /home/<votre_utilisateur>/Projects/GeolocS23/serveur_geoloc.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Puis :

```bash
sudo systemctl daemon-reload
sudo systemctl enable geoloc-serveur.service
sudo systemctl start geoloc-serveur.service
sudo systemctl status geoloc-serveur.service   # doit afficher "active (running)"
```

Consulter les logs : `sudo journalctl -u geoloc-serveur.service -f`

### 2. Script d'envoi sur le S23 (Termux)

```bash
pkg update && pkg upgrade
pkg install nano python termux-api
pip install requests
```

Créer `~/position_boucle.py` :

```python
import subprocess, json, time
from datetime import datetime
import requests

URL_SERVEUR = "http://10.221.90.1:5000/position"  # IP WireGuard du Pi5
INTERVALLE_SECONDES = 3600  # voir la fenêtre "Réglages Freq. Datas" du visualiseur

def envoyer_position():
    resultat = subprocess.run(["termux-location"], capture_output=True, text=True, timeout=60)
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
            print(f"[{horodatage}] Envoyé : {envoyer_position()}")
        except Exception as erreur:
            print(f"[{horodatage}] Erreur (nouvel essai au prochain cycle) : {erreur}")
        time.sleep(INTERVALLE_SECONDES)
```

Test manuel avant automatisation :

```bash
python position_boucle.py
```

### 3. Automatisation complète sur le S23

**a) WireGuard toujours actif**
Paramètres → Connexions → Plus de paramètres de connexion → VPN → icône ⚙️
à côté du tunnel → activer **« VPN toujours actif »**. Le tunnel se
reconnecte alors automatiquement, y compris après un redémarrage.

**b) Lancement automatique au démarrage (Termux:Boot)**

```bash
mkdir -p ~/.termux/boot
nano ~/.termux/boot/lancer_geoloc.sh
```

Contenu :

```bash
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd ~
python position_boucle.py >> ~/geoloc.log 2>&1
```

```bash
chmod +x ~/.termux/boot/lancer_geoloc.sh
```

Ouvrir l'application **Termux:Boot** une première fois pour autoriser son
exécution au démarrage.

**c) Permissions Android nécessaires**

- **Termux:API** → Paramètres → Position → Autorisations de l'application →
  **« Autoriser tout le temps »** (indispensable : sans cette autorisation
  en arrière-plan, `termux-location` expire après un redémarrage).
- **Termux** et **Termux:API** → Batterie → **« Non restreint »** (évite
  qu'Android tue le script en arrière-plan pour économiser la batterie).

**d) Test**

Redémarrer le S23, attendre le déverrouillage, puis vérifier :

```bash
cat ~/geoloc.log
```

Une ligne `Envoyé : {'status': 'ok'}` doit apparaître sans qu'aucune action
manuelle n'ait été faite, et `positions.log` doit recevoir une nouvelle
ligne côté Pi5.

## Visualiseur (`visualiseur_geoloc.py`)

Interface Tkinter avec carte OpenStreetMap interactive, à lancer sur le Pi5 :

```bash
python3 visualiseur_geoloc.py
```

**Fonctionnalités :**

- Splash de démarrage avec icône `icons/gps-phone.png`.
- Carte avec trajet tracé (ligne fine bleu discret) et marqueurs, dernière
  position étiquetée « Position actuelle ».
- Liste latérale de l'historique (date/heure, latitude, longitude) avec
  ascenseur — affiche **toutes** les positions de la période sélectionnée
  (contrairement à la carte, volontairement simplifiée pour rester lisible).
- **Filtre par période** : toutes les positions, dernières 24h, 7 ou 30
  derniers jours, ou plage personnalisée choisie directement parmi les
  horodatages déjà enregistrés (menus déroulants, pas de saisie libre).
- **Regroupement adaptatif** (carte uniquement) : automatique (1 point par
  heure sur une semaine, par jour sur un mois, etc.), ou choix manuel (tous
  les points / 1 par heure / 1 par jour). Le tracé du trajet utilise
  toujours l'ensemble des points de la période.
- **Déduplication spatiale** (carte uniquement) : si le téléphone reste
  immobile, seule la dernière position de la zone stationnaire (< 30 m) est
  affichée, pour éviter les marqueurs superposés.
- **Bouton « Effacer les positions »** : vide `positions.log` (avec
  confirmation).
- **Réglages Freq. Datas (S23)** : calculateur d'intervalle d'envoi (toutes
  les X minutes / X fois par jour / X fois par semaine) pour réduire la
  fréquence de collecte et économiser la batterie du téléphone. Génère la
  ligne `INTERVALLE_SECONDES = ...` à reporter manuellement dans
  `position_boucle.py` sur le S23.
  - Option **« Caler sur une heure de départ fixe »** : au lieu d'un simple
    intervalle depuis le démarrage du script, permet de fixer une heure de
    référence (ex. 8h) puis de répéter à intervalle régulier (ex. toutes
    les 4h → envois à 8h, 12h, 16h, 20h, 0h, 4h). Dans ce cas, la fenêtre
    génère un **script complet** à recopier intégralement (et pas qu'une
    ligne), car la logique d'attente change.
- Pas de rafraîchissement automatique : utilisez le bouton « Rafraîchir »
  ou « Appliquer » pour recharger les dernières positions.

## Fiabilité de la connexion sur de longues périodes d'inactivité

Sur un réseau mobile, un tunnel WireGuard resté inactif plusieurs heures
(ex. la nuit, téléphone immobile) peut voir sa correspondance NAT fermée
côté opérateur, sans que WireGuard ne le détecte automatiquement. Symptôme
observé : les envois fonctionnent plusieurs heures, puis échouent en boucle
avec `Connection timed out`, jusqu'au prochain événement qui relance le
tunnel (déverrouillage du téléphone, changement de réseau...).

**Diagnostic :**
```bash
sudo wg show   # sur le Pi5 : regarder "latest handshake" pour le peer S23
sudo journalctl -u geoloc-serveur.service --since "<date>" --until "<date>"
```
Si le Pi5 tourne sans interruption (`uptime`) mais que le handshake date
d'avant les erreurs, la coupure vient du S23, pas du Pi5.

**Options possibles (compromis autonomie vs continuité des données) :**
- Activer `PersistentKeepalive = 25` (ou une valeur plus espacée, ex. `120`)
  dans la configuration WireGuard du S23 — maintient le tunnel ouvert en
  continu, au prix d'une légère consommation batterie supplémentaire.
- Réduire la fréquence de collecte (voir la fenêtre « Réglages Freq. Datas »)
  et accepter l'absence de données pendant les longues périodes
  d'inactivité si le suivi nocturne n'est pas essentiel — c'est le choix
  retenu par défaut dans ce projet (`INTERVALLE_SECONDES = 28800`, soit
  toutes les 8h, sans keepalive permanent).

## Dépannage rapide

| Symptôme                                           | Cause probable                                                                                                                              |
|---                                                 |---                                                                                                                                          |
| `termux-location` timeout après redémarrage        | Permission de localisation de Termux:API pas réglée sur « Tout le temps », ou optimisation batterie active                                  |
| Le script ne se relance pas après redémarrage      | Termux:Boot jamais ouvert manuellement une première fois, ou script absent de `~/.termux/boot/`                                             |
| Connexion refusée / timeout côté S23               | Tunnel WireGuard non actif sur le S23 au moment de l'envoi                                                                                  |
| `Connection timed out` après plusieurs heures OK   | NAT mobile ayant fermé le tunnel WireGuard inactif — voir section « Fiabilité de la connexion » ci-dessus                                   |
| Le Pi5 ne reçoit rien alors que le S23 envoie bien | Vérifier que `geoloc-serveur.service` est actif (`systemctl status`), et que l'IP dans les deux scripts correspond bien à l'IP `wg0` du Pi5 |
| `> ~/geoloc.log` renvoie "Permission denied"       | Vérifier que le `>` est bien tapé avant le chemin (sans lui, le shell essaie d'exécuter le fichier au lieu de le vider)                     |

## Structure du projet

```
GeolocS23/
├── serveur_geoloc.py       # serveur Flask sur le Pi5
├── visualiseur_geoloc.py   # interface Tkinter de visualisation
├── positions.log           # historique des positions (horodatage,lat,lon)
├── icons/
│   └── gps-phone.png
└── geoloc-serveur.service  # unité systemd (à copier dans /etc/systemd/system/)
```

*(Côté S23, dans Termux : `position_boucle.py` et `~/.termux/boot/lancer_geoloc.sh`.)*
