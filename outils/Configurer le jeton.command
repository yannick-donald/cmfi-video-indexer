#!/bin/bash
# Génère un jeton de service pour le worker de transcription et l'inscrit dans
# le .env du dépôt. Le même jeton doit être posé dans Render, sinon les deux
# côtés ne se reconnaissent pas.
#
# Double-cliquez ce fichier depuis le Finder.

cd "$(dirname "$0")/.." || exit 1

BLEU=$'\033[1;36m'; VERT=$'\033[1;32m'; ROUGE=$'\033[1;31m'; JAUNE=$'\033[1;33m'; GRIS=$'\033[0;90m'; RAZ=$'\033[0m'

echo ""
echo "${BLEU}  Jeton du worker — CMFI${RAZ}"
echo "${GRIS}  Le secret partagé entre votre Mac et l'application en ligne${RAZ}"
echo ""

fermer() { echo ""; read -r -p "  Entrée pour fermer… "; exit "${1:-0}"; }

ENV_FILE=".env"
ACTUEL=""
[ -f "$ENV_FILE" ] && ACTUEL=$(grep -m1 '^WORKER_TOKEN=' "$ENV_FILE" | cut -d= -f2-)

if [ -n "$ACTUEL" ]; then
  echo "${JAUNE}  Un jeton est déjà inscrit dans le .env${RAZ} ${GRIS}(${#ACTUEL} caractères)${RAZ}"
  echo "  En générer un nouveau vous obligera à le remplacer dans Render,"
  echo "  faute de quoi le worker sera refusé."
  echo ""
  read -r -p "  Remplacer ce jeton ? [o/N] " REPONSE
  case "$REPONSE" in
    o|O|oui|Oui) ;;
    *) echo ""; echo "${GRIS}  Inchangé.${RAZ}"; fermer 0 ;;
  esac
  echo ""
fi

echo "  ${BLEU}1${RAZ}  J'ai déjà un jeton dans Render  ${GRIS}— je le colle ici${RAZ}"
echo "  ${BLEU}2${RAZ}  En générer un nouveau  ${GRIS}— à reporter ensuite dans Render${RAZ}"
echo ""
read -r -p "  Votre choix [1] : " MODE
MODE="${MODE:-1}"
echo ""

GENERE=0
if [ "$MODE" = "2" ]; then
  # 10 caractères tirés de /dev/urandom. On écarte ceux qui se confondent à
  # l'œil (0/O, 1/l/I) : ce jeton sera recopié à la main dans Render.
  JETON=$(LC_ALL=C tr -dc 'A-HJ-NP-Za-km-z2-9' < /dev/urandom | head -c 10)
  GENERE=1
  if [ ${#JETON} -ne 10 ]; then
    echo "${ROUGE}  Génération impossible.${RAZ}"; fermer 1
  fi
else
  echo "${GRIS}  Copiez la valeur de WORKER_TOKEN depuis le tableau de bord Render.${RAZ}"
  read -r -p "  Jeton : " JETON
  JETON=$(echo "$JETON" | tr -d '[:space:]')
  if [ -z "$JETON" ]; then
    echo ""; echo "${GRIS}  Rien saisi, inchangé.${RAZ}"; fermer 0
  fi
  echo ""
fi

# Écriture atomique : une interruption ne doit pas laisser un .env tronqué,
# il porte les autres secrets du projet.
TEMP=$(mktemp) || exit 1
if [ -f "$ENV_FILE" ]; then
  grep -v '^WORKER_TOKEN=' "$ENV_FILE" > "$TEMP"
  # Une dernière ligne sans retour chariot collerait nos deux variables.
  [ -s "$TEMP" ] && [ "$(tail -c1 "$TEMP" | wc -l)" -eq 0 ] && echo "" >> "$TEMP"
fi
echo "WORKER_TOKEN=$JETON" >> "$TEMP"
mv "$TEMP" "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "  ${VERT}✓${RAZ} Inscrit dans ${GRIS}$(pwd)/.env${RAZ}"
echo ""
echo "${BLEU}  Votre jeton :${RAZ}"
echo ""
echo "      ${VERT}${JETON}${RAZ}"
echo ""
if [ "$GENERE" = "1" ]; then
  echo "  ${JAUNE}Il reste à le poser dans Render :${RAZ}"
  echo "    1. dashboard.render.com → service ${GRIS}cmfi-video-indexer${RAZ} → Environment"
  echo "    2. la variable ${GRIS}WORKER_TOKEN${RAZ} → coller la valeur ci-dessus"
  echo "    3. enregistrer ${GRIS}(le service redémarre seul, une dizaine de secondes)${RAZ}"
  echo ""
fi
echo "${GRIS}  Ensuite : « Lancer le worker.command », juste à côté.${RAZ}"
echo ""
fermer 0
