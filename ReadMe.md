# GeolocS23 — Géolocalisation & Télémétrie Samsung S23 → Raspberry Pi 5

**Suivi de Position et de Télémétrie** (vitesse, batterie, accéléromètre,luminosité, Wi-Fi, réseau mobile) 
d'un Samsung S23 depuis un Raspberry Pi 5, via un tunnel WireGuard chiffré. 
Le téléphone envoie périodiquement ses données à un petit serveur Flask hébergé sur le Pi5, qui les enregistre
et permet de les visualiser (carte interactive + tableau de bord technique) puis de les analyser :
détection des **lieux fréquentés** et du temps passé dans chacun.

## Fonctionnement général

```
[Samsung S23]                      [Raspberry Pi 5]
  Termux + Termux:API        --->    serveur_geoloc.py (Flask, service systemd)
  envoyer_position.py                positions.log
  (déclenché par                     telemetrie.log
   termux-job-scheduler,             lieux.json (lieux nommés, créé par le Tracker)
   via tunnel WireGuard)             Tracker_S23.py (Tkinter, onglets
                                      Carte + Télémétrie + Lieux)
```

Le téléphone n'a besoin d'aucune connexion Wi-Fi partagée avec le Pi5 : le
tunnel WireGuard fonctionne aussi bien à la maison qu'en 4G/5G en mobilité.

`envoyer_position.py` est un script **one-shot** (il collecte, envoie, puis se termine) 
déclenché toutes les X heures par `termux-job-scheduler`, l'API de planification d'Android. 
C'est Android lui-même qui réveille brièvement Termux au bon moment, exécute le script, 
puis le relâche — pas de processus qui tourne en continu en arrière-plan, donc une consommation
de batterie minimale, tout en restant fiable même écran éteint sur de longues périodes.

## Prérequis

### Sur le Raspberry Pi 5

- Python 3 avec Flask (souvent déjà présent sur Raspberry Pi OS)
- `pip install pillow tkintermapview --break-system-packages`
- Un tunnel WireGuard fonctionnel entre le Pi5 (serveur) et le S23 (client)
- Un accès Internet sur le Pi5 pour le fond de carte OpenStreetMap et, en option, la
  recherche d'adresse de l'onglet Lieux (aucune dépendance Python supplémentaire :
  la recherche d'adresse utilise uniquement la bibliothèque standard)

### Sur le Samsung S23

- **Termux**, installé **depuis F-Droid** (`f-droid.org/packages/com.termux`),
  pas depuis le Play Store — la version Play Store n'est plus maintenue par
  l'éditeur et a divergé du code source officiel.
- **Termux:API** (même source, F-Droid), plugin nécessaire pour accéder au
  GPS et aux capteurs depuis Termux.
- L'application WireGuard officielle, avec un tunnel déjà configuré vers le Pi5.
- *(Termux:Boot n'est plus indispensable pour la collecte elle-même —
  `termux-job-scheduler --persisted true` survit déjà à un redémarrage.
  Utile uniquement si vous ajoutez d'autres scripts à lancer au boot.)*

## Installation

### 1. Serveur sur le Pi5

```bash
mkdir -p ~/Projects/GeolocS23
cd ~/Projects/GeolocS23
# copier serveur_geoloc.py, Tracker_S23.py, et le dossier icons/ ici
# (lieux.json sera créé automatiquement par le Tracker au premier lieu nommé)
```

Dans `serveur_geoloc.py`, l'adresse d'écoute doit correspondre à l'IP
WireGuard du Pi5 (visible avec `ip a`, interface `wg0`) :

```python
app.run(host="10.221.90.1", port=5000)  # à adapter à votre IP wg0
```

Le serveur enregistre deux fichiers séparés :
- `positions.log` : historique des positions (`horodatage,lat,lon`)
- `telemetrie.log` : vitesse, batterie, accéléromètre, luminosité, Wi-Fi,réseau 
  une ligne par envoi, colonnes fixes (valeur vide si un capteur a échoué côté S23, jamais de colonne manquante)

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

**Après toute mise à jour de `serveur_geoloc.py`**, redémarrer le service
pour que le changement soit pris en compte :
```bash
sudo systemctl restart geoloc-serveur.service
```

### 2. Script d'envoi sur le S23 (Termux)

```bash
pkg update && pkg upgrade
pkg install nano python termux-api
pip install requests
```

Copier `envoyer_position.py` dans `~/` sur le S23.
Rendre le script exécutable, avec un shebang pointant vers le Python de Termux 
(indispensable pour `termux-job-scheduler`, qui lance le script comme un exécutable) :
```bash
chmod +x ~/envoyer_position.py
```
La première ligne du fichier doit être :
```python
#!/data/data/com.termux/files/usr/bin/python3
```

Test manuel avant automatisation :
```bash
python3 -u ~/envoyer_position.py
cat ~/geoloc.log
```
Une ligne `Envoyé : {'status': 'ok'}` doit apparaître.

### 3. Automatisation avec termux-job-scheduler

**a) WireGuard toujours actif**
Paramètres → Connexions → Plus de paramètres de connexion → VPN → icône ⚙️
à côté du tunnel → activer **« VPN toujours actif »**. Le tunnel se reconnecte 
alors automatiquement, y compris après un redémarrage.

**b) Planifier la collecte périodique** (voir aussi `ConfigS23.txt`) :
```bash
termux-job-scheduler --job-id 1001 --script ~/envoyer_position.py \
  --period-ms 28800000 --network any --persisted true
```
- `--job-id 1001` : identifiant du job ; relancer la commande avec le même
  id remplace le job existant plutôt que d'en créer un doublon.
- `--period-ms` : intervalle en millisecondes (28 800 000 = 8h). Minimum
  accepté par Android : 900 000 ms (15 min). Android choisit un moment
  approximatif dans une fenêtre autour de cette période — ne pas s'attendre
  à un déclenchement pile à l'heure.
- `--network any` : le job attend qu'une connexion réseau soit disponible
  avant de s'exécuter (Wi-Fi ou données mobiles).
- `--persisted true` : le job survit à un redémarrage du téléphone.

La fenêtre **« Réglages Freq. Datas (S23) »** de `Tracker_S23.py` génère
cette commande automatiquement (minutes / fois par jour / fois par semaine
/ fois par mois) et permet de la copier directement dans le presse-papiers.

Vérifier que le job est bien enregistré :
```bash
termux-job-scheduler --pending
```

**c) Permissions Android nécessaires**

- **Termux:API** → Paramètres → Position → Autorisations de l'application →
  **« Autoriser tout le temps »** (indispensable : sans cette autorisation
  en arrière-plan, `termux-location` expire après un redémarrage).
- **Termux:API** → Autorisations → **Téléphone** → autoriser (nécessaire
  pour `termux-telephony-deviceinfo` : opérateur, type de réseau).
- **Termux** et **Termux:API** → Batterie → **« Non restreint »** (évite
  qu'Android limite l'exécution du job en arrière-plan). Sur les Samsung,
  vérifier aussi *Paramètres → Maintenance de l'appareil → Batterie →
  Limites d'utilisation en arrière-plan* pour ces deux applications.

**d) Test**

Redémarrer le S23, attendre le déverrouillage, puis (après le prochain cycle planifié) vérifier :
```bash
cat ~/geoloc.log
```
Une ligne `Envoyé : {'status': 'ok'}` doit apparaître sans qu'aucune action manuelle n'ait été faite,
et `positions.log` / `telemetrie.log` doivent recevoir une nouvelle ligne côté Pi5. 
**Compter au moins 24h avant de juger la stabilité du réglage** : il faut voir plusieurs cycles passer, y compris
un cycle de nuit écran éteint, pour confirmer qu'aucun capteur ne timeout.

### Gestion du job (rappel)

```bash
termux-job-scheduler --pending          # lister les jobs en attente
kill <PID>                              # arrêter un envoyer_position.py resté actif (rare, one-shot)
python3 ~/envoyer_position.py           # déclencher un envoi manuel immédiat, sans attendre le prochain cycle
```

Purge périodique des logs sur le S23 (à faire de temps en temps, pas de rotation automatique) :
```bash
> ~/geoloc.log
```

## Télémétrie collectée

En plus de la position GPS, chaque envoi tente de récupérer (chaque source
est indépendante : l'échec d'un capteur n'empêche jamais l'envoi de la
position) :

| Donnée        | Source Termux:API                                    | Remarque                                                                                                                                                                                                                              |
|---            |---                                                   |---                                                                                                                                                                                                                                    |
| Vitesse       | champ `speed` du JSON `termux-location`              | en km/h, fiabilité variable selon le provider GPS/réseau                                                                                                                                                                              |
| Batterie      | `termux-battery-status`                              | pourcentage, statut de charge, température                                                                                                                                                                                            |
| Accéléromètre | `termux-sensor -s accelerometer -n 1`                | **instantané simple** (une mesure au moment de l'envoi), pas un flux continu — reste compatible avec le fonctionnement one-shot du job-scheduler. Sert aussi à calculer l'indicateur Stable/En mouvement (voir onglet Télémétrie)     |
| Luminosité    | `termux-sensor -s "STK33911 Light  Non-wakeup" -n 1` | nom de capteur **spécifique au modèle de téléphone** (plusieurs capteurs "Light" existent sur le S23) — à vérifier avec `termux-sensor -l` sur un autre appareil                                                                      |
| Wi-Fi         | `termux-wifi-connectioninfo`                         | SSID + RSSI, vide si pas de Wi-Fi connecté (téléphone en 4G/5G)                                                                                                                                                                       |
| Réseau mobile | `termux-telephony-deviceinfo`                        | opérateur (`network_operator_name`) et type de réseau (`network_type`, ex. `"lte"`)                                                                                                                                                   |

## Tracker_S23.py (remplace l'ancien `visualiseur_geoloc.py` - version de tests)

Interface Tkinter à trois onglets (**Carte**, **Télémétrie**, **Lieux**), à lancer sur le Pi5 :

```bash
python3 Tracker_S23.py
```

### Onglet Carte

- Carte OpenStreetMap avec trajet tracé (ligne fine bleu discret) et marqueurs, dernière position étiquetée « Position actuelle ».
- Liste latérale de l'historique (date/heure, latitude, longitude) avec ascenseur — affiche **toutes** les positions de la 
  période sélectionnée (contrairement à la carte, volontairement simplifiée pour rester lisible).
- **Filtre par période** : toutes les positions, dernières 24h, 7 ou 30 derniers jours, ou plage personnalisée choisie directement
  parmi les horodatages déjà enregistrés (menus déroulants, pas de saisie libre).
- **Regroupement adaptatif** (carte uniquement) : automatique (1 point par heure sur une semaine, par jour sur un mois, etc.), 
  ou choix manuel (tous les points / 1 par heure / 1 par jour). Le tracé du trajet utilise toujours l'ensemble des points de la période.
- **Déduplication spatiale** (carte uniquement) : si le téléphone reste immobile, seule la dernière position de la zone stationnaire 
  (< 30 m) est affichée, pour éviter les marqueurs superposés.
- **Bouton « Effacer les positions »** : vide `positions.log` (avec confirmation).

### Onglet Télémétrie

- Bandeau résumé affichant la dernière mesure connue : vitesse, batterie, accélération (x, y, z), 
  **indicateur Stable / En mouvement**, luminosité, Wi-Fi, réseau mobile.
- L'indicateur de mouvement est calculé à partir de la norme du vecteur accélération (`√(x²+y²+z²)`) :
  proche de 9,8 m/s² (gravité seule) → **Stable** (badge vert) ; écart de plus de 1,5 m/s² → **En mouvement**
  (badge rouge). Comme la mesure est un instantané unique par cycle, c'est un indicateur ponctuel 
  (l'état du téléphone à l'instant précis de l'envoi), pas un suivi d'activité continu. 
  Le même indicateur apparaît aussi en colonne dans le tableau d'historique.
- Tableau d'historique, avec le même système de filtre que l'onglet Carte :
  période prédéfinie (24h / 7j / 30j) ou **Personnalisée** avec sélection
  Du/Au parmi les horodatages enregistrés, validée par le bouton **Appliquer**.
- **Bouton « Effacer la télémétrie »** : vide `telemetrie.log` (avec confirmation, sans toucher à `positions.log`).

### Onglet Lieux

Détecte les endroits où le téléphone séjourne (*stay points*) et calcule le **temps passé par lieu**, 
à partir de `positions.log` uniquement.

**Principe**

- Un **séjour** regroupe des relevés consécutifs proches (dans le *rayon d'un lieu*, 100 m par défaut, autour du centre du groupe). 
  Un nouveau séjour démarre si le relevé est trop loin, ou si l'écart avec le relevé précédent dépasse l'*écart max* (48 h par défaut) :
  on ne suppose pas que le téléphone est resté sur place pendant un trou de données.
- Un **lieu** regroupe les séjours proches les uns des autres (même rayon). Son centre est la moyenne des relevés, pondérée par leur nombre.
- Les relevés étant espacés (3 par jour avec le réglage par défaut), l'heure exacte d'arrivée ou de départ est inconnue. 
  Chaque lieu a donc **deux durées** :
  - **Mesuré** : du premier au dernier relevé du séjour (minimum garanti) ;
  - **Estimé** : le mesuré + la moitié de l'intervalle avant et de l'intervalle
    après le séjour. Fiable pour les longs séjours (domicile, bureau), grossier
    pour un passage isolé (un seul relevé : mesuré à 0 min, mais estimé à la
    moitié des deux intervalles voisins).
- Les intervalles plus longs que l'écart max ne sont attribués à aucun lieu : ils forment les **trous de données**.

**Affichage**

- Ligne de synthèse : nombre de lieux, nombre de relevés, période analysée, temps attribué à des lieux (avec son pourcentage) et trous de données.
- Tableau **Lieux fréquentés**, du plus long au plus court : nombre de séjours, durée mesurée, durée estimée, part (durée estimée / période analysée).
- Tableau **Séjours du lieu sélectionné** (du plus récent au plus ancien) :
  arrivée, départ, durées mesurée et estimée, nombre de relevés.
- Carte OpenStreetMap avec un marqueur par lieu (nom + durée estimée ; marqueur gris pour un lieu non nommé). 
  Sélectionner un lieu dans la liste centre la carte dessus.
- Paramètres : **période** (toutes les positions, 24 h, 7 ou 30 derniers jours),
  **rayon d'un lieu** (m) et **écart max entre 2 relevés** (h), appliqués par le bouton **Appliquer** (ou **Rafraîchir**).

**Nommer, déplacer, retrouver l'adresse d'un lieu**

- **Nommer le lieu…** : associe un nom (Domicile, Bureau…) au lieu sélectionné.
  Le nom et la position sont enregistrés dans `lieux.json`. Donner un nom déjà utilisé à un autre lieu 
  **fusionne** les deux en une seule ligne. Sur un lieu déjà nommé, la même commande le renomme.
- **Retirer le nom** : supprime le nom (avec confirmation).
- **Déplacer le lieu** : pour affiner une position approximative.
  1. Sélectionner un lieu **nommé**, cliquer sur **Déplacer le lieu**.
  2. Cliquer sur la carte à l'endroit voulu (un simple clic, sans faire glisser la carte : 
     on peut zoomer et se déplacer avant). Un marqueur bleu « Nouvelle position » apparaît.
  3. Valider la confirmation, qui indique la distance de déplacement :
     `lieux.json` est mis à jour. **Échap**, le bouton « Annuler le déplacement »,
     un rafraîchissement ou un changement de sélection annulent le mode.
  Pour un lieu nommé, la carte utilise la **position enregistrée** dans `lieux.json`
  (et non le centre calculé des relevés). Si la nouvelle position est plus éloignée
  du centre des relevés que le rayon du lieu, **le rayon est augmenté automatiquement** 
  (la confirmation l'indique) : sinon les relevés ne seraient plus rattachés au nom et 
  le lieu redeviendrait « Non nommé ». Un lieu non nommé n'a pas de position enregistrée : le nommer d'abord.
- **Adresse (OSM)** : interroge le service Nominatim d'OpenStreetMap pour afficher
  l'adresse approximative du lieu sélectionné, afin d'aider à le nommer. Requête lancée uniquement au clic
  (les coordonnées du lieu sont envoyées à OpenStreetMap), en tâche de fond pour ne pas bloquer l'interface,
  résultat mis en cache pendant la session.

**Fichier `lieux.json`**

```json
[
  {"nom": "Domicile", "lat": 49.18295, "lon": -0.37065, "rayon": 100},
  {"nom": "Bureau",   "lat": 49.2050,  "lon": -0.3505,  "rayon": 150}
]
```

Un lieu calculé prend le nom de l'entrée la plus proche dont le rayon (en m) contient son centre. 
Le fichier est relu à chaque rafraîchissement : il peut être édité à la main. Il est écrit de façon atomique. 
S'il est illisible (JSON invalide), il est ignoré : **en faire une copie de sauvegarde** avant de l'éditer,
car le prochain nommage le réécrirait. Il n'est pas concerné par les boutons « Effacer » des autres onglets.

**Quand l'onglet se met à jour** : au premier affichage, puis à nouveau à l'ouverture suivante après un 
rafraîchissement ou un effacement des positions depuis l'onglet Carte. Sinon, utiliser **Rafraîchir** ou **Appliquer**.

### Réglages Freq. Datas (S23) — accessible depuis tous les onglets

Calculateur d'intervalle de collecte, quatre modes au choix :
- toutes les **X minutes** (minimum 15, limite Android),
- **X fois par jour**,
- **X fois par semaine**,
- **X fois par mois** (approximé à 30 jours).

Génère la commande `termux-job-scheduler` complète à reporter dans Termux, avec un bouton **Copier** pour la mettre 
directement dans le presse-papiers. Cette fenêtre ne modifie rien sur le téléphone — c'est un calculateur, la
commande doit toujours être exécutée manuellement dans Termux (ou copiée puis collée dans une session SSH vers le S23).

Pas de rafraîchissement automatique : utiliser le bouton « Rafraîchir » ou « Appliquer » pour recharger les dernières
données (l'onglet Lieux se recalcule aussi à l'ouverture, voir plus haut).

## Fiabilité de la connexion sur de longues périodes d'inactivité

Sur un réseau mobile, un tunnel WireGuard resté inactif plusieurs heures (ex. la nuit, téléphone immobile) peut voir
sa correspondance NAT fermée côté opérateur, sans que WireGuard ne le détecte automatiquement. La condition 
`--network any` du job-scheduler limite ce risque (Android attend qu'une connectivité soit rapportée comme disponible
avant de déclencher le job), mais ne garantit pas que le tunnel WireGuard lui-même soit encore ouvert.

**Diagnostic :**
```bash
sudo wg show   # sur le Pi5 : regarder "latest handshake" pour le peer S23
sudo journalctl -u geoloc-serveur.service --since "<date>" --until "<date>"
```
Si le Pi5 tourne sans interruption (`uptime`) mais que le handshake date d'avant les erreurs, la coupure vient du S23, pas du Pi5.

L'onglet **Lieux** du Tracker chiffre ces coupures : la ligne de synthèse indique le temps de « trous de données » 
(intervalles plus longs que l'écart max) sur la période analysée, ce qui permet de mesurer l'effet d'un changement de réglage 
(fréquence,`PersistentKeepalive`).

**Options possibles (compromis autonomie vs continuité des données) :**
- Activer `PersistentKeepalive = 25` (ou une valeur plus espacée, ex. `120`)
  dans la configuration WireGuard du S23 — maintient le tunnel ouvert en
  continu, au prix d'une légère consommation batterie supplémentaire.
- Réduire la fréquence de collecte (voir « Réglages Freq. Datas ») et
  accepter l'absence de données pendant les longues périodes d'inactivité
  si le suivi nocturne n'est pas essentiel — c'est le choix retenu par
  défaut dans ce projet (8h entre chaque collecte, sans keepalive
  permanent).

## Dépannage rapide

| Symptôme                                                         | Cause probable                                                                                                                                                                                                                                        |
|---                                                               |---                                                                                                                                                                                                                                                    |
| `termux-location` timeout                                        | Permission de localisation de Termux:API pas réglée sur « Tout le temps », ou optimisation batterie active                                                                                                                                            |
| `Cannot execute file` au lancement du job                        | Script pas rendu exécutable (`chmod +x`), ou shebang absent/incorrect (`#!/data/data/com.termux/files/usr/bin/python3`)                                                                                                                               |
| `getopt: unrecognized option` sur `termux-job-scheduler`         | Flag invalide — c'est `--network` (pas `--network-type`)                                                                                                                                                                                              |
| Le script semble ne rien faire (code de sortie 0, log inchangé)  | Fichier tronqué par un collage `nano` incomplet — vérifier avec `wc -l ~/envoyer_position.py` (~140 lignes attendues), privilégier le téléchargement du fichier plutôt que le copier-coller                                                           |
| `termux-sensor -s "..."` renvoie `{}`                            | Nom de capteur incorrect ou sensible à la casse — lister les capteurs disponibles avec `termux-sensor -l` et ajuster le nom exact dans `envoyer_position.py`                                                                                          |
| Champ `type_reseau` toujours vide en télémétrie                  | Vérifier que la permission Téléphone est accordée à Termux:API, et que le script utilise bien la clé `network_type` (pas `data_network_type`) du JSON de `termux-telephony-deviceinfo`                                                                |
| Connexion refusée / timeout côté S23                             | Tunnel WireGuard non actif sur le S23 au moment de l'envoi                                                                                                                                                                                            |
| `Connection timed out` après plusieurs heures OK                 | NAT mobile ayant fermé le tunnel WireGuard inactif — voir section « Fiabilité de la connexion » ci-dessus                                                                                                                                             |
| Le Pi5 ne reçoit rien alors que le S23 envoie bien               | Vérifier que `geoloc-serveur.service` est actif (`systemctl status`), et que l'IP dans les deux scripts correspond bien à l'IP `wg0` du Pi5                                                                                                           |
| Un lieu affiche « Non nommé » alors qu'il a été nommé            | Le centre calculé des relevés est hors du rayon du lieu enregistré dans `lieux.json` (GPS imprécis, rayon d'analyse modifié) — augmenter `rayon` dans le fichier, ou déplacer le lieu depuis l'onglet Lieux (le rayon s'adapte alors automatiquement) |
| « Adresse indisponible : HTTP Error 403 » (bouton Adresse (OSM)) | Le Pi5 n'a pas d'accès Internet, ou un proxy/pare-feu bloque `nominatim.openstreetmap.org` — la fonction est facultative, le reste de l'onglet n'en dépend pas                                                                                        |
| Tous les noms de lieux ont disparu                               | `lieux.json` illisible (JSON invalide après une édition manuelle) : il est alors ignoré — le corriger ou restaurer la sauvegarde                                                                                                                      |
| `> ~/geoloc.log` renvoie "Permission denied"                     | Vérifier que le `>` est bien tapé avant le chemin (sans lui, le shell essaie d'exécuter le fichier au lieu de le vider)                                                                                                                               |

## Structure du projet

```
GeolocS23/
├── serveur_geoloc.py       # serveur Flask sur le Pi5
├── Tracker_S23.py          # interface Tkinter (onglets Carte + Télémétrie + Lieux)
├── positions.log           # historique des positions (horodatage,lat,lon)
├── telemetrie.log          # historique de la télémétrie (horodatage,vitesse,...)
├── lieux.json              # lieux nommés (nom, lat, lon, rayon) — créé par l'onglet Lieux
├── icons/
│   └── gps-phone.png
└── geoloc-serveur.service  # unité systemd (à copier dans /etc/systemd/system/)
```

*(Côté S23, dans Termux : `envoyer_position.py` et le job `termux-job-scheduler`
voir `ConfigS23.txt` pour la liste des commandes utiles.)*

## Auteur
**Jean-François Brunet** — [JFBConseils](https://github.com/JeanFrancoisBrunet)
Consultant Lean Management — projet personnel sur Raspberry Pi 5 *Septembre 2026*
