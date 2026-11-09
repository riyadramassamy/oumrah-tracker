# Oumrah 2027 · suivi des prix

Relève chaque jour les prix des vols (La Réunion → Jeddah, direct ou avec stopover
Doha / Abu Dhabi / Dubaï), trace l'historique et t'alerte sur Telegram.

Le dashboard est `index.html`. Les prix viennent de Travelpayouts (Aviasales).

## Installation (une seule fois, ~15 min)

1. Crée un compte sur github.com, puis un dépôt **public** (ex. `oumrah-tracker`).
2. Envoie-y tous les fichiers de ce dossier (Add file > Upload files),
   y compris `.github/workflows/daily.yml`. Sur téléphone, c'est plus simple depuis un
   ordinateur, ou demande à Hassib de le faire avec toi.
3. Settings > Secrets and variables > Actions > New repository secret.
   Crée ces trois secrets :
   - `TRAVELPAYOUTS_TOKEN` : ton token API Travelpayouts
   - `TELEGRAM_BOT_TOKEN` : le token donné par @BotFather
   - `TELEGRAM_CHAT_ID` : ton identifiant Telegram (voir plus bas)
4. Settings > Pages > Source : branche `main`, dossier `/ (root)`. Ton dashboard sera sur
   `https://TON-PSEUDO.github.io/oumrah-tracker/`.
5. Onglet Actions > « Relevé quotidien des prix » > Run workflow, pour lancer le premier relevé.
   Ensuite c'est automatique, tous les jours à 6 h (heure de La Réunion).
6. Les données de démonstration seront remplacées au premier relevé réussi.

## Trouver ton TELEGRAM_CHAT_ID

1. Envoie un message quelconque à ton bot dans Telegram.
2. Ouvre dans ton navigateur : `https://api.telegram.org/botTON_TOKEN/getUpdates`
3. Cherche `"chat":{"id":123456789` : ce nombre est ton identifiant.

## Régler la recherche

Tout est dans `config.json` : année, nombre de voyageurs, nuits sur place (8 à 10 ici),
durée du stopover (6 à 7 jours), aéroports de stopover, et seuils d'alerte
(`alert_below`, par exemple `"direct": 1500` pour être prévenu sous 1 500 €).
Tu es aussi alerté à chaque nouveau plus bas (baisse d'au moins 2 %).

## Limites à connaître

- Les prix sont ceux en cache d'Aviasales, relevés une fois par jour : une tendance fiable,
  pas un prix garanti. Vérifie toujours le prix final avant de réserver.
- Le prix enfant est estimé à 75 % du tarif adulte (`child_price_factor`).
- Version 1 : vols seulement. Hôtels, visa, train et transferts viennent ensuite.

## Tester en local

    python fetch_prices.py --mock        # données factices
    python make_demo_history.py          # historique factice
    python -m http.server                # puis ouvre http://localhost:8000
