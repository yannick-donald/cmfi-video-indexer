# Environnement UAT — PostgreSQL + pgvector

La production tourne sur SQLite depuis `main` et ne bouge pas. La branche `uat`
fait tourner **le même code** sur PostgreSQL ; `DATABASE_URL` choisit le moteur.

## Installer PostgreSQL sur macOS 13 (Intel)

Homebrew ne fournit plus de binaire précompilé pour Ventura : tout se compile.
Comptez une vingtaine de minutes.

```bash
brew install postgresql@17
```

`pgvector` échoue ensuite, parce que la formule `postgresql@17` est *keg-only* :
elle n'est jamais liée dans `/usr/local`, alors que `pg_config` y annonce ses
chemins. Trois liens manquent, et `pgxs.mk` reste introuvable.

```bash
brew link --force --overwrite postgresql@17

C=/usr/local/Cellar/postgresql@17/17.11
ln -s "$C/lib/postgresql"     /usr/local/lib/postgresql@17
ln -s "$C/share/postgresql"   /usr/local/share/postgresql@17
ln -s "$C/include/postgresql" /usr/local/include/postgresql@17
```

pgvector se construit alors normalement, depuis la source que brew a déjà
téléchargée :

```bash
tar xzf ~/Library/Caches/Homebrew/downloads/*pgvector-0.8.6.tar.gz
cd pgvector-0.8.6 && make && make install
```

## Démarrer le serveur

`LC_ALL` doit être défini, sans quoi le postmaster devient multithread au
démarrage et refuse de se lancer — panne classique de PostgreSQL sur macOS.

```bash
export LC_ALL=C
initdb -D /usr/local/var/postgresql@17 -E UTF8 --locale=C
pg_ctl -D /usr/local/var/postgresql@17 -l /usr/local/var/log/postgresql@17.log start
createdb ztf_uat
psql -d ztf_uat -c "CREATE EXTENSION vector;"
```

## Configurer l'application

```bash
echo "DATABASE_URL=postgresql://$(whoami)@localhost:5432/ztf_uat" > .env.uat
pip install -r requirements-uat.txt
```

`.env.uat` est ignoré par git : sur un serveur distant, cette URL contient un
mot de passe.

## Migrer les données

Le script est rejouable. `--reset` remet le schéma à plat, ce qui est nécessaire
quand le schéma a changé : `CREATE TABLE IF NOT EXISTS` ne modifie pas une table
existante.

```bash
export $(grep -v '^#' .env.uat | xargs) LC_ALL=C
python -m scripts.migrate_sqlite_to_pg --reset   # migre et vérifie
python -m scripts.migrate_sqlite_to_pg --check   # compare seulement
```

Le script travaille sur une **copie** de la base SQLite, à laquelle il applique
d'abord les migrations du projet. C'est indispensable : le fichier sur disque
est en retard sur le code, qui ajoute ses colonnes au démarrage de
l'application. Au moment de l'audit, le fichier portait 68 colonnes et le code
en attendait 74.

## Vérifier la parité

C'est ce qui transforme « les tests passent » en preuve. Avec `DATABASE_URL`
défini, chaque test de comportement s'exécute **une fois par moteur**.

```bash
export $(grep -v '^#' .env.uat | xargs) LC_ALL=C
pytest tests/ -q
```

Sans `DATABASE_URL`, les tests PostgreSQL sont ignorés et la suite tourne sur
SQLite seul — ce que fait n'importe quelle machine de développement.

## Écarts de dialecte

Cinq sont traduits dans `database/driver.py`, à un seul endroit plutôt qu'aux
cinquante sites d'appel :

| SQLite | PostgreSQL |
|---|---|
| `datetime('now')` | `to_char(now() AT TIME ZONE 'UTC', …)` |
| `COLLATE NOCASE` | `LOWER()` |
| `INSERT OR IGNORE` | `ON CONFLICT DO NOTHING` |
| `videos_fts MATCH ?` | `document @@ websearch_to_tsquery('french', ?)` |
| `?` | `%s` |

Deux ne se traduisent pas par une chaîne et sont traités à part :

- **L'index plein texte.** FTS5 range 26 champs dans autant de colonnes,
  PostgreSQL les fond dans un `tsvector`. Les deux formes n'ont pas la même
  arité : `Driver.fts_upsert` tranche.
- **`SELECT DISTINCT` avec tri insensible à la casse.** PostgreSQL refuse une
  expression de tri absente de la sélection. Les trois listes concernées trient
  en Python, ce qui donne le même ordre sur les deux moteurs.

`executemany` existe sur la connexion en sqlite3, seulement sur le curseur en
psycopg ; le pilote rétablit l'équivalence.

## Ce que le portage améliore

FTS5 utilise le tokeniseur `unicode61` : ni racinisation, ni mots vides.
PostgreSQL a une configuration `french` native. « Consécration » et
« consacrer » partagent désormais la même racine.
