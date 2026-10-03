#!/data/data/com.termux/files/usr/bin/sh
# Lancé automatiquement par Termux:Boot au démarrage du S23.
termux-wake-lock
cd ~
python position_boucle.py >> ~/geoloc.log 2>&1
