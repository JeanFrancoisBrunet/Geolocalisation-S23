#!/usr/bin/env python3
# ======================================================================================================================
#  Tracker S23 - Suivi de Position & de Télémétrie du Samsung S23 sous Wireguard
#  Onglet "Carte" : positions envoyées par le S23 (positions.log)
#  Onglet "Télémétrie" : vitesse, batterie, accéléromètre, wifi, réseau, luminosité (telemetrie.log)
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
import tkinter as tk
from tkinter import ttk, messagebox
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
        self.notebook.add(self.onglet_carte, text="Carte")
        self.notebook.add(self.onglet_telemetrie, text="Télémétrie")

        self.construire_onglet_carte(self.onglet_carte)
        self.construire_onglet_telemetrie(self.onglet_telemetrie)

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

if __name__ == "__main__":
    app = TrackerS23()
    app.mainloop()
