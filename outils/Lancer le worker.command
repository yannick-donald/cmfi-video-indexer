#!/bin/bash
# Lance le worker de transcription sur cette machine. Il interroge
# l'application en ligne, prend ce qui n'est pas encore transcrit, le traite
# et repose le résultat. Rien ne tourne sur Render : la transcription demande
# un processeur que le serveur n'a pas.
#
# Double-cliquez ce fichier depuis le Finder.

cd "$(dirname "$0")/.." || exit 1

BLEU=$'\033[1;36m'; VERT=$'\033[1;32m'; ROUGE=$'\033[1;31m'; JAUNE=$'\033[1;33m'; GRIS=$'\033[0;90m'; RAZ=$'\033[0m'

URL="${CMFI_URL:-https://cmfi-video-indexer.org}"

echo ""
echo "${BLEU}  Worker de transcription — CMFI${RAZ}"
echo "${GRIS}  ${URL}${RAZ}"
echo ""

fermer() { echo ""; read -r -p "  Entrée pour fermer… "; exit "${1:-0}"; }

# --- L'environnement Python ---
PY=".venv-local/bin/python"
[ -x "$PY" ] || PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "${ROUGE}  Aucun environnement Python dans le dépôt.${RAZ}"
  echo "${GRIS}  Attendu : .venv-local/bin/python${RAZ}"
  fermer 1
fi

# --- Le jeton ---
JETON="${WORKER_TOKEN:-}"
[ -z "$JETON" ] && [ -f .env ] && JETON=$(grep -m1 '^WORKER_TOKEN=' .env | cut -d= -f2-)
if [ -z "$JETON" ]; then
  echo "${ROUGE}  Aucun jeton configuré.${RAZ}"
  echo "  Lancez d'abord ${GRIS}« Configurer le jeton.command »${RAZ}, juste à côté."
  fermer 1
fi

# --- Le serveur nous reconnaît-il ? On le vérifie avant tout téléchargement. ---
printf "${GRIS}  Vérification du jeton…${RAZ}"
CODE=$(curl -s -m 25 -o /dev/null -w '%{http_code}' -H "x-worker-token: $JETON" "$URL/api/worker/pending?limit=1")
printf "\r\033[K"
case "$CODE" in
  200) echo "  ${VERT}✓${RAZ} Jeton accepté." ;;
  401) echo "  ${ROUGE}✗ Jeton refusé.${RAZ}"
       echo "  Celui du .env et celui de Render ne sont pas les mêmes."
       echo "${GRIS}  Corrigez la variable WORKER_TOKEN dans le tableau de bord Render.${RAZ}"
       fermer 1 ;;
  503) echo "  ${ROUGE}✗ Le pont est fermé côté serveur.${RAZ}"
       echo "${GRIS}  WORKER_TOKEN n'est pas défini dans Render.${RAZ}"
       fermer 1 ;;
  000) echo "  ${ROUGE}✗ Serveur injoignable.${RAZ} ${GRIS}(${URL})${RAZ}"; fermer 1 ;;
  *)   echo "  ${ROUGE}✗ Réponse inattendue : HTTP ${CODE}${RAZ}"; fermer 1 ;;
esac

# --- Le disque, avant de télécharger des gigaoctets ---
LIBRE=$(df -g . | tail -1 | awk '{print $4}')
if [ "${LIBRE:-0}" -lt 10 ]; then
  echo "  ${JAUNE}⚠${RAZ}  ${LIBRE} Go libres seulement. Le worker sautera les grosses vidéos."
fi
echo ""

# --- Quoi faire ---
# Chaque intitulé dit exactement ce qui sera lancé. Un recensement qui ne
# porte pas sur le même périmètre que la transcription qui suit ne sert à rien :
# sans filtre, la liste remonte de vieilles vidéos brutes de taille inconnue,
# et non les découpes.
echo "  ${GRIS}Les découpes${RAZ}"
echo "  ${BLEU}1${RAZ}  Les recenser  ${GRIS}— ne télécharge rien${RAZ}"
echo "  ${BLEU}2${RAZ}  Les transcrire  ${GRIS}— à commencer par là${RAZ}"
echo ""
echo "  ${GRIS}Tout le corpus, découpes et vidéos brutes${RAZ}"
echo "  ${BLEU}3${RAZ}  Le recenser"
echo "  ${BLEU}4${RAZ}  Le transcrire  ${GRIS}— plusieurs jours de calcul${RAZ}"
echo ""
echo "  ${BLEU}5${RAZ}  Quitter"
echo ""
read -r -p "  Votre choix [1] : " CHOIX
CHOIX="${CHOIX:-1}"
echo ""

case "$CHOIX" in
  1) ARGS=(--asset-type cut --dry-run --limit 200) ;;
  2) ARGS=(--asset-type cut) ;;
  3) ARGS=(--dry-run --limit 200) ;;
  4) echo "${JAUNE}  Le corpus entier représente 1,28 To à télécharger et"
     echo "  plusieurs centaines d'heures de calcul.${RAZ}"
     echo ""
     read -r -p "  Confirmer ? [o/N] " SUR
     case "$SUR" in o|O|oui|Oui) ARGS=() ;; *) echo ""; echo "${GRIS}  Annulé.${RAZ}"; fermer 0 ;; esac
     echo "" ;;
  *) echo "${GRIS}  Annulé.${RAZ}"; fermer 0 ;;
esac

case " ${ARGS[*]} " in
  *" --dry-run "*) ;;
  *) echo "${GRIS}  Ctrl-C arrête à tout moment : seule la vidéo en cours est perdue,${RAZ}"
     echo "${GRIS}  celles déjà déposées ne seront pas refaites.${RAZ}"
     echo "" ;;
esac

WORKER_TOKEN="$JETON" "$PY" scripts/worker_distant.py --url "$URL" "${ARGS[@]}"
CODE=$?

echo ""
[ $CODE -eq 0 ] && echo "  ${VERT}✓${RAZ} Terminé." || echo "  ${JAUNE}Arrêté${RAZ} ${GRIS}(code $CODE)${RAZ}"
fermer 0
