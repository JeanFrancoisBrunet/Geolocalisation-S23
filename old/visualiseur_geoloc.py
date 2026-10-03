#!/usr/bin/env python3
# =============================================================================
#  Visualiseur GPS - Projet de Géolocalisation du Samsung S23
#  Affiche sur une carte les positions envoyées par le S23 
#  et enregistrées par le serveur_geoloc.py dans le fichier positions.log
#
#  Raspberry Pi 5 (16 Go RAM, SSD NVMe 256 Go / 1 To, OS Bookworm)
#
#  Réglage de la fréquence de collecte des positions se fait sur S23
#  nano ~/position_boucle.py
#  INTERVALLE_SECONDES = 28800 (pour toutes les 8 heures, valeur en secondes)
#  Ctrl X pour sauvegarder la fréquence de collecte des positions
#
#  cat ~/geoloc.log (pour vérifier l'envoie des données vers le Pi5)
#  > ~/geolog.log   (pour purger le fichier sur le S23)
#
#  Auteur : Jean-François BRUNET – JFBConseils – Septembre 2026
# =============================================================================

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
FICHIER_LOG = os.path.join(DOSSIER_PROJET, "positions.log")
DOSSIER_ICONS = os.path.join(DOSSIER_PROJET, "icons")
FICHIER_ICONE = os.path.join(DOSSIER_ICONS, "gps-phone.png")

def parser_horodatage(horodatage):
    """Convertit une chaîne 'YYYY-MM-DD HH:MM:SS' du log en objet datetime."""
    return datetime.strptime(horodatage, "%Y-%m-%d %H:%M:%S")



def filtrer_positions(positions, debut, fin):
    """Filtre une liste (horodatage, lat, lon) selon une plage [debut, fin] (bornes optionnelles)."""
    resultat = []
    for horodatage, lat, lon in positions:
        dt = parser_horodatage(horodatage)
        if debut and dt < debut:
            continue
        if fin and dt > fin:
            continue
        resultat.append((horodatage, lat, lon, dt))
    return resultat

def choisir_regroupement_auto(positions_avec_dt):
    """Détermine automatiquement une granularité d'affichage selon l'étendue des dates."""
    if len(positions_avec_dt) < 2:
        return "tous"
    etendue = positions_avec_dt[-1][3] - positions_avec_dt[0][3]
    if etendue <= timedelta(days=1):
        return "tous"
    elif etendue <= timedelta(days=31):
        return "heure"
    else:
        return "jour"

def regrouper_positions(positions_avec_dt, mode):
    """Réduit le nombre de points affichés en gardant un point par heure ou par jour.
    'positions_avec_dt' est triée par date croissante ; on garde le dernier point de chaque période."""
    if mode == "tous" or len(positions_avec_dt) < 2:
        return positions_avec_dt

    par_cle = {}
    for horodatage, lat, lon, dt in positions_avec_dt:
        if mode == "heure":
            cle = dt.strftime("%Y-%m-%d %H")
        else:  # "jour"
            cle = dt.strftime("%Y-%m-%d")
        par_cle[cle] = (horodatage, lat, lon, dt)  # écrase avec le plus récent de la période

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
    resultat.append(positions_avec_dt[-1])  # dernière position (fin du dernier groupe)
    return resultat

def lire_positions():
    """Lit le fichier positions.log et renvoie une liste de tuples (horodatage, lat, lon)."""
    positions = []
    if not os.path.exists(FICHIER_LOG):
        return positions
    with open(FICHIER_LOG, "r") as f:
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
    """Vide le fichier positions.log (le fichier est conservé, mais vidé)."""
    with open(FICHIER_LOG, "w"):
        pass

class FenetreReglagesCollecte(tk.Toplevel):
    """Calculateur d'intervalle de collecte à reporter dans position_boucle.py sur le S23.
    Cette fenêtre ne modifie rien sur le téléphone : elle aide juste à choisir
    et à copier la bonne valeur d'INTERVALLE_SECONDES."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Réglages de collecte (S23)")
        self.geometry("480x330")
        self.resizable(False, False)
        self.transient(parent)

        ttk.Label(
            self, text="Choisissez un mode pour calculer l'intervalle d'envoi",
            font=("Helvetica", 11, "bold")
        ).pack(anchor="w", padx=12, pady=(12, 8))

        self.mode_var = tk.StringVar(value="minutes")

        # --- Mode 1 : toutes les X minutes ---
        cadre1 = ttk.Frame(self)
        cadre1.pack(fill="x", padx=12, pady=4)
        ttk.Radiobutton(
            cadre1, text="Toutes les", variable=self.mode_var, value="minutes",
            command=self.recalculer
        ).pack(side="left")
        self.spin_minutes = ttk.Spinbox(
            cadre1, from_=5, to=60, increment=5, width=5, command=self.recalculer
        )
        self.spin_minutes.set(60)
        self.spin_minutes.pack(side="left", padx=6)
        ttk.Label(cadre1, text="minutes").pack(side="left")

        # --- Mode 2 : X fois par jour ---
        cadre2 = ttk.Frame(self)
        cadre2.pack(fill="x", padx=12, pady=4)
        ttk.Radiobutton(
            cadre2, text="", variable=self.mode_var, value="jour",
            command=self.recalculer
        ).pack(side="left")
        self.spin_jour = ttk.Spinbox(
            cadre2, from_=1, to=24, width=5, command=self.recalculer
        )
        self.spin_jour.set(1)
        self.spin_jour.pack(side="left", padx=6)
        ttk.Label(cadre2, text="fois par jour").pack(side="left")

        # --- Mode 3 : X fois par semaine ---
        cadre3 = ttk.Frame(self)
        cadre3.pack(fill="x", padx=12, pady=4)
        ttk.Radiobutton(
            cadre3, text="", variable=self.mode_var, value="semaine",
            command=self.recalculer
        ).pack(side="left")
        self.spin_semaine = ttk.Spinbox(
            cadre3, from_=1, to=7, width=5, command=self.recalculer
        )
        self.spin_semaine.set(7)
        self.spin_semaine.pack(side="left", padx=6)
        ttk.Label(cadre3, text="fois par semaine").pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=12)

        ttk.Label(self, text="Ligne à reporter dans ~/position_boucle.py sur le S23 :").pack(
            anchor="w", padx=12
        )
        self.entree_resultat = ttk.Entry(self, font=("Courier", 11))
        self.entree_resultat.pack(fill="x", padx=12, pady=(4, 4))

        self.label_equivalence = ttk.Label(self, text="", foreground="#555")
        self.label_equivalence.pack(anchor="w", padx=12)

        ttk.Label(
            self,
            text="Astuce : réduire la fréquence économise la batterie du téléphone.",
            foreground="#777"
        ).pack(anchor="w", padx=12, pady=(10, 0))

        for widget in (self.spin_minutes, self.spin_jour, self.spin_semaine):
            widget.bind("<KeyRelease>", lambda e: self.recalculer())

        self.recalculer()

    def recalculer(self):
        mode = self.mode_var.get()
        try:
            if mode == "minutes":
                minutes = int(self.spin_minutes.get())
                secondes = minutes * 60
                equivalence = f"= une position toutes les {minutes} minutes"
            elif mode == "jour":
                fois = int(self.spin_jour.get())
                secondes = (24 * 3600) // fois
                equivalence = f"= une position toutes les {secondes // 60} minutes environ"
            else:  # "semaine"
                fois = int(self.spin_semaine.get())
                secondes = (7 * 24 * 3600) // fois
                equivalence = f"= une position toutes les {secondes // 3600} heures environ"
        except (ValueError, ZeroDivisionError):
            return

        self.entree_resultat.delete(0, "end")
        self.entree_resultat.insert(0, f"INTERVALLE_SECONDES = {secondes}")
        self.label_equivalence.config(text=equivalence)

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
            self, text="Visualiseur GPS - Géolocalisation S23",
            font=("Helvetica", 14, "bold"), bg="white"
        ).pack(pady=(0, 10))
        tk.Label(self, text="Chargement...", font=("Helvetica", 10), bg="white").pack()

        self.after(duree_ms, lambda: (self.destroy(), callback_fin()))

class VisualiseurGeoloc(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Visualiseur GPS - Samsung S23 & Raspberry Pi5")
        self.geometry("1150x680")
        self.minsize(1150, 550)

        if os.path.exists(FICHIER_ICONE):
            icone = Image.open(FICHIER_ICONE)
            self.icone_tk = ImageTk.PhotoImage(icone)
            self.iconphoto(True, self.icone_tk)

        self.withdraw()  # caché tant que le splash tourne
        SplashScreen(self, 2000, self.construire_interface)

    def construire_interface(self):
        self.deiconify()

        # --- Barre du haut ---
        barre_haut = ttk.Frame(self, padding=8)
        barre_haut.pack(side="top", fill="x")

        ttk.Label(
            barre_haut, text="Trajets Samsung S23", font=("Helvetica", 13, "bold")
        ).pack(side="left")

        self.label_derniere_maj = ttk.Label(barre_haut, text="")
        self.label_derniere_maj.pack(side="right", padx=(0, 10))

        ttk.Button(
            barre_haut, text="Rafraîchir", command=self.rafraichir
        ).pack(side="right")

        ttk.Button(
            barre_haut, text="Effacer les positions", command=self.demander_effacement
        ).pack(side="right", padx=(0, 8))

        ttk.Button(
            barre_haut, text="Réglages Freq. Datas (S23)", command=self.ouvrir_reglages_collecte
        ).pack(side="right", padx=(0, 8))

        # --- Barre de filtre par période (sur 2 lignes pour rester toujours visible) ---
        barre_filtre = ttk.Frame(self, padding=(8, 0, 8, 8))
        barre_filtre.pack(side="top", fill="x")

        ligne1 = ttk.Frame(barre_filtre)
        ligne1.pack(side="top", fill="x", pady=(0, 4))

        ligne2 = ttk.Frame(barre_filtre)
        ligne2.pack(side="top", fill="x")

        ttk.Label(ligne1, text="Période :").pack(side="left")

        self.periode_var = tk.StringVar(value="Toutes les positions")
        liste_periodes = [
            "Toutes les positions",
            "Dernières 24 heures",
            "7 derniers jours",
            "30 derniers jours",
            "Personnalisée",
        ]
        self.combo_periode = ttk.Combobox(
            ligne1, textvariable=self.periode_var, values=liste_periodes,
            state="readonly", width=20
        )
        self.combo_periode.pack(side="left", padx=(4, 12))
        self.combo_periode.bind("<<ComboboxSelected>>", self.changer_periode)

        ttk.Label(ligne1, text="Du :").pack(side="left")
        self.combo_debut = ttk.Combobox(ligne1, width=18, state="disabled")
        self.combo_debut.pack(side="left", padx=(4, 10))

        ttk.Label(ligne1, text="Au :").pack(side="left")
        self.combo_fin = ttk.Combobox(ligne1, width=18, state="disabled")
        self.combo_fin.pack(side="left", padx=(4, 10))

        ttk.Label(ligne1, text="(choisis parmi les positions enregistrées)", foreground="#777").pack(side="left")

        ttk.Label(ligne2, text="Regroupement :").pack(side="left")
        self.regroupement_var = tk.StringVar(value="Automatique")
        self.combo_regroupement = ttk.Combobox(
            ligne2, textvariable=self.regroupement_var,
            values=["Automatique", "Tous les points", "1 point / heure", "1 point / jour"],
            state="readonly", width=14
        )
        self.combo_regroupement.pack(side="left", padx=(4, 12))

        ttk.Button(ligne2, text="Appliquer", command=self.rafraichir).pack(side="left")

        # --- Zone principale : carte + liste ---
        zone_principale = ttk.Frame(self)
        zone_principale.pack(side="top", fill="both", expand=True)

        # Carte
        self.widget_carte = tkintermapview.TkinterMapView(
            zone_principale, width=700, height=600, corner_radius=0
        )
        self.widget_carte.pack(side="left", fill="both", expand=True)
        self.widget_carte.set_tile_server(
            "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"
        )

        # Liste des positions
        cadre_liste = ttk.Frame(zone_principale, width=380)
        cadre_liste.pack(side="right", fill="y")
        cadre_liste.pack_propagate(False)

        ttk.Label(cadre_liste, text="Historique", font=("Helvetica", 11, "bold")).pack(
            anchor="w", padx=8, pady=(8, 4)
        )

        cadre_arbre = ttk.Frame(cadre_liste)
        cadre_arbre.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        colonnes = ("horodatage", "lat", "lon")
        self.arbre = ttk.Treeview(cadre_arbre, columns=colonnes, show="headings", height=25)
        self.arbre.heading("horodatage", text="Date/heure")
        self.arbre.heading("lat", text="Latitude")
        self.arbre.heading("lon", text="Longitude")
        self.arbre.column("horodatage", width=140)
        self.arbre.column("lat", width=95)
        self.arbre.column("lon", width=95)

        ascenseur = ttk.Scrollbar(cadre_arbre, orient="vertical", command=self.arbre.yview)
        self.arbre.configure(yscrollcommand=ascenseur.set)

        self.arbre.pack(side="left", fill="both", expand=True)
        ascenseur.pack(side="right", fill="y")

        self.marqueurs = []
        self.trajet = None

        self.rafraichir()

    def changer_periode(self, evenement=None):
        if self.periode_var.get() == "Personnalisée":
            self.combo_debut.configure(state="readonly")
            self.combo_fin.configure(state="readonly")
        else:
            self.combo_debut.configure(state="disabled")
            self.combo_fin.configure(state="disabled")
            self.rafraichir()

    def calculer_plage_periode(self):
        """Renvoie (debut, fin) selon la période sélectionnée, ou (None, None) pour tout afficher."""
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
        else:  # "Toutes les positions"
            return None, None

    def rafraichir(self):
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

        for item in self.arbre.get_children():
            self.arbre.delete(item)

        try:
            debut, fin = self.calculer_plage_periode()
        except ValueError as erreur:
            messagebox.showerror("Date invalide", str(erreur))
            return

        positions_filtrees = filtrer_positions(positions_brutes, debut, fin)

        if not positions_filtrees:
            self.label_derniere_maj.config(text="Aucune position pour cette période")
            return

        choix_regroupement = self.regroupement_var.get()
        correspondance = {
            "Automatique": None,
            "Tous les points": "tous",
            "1 point / heure": "heure",
            "1 point / jour": "jour",
        }
        mode = correspondance[choix_regroupement]
        if mode is None:
            mode = choisir_regroupement_auto(positions_filtrees)

        positions_affichees = regrouper_positions(positions_filtrees, mode)
        positions_affichees = dedupliquer_positions_stationnaires(positions_affichees)

        for horodatage, lat, lon, _ in reversed(positions_affichees):
            self.arbre.insert("", "end", values=(horodatage, f"{lat:.5f}", f"{lon:.5f}"))

        # Le tracé utilise TOUJOURS toutes les positions filtrées, pour un trajet fidèle
        coordonnees_trajet = [(lat, lon) for _, lat, lon, _ in positions_filtrees]
        if len(coordonnees_trajet) > 1:
            self.trajet = self.widget_carte.set_path(
                coordonnees_trajet, color="#5b9bd5", width=2
            )

        for i, (horodatage, lat, lon, _) in enumerate(positions_affichees):
            est_dernier = i == len(positions_affichees) - 1
            texte = "Position actuelle" if est_dernier else horodatage[:16]  # sans les secondes
            marqueur = self.widget_carte.set_marker(
                lat, lon, text=texte, font=("Helvetica", 9)
            )
            self.marqueurs.append(marqueur)

        derniere_lat, derniere_lon = coordonnees_trajet[-1]
        self.widget_carte.set_position(derniere_lat, derniere_lon)
        self.widget_carte.set_zoom(15)

        self.label_derniere_maj.config(
            text=f"Dernière position : {positions_filtrees[-1][0]}  "
                 f"({len(positions_affichees)} points affichés / {len(positions_filtrees)} sur la période)"
        )

    def ouvrir_reglages_collecte(self):
        FenetreReglagesCollecte(self)


    def demander_effacement(self):
        confirme = messagebox.askyesno(
            "Effacer les positions",
            "Voulez-vous vraiment effacer tout l'historique des positions ?\n"
            "Le fichier positions.log sera vidé définitivement.",
        )
        if confirme:
            effacer_positions()
            self.rafraichir()

if __name__ == "__main__":
    app = VisualiseurGeoloc()
    app.mainloop()
