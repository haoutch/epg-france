# EPG France pour TiviMate

Ce dépôt publie uniquement un guide XMLTV français. Il ne doit contenir aucune playlist IPTV, URL Xtream, nom d'utilisateur ou mot de passe.

## Mise en route

1. Créer un dépôt GitHub public nommé `epg-france` sans ajouter de README.
2. Déposer tout le contenu de ce dossier à la racine du dépôt.
3. Ouvrir **Settings → Pages** et choisir **GitHub Actions** comme source.
4. Ouvrir **Actions → Mettre à jour et publier l'EPG → Run workflow**.
5. Après réussite, l'URL sera :

   `https://VOTRE-PSEUDO.github.io/epg-france/epg_france.xml.gz`

6. Dans TiviMate : **Paramètres → EPG → Sources EPG → Ajouter une source**, puis associer cette source à la playlist Xtream.

## Automatisation

Le workflow s'exécute chaque jour à 03:27 UTC et peut aussi être lancé manuellement.

## Sécurité

Ne jamais ajouter au dépôt :

- une playlist `.m3u` ou `.m3u8` ;
- une URL contenant `username=` ou `password=` ;
- les identifiants Xtream.
