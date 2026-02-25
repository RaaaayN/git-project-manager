# Project OS Agent

Agent IA de gouvernance de projet GitLab : CLI Python et serveur webhook pour maintenir automatiquement les fichiers de pilotage (specs, statut, décisions) et proposer des actions (MR, issues).

---

## Prérequis

- **Python 3.10+**
- Accès au dépôt (clone Git)

---

## Installation

### 1. Cloner le dépôt

```bash
git clone https://github.com/votre-org/git-project-manager.git
cd git-project-manager
```

### 2. Environnement virtuel (recommandé)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate
```

### 3. Installer les dépendances

```bash
pip install -r requirements.txt
```

La seule dépendance est **PyYAML** (lecture de la configuration YAML).

### 4. Vérifier l’installation

Depuis la racine du dépôt :

```bash
python tools/project_os_agent.py --help
python tools/project_os_agent.py bootstrap --dry-run
```

---

## Configuration

Le fichier **`.project-os-agent.yml`** à la racine du dépôt contient toute la configuration (templates, webhook, GitLab, garde-fous, KPIs).

- **Mode dry-run** : par défaut `dry_run: true` ; aucune écriture sans `--apply`.
- **GitLab** : `gitlab.enabled: false` par défaut ; définir `GITLAB_TOKEN` si vous activez l’intégration.
- **Webhook** : `webhook.secret_env` (ex. `GITLAB_WEBHOOK_SECRET`) pour valider les requêtes entrantes.

Variables d’environnement utiles :

| Variable | Usage |
|----------|--------|
| `GITLAB_TOKEN` | Token API GitLab (phase 4 – act) |
| `GITLAB_WEBHOOK_SECRET` | Secret de validation des webhooks |
| `GEMINI_API_KEY` | Optionnel, si phase3 LLM activée |

---

## Démarrage rapide

| Action | Commande |
|--------|----------|
| Prévisualiser la création des fichiers manquants | `python tools/project_os_agent.py bootstrap --dry-run` |
| Créer les fichiers manquants | `python tools/project_os_agent.py bootstrap --apply` |
| Lancer les tests | `python -m unittest discover -s tests -v` |
| Diagnostic config / dépôt | `python tools/project_os_agent.py diagnose --stdout-only` |
| Rapport KPIs (stdout) | `python tools/project_os_agent.py report-kpis --stdout-only` |
| Démarrer le serveur webhook (port 8080) | `python tools/project_os_agent.py serve-webhook --port 8080` |

---

## Documentation

- **AGENTS.md** — Instructions pour les agents / Cursor (commandes, tests, pièges).
- **PROJECT_DOCUMENTATION.md** — Documentation technique et fonctionnelle complète.
- **AGENT_GLOBAL_SPEC.md** — Spécification globale de l’agent (mission, règles, KPIs).
