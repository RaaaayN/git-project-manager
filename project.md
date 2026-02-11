# 📘 PRODUCT_SPEC.md

## Project OS Agent — AI-native Project Governance for GitLab

---

## 1. Vision & Contexte

### 1.1 Problème

Les projets logiciels souffrent de :

* spécifications obsolètes ou inexistantes
* suivi d’avancement manuel et incohérent
* documentation agents / IA non maintenue
* manque de visibilité sur les prochaines étapes réelles

Les outils actuels (issues, boards, CI) existent, mais **personne ne les orchestre intelligemment**.

---

### 1.2 Vision

Créer un **agent IA chef de projet** intégré à GitLab, capable de :

* maintenir un **cahier des charges vivant**
* standardiser le suivi du projet
* détecter automatiquement les prochaines étapes
* garantir la cohérence entre code, specs, agents et CI

> Un **Project OS** piloté par événements GitLab, pas un chatbot.

---

### 1.3 Objectifs

* Réduire la dérive entre specs et implémentation
* Automatiser le pilotage du projet
* Rendre les repos compréhensibles par les humains **et** les agents IA
* Améliorer onboarding, gouvernance et livraison

---

### 1.4 Indicateurs de succès (KPI)

* % de features livrées conformes au cahier des charges
* Temps moyen entre “specifiée” → “livrée”
* Nombre de mises à jour automatiques des fichiers `.md`
* Réduction des issues bloquées / mal définies
* CI stability (pipelines verts consécutifs)

---

## 2. Périmètre Fonctionnel

### 2.1 In Scope

* Agents déclenchés par événements GitLab
* Mise à jour automatique de fichiers `.md`
* Détection d’écarts spec ↔ code
* Génération de next steps standardisées
* Ouverture automatique de MR / issues

### 2.2 Out of Scope

* Génération complète de features complexes
* Décisions produit sans validation humaine
* Déploiement production réel
* Gestion RH ou financière

---

## 3. Utilisateurs Cibles

* Développeurs
* Tech Leads
* Product Managers
* Mainteneurs open-source
* Équipes DevOps

---

## 4. Artefacts Maintenus par l’Agent

### 4.1 Fichiers Source of Truth

| Fichier             | Rôle                             |
| ------------------- | -------------------------------- |
| `PRODUCT_SPEC.md`   | Cahier des charges produit       |
| `PROJECT_STATUS.md` | État réel du projet              |
| `AGENTS.md`         | Capacités et rôles des agents    |
| `CLAUDE.md`         | Règles LLM, conventions repo     |
| `DECISIONS.md`      | Décisions techniques (ADR light) |

---

## 5. User Stories

### US-01 — Maintien du cahier des charges

**En tant que** mainteneur
**Je veux** un cahier des charges toujours à jour
**Afin de** garder une vision claire du produit

**Critères d’acceptation**

* Toute modification est tracée via MR
* Les écarts sont signalés automatiquement

---

### US-02 — Suivi projet automatisé

**En tant que** PM / TL
**Je veux** voir l’avancement réel du projet
**Afin de** savoir quoi faire ensuite

---

### US-03 — Standardisation des issues

**En tant que** développeur
**Je veux** des issues claires et exploitables
**Afin de** éviter les allers-retours inutiles

---

### US-04 — Gouvernance IA explicable

**En tant que** équipe
**Je veux** comprendre pourquoi l’agent agit
**Afin de** lui faire confiance

---

## 6. Triggers & Comportements de l’Agent

### 6.1 Merge Request merged

Actions :

* Met à jour `PROJECT_STATUS.md`
* Vérifie conformité avec `PRODUCT_SPEC.md`
* Met à jour `AGENTS.md` si nouvelles capacités
* Met à jour `CLAUDE.md` si conventions modifiées
* Ouvre un MR si incohérence détectée

---

### 6.2 Issue créée / modifiée

Actions :

* Applique un template standard
* Détecte les infos manquantes
* Déduit si l’issue est actionable
* Propose des next steps

---

### 6.3 Pipeline failed

Actions :

* Analyse la cause probable
* Suggère une action corrective
* Crée une issue si nécessaire
* Marque le projet comme “At Risk”

---

## 7. Détection des Next Steps (Core Feature)

### 7.1 Sources analysées

* User stories non terminées
* Definition of Done non respectée
* Pipelines en échec
* Milestones GitLab
* `PROJECT_STATUS.md`

---

### 7.2 Format standard des Next Steps

```
- [ ] Step: <description>
      Owner: <user/team>
      Depends on: <step>
      Status: Pending / Blocked
```

---

## 8. Architecture Fonctionnelle

### 8.1 Agents

1. **Context Extractor Agent**
2. **Project Planner Agent**
3. **Docs Updater Agent**
4. **GitLab Actor Agent**

---

### 8.2 Flux simplifié

```
GitLab Event
   ↓
Context Extraction
   ↓
Planning & Reasoning
   ↓
Docs Diff Generation
   ↓
MR / Issue / Comment
```

---

## 9. Contraintes

### 9.1 Techniques

* Open source
* Repo public
* Actions réversibles
* Pas de modification silencieuse

### 9.2 Sécurité

* Aucun secret exposé
* Pas de PII
* Permissions minimales

### 9.3 Green / Coûts

* Pas de déclenchements inutiles
* CI non relancée sans raison
* Justification des actions coûteuses

---

## 10. Definition of Done (DoD)

Une fonctionnalité est considérée “Done” si :

* Implémentée
* Testée
* CI verte
* `PROJECT_STATUS.md` à jour
* Conforme à `PRODUCT_SPEC.md`
* Documentée dans `AGENTS.md` / `CLAUDE.md`

---

## 11. Roadmap Hackathon

### Phase 1 — Core (obligatoire)

* Triggers MR / Issue
* Mise à jour des `.md`
* Next steps auto

### Phase 2 — Governance

* Détection des écarts
* DECISIONS.md
* Health status projet

### Phase 3 — Bonus

* Green metrics
* Compliance hints
* Multi-repo support (optionnel)

---

## 12. Risques & Limitations

* Interprétation LLM imprécise
* Repos très non structurés
* Dépendance à la qualité des issues

Mitigation :

* Templates stricts
* Diff limité
* Validation via MR uniquement

---

## 13. Positionnement Hackathon

> Project OS Agent n’écrit pas du code.
> Il **orches tre le projet**, maintient la vérité produit, et agit comme un **chef de projet IA responsable**, intégré nativement à GitLab.

---
