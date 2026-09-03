#!/bin/bash
# Extrait la piste audio d'une ou plusieurs vidéos, au format qu'attend Whisper :
# WAV mono 16 kHz. Le fichier .wav est déposé à côté de la vidéo d'origine.
#
# Double-cliquez ce fichier depuis le Finder. Une fenêtre de choix s'ouvre.
# On peut aussi le lancer en ligne de commande avec des chemins en arguments.

cd "$(dirname "$0")" || exit 1

BLEU=$'\033[1;36m'; VERT=$'\033[1;32m'; ROUGE=$'\033[1;31m'; GRIS=$'\033[0;90m'; RAZ=$'\033[0m'

echo ""
echo "${BLEU}  Extraction audio — CMFI${RAZ}"
echo "${GRIS}  WAV mono 16 kHz, déposé à côté de la vidéo${RAZ}"
echo ""

# --- ffmpeg est-il là ? ---
FFMPEG=$(command -v ffmpeg)
if [ -z "$FFMPEG" ]; then
  for essai in /usr/local/bin/ffmpeg /opt/homebrew/bin/ffmpeg; do
    [ -x "$essai" ] && FFMPEG="$essai" && break
  done
fi
if [ -z "$FFMPEG" ]; then
  echo "${ROUGE}  ffmpeg est introuvable.${RAZ}"
  echo "  Installez-le :  brew install ffmpeg"
  echo ""
  read -r -p "  Entrée pour fermer… "
  exit 1
fi
FFPROBE="${FFMPEG%ffmpeg}ffprobe"

# --- Quelles vidéos ? ---
if [ "$#" -gt 0 ]; then
  FICHIERS=("$@")
else
  echo "${GRIS}  Choisissez une ou plusieurs vidéos…${RAZ}"
  CHOIX=$(osascript -e 'set f to choose file with prompt "Choisissez une ou plusieurs vidéos" with multiple selections allowed' \
                    -e 'set t to ""' \
                    -e 'repeat with i in f' \
                    -e 'set t to t & POSIX path of i & linefeed' \
                    -e 'end repeat' \
                    -e 'return t' 2>/dev/null)
  if [ -z "$CHOIX" ]; then
    echo "${GRIS}  Annulé.${RAZ}"; echo ""; exit 0
  fi
  FICHIERS=()
  while IFS= read -r ligne; do
    [ -n "$ligne" ] && FICHIERS+=("$ligne")
  done <<< "$CHOIX"
fi

TOTAL=${#FICHIERS[@]}
REUSSIS=0
echo "  ${TOTAL} fichier(s) à traiter."
echo ""

for VIDEO in "${FICHIERS[@]}"; do
  NOM=$(basename "$VIDEO")

  if [ ! -f "$VIDEO" ]; then
    echo "  ${ROUGE}✗${RAZ} ${NOM} — introuvable"
    continue
  fi

  # Une vidéo muette occuperait le traitement pour rien.
  if [ -x "$FFPROBE" ]; then
    PISTE=$("$FFPROBE" -v error -select_streams a -show_entries stream=codec_type -of csv=p=0 "$VIDEO" 2>/dev/null)
    if [ -z "$PISTE" ]; then
      echo "  ${ROUGE}✗${RAZ} ${NOM} — aucune piste audio"
      continue
    fi
  fi

  DOSSIER=$(dirname "$VIDEO")
  BASE="${NOM%.*}"
  SORTIE="${DOSSIER}/${BASE}.wav"
  PARTIEL="${SORTIE}.partial"

  TAILLE_IN=$(stat -f%z "$VIDEO" 2>/dev/null || echo 0)
  printf "  … %s (%.0f Mo)\n" "$NOM" "$(echo "$TAILLE_IN / 1048576" | bc -l)"

  DEBUT=$(date +%s)
  # Écriture sous un nom temporaire : une interruption ne laisse jamais
  # un .wav tronqué qui passerait ensuite pour valide.
  if "$FFMPEG" -nostdin -loglevel error -y -i "$VIDEO" \
       -vn -ac 1 -ar 16000 -c:a pcm_s16le -f wav "$PARTIEL" 2>/dev/null; then
    mv "$PARTIEL" "$SORTIE"
    FIN=$(date +%s)
    TAILLE_OUT=$(stat -f%z "$SORTIE" 2>/dev/null || echo 0)
    printf "  ${VERT}✓${RAZ} %s  ${GRIS}%.1f Mo · %d× plus léger · %d s${RAZ}\n\n" \
      "$(basename "$SORTIE")" \
      "$(echo "$TAILLE_OUT / 1048576" | bc -l)" \
      "$(echo "$TAILLE_IN / $TAILLE_OUT" | bc 2>/dev/null || echo 0)" \
      "$((FIN - DEBUT))"
    REUSSIS=$((REUSSIS + 1))
  else
    rm -f "$PARTIEL"
    echo "  ${ROUGE}✗${RAZ} ${NOM} — ffmpeg a refusé ce fichier"
    echo ""
  fi
done

echo "  ${VERT}${REUSSIS}${RAZ} sur ${TOTAL} traité(s)."
echo "${GRIS}  Les .wav sont à côté de leurs vidéos.${RAZ}"
echo ""
read -r -p "  Entrée pour fermer… "
