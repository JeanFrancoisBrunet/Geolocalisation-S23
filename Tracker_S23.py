#!/usr/bin/env python3
# ======================================================================================================================
#  Tracker S23 - Suivi de Position & de Télémétrie du Samsung S23 sous Wireguard
#  Onglet "Carte" : positions envoyées par le S23 (positions.log)
#  Onglet "Télémétrie" : vitesse, batterie, accéléromètre, wifi, réseau, luminosité (telemetrie.log)
#  Onglet "Lieux" : détection des lieux de séjour (stay points) et temps passé par lieu
#                   (calculé à partir de positions.log ; noms des lieux mémorisés dans lieux.json)
#
#  Raspberry Pi 5 (16 Go RAM, SSD NVMe 256 Go / 1 To, OS Bookworm)
#
#  Réglage de la fréquence de collecte : voir le bouton "Réglages Freq. Datas (S23)" 
#  et la commande termux-job-scheduler sur le S23 (ci-dessous 3 envois / jour)
#  termux-job-scheduler --job-id 1001 --script ~/envoyer_position.py --period-ms 28800000 --network any --persisted true
#
# Lancer manuellement un envoi de données à partir du S23
# python3 ~/envoyer_position.py
#
# Lire sur le S23 le fichier geoloc.log 
# cat ~/geoloc.log 
#
# Purge sur S23 le fichier geoloc.log (à faire de temps en temps)
# > ~/geoloc.log
#
#  Auteur : Jean-François BRUNET – JFBConseils – Septembre 2026
# ======================================================================================================================

import os
import json
import math
import queue
import threading
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime, timedelta

try:
    from PIL import Image, ImageTk
except ImportError:
    raise SystemExit("Il manque Pillow : pip install pillow --break-system-packages")

try:
    import tkintermapview
except ImportError:
    raise SystemExit("Il manque tkintermapview : pip install tkintermapview --break-system-packages")

DOSSIER_PROJET = os.path.dirname(os.path.abspath(__file__))
FICHIER_POSITIONS = os.path.join(DOSSIER_PROJET, "positions.log")
FICHIER_TELEMETRIE = os.path.join(DOSSIER_PROJET, "telemetrie.log")
FICHIER_LIEUX = os.path.join(DOSSIER_PROJET, "lieux.json")
DOSSIER_ICONS = os.path.join(DOSSIER_PROJET, "icons")
FICHIER_ICONE = os.path.join(DOSSIER_ICONS, "gps-phone.png")

# Doit correspondre exactement à COLONNES_TELEMETRIE dans serveur_geoloc.py
COLONNES_TELEMETRIE = [
    "vitesse_kmh",
    "batterie_pct", "batterie_statut", "batterie_temp",
    "accel_x", "accel_y", "accel_z",
    "luminosite_lux",
    "wifi_ssid", "wifi_rssi",
    "operateur", "type_reseau",
]

def parser_horodatage(horodatage):
    """Convertit une chaîne 'YYYY-MM-DD HH:MM:SS' du log en objet datetime."""
    return datetime.strptime(horodatage, "%Y-%m-%d %H:%M:%S")

def filtrer_par_periode(entrees, debut, fin):
    """Filtre une liste d'entrées dont le premier élément est l'horodatage (str).
    Renvoie les entrées inchangées, plus un datetime en dernière position."""
    resultat = []
    for entree in entrees:
        dt = parser_horodatage(entree[0])
        if debut and dt < debut:
            continue
        if fin and dt > fin:
            continue
        resultat.append(entree + (dt,))
    return resultat

def choisir_regroupement_auto(entrees_avec_dt):
    """Détermine automatiquement une granularité d'affichage selon l'étendue des dates."""
    if len(entrees_avec_dt) < 2:
        return "tous"
    etendue = entrees_avec_dt[-1][-1] - entrees_avec_dt[0][-1]
    if etendue <= timedelta(days=1):
        return "tous"
    elif etendue <= timedelta(days=31):
        return "heure"
    else:
        return "jour"

def regrouper_entrees(entrees_avec_dt, mode):
    """Réduit le nombre d'entrées affichées en gardant la dernière de chaque période."""
    if mode == "tous" or len(entrees_avec_dt) < 2:
        return entrees_avec_dt

    par_cle = {}
    for entree in entrees_avec_dt:
        dt = entree[-1]
        cle = dt.strftime("%Y-%m-%d %H") if mode == "heure" else dt.strftime("%Y-%m-%d")
        par_cle[cle] = entree  # écrase avec le plus récent de la période

    return [par_cle[cle] for cle in sorted(par_cle.keys())]

SEUIL_DEDUPLICATION_METRES = 30  # positions plus proches que ça = considérées comme "sur place"

def distance_metres(lat1, lon1, lat2, lon2):
    """Distance approximative en mètres entre deux points GPS (formule de Haversine)."""
    from math import radians, sin, cos, sqrt, atan2
    rayon_terre = 6371000
    phi1, phi2 = radians(lat1), radians(lat2)
    delta_phi = radians(lat2 - lat1)
    delta_lambda = radians(lon2 - lon1)
    a = sin(delta_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta_lambda / 2) ** 2
    return 2 * rayon_terre * atan2(sqrt(a), sqrt(1 - a))

GRAVITE_MS2 = 9.8          # accélération terrestre au repos, quelle que soit l'orientation du téléphone
SEUIL_MOUVEMENT_MS2 = 1.5  # écart toléré par rapport à la gravité avant de considérer "en mouvement"

def calculer_norme_acceleration(x, y, z):
    """Magnitude du vecteur accélération. Proche de GRAVITE_MS2 au repos ;
    un écart plus important signale un mouvement/choc au moment précis de la mesure."""
    from math import sqrt
    return sqrt(x ** 2 + y ** 2 + z ** 2)

def evaluer_mouvement(norme):
    """Renvoie 'Stable' ou 'En mouvement' selon l'écart à la gravité au repos."""
    if abs(norme - GRAVITE_MS2) > SEUIL_MOUVEMENT_MS2:
        return "En mouvement"
    return "Stable"

def dedupliquer_positions_stationnaires(positions_avec_dt, seuil_metres=SEUIL_DEDUPLICATION_METRES):
    """Quand plusieurs positions consécutives sont proches (téléphone immobile), ne garde
    que la dernière de chaque groupe stationnaire, pour éviter les marqueurs superposés."""
    if len(positions_avec_dt) < 2:
        return positions_avec_dt

    resultat = []
    for i in range(1, len(positions_avec_dt)):
        _, lat_ref, lon_ref, _ = positions_avec_dt[i - 1]
        _, lat_actuelle, lon_actuelle, _ = positions_avec_dt[i]
        if distance_metres(lat_ref, lon_ref, lat_actuelle, lon_actuelle) > seuil_metres:
            resultat.append(positions_avec_dt[i - 1])  # fin du groupe stationnaire précédent
    resultat.append(positions_avec_dt[-1])             # dernière position (fin du dernier groupe)
    return resultat

def lire_positions():
    """Lit positions.log et renvoie une liste de tuples (horodatage, lat, lon)."""
    positions = []
    if not os.path.exists(FICHIER_POSITIONS):
        return positions
    with open(FICHIER_POSITIONS, "r") as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                horodatage, lat, lon = ligne.split(",")
                positions.append((horodatage, float(lat), float(lon)))
            except ValueError:
                continue
    return positions

def effacer_positions():
    """Vide positions.log (le fichier est conservé, mais vidé)."""
    with open(FICHIER_POSITIONS, "w"):
        pass

def lire_telemetrie():
    """Lit telemetrie.log et renvoie une liste de tuples
    (horodatage, vitesse_kmh, batterie_pct, ..., type_reseau) — champs texte,
    conversion faite à l'affichage pour tolérer les valeurs manquantes."""
    entrees = []
    if not os.path.exists(FICHIER_TELEMETRIE):
        return entrees
    with open(FICHIER_TELEMETRIE, "r") as f:
        for ligne in f:
            ligne = ligne.rstrip("\n")
            if not ligne:
                continue
            champs = ligne.split(",")
            if len(champs) != len(COLONNES_TELEMETRIE) + 1:  # +1 pour l'horodatage
                continue  # ligne mal formée (ancien format, coupure d'écriture...) : ignorée
            entrees.append(tuple(champs))
    return entrees

def effacer_telemetrie():
    """Vide telemetrie.log (le fichier est conservé, mais vidé)."""
    with open(FICHIER_TELEMETRIE, "w"):
        pass

def formater_champ(valeur, suffixe=""):
    """Affiche '—' pour un champ vide plutôt qu'une chaîne vide illisible."""
    if valeur is None or valeur == "" or valeur == "None":
        return "—"
    return f"{valeur}{suffixe}"

# ----------------------------------------------------------------------------------------------------------------------
#  Détection des lieux de séjour (stay points) et temps passé par lieu
# ----------------------------------------------------------------------------------------------------------------------
RAYON_LIEU_DEFAUT_METRES = 100   # relevés plus proches que ça = même séjour ; séjours plus proches = même lieu
ECART_MAX_DEFAUT_HEURES = 48     # au-delà, l'intervalle entre 2 relevés est un "trou de données" (non attribué)

def formater_duree(duree):
    """Durée lisible : '3 j 05 h', '5 h 20' ou '12 min'."""
    total_minutes = int(duree.total_seconds() // 60)
    jours, reste = divmod(total_minutes, 1440)
    heures, minutes = divmod(reste, 60)
    if jours:
        return f"{jours} j {heures:02d} h"
    if heures:
        return f"{heures} h {minutes:02d}"
    return f"{minutes} min"

def duree_mesuree(sejour):
    """Durée entre le premier et le dernier relevé du séjour (minimum garanti)."""
    return sejour["fin"] - sejour["debut"]

def duree_estimee(sejour):
    """Durée mesurée + moitié des intervalles avant/après le séjour (les relevés étant espacés,
    on ne sait pas quand le téléphone est arrivé / reparti : on coupe l'intervalle en deux)."""
    return duree_mesuree(sejour) + sejour["avant"] + sejour["apres"]

def detecter_sejours(positions_avec_dt, rayon_metres, ecart_max):
    """Regroupe les relevés consécutifs proches (dans rayon_metres du centre du groupe) en séjours.
    positions_avec_dt : liste triée de (horodatage, lat, lon, dt).
    Un nouveau séjour démarre si le relevé est trop loin OU si l'écart avec le relevé précédent dépasse
    ecart_max (timedelta) : on ne suppose pas que le téléphone est resté sur place pendant un trou de données."""
    sejours = []
    courant = None
    for _, lat, lon, dt in positions_avec_dt:
        if courant is not None:
            trop_loin = distance_metres(courant["lat"], courant["lon"], lat, lon) > rayon_metres
            trou = (dt - courant["fin"]) > ecart_max
            if not (trop_loin or trou):
                n = courant["nb_points"]
                courant["lat"] = (courant["lat"] * n + lat) / (n + 1)
                courant["lon"] = (courant["lon"] * n + lon) / (n + 1)
                courant["nb_points"] = n + 1
                courant["fin"] = dt
                continue
            sejours.append(courant)
        courant = {"debut": dt, "fin": dt, "lat": lat, "lon": lon, "nb_points": 1,
                   "avant": timedelta(0), "apres": timedelta(0)}
    if courant is not None:
        sejours.append(courant)
    return sejours

def attribuer_ecarts(sejours, ecart_max):
    """Répartit à parts égales chaque intervalle (<= ecart_max) entre la fin d'un séjour et le début du suivant.
    Les intervalles plus longs restent non attribués (trous de données)."""
    for precedent, suivant in zip(sejours, sejours[1:]):
        ecart = suivant["debut"] - precedent["fin"]
        if ecart <= ecart_max:
            precedent["apres"] = ecart / 2
            suivant["avant"] = ecart / 2

def trouver_index_lieu_nomme(lat, lon, lieux_nommes):
    """Index (dans lieux_nommes) du lieu enregistré le plus proche dont le rayon contient (lat, lon), sinon None."""
    meilleur_index, meilleure_distance = None, None
    for index, entree in enumerate(lieux_nommes):
        d = distance_metres(lat, lon, entree["lat"], entree["lon"])
        if d <= entree["rayon"] and (meilleure_distance is None or d < meilleure_distance):
            meilleur_index, meilleure_distance = index, d
    return meilleur_index

def _ajouter_sejour_au_lieu(lieu, sejour):
    """Ajoute un séjour à un lieu et recalcule le centre (moyenne pondérée par le nombre de relevés)."""
    p, n = lieu["poids"], sejour["nb_points"]
    lieu["lat"] = (lieu["lat"] * p + sejour["lat"] * n) / (p + n)
    lieu["lon"] = (lieu["lon"] * p + sejour["lon"] * n) / (p + n)
    lieu["poids"] = p + n
    lieu["sejours"].append(sejour)

def regrouper_en_lieux(sejours, rayon_metres, lieux_nommes):
    """Regroupe les séjours proches en lieux, applique les noms enregistrés (les lieux qui portent
    le même nom sont fusionnés) et calcule les durées cumulées. Résultat trié du plus long au plus court."""
    lieux = []
    for sejour in sejours:
        candidat, meilleure_distance = None, None
        for lieu in lieux:
            d = distance_metres(lieu["lat"], lieu["lon"], sejour["lat"], sejour["lon"])
            if d <= rayon_metres and (meilleure_distance is None or d < meilleure_distance):
                candidat, meilleure_distance = lieu, d
        if candidat is None:
            lieux.append({"lat": sejour["lat"], "lon": sejour["lon"],
                          "poids": sejour["nb_points"], "sejours": [sejour]})
        else:
            _ajouter_sejour_au_lieu(candidat, sejour)

    # Rattachement aux lieux enregistrés. "appariements" garde, pour chaque groupe rattaché, l'index du lieu
    # enregistré et le centre du groupe avant fusion (utile pour ajuster le rayon lors d'un déplacement).
    for lieu in lieux:
        index = trouver_index_lieu_nomme(lieu["lat"], lieu["lon"], lieux_nommes)
        lieu["nom"] = lieux_nommes[index]["nom"] if index is not None else None
        lieu["appariements"] = [] if index is None else [(index, lieu["lat"], lieu["lon"])]

    resultat, par_nom = [], {}
    for lieu in lieux:
        nom = lieu["nom"]
        if nom is not None and nom in par_nom:
            base = par_nom[nom]
            base["appariements"].extend(lieu["appariements"])
            for sejour in lieu["sejours"]:
                _ajouter_sejour_au_lieu(base, sejour)
        else:
            if nom is not None:
                par_nom[nom] = lieu
            resultat.append(lieu)

    for lieu in resultat:
        # Position affichée : celle du lieu enregistré (affinée par l'utilisateur) sinon le centre calculé.
        if lieu["appariements"]:
            lieu["index_entree"] = lieu["appariements"][0][0]
            lieu["lat_aff"] = lieux_nommes[lieu["index_entree"]]["lat"]
            lieu["lon_aff"] = lieux_nommes[lieu["index_entree"]]["lon"]
        else:
            lieu["index_entree"] = None
            lieu["lat_aff"], lieu["lon_aff"] = lieu["lat"], lieu["lon"]
        lieu["sejours"].sort(key=lambda s: s["debut"])
        lieu["nb_sejours"] = len(lieu["sejours"])
        lieu["mesuree"] = sum((duree_mesuree(s) for s in lieu["sejours"]), timedelta(0))
        lieu["estimee"] = sum((duree_estimee(s) for s in lieu["sejours"]), timedelta(0))
        lieu["premiere"] = lieu["sejours"][0]["debut"]
        lieu["derniere"] = lieu["sejours"][-1]["fin"]
    resultat.sort(key=lambda l: l["estimee"], reverse=True)
    return resultat

def analyser_lieux(positions_avec_dt, rayon_metres, ecart_max_heures, lieux_nommes):
    """Chaîne complète : séjours -> intervalles -> lieux. Renvoie (lieux, resume).
    resume : nb_releves, nb_sejours, etendue (durée totale analysée), attribue (temps rattaché à des lieux)
    et trous (temps non attribué : intervalles plus longs que l'écart max)."""
    if not positions_avec_dt:
        return [], {"nb_releves": 0, "nb_sejours": 0, "etendue": timedelta(0),
                    "attribue": timedelta(0), "trous": timedelta(0)}
    triees = sorted(positions_avec_dt, key=lambda p: p[-1])
    ecart_max = timedelta(hours=ecart_max_heures)
    sejours = detecter_sejours(triees, rayon_metres, ecart_max)
    attribuer_ecarts(sejours, ecart_max)
    lieux = regrouper_en_lieux(sejours, rayon_metres, lieux_nommes)
    etendue = triees[-1][-1] - triees[0][-1]
    attribue = sum((l["estimee"] for l in lieux), timedelta(0))
    return lieux, {"nb_releves": len(triees), "nb_sejours": len(sejours), "etendue": etendue,
                   "attribue": attribue, "trous": max(etendue - attribue, timedelta(0))}

def charger_lieux_nommes():
    """Lit lieux.json : liste de {nom, lat, lon, rayon}. Renvoie [] si absent ou illisible."""
    if not os.path.exists(FICHIER_LIEUX):
        return []
    try:
        with open(FICHIER_LIEUX, "r", encoding="utf-8") as f:
            donnees = json.load(f)
    except (OSError, ValueError):
        return []
    lieux = []
    for entree in (donnees if isinstance(donnees, list) else []):
        try:
            lieux.append({"nom": str(entree["nom"]), "lat": float(entree["lat"]), "lon": float(entree["lon"]),
                          "rayon": float(entree.get("rayon", RAYON_LIEU_DEFAUT_METRES))})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return lieux

def sauver_lieux_nommes(lieux):
    """Écrit lieux.json de façon atomique (fichier temporaire puis remplacement)."""
    temporaire = FICHIER_LIEUX + ".tmp"
    with open(temporaire, "w", encoding="utf-8") as f:
        json.dump(lieux, f, ensure_ascii=False, indent=2)
    os.replace(temporaire, FICHIER_LIEUX)

def adresse_depuis_osm(lat, lon):
    """Adresse approximative d'un point via Nominatim (OpenStreetMap). Appel réseau : à lancer dans un thread."""
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "zoom": 18, "accept-language": "fr"}
    )
    requete = urllib.request.Request(url, headers={"User-Agent": "TrackerS23/1.0 (usage personnel)"})
    with urllib.request.urlopen(requete, timeout=10) as reponse:
        donnees = json.load(reponse)
    return donnees.get("display_name", "Adresse introuvable")

class FenetreReglagesCollecte(tk.Toplevel):
    """Calculateur d'intervalle de collecte, à reporter dans termux-job-scheduler
    sur le S23 (voir ConfigS23.txt). Cette fenêtre ne modifie rien sur le téléphone : 
    elle aide juste à choisir et copier la bonne valeur en ms."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Réglages Freq. Datas (S23)")
        self.geometry("680x420")
        self.minsize(680, 420)
        self.resizable(True, True)
        self.transient(parent)

        ttk.Label(
            self, text="Choisissez un mode pour calculer l'intervalle d'envoi",
            font=("Helvetica", 11, "bold")
        ).pack(anchor="w", padx=12, pady=(12, 8))

        self.mode_var = tk.StringVar(value="minutes")

        # --- Toutes les X minutes ---
        cadre1 = ttk.Frame(self)
        cadre1.pack(fill="x", padx=12, pady=4)
        ttk.Radiobutton(
            cadre1, text="Toutes les", variable=self.mode_var, value="minutes",
            command=self.recalculer
        ).pack(side="left")
        self.spin_minutes = ttk.Spinbox(
            cadre1, from_=15, to=1440, increment=15, width=6, command=self.recalculer
        )
        self.spin_minutes.set(480)  # 8h par défaut
        self.spin_minutes.pack(side="left", padx=6)
        ttk.Label(cadre1, text="minutes  (minimum Android : 15 min)").pack(side="left")

        # --- X fois par jour ---
        cadre2 = ttk.Frame(self)
        cadre2.pack(fill="x", padx=12, pady=4)
        ttk.Radiobutton(
            cadre2, text="", variable=self.mode_var, value="jour",
            command=self.recalculer
        ).pack(side="left")
        self.spin_jour = ttk.Spinbox(
            cadre2, from_=1, to=24, width=6, command=self.recalculer
        )
        self.spin_jour.set(3)
        self.spin_jour.pack(side="left", padx=6)
        ttk.Label(cadre2, text="fois par jour").pack(side="left")

        # --- X fois par semaine ---
        cadre3 = ttk.Frame(self)
        cadre3.pack(fill="x", padx=12, pady=4)
        ttk.Radiobutton(
            cadre3, text="", variable=self.mode_var, value="semaine",
            command=self.recalculer
        ).pack(side="left")
        self.spin_semaine = ttk.Spinbox(
            cadre3, from_=1, to=21, width=6, command=self.recalculer
        )
        self.spin_semaine.set(7)
        self.spin_semaine.pack(side="left", padx=6)
        ttk.Label(cadre3, text="fois par semaine").pack(side="left")

        # --- X fois par mois ---
        cadre4 = ttk.Frame(self)
        cadre4.pack(fill="x", padx=12, pady=4)
        ttk.Radiobutton(
            cadre4, text="", variable=self.mode_var, value="mois",
            command=self.recalculer
        ).pack(side="left")
        self.spin_mois = ttk.Spinbox(
            cadre4, from_=1, to=30, width=6, command=self.recalculer
        )
        self.spin_mois.set(30)
        self.spin_mois.pack(side="left", padx=6)
        ttk.Label(cadre4, text="fois par mois").pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=12)

        ttk.Label(
            self, text="Commande à reporter dans Termux (remplace le job existant) :"
        ).pack(anchor="w", padx=12)

        cadre_commande = ttk.Frame(self)
        cadre_commande.pack(fill="x", padx=12, pady=(4, 4))
        self.entree_resultat = ttk.Entry(cadre_commande, font=("Courier", 10))
        self.entree_resultat.pack(side="left", fill="x", expand=True)
        self.bouton_copier = ttk.Button(cadre_commande, text="Copier", command=self.copier_commande)
        self.bouton_copier.pack(side="left", padx=(6, 0))

        self.label_equivalence = ttk.Label(self, text="", foreground="#555", wraplength=650)
        self.label_equivalence.pack(anchor="w", padx=12, fill="x")

        ttk.Label(
            self,
            text="Astuce : réduire la fréquence économise la batterie du téléphone.",
            foreground="#777"
        ).pack(anchor="w", padx=12, pady=(10, 0))

        for widget in (self.spin_minutes, self.spin_jour, self.spin_semaine, self.spin_mois):
            widget.bind("<KeyRelease>", lambda e: self.recalculer())

        self.recalculer()

    def calculer_intervalle_ms(self):
        mode = self.mode_var.get()
        if mode == "minutes":
            minutes = max(int(self.spin_minutes.get()), 15)
            return minutes * 60 * 1000, f"= une collecte toutes les {minutes} minutes"
        elif mode == "jour":
            fois = max(int(self.spin_jour.get()), 1)
            minutes = max((24 * 60) // fois, 15)
            return minutes * 60 * 1000, f"= une collecte toutes les {minutes} minutes environ"
        elif mode == "semaine":
            fois = max(int(self.spin_semaine.get()), 1)
            minutes = max((7 * 24 * 60) // fois, 15)
            return minutes * 60 * 1000, f"= une collecte toutes les {minutes} minutes environ (≈ {minutes // 60} h)"
        else:  # mois (approximation à 30 jours)
            fois = max(int(self.spin_mois.get()), 1)
            minutes = max((30 * 24 * 60) // fois, 15)
            heures = minutes // 60
            return minutes * 60 * 1000, f"= une collecte toutes les {heures} h environ (mois approximé à 30 jours)"

    def recalculer(self):
        try:
            ms, equivalence = self.calculer_intervalle_ms()
        except (ValueError, ZeroDivisionError):
            return
        commande = (
            "termux-job-scheduler --job-id 1001 --script ~/envoyer_position.py "
            f"--period-ms {ms} --network any --persisted true"
        )
        self.entree_resultat.delete(0, "end")
        self.entree_resultat.insert(0, commande)
        self.label_equivalence.config(text=equivalence)

    def copier_commande(self):
        commande = self.entree_resultat.get()
        self.clipboard_clear()
        self.clipboard_append(commande)
        self.update()  # nécessaire pour que le presse-papiers survive à la fermeture de la fenêtre
        self.bouton_copier.config(text="Copié !")
        self.after(1500, lambda: self.bouton_copier.config(text="Copier"))

class SplashScreen(tk.Toplevel):
    def __init__(self, parent, duree_ms, callback_fin):
        super().__init__(parent)
        self.overrideredirect(True)
        self.configure(bg="white")

        largeur, hauteur = 400, 400
        x = (self.winfo_screenwidth() // 2) - (largeur // 2)
        y = (self.winfo_screenheight() // 2) - (hauteur // 2)
        self.geometry(f"{largeur}x{hauteur}+{x}+{y}")

        if os.path.exists(FICHIER_ICONE):
            image = Image.open(FICHIER_ICONE)
            image.thumbnail((280, 280))
            self.photo = ImageTk.PhotoImage(image)
            tk.Label(self, image=self.photo, bg="white").pack(expand=True, pady=(30, 10))
        else:
            tk.Label(self, text="[icône introuvable]", bg="white").pack(expand=True)

        tk.Label(
            self, text="Tracker S23 - Position & Télémétrie",
            font=("Helvetica", 14, "bold"), bg="white"
        ).pack(pady=(0, 10))
        tk.Label(self, text="Chargement...", font=("Helvetica", 10), bg="white").pack()

        self.after(duree_ms, lambda: (self.destroy(), callback_fin()))

class TrackerS23(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Tracker S23 - Samsung S23 & Raspberry Pi5")
        self.geometry("1280x700")
        self.minsize(1250, 550)

        if os.path.exists(FICHIER_ICONE):
            icone = Image.open(FICHIER_ICONE)
            self.icone_tk = ImageTk.PhotoImage(icone)
            self.iconphoto(True, self.icone_tk)

        self.withdraw()  # caché tant que le splash tourne
        SplashScreen(self, 2000, self.construire_interface)

    # ------------------------------------------------------------------ #
    #  Construction générale : barre du haut + onglets
    # ------------------------------------------------------------------ #
    def construire_interface(self):
        self.deiconify()

        ligne_titre = ttk.Frame(self, padding=(8, 8, 8, 4))
        ligne_titre.pack(side="top", fill="x")
        ttk.Label(
            ligne_titre, text="Tracker S23", font=("Helvetica", 13, "bold")
        ).pack(side="left")
        ttk.Button(
            ligne_titre, text="Réglages Freq. Datas (S23)", command=self.ouvrir_reglages_collecte
        ).pack(side="right")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(side="top", fill="both", expand=True)

        self.onglet_carte = ttk.Frame(self.notebook)
        self.onglet_telemetrie = ttk.Frame(self.notebook)
        self.onglet_lieux = ttk.Frame(self.notebook)
        self.notebook.add(self.onglet_carte, text="Carte")
        self.notebook.add(self.onglet_telemetrie, text="Télémétrie")
        self.notebook.add(self.onglet_lieux, text="Lieux")

        # L'onglet Lieux se calcule à la première ouverture (la carte a alors sa vraie taille),
        # puis chaque fois que les positions ont changé (rafraîchissement/effacement de l'onglet Carte).
        self.lieux_a_rafraichir = True

        self.construire_onglet_carte(self.onglet_carte)
        self.construire_onglet_telemetrie(self.onglet_telemetrie)
        self.construire_onglet_lieux(self.onglet_lieux)
        self.notebook.bind("<<NotebookTabChanged>>", self.changement_onglet)

    def ouvrir_reglages_collecte(self):
        FenetreReglagesCollecte(self)

    # ------------------------------------------------------------------ #
    #  Onglet Carte
    # ------------------------------------------------------------------ #
    def construire_onglet_carte(self, parent):
        ligne_actions = ttk.Frame(parent, padding=(8, 8, 8, 4))
        ligne_actions.pack(side="top", fill="x")

        self.label_derniere_maj = ttk.Label(ligne_actions, text="")
        self.label_derniere_maj.pack(side="right")

        ttk.Button(
            ligne_actions, text="Effacer les positions", command=self.demander_effacement_positions
        ).pack(side="left")
        ttk.Button(
            ligne_actions, text="Rafraîchir", command=self.rafraichir_carte
        ).pack(side="left", padx=(8, 0))

        ligne_filtre = ttk.Frame(parent, padding=(8, 0, 8, 8))
        ligne_filtre.pack(side="top", fill="x")

        ttk.Label(ligne_filtre, text="Période :").pack(side="left")
        self.periode_var = tk.StringVar(value="Toutes les positions")
        liste_periodes = [
            "Toutes les positions", "Dernières 24 heures",
            "7 derniers jours", "30 derniers jours", "Personnalisée",
        ]
        self.combo_periode = ttk.Combobox(
            ligne_filtre, textvariable=self.periode_var, values=liste_periodes,
            state="readonly", width=18
        )
        self.combo_periode.pack(side="left", padx=(4, 10))
        self.combo_periode.bind("<<ComboboxSelected>>", self.changer_periode)

        ttk.Label(ligne_filtre, text="Du :").pack(side="left")
        self.combo_debut = ttk.Combobox(ligne_filtre, width=16, state="disabled")
        self.combo_debut.pack(side="left", padx=(4, 8))
        ttk.Label(ligne_filtre, text="Au :").pack(side="left")
        self.combo_fin = ttk.Combobox(ligne_filtre, width=16, state="disabled")
        self.combo_fin.pack(side="left", padx=(4, 12))

        ttk.Label(ligne_filtre, text="Regroupement :").pack(side="left")
        self.regroupement_var = tk.StringVar(value="Automatique")
        self.combo_regroupement = ttk.Combobox(
            ligne_filtre, textvariable=self.regroupement_var,
            values=["Automatique", "Tous les points", "1 point / heure", "1 point / jour"],
            state="readonly", width=13
        )
        self.combo_regroupement.pack(side="left", padx=(4, 12))
        ttk.Button(ligne_filtre, text="Appliquer", command=self.rafraichir_carte).pack(side="left")

        zone_principale = ttk.Frame(parent)
        zone_principale.pack(side="top", fill="both", expand=True)

        self.widget_carte = tkintermapview.TkinterMapView(
            zone_principale, width=700, height=600, corner_radius=0
        )
        self.widget_carte.pack(side="left", fill="both", expand=True)
        self.widget_carte.set_tile_server("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")

        cadre_liste = ttk.Frame(zone_principale, width=380)
        cadre_liste.pack(side="right", fill="y")
        cadre_liste.pack_propagate(False)

        ttk.Label(cadre_liste, text="Historique", font=("Helvetica", 11, "bold")).pack(
            anchor="w", padx=8, pady=(8, 4)
        )
        cadre_arbre = ttk.Frame(cadre_liste)
        cadre_arbre.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        colonnes = ("horodatage", "lat", "lon")
        self.arbre_carte = ttk.Treeview(cadre_arbre, columns=colonnes, show="headings", height=25)
        self.arbre_carte.heading("horodatage", text="Date/heure")
        self.arbre_carte.heading("lat", text="Latitude")
        self.arbre_carte.heading("lon", text="Longitude")
        self.arbre_carte.column("horodatage", width=140)
        self.arbre_carte.column("lat", width=95)
        self.arbre_carte.column("lon", width=95)

        ascenseur = ttk.Scrollbar(cadre_arbre, orient="vertical", command=self.arbre_carte.yview)
        self.arbre_carte.configure(yscrollcommand=ascenseur.set)
        self.arbre_carte.pack(side="left", fill="both", expand=True)
        ascenseur.pack(side="right", fill="y")

        self.marqueurs = []
        self.trajet = None

        self.rafraichir_carte()

    def changer_periode(self, evenement=None):
        if self.periode_var.get() == "Personnalisée":
            self.combo_debut.configure(state="readonly")
            self.combo_fin.configure(state="readonly")
        else:
            self.combo_debut.configure(state="disabled")
            self.combo_fin.configure(state="disabled")
            self.rafraichir_carte()

    def calculer_plage_periode(self):
        maintenant = datetime.now()
        choix = self.periode_var.get()
        if choix == "Dernières 24 heures":
            return maintenant - timedelta(hours=24), maintenant
        elif choix == "7 derniers jours":
            return maintenant - timedelta(days=7), maintenant
        elif choix == "30 derniers jours":
            return maintenant - timedelta(days=30), maintenant
        elif choix == "Personnalisée":
            texte_debut = self.combo_debut.get().strip()
            texte_fin = self.combo_fin.get().strip()
            debut = parser_horodatage(texte_debut) if texte_debut else None
            fin = parser_horodatage(texte_fin) if texte_fin else None
            return debut, fin
        else:
            return None, None

    def rafraichir_carte(self):
        self.lieux_a_rafraichir = True  # les positions ont pu changer : l'onglet Lieux se recalculera
        positions_brutes = lire_positions()

        horodatages_disponibles = [h for h, _, _ in positions_brutes]
        self.combo_debut["values"] = horodatages_disponibles
        self.combo_fin["values"] = horodatages_disponibles
        if horodatages_disponibles and not self.combo_debut.get():
            self.combo_debut.set(horodatages_disponibles[0])
        if horodatages_disponibles and not self.combo_fin.get():
            self.combo_fin.set(horodatages_disponibles[-1])

        for marqueur in self.marqueurs:
            marqueur.delete()
        self.marqueurs = []
        if self.trajet:
            self.trajet.delete()
            self.trajet = None
        for item in self.arbre_carte.get_children():
            self.arbre_carte.delete(item)

        try:
            debut, fin = self.calculer_plage_periode()
        except ValueError as erreur:
            messagebox.showerror("Date invalide", str(erreur))
            return

        positions_filtrees = filtrer_par_periode(positions_brutes, debut, fin)
        if not positions_filtrees:
            self.label_derniere_maj.config(text="Aucune position pour cette période")
            return

        correspondance = {
            "Automatique": None, "Tous les points": "tous",
            "1 point / heure": "heure", "1 point / jour": "jour",
        }
        mode = correspondance[self.regroupement_var.get()]
        if mode is None:
            mode = choisir_regroupement_auto(positions_filtrees)

        positions_affichees = regrouper_entrees(positions_filtrees, mode)
        positions_affichees = dedupliquer_positions_stationnaires(positions_affichees)

        for horodatage, lat, lon, _ in reversed(positions_filtrees):
            self.arbre_carte.insert("", "end", values=(horodatage, f"{lat:.5f}", f"{lon:.5f}"))

        coordonnees_trajet = [(lat, lon) for _, lat, lon, _ in positions_filtrees]
        if len(coordonnees_trajet) > 1:
            self.trajet = self.widget_carte.set_path(coordonnees_trajet, color="#5b9bd5", width=2)

        for i, (horodatage, lat, lon, _) in enumerate(positions_affichees):
            est_dernier = i == len(positions_affichees) - 1
            texte = "Position actuelle" if est_dernier else horodatage[:16]
            marqueur = self.widget_carte.set_marker(lat, lon, text=texte, font=("Helvetica", 9))
            self.marqueurs.append(marqueur)

        derniere_lat, derniere_lon = coordonnees_trajet[-1]
        self.widget_carte.set_position(derniere_lat, derniere_lon)
        self.widget_carte.set_zoom(15)

        self.label_derniere_maj.config(
            text=f"Dernière position : {positions_filtrees[-1][0]}  "
                 f"(carte : {len(positions_affichees)} points regroupés — "
                 f"liste : {len(positions_filtrees)} points sur la période)"
        )

    def demander_effacement_positions(self):
        confirme = messagebox.askyesno(
            "Effacer les positions",
            "Voulez-vous vraiment effacer l'historique ?\n"
            "(RaZ du fichier positions.log)\n"
            "l'onglet Télémétrie n'est pas concerné.",
        )
        if confirme:
            effacer_positions()
            self.rafraichir_carte()

    # ------------------------------------------------------------------ #
    #  Onglet Télémétrie
    # ------------------------------------------------------------------ #
    def construire_onglet_telemetrie(self, parent):
        # --- Bandeau "dernière valeur connue" ---
        cadre_resume = ttk.LabelFrame(parent, text="Dernière télémétrie reçue", padding=10)
        cadre_resume.pack(side="top", fill="x", padx=8, pady=8)

        self.labels_resume = {}
        champs_resume = [
            ("horodatage", "Relevé le"),
            ("vitesse_kmh", "Vitesse"),
            ("batterie", "Batterie"),
            ("accel", "Accélération"),
            ("mouvement", "Mouvement"),
            ("luminosite_lux", "Luminosité"),
            ("wifi", "Wi-Fi"),
            ("reseau", "Réseau mobile"),
        ]
        for i, (cle, titre) in enumerate(champs_resume):
            colonne = ttk.Frame(cadre_resume)
            colonne.grid(row=0, column=i, padx=12, sticky="n")
            ttk.Label(colonne, text=titre, font=("Helvetica", 9, "bold")).pack()
            label_valeur = ttk.Label(colonne, text="—", font=("Helvetica", 11))
            label_valeur.pack()
            self.labels_resume[cle] = label_valeur

        # --- Actions + filtre (même logique que l'onglet Carte) ---
        ligne_actions = ttk.Frame(parent, padding=(8, 0, 8, 4))
        ligne_actions.pack(side="top", fill="x")
        ttk.Button(
            ligne_actions, text="Effacer la télémétrie", command=self.demander_effacement_telemetrie
        ).pack(side="left")
        ttk.Button(
            ligne_actions, text="Rafraîchir", command=self.rafraichir_telemetrie
        ).pack(side="left", padx=(8, 0))

        ligne_filtre = ttk.Frame(parent, padding=(8, 0, 8, 8))
        ligne_filtre.pack(side="top", fill="x")
        ttk.Label(ligne_filtre, text="Période :").pack(side="left")
        self.periode_telemetrie_var = tk.StringVar(value="Toutes les mesures")
        self.combo_periode_telemetrie = ttk.Combobox(
            ligne_filtre, textvariable=self.periode_telemetrie_var,
            values=["Toutes les mesures", "Dernières 24 heures", "7 derniers jours",
                    "30 derniers jours", "Personnalisée"],
            state="readonly", width=18
        )
        self.combo_periode_telemetrie.pack(side="left", padx=(4, 10))
        self.combo_periode_telemetrie.bind("<<ComboboxSelected>>", self.changer_periode_telemetrie)

        ttk.Label(ligne_filtre, text="Du :").pack(side="left")
        self.combo_debut_telemetrie = ttk.Combobox(ligne_filtre, width=16, state="disabled")
        self.combo_debut_telemetrie.pack(side="left", padx=(4, 8))
        ttk.Label(ligne_filtre, text="Au :").pack(side="left")
        self.combo_fin_telemetrie = ttk.Combobox(ligne_filtre, width=16, state="disabled")
        self.combo_fin_telemetrie.pack(side="left", padx=(4, 12))

        ttk.Button(
            ligne_filtre, text="Appliquer", command=self.rafraichir_telemetrie
        ).pack(side="left")

        # --- Tableau d'historique ---
        cadre_arbre = ttk.Frame(parent)
        cadre_arbre.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 8))

        colonnes = ("horodatage", "vitesse", "batterie", "accel", "mouvement", "luminosite", "wifi", "reseau")
        self.arbre_telemetrie = ttk.Treeview(cadre_arbre, columns=colonnes, show="headings", height=20)
        entetes = {
            "horodatage": ("Date/heure", 140),
            "vitesse": ("Vitesse (km/h)", 100),
            "batterie": ("Batterie", 130),
            "accel": ("Accél. (x,y,z)", 160),
            "mouvement": ("Mouvement", 110),
            "luminosite": ("Luminosité (lux)", 110),
            "wifi": ("Wi-Fi", 200),
            "reseau": ("Réseau mobile", 140),
        }
        for cle, (titre, largeur) in entetes.items():
            self.arbre_telemetrie.heading(cle, text=titre)
            self.arbre_telemetrie.column(cle, width=largeur)

        ascenseur = ttk.Scrollbar(cadre_arbre, orient="vertical", command=self.arbre_telemetrie.yview)
        self.arbre_telemetrie.configure(yscrollcommand=ascenseur.set)
        self.arbre_telemetrie.pack(side="left", fill="both", expand=True)
        ascenseur.pack(side="right", fill="y")

        self.rafraichir_telemetrie()

    def changer_periode_telemetrie(self, evenement=None):
        if self.periode_telemetrie_var.get() == "Personnalisée":
            self.combo_debut_telemetrie.configure(state="readonly")
            self.combo_fin_telemetrie.configure(state="readonly")
        else:
            self.combo_debut_telemetrie.configure(state="disabled")
            self.combo_fin_telemetrie.configure(state="disabled")
            self.rafraichir_telemetrie()

    def calculer_plage_periode_telemetrie(self):
        maintenant = datetime.now()
        choix = self.periode_telemetrie_var.get()
        if choix == "Dernières 24 heures":
            return maintenant - timedelta(hours=24), None
        elif choix == "7 derniers jours":
            return maintenant - timedelta(days=7), None
        elif choix == "30 derniers jours":
            return maintenant - timedelta(days=30), None
        elif choix == "Personnalisée":
            texte_debut = self.combo_debut_telemetrie.get().strip()
            texte_fin = self.combo_fin_telemetrie.get().strip()
            debut = parser_horodatage(texte_debut) if texte_debut else None
            fin = parser_horodatage(texte_fin) if texte_fin else None
            return debut, fin
        else:
            return None, None

    def rafraichir_telemetrie(self):
        entrees_brutes = lire_telemetrie()  # (horodatage, vitesse, batt_pct, batt_statut, batt_temp,
                                            #  accel_x, accel_y, accel_z, lux, ssid, rssi, operateur, type_reseau)
        for item in self.arbre_telemetrie.get_children():
            self.arbre_telemetrie.delete(item)

        horodatages_disponibles = [entree[0] for entree in entrees_brutes]
        self.combo_debut_telemetrie["values"] = horodatages_disponibles
        self.combo_fin_telemetrie["values"] = horodatages_disponibles
        if horodatages_disponibles and not self.combo_debut_telemetrie.get():
            self.combo_debut_telemetrie.set(horodatages_disponibles[0])
        if horodatages_disponibles and not self.combo_fin_telemetrie.get():
            self.combo_fin_telemetrie.set(horodatages_disponibles[-1])

        if not entrees_brutes:
            return

        try:
            debut, fin = self.calculer_plage_periode_telemetrie()
        except ValueError as erreur:
            messagebox.showerror("Date invalide", str(erreur))
            return

        entrees_filtrees = filtrer_par_periode(entrees_brutes, debut, fin)
        if not entrees_filtrees:
            return

        for entree in reversed(entrees_filtrees):
            (horodatage, vitesse, batt_pct, batt_statut, batt_temp,
             accel_x, accel_y, accel_z, lux, ssid, rssi, operateur, type_reseau, _dt) = entree

            texte_batterie = formater_champ(batt_pct, " %")
            if batt_statut not in ("", "None"):
                texte_batterie += f" ({batt_statut})"
            texte_accel = "—"
            texte_mouvement = "—"
            if accel_x not in ("", "None"):
                texte_accel = f"{accel_x}, {accel_y}, {accel_z}"
                try:
                    norme = calculer_norme_acceleration(float(accel_x), float(accel_y), float(accel_z))
                    texte_mouvement = evaluer_mouvement(norme)
                except ValueError:
                    pass
            texte_wifi = formater_champ(ssid)
            if ssid not in ("", "None") and rssi not in ("", "None"):
                texte_wifi += f" ({rssi} dBm)"
            texte_reseau = formater_champ(operateur)
            if operateur not in ("", "None") and type_reseau not in ("", "None"):
                texte_reseau += f" / {type_reseau}"

            self.arbre_telemetrie.insert("", "end", values=(
                horodatage, formater_champ(vitesse), texte_batterie,
                texte_accel, texte_mouvement, formater_champ(lux), texte_wifi, texte_reseau,
            ))

        # --- Mise à jour du bandeau résumé avec la mesure la plus récente ---
        derniere = entrees_filtrees[-1]
        (horodatage, vitesse, batt_pct, batt_statut, batt_temp,
         accel_x, accel_y, accel_z, lux, ssid, rssi, operateur, type_reseau, _dt) = derniere

        self.labels_resume["horodatage"].config(text=horodatage)
        self.labels_resume["vitesse_kmh"].config(text=formater_champ(vitesse, " km/h"))

        texte_batterie = formater_champ(batt_pct, " %")
        if batt_statut not in ("", "None"):
            texte_batterie += f"\n{batt_statut}"
        self.labels_resume["batterie"].config(text=texte_batterie)

        if accel_x not in ("", "None"):
            self.labels_resume["accel"].config(text=f"x={accel_x}\ny={accel_y}\nz={accel_z}")
            try:
                norme = calculer_norme_acceleration(float(accel_x), float(accel_y), float(accel_z))
                statut_mouvement = evaluer_mouvement(norme)
                couleur = "#c0392b" if statut_mouvement == "En mouvement" else "#27ae60"
                self.labels_resume["mouvement"].config(
                    text=f"{statut_mouvement}\n({norme:.1f} m/s²)", foreground=couleur
                )
            except ValueError:
                self.labels_resume["mouvement"].config(text="—", foreground="black")
        else:
            self.labels_resume["accel"].config(text="—")
            self.labels_resume["mouvement"].config(text="—", foreground="black")

        self.labels_resume["luminosite_lux"].config(text=formater_champ(lux, " lux"))

        texte_wifi = formater_champ(ssid)
        if ssid not in ("", "None") and rssi not in ("", "None"):
            texte_wifi += f"\n{rssi} dBm"
        self.labels_resume["wifi"].config(text=texte_wifi)

        texte_reseau = formater_champ(operateur)
        if type_reseau not in ("", "None"):
            texte_reseau += f"\n{type_reseau}"
        self.labels_resume["reseau"].config(text=texte_reseau)

    def demander_effacement_telemetrie(self):
        confirme = messagebox.askyesno(
            "Effacer la télémétrie",
            "Voulez-vous vraiment effacer l'historique ?\n"
            "(RaZ du fichier telemetrie.log)\n"
            "l'onglet Carte n'est pas concerné.",
        )
        if confirme:
            effacer_telemetrie()
            self.rafraichir_telemetrie()

    # ------------------------------------------------------------------ #
    #  Onglet Lieux (stay points + temps passé par lieu)
    # ------------------------------------------------------------------ #
    def construire_onglet_lieux(self, parent):
        self.lieux_nommes = charger_lieux_nommes()
        self.lieux_courants = []
        self.rayon_analyse = RAYON_LIEU_DEFAUT_METRES
        self.cache_adresses = {}
        self.marqueurs_lieux = []
        self.deplacement_en_cours = None   # {"index": ..., "nom": ..., "lieu": ...} pendant un déplacement
        self.marqueur_apercu = None

        ligne_actions = ttk.Frame(parent, padding=(8, 8, 8, 4))
        ligne_actions.pack(side="top", fill="x")
        ttk.Button(ligne_actions, text="Rafraîchir", command=self.rafraichir_lieux).pack(side="left")
        ttk.Button(
            ligne_actions, text="Nommer le lieu…", command=self.nommer_lieu_selectionne
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            ligne_actions, text="Retirer le nom", command=self.retirer_nom_lieu_selectionne
        ).pack(side="left", padx=(8, 0))
        self.bouton_deplacer = ttk.Button(
            ligne_actions, text="Déplacer le lieu", command=self.basculer_deplacement_lieu
        )
        self.bouton_deplacer.pack(side="left", padx=(8, 0))
        ttk.Button(
            ligne_actions, text="Adresse (OSM)", command=self.chercher_adresse_lieu
        ).pack(side="left", padx=(8, 0))
        self.label_mode_deplacement = ttk.Label(ligne_actions, text="", foreground="#B03A2E")
        self.label_mode_deplacement.pack(side="left", padx=(12, 0))

        ligne_param = ttk.Frame(parent, padding=(8, 0, 8, 4))
        ligne_param.pack(side="top", fill="x")

        ttk.Label(ligne_param, text="Période :").pack(side="left")
        self.periode_lieux_var = tk.StringVar(value="Toutes les positions")
        self.combo_periode_lieux = ttk.Combobox(
            ligne_param, textvariable=self.periode_lieux_var, state="readonly", width=18,
            values=["Toutes les positions", "Dernières 24 heures", "7 derniers jours", "30 derniers jours"],
        )
        self.combo_periode_lieux.pack(side="left", padx=(4, 12))
        self.combo_periode_lieux.bind("<<ComboboxSelected>>", lambda evenement: self.rafraichir_lieux())

        ttk.Label(ligne_param, text="Rayon d'un lieu (m) :").pack(side="left")
        self.spin_rayon_lieux = ttk.Spinbox(ligne_param, from_=20, to=2000, increment=10, width=6)
        self.spin_rayon_lieux.set(RAYON_LIEU_DEFAUT_METRES)
        self.spin_rayon_lieux.pack(side="left", padx=(4, 12))

        ttk.Label(ligne_param, text="Écart max entre 2 relevés (h) :").pack(side="left")
        self.spin_ecart_lieux = ttk.Spinbox(ligne_param, from_=1, to=720, increment=1, width=6)
        self.spin_ecart_lieux.set(ECART_MAX_DEFAUT_HEURES)
        self.spin_ecart_lieux.pack(side="left", padx=(4, 12))

        ttk.Button(ligne_param, text="Appliquer", command=self.rafraichir_lieux).pack(side="left")

        self.label_resume_lieux = ttk.Label(parent, text="", foreground="#333333")
        self.label_resume_lieux.pack(side="top", fill="x", padx=8, pady=(0, 6))

        zone_principale = ttk.Frame(parent)
        zone_principale.pack(side="top", fill="both", expand=True)

        self.carte_lieux = tkintermapview.TkinterMapView(zone_principale, width=700, height=560, corner_radius=0)
        self.carte_lieux.pack(side="left", fill="both", expand=True)
        self.carte_lieux.set_tile_server("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")
        self.carte_lieux.add_left_click_map_command(self.clic_carte_lieux)
        self.bind("<Escape>", lambda evenement: self.annuler_deplacement())

        panneau = ttk.Frame(zone_principale, width=560)
        panneau.pack(side="right", fill="y")
        panneau.pack_propagate(False)

        # Pieds de panneau (packés en premier pour rester visibles)
        ttk.Label(
            panneau, foreground="#777777", wraplength=535, justify="left",
            text="Durée estimée = durée mesurée + moitié des intervalles avant/après le séjour "
                 "(les relevés étant espacés, l'heure exacte d'arrivée/départ est inconnue). "
                 "Part = durée estimée / période analysée. Rel. = nombre de relevés.",
        ).pack(side="bottom", anchor="w", padx=8, pady=(0, 8))
        self.label_adresse = ttk.Label(panneau, text="", foreground="#555555", wraplength=535, justify="left")
        self.label_adresse.pack(side="bottom", anchor="w", padx=8, pady=(4, 4))

        ttk.Label(
            panneau, text="Lieux fréquentés (du plus long au plus court)", font=("Helvetica", 10, "bold")
        ).pack(side="top", anchor="w", padx=8, pady=(8, 4))

        cadre_lieux = ttk.Frame(panneau)
        cadre_lieux.pack(side="top", fill="both", expand=True, padx=8)
        colonnes_lieux = [
            ("lieu", "Lieu", 210, "w"), ("sejours", "Séj.", 45, "center"),
            ("mesuree", "Mesuré", 80, "center"), ("estimee", "Estimé", 80, "center"),
            ("part", "Part", 55, "center"),
        ]
        self.arbre_lieux = ttk.Treeview(
            cadre_lieux, columns=[c[0] for c in colonnes_lieux], show="headings", height=9, selectmode="browse"
        )
        for identifiant, titre, largeur, ancrage in colonnes_lieux:
            self.arbre_lieux.heading(identifiant, text=titre)
            self.arbre_lieux.column(identifiant, width=largeur, anchor=ancrage, stretch=(identifiant == "lieu"))
        defilement_lieux = ttk.Scrollbar(cadre_lieux, orient="vertical", command=self.arbre_lieux.yview)
        self.arbre_lieux.configure(yscrollcommand=defilement_lieux.set)
        self.arbre_lieux.pack(side="left", fill="both", expand=True)
        defilement_lieux.pack(side="right", fill="y")
        self.arbre_lieux.bind("<<TreeviewSelect>>", self.selection_lieu_changee)

        ttk.Label(
            panneau, text="Séjours du lieu sélectionné (du plus récent au plus ancien)",
            font=("Helvetica", 10, "bold")
        ).pack(side="top", anchor="w", padx=8, pady=(8, 4))

        cadre_sejours = ttk.Frame(panneau)
        cadre_sejours.pack(side="top", fill="both", expand=True, padx=8)
        colonnes_sejours = [
            ("arrivee", "Arrivée", 105, "center"), ("depart", "Départ", 105, "center"),
            ("mesuree", "Mesuré", 85, "center"), ("estimee", "Estimé", 85, "center"),
            ("points", "Rel.", 45, "center"),
        ]
        self.arbre_sejours = ttk.Treeview(
            cadre_sejours, columns=[c[0] for c in colonnes_sejours], show="headings", height=7, selectmode="none"
        )
        for identifiant, titre, largeur, ancrage in colonnes_sejours:
            self.arbre_sejours.heading(identifiant, text=titre)
            self.arbre_sejours.column(identifiant, width=largeur, anchor=ancrage, stretch=False)
        defilement_sejours = ttk.Scrollbar(cadre_sejours, orient="vertical", command=self.arbre_sejours.yview)
        self.arbre_sejours.configure(yscrollcommand=defilement_sejours.set)
        self.arbre_sejours.pack(side="left", fill="both", expand=True)
        defilement_sejours.pack(side="right", fill="y")

    def changement_onglet(self, evenement=None):
        if self.lieux_a_rafraichir and self.notebook.select() == str(self.onglet_lieux):
            self.lieux_a_rafraichir = False
            self.after(100, self.rafraichir_lieux)  # laisse l'onglet s'afficher : la carte a alors sa vraie taille

    def lire_parametres_lieux(self):
        try:
            rayon = int(float(self.spin_rayon_lieux.get()))
            ecart_heures = int(float(self.spin_ecart_lieux.get()))
        except ValueError:
            messagebox.showerror("Paramètre invalide", "Le rayon et l'écart max doivent être des nombres.")
            return None
        return max(rayon, 10), max(ecart_heures, 1)

    def plage_periode_lieux(self):
        maintenant = datetime.now()
        choix = self.periode_lieux_var.get()
        if choix == "Dernières 24 heures":
            return maintenant - timedelta(hours=24), None
        if choix == "7 derniers jours":
            return maintenant - timedelta(days=7), None
        if choix == "30 derniers jours":
            return maintenant - timedelta(days=30), None
        return None, None

    @staticmethod
    def libelle_lieu(lieu):
        return lieu["nom"] or f"Non nommé ({lieu['lat']:.4f}, {lieu['lon']:.4f})"

    def rafraichir_lieux(self):
        self.annuler_deplacement()
        parametres = self.lire_parametres_lieux()
        if parametres is None:
            return
        rayon, ecart_heures = parametres
        self.rayon_analyse = rayon
        self.lieux_nommes = charger_lieux_nommes()  # relu à chaque fois : le fichier peut être édité à la main
        debut, fin = self.plage_periode_lieux()
        positions = filtrer_par_periode(lire_positions(), debut, fin)
        self.lieux_courants, resume = analyser_lieux(positions, rayon, ecart_heures, self.lieux_nommes)
        self.afficher_lieux(resume)

    def afficher_lieux(self, resume):
        for item in self.arbre_lieux.get_children():
            self.arbre_lieux.delete(item)
        for item in self.arbre_sejours.get_children():
            self.arbre_sejours.delete(item)
        for marqueur in self.marqueurs_lieux:
            marqueur.delete()
        self.marqueurs_lieux = []
        self.label_adresse.config(text="")

        if not self.lieux_courants:
            self.label_resume_lieux.config(text="Aucune position sur cette période.")
            return

        etendue_s = resume["etendue"].total_seconds()
        for i, lieu in enumerate(self.lieux_courants):
            if etendue_s > 0:
                pourcentage = 100 * lieu["estimee"].total_seconds() / etendue_s
                part = "< 1 %" if 0 < pourcentage < 1 else f"{pourcentage:.0f} %"
            else:
                part = "—"
            self.arbre_lieux.insert(
                "", "end", iid=str(i),
                values=(self.libelle_lieu(lieu), lieu["nb_sejours"], formater_duree(lieu["mesuree"]),
                        formater_duree(lieu["estimee"]), part),
            )
            if i < 200:  # au-delà, la carte devient illisible
                couleurs = {} if lieu["nom"] else {"marker_color_circle": "#8A8A8A", "marker_color_outside": "#5E5E5E"}
                texte = f"{lieu['nom'] or 'Non nommé'}\n{formater_duree(lieu['estimee'])}"
                self.marqueurs_lieux.append(
                    self.carte_lieux.set_marker(lieu["lat_aff"], lieu["lon_aff"], text=texte, font=("Helvetica", 9), **couleurs)
                )

        self.ajuster_carte_lieux()

        taux = f" ({100 * resume['attribue'].total_seconds() / etendue_s:.0f} %)" if etendue_s > 0 else ""
        self.label_resume_lieux.config(
            text=f"{len(self.lieux_courants)} lieu(x) · {resume['nb_releves']} relevés · période analysée : "
                 f"{formater_duree(resume['etendue'])} · attribué à des lieux : {formater_duree(resume['attribue'])}{taux}"
                 f" · trous de données : {formater_duree(resume['trous'])}"
        )

    def ajuster_carte_lieux(self):
        latitudes = [lieu["lat_aff"] for lieu in self.lieux_courants]
        longitudes = [lieu["lon_aff"] for lieu in self.lieux_courants]
        marge = 0.002  # ~200 m : garantit un cadre valide même pour un seul lieu
        try:
            self.carte_lieux.fit_bounding_box(
                (max(latitudes) + marge, min(longitudes) - marge),
                (min(latitudes) - marge, max(longitudes) + marge),
            )
        except Exception:
            self.carte_lieux.set_position(latitudes[0], longitudes[0])
            self.carte_lieux.set_zoom(15)

    def lieu_selectionne(self):
        selection = self.arbre_lieux.selection()
        if not selection:
            return None
        try:
            return self.lieux_courants[int(selection[0])]
        except (ValueError, IndexError):
            return None

    def selection_lieu_changee(self, evenement=None):
        self.annuler_deplacement()
        for item in self.arbre_sejours.get_children():
            self.arbre_sejours.delete(item)
        lieu = self.lieu_selectionne()
        if lieu is None:
            return
        for sejour in reversed(lieu["sejours"]):
            self.arbre_sejours.insert(
                "", "end",
                values=(sejour["debut"].strftime("%d/%m %H:%M"), sejour["fin"].strftime("%d/%m %H:%M"),
                        formater_duree(duree_mesuree(sejour)), formater_duree(duree_estimee(sejour)),
                        sejour["nb_points"]),
            )
        self.carte_lieux.set_position(lieu["lat_aff"], lieu["lon_aff"])
        self.carte_lieux.set_zoom(16)
        cle = (round(lieu["lat_aff"], 4), round(lieu["lon_aff"], 4))
        self.label_adresse.config(text=self.cache_adresses.get(cle, ""))

    def selectionner_lieu_par_nom(self, nom):
        for i, lieu in enumerate(self.lieux_courants):
            if lieu["nom"] == nom:
                self.arbre_lieux.selection_set(str(i))
                self.arbre_lieux.see(str(i))
                return

    def enregistrer_lieux_nommes(self):
        try:
            sauver_lieux_nommes(self.lieux_nommes)
        except OSError as erreur:
            messagebox.showerror("Enregistrement impossible", f"Impossible d'écrire lieux.json :\n{erreur}")

    def nommer_lieu_selectionne(self):
        lieu = self.lieu_selectionne()
        if lieu is None:
            messagebox.showinfo("Nommer le lieu", "Sélectionnez d'abord un lieu dans la liste.")
            return
        ancien = lieu["nom"]
        nouveau = simpledialog.askstring(
            "Nommer le lieu", "Nom de ce lieu (ex. Domicile, Bureau, ...) :",
            initialvalue=ancien or "", parent=self,
        )
        if nouveau is None or not nouveau.strip():
            return
        nouveau = nouveau.strip()
        if ancien:
            for entree in self.lieux_nommes:  # renomme toutes les entrées portant l'ancien nom
                if entree["nom"] == ancien:
                    entree["nom"] = nouveau
        else:
            # un nom déjà utilisé ailleurs fusionnera les deux lieux dans une seule ligne
            self.lieux_nommes.append({"nom": nouveau, "lat": round(lieu["lat"], 6),
                                      "lon": round(lieu["lon"], 6), "rayon": self.rayon_analyse})
        self.enregistrer_lieux_nommes()
        self.rafraichir_lieux()
        self.selectionner_lieu_par_nom(nouveau)

    def retirer_nom_lieu_selectionne(self):
        lieu = self.lieu_selectionne()
        if lieu is None or not lieu["nom"]:
            messagebox.showinfo("Retirer le nom", "Sélectionnez un lieu nommé dans la liste.")
            return
        if not messagebox.askyesno("Retirer le nom", f"Retirer le nom « {lieu['nom']} » de ce lieu ?"):
            return
        nom = lieu["nom"]
        self.lieux_nommes = [entree for entree in self.lieux_nommes if entree["nom"] != nom]
        self.enregistrer_lieux_nommes()
        self.rafraichir_lieux()

    # --- Déplacement d'un lieu par clic sur la carte -------------------------------------------------------
    def basculer_deplacement_lieu(self):
        if self.deplacement_en_cours is not None:
            self.annuler_deplacement()
            return
        lieu = self.lieu_selectionne()
        if lieu is None or lieu["index_entree"] is None:
            messagebox.showinfo(
                "Déplacer le lieu",
                "Sélectionnez un lieu nommé dans la liste.\n"
                "(Un lieu non nommé n'a pas de position enregistrée : nommez-le d'abord.)",
            )
            return
        self.deplacement_en_cours = {"index": lieu["index_entree"], "nom": lieu["nom"], "lieu": lieu}
        self.bouton_deplacer.config(text="Annuler le déplacement")
        self.label_mode_deplacement.config(
            text=f"Cliquez sur la carte pour placer « {lieu['nom']} » (Échap pour annuler)"
        )
        self.appliquer_curseur_carte("crosshair")

    def annuler_deplacement(self):
        self.effacer_apercu_deplacement()
        if self.deplacement_en_cours is None:
            return
        self.deplacement_en_cours = None
        self.bouton_deplacer.config(text="Déplacer le lieu")
        self.label_mode_deplacement.config(text="")
        self.appliquer_curseur_carte("")

    def effacer_apercu_deplacement(self):
        if self.marqueur_apercu is not None:
            self.marqueur_apercu.delete()
            self.marqueur_apercu = None

    def appliquer_curseur_carte(self, curseur):
        try:
            self.carte_lieux.canvas.configure(cursor=curseur)
        except Exception:
            pass  # cosmétique : ignoré si la version de tkintermapview n'expose pas le canvas

    def clic_carte_lieux(self, coordonnees):
        """Clic gauche sur la carte : sans effet hors du mode déplacement."""
        contexte = self.deplacement_en_cours
        if contexte is None:
            return
        lat, lon = coordonnees
        lieu, index, nom = contexte["lieu"], contexte["index"], contexte["nom"]

        self.effacer_apercu_deplacement()
        self.marqueur_apercu = self.carte_lieux.set_marker(
            lat, lon, text="Nouvelle position", font=("Helvetica", 9),
            marker_color_circle="#1F618D", marker_color_outside="#154360",
        )

        entree = self.lieux_nommes[index]
        deplacement = distance_metres(lieu["lat_aff"], lieu["lon_aff"], lat, lon)
        # Le rayon doit continuer à couvrir le centre calculé des groupes de relevés rattachés à ce lieu,
        # sinon ils redeviendraient "non nommés" après le déplacement.
        distance_max = max(
            (distance_metres(lat, lon, c_lat, c_lon) for i, c_lat, c_lon in lieu["appariements"] if i == index),
            default=0.0,
        )
        nouveau_rayon = entree["rayon"]
        message = f"Déplacer « {nom} » ici ?\n\nDéplacement : {deplacement:.0f} m"
        if distance_max > entree["rayon"]:
            nouveau_rayon = int(math.ceil(distance_max / 10.0) * 10 + 10)
            message += (f"\n\nLe rayon du lieu passera de {entree['rayon']:.0f} m à {nouveau_rayon} m "
                        f"pour continuer à lui rattacher vos relevés.")

        if not messagebox.askyesno("Déplacer le lieu", message, parent=self):
            self.effacer_apercu_deplacement()  # on reste en mode déplacement : nouveau clic possible
            return

        entree["lat"] = round(lat, 6)
        entree["lon"] = round(lon, 6)
        entree["rayon"] = nouveau_rayon
        self.enregistrer_lieux_nommes()
        self.rafraichir_lieux()  # quitte aussi le mode déplacement
        self.selectionner_lieu_par_nom(nom)

    def chercher_adresse_lieu(self):
        lieu = self.lieu_selectionne()
        if lieu is None:
            messagebox.showinfo("Adresse", "Sélectionnez d'abord un lieu dans la liste.")
            return
        lat, lon = lieu["lat_aff"], lieu["lon_aff"]
        cle = (round(lat, 4), round(lon, 4))
        if cle in self.cache_adresses:
            self.label_adresse.config(text=self.cache_adresses[cle])
            return
        self.label_adresse.config(text="Recherche de l'adresse…")
        boite = queue.Queue()

        def tache():
            try:
                boite.put(("ok", adresse_depuis_osm(lat, lon)))
            except Exception as erreur:
                boite.put(("erreur", str(erreur)))

        threading.Thread(target=tache, daemon=True).start()
        self.after(200, lambda: self.attendre_adresse(boite, cle))

    def attendre_adresse(self, boite, cle):
        try:
            statut, texte = boite.get_nowait()
        except queue.Empty:
            self.after(200, lambda: self.attendre_adresse(boite, cle))
            return
        if statut == "ok":
            self.cache_adresses[cle] = texte
        lieu = self.lieu_selectionne()
        if lieu is not None and (round(lieu["lat_aff"], 4), round(lieu["lon_aff"], 4)) == cle:
            self.label_adresse.config(text=texte if statut == "ok" else f"Adresse indisponible : {texte}")


if __name__ == "__main__":
    app = TrackerS23()
    app.mainloop()
