# AGENT_GLOBAL_SPEC.md

## 1. Objectif du document

Ce document definit la specification globale de l'agent IA "Project OS Agent" qui opere sur un repository GitLab pour :

- creer les fichiers de gouvernance projet manquants
- maintenir automatiquement les fichiers Markdown de reference
- detecter les ecarts entre vision produit, code, CI et documentation
- proposer les prochaines actions via Merge Request, issues et commentaires explicables

Ce document sert de base d'implementation technique et de gouvernance operationnelle.

---

## 2. Mission de l'agent

### 2.1 Mission principale

L'agent agit comme un chef de projet IA evenementiel :

- il observe les evenements GitLab
- il extrait le contexte produit et technique
- il propose des mises a jour de documentation tracees
- il cree des actions (MR/issues) priorisees et justifiees

### 2.2 Resultat attendu

Un repository ou les fichiers de pilotage sont toujours coherents, exploitables par des humains et utilisables par d'autres agents IA.

---

## 3. Perimetre fonctionnel

### 3.1 In scope

- gestion des fichiers `.md` de pilotage
- creation de fichiers manquants a partir de templates
- mise a jour des sections obsoletes apres evenements GitLab
- generation de "next steps" standardises
- ouverture de MR/issues de correction ou de clarif

### 3.2 Out of scope

- ecriture autonome de features produit complexes
- deploiement production autonome
- prise de decision produit irrevocable sans validation humaine
- modifications silencieuses sans trace Git

---

## 4. Artefacts geres et politique de synchronisation

### 4.1 Fichiers cibles

| Fichier | Role | Creation auto | Mise a jour auto |
| --- | --- | --- | --- |
| `PRODUCT_SPEC.md` | Vision, scope, user stories, DoD | Oui | Oui |
| `PROJECT_STATUS.md` | Etat reel du projet | Oui | Oui |
| `ROADMAP.md` | Plan de phases | Oui | Oui |
| `ARCHITECTURE.md` | Vue architecture et choix techniques | Oui | Oui |
| `API_SPEC.md` | Contrats API de reference | Oui | Oui |
| `SECURITY.md` | Regles securite et conformite | Oui | Oui |
| `AGENTS.md` | Roles, actions autorisees/interdites | Oui | Oui |
| `CLAUDE.md` | Regles LLM, conventions repo | Oui | Oui |
| `DECISIONS.md` | Journal ADR light | Oui | Oui |
| `README.md` | Vue d'ensemble et onboarding | Oui | Oui |
| `DOCUMENTATION.md` | Guide documentaire transverse | Oui | Oui |

### 4.2 Regles de synchronisation

- ne modifier que les sections impactees par un evenement
- conserver la structure et les titres existants
- ajouter un changelog synthetique dans la description de MR
- eviter les reformatages massifs non fonctionnels
- refuser toute suppression de contenu sans justification explicite

---

## 5. Capacites detaillees de l'agent

### 5.1 Bootstrap documentaire

- detecter les fichiers de gouvernance absents
- initialiser chaque fichier depuis `templates/`
- adapter automatiquement les placeholders au contexte repo

### 5.2 Maintien de coherence spec <-> execution

- comparer `PRODUCT_SPEC.md` avec etat reel dans `PROJECT_STATUS.md`
- detecter stories sans avancement associe
- detecter fonctionnalites livrees non referencees dans la spec

### 5.3 Planification automatique des prochaines etapes

- construire une liste de taches actionnables
- deduire dependances et blocages
- proposer owner potentiel et priorite

### 5.4 Standardisation des issues

- verifier presence du contexte, impact, acceptance criteria
- completer les sections manquantes avec un template standard
- tagger les issues "ready", "needs-info", "blocked"

### 5.5 Gouvernance CI et risque

- lire les statuts pipeline et incidents de qualite
- marquer le projet "At Risk" si echec recurrent
- proposer issue corrective avec hypothese de cause

### 5.6 Journal des decisions

- detecter decisions implicites (stack, architecture, securite)
- proposer entree `DECISIONS.md` au format ADR light
- relier chaque decision a son impact et ses alternatives

### 5.7 Explicabilite et auditabilite

- documenter "Pourquoi cette action a ete proposee"
- inclure sources de contexte (issue, MR, pipeline, fichier)
- produire des changements reversibles via MR uniquement

---

## 6. Triggers GitLab et comportements attendus

### 6.1 Merge Request merged

Actions minimales :

- mettre a jour `PROJECT_STATUS.md` (progression, taches, blocages)
- verifier impact sur `PRODUCT_SPEC.md` et `ROADMAP.md`
- mettre a jour `ARCHITECTURE.md` / `API_SPEC.md` si changement technique
- completer `DECISIONS.md` si decision implicite detectee

### 6.2 Issue created / updated

Actions minimales :

- valider la qualite de l'issue (contexte + criteres d'acceptation)
- appliquer template de normalisation si incomplet
- enrichir `PROJECT_STATUS.md` (active tasks, blockers)
- proposer next step concret en commentaire

### 6.3 Pipeline failed

Actions minimales :

- classifier l'echec (tests, lint, build, deploy, securite)
- marquer risque dans `PROJECT_STATUS.md`
- ouvrir issue de remediation si recurrent
- proposer checklist corrective rapide

### 6.4 Schedule journalier (optionnel recommande)

Actions minimales :

- verifier coherence globale des docs
- consolider KPIs dans `PROJECT_STATUS.md`
- proposer priorites des 24-72h suivantes

---

## 7. Workflow interne de decision de l'agent

### 7.1 Etape A - Extraction contexte

Sources :

- evenement GitLab entrant
- diff MR / metadata issue / rapport pipeline
- fichiers Markdown de reference

### 7.2 Etape B - Detection d'ecarts

L'agent attribue un score de divergence par categorie :

- `spec_gap`: ecart vision vs execution
- `status_gap`: avancement non reflechi
- `risk_gap`: incidents non traites
- `docs_gap`: docs incompletes/obsoletes

### 7.3 Etape C - Choix d'action

Regle :

- score faible : commentaire de recommandation
- score moyen : issue actionnable
- score eleve : MR documentaire + issue de suivi

### 7.4 Etape D - Generation de diff

- produire des modifications minimales et idempotentes
- conserver le style du repository
- ajouter un resume des impacts

### 7.5 Etape E - Validation

- verifier qu'aucune regle de securite n'est violee
- verifier que la proposition reste dans le scope de l'agent
- publier via MR/commentaire avec justification

---

## 8. Regles strictes de modification de fichiers

### 8.1 Regles globales

- aucune modification directe sur `main`
- toute action passe par branche + MR
- tout changement doit etre explicable en 5 lignes max

### 8.2 Regles par fichier

- `PRODUCT_SPEC.md`: modifier uniquement scope, stories, DoD impactes
- `PROJECT_STATUS.md`: mise a jour frequente de progression, blockers, health
- `DECISIONS.md`: une entree par decision, datee et motivee
- `AGENTS.md` et `CLAUDE.md`: mise a jour uniquement si comportement agent ou conventions changent

### 8.3 Format standard des next steps

```md
- [ ] Step: <description claire>
      Owner: <@user | team | TBD>
      Depends on: <step id | none>
      Priority: P0 | P1 | P2
      Status: Pending | Blocked | In Progress
      Evidence: <issue/mr/pipeline/file>
```

---

## 9. Securite, compliance et garde-fous

- permissions minimales GitLab (principe du moindre privilege)
- interdiction de manipuler secrets et PII
- verification de non exposition de tokens dans les diffs
- limitation de frequence des actions pour eviter bruit et cout
- mode "safe-write": si doute > seuil, ouvrir issue au lieu de modifier un fichier

---

## 10. KPIs de pilotage de l'agent

- taux de docs a jour (`docs_freshness_ratio`)
- temps moyen issue -> action proposee (`mean_response_time`)
- taux de MR agent acceptees sans rework majeur
- reduction des issues bloquees
- stabilite CI (series de pipelines verts)

---

## 11. Backlog d'implementation (taches concretes)

### Phase 1 - Foundations

- [x] Definir schema de configuration agent (`.project-os-agent.yml`)
- [x] Implementer loader des templates `templates/*.md`
- [x] Implementer moteur de creation des fichiers manquants
- [x] Ajouter mode dry-run pour visualiser les diffs sans push

### Phase 2 - Event processing

- [x] Connecter webhook GitLab (MR, issue, pipeline)
- [x] Normaliser payloads en modele interne
- [x] Implementer pipeline "extract -> detect gaps -> propose action"
- [x] Ajouter policy engine (actions autorisees/interdites)

### Phase 3 - Markdown intelligence

- [x] Parser/mettre a jour sections Markdown de maniere idempotente
- [x] Ajouter detecteur d'obsolescence documentaire
- [x] Ajouter generateur de "next steps" priorises
- [x] Ajouter writer ADR pour `DECISIONS.md`

### Phase 4 - GitLab actor

- [ ] Generer branches techniques par action
- [ ] Ouvrir MR automatiques avec template de justification
- [ ] Creer/metre a jour issues de suivi
- [ ] Publier commentaires explicatifs relies aux preuves

### Phase 5 - Reliability & governance

- [ ] Ajouter journaux d'audit d'action agent
- [ ] Ajouter guardrails securite et rate limiting
- [ ] Ajouter tests de non-regression sur fichiers templates
- [ ] Mesurer les KPIs et produire rapport hebdomadaire

---

## 12. Definition of Done de l'agent

L'agent est considere operationnel quand :

- il peut creer tous les fichiers de base depuis `templates/`
- il met a jour correctement les docs suite aux 3 triggers principaux
- il n'effectue aucune modification hors scope documente
- chaque action est tracee et explicable
- les KPIs minimaux sont disponibles dans `PROJECT_STATUS.md`

---

## 13. Risques principaux et mitigations

- Ambiguite des prompts ou des issues
  - Mitigation: templates stricts + validation de completude
- Faux positifs dans la detection d'ecarts
  - Mitigation: seuils de confiance + revue humaine via MR
- Bruit documentaire excessif
  - Mitigation: modifications minimales + regroupement des updates
- Derive du role agent (scope creep)
  - Mitigation: `AGENTS.md` et policy engine contraignants

---

## 14. Plan d'activation recommande (7 jours)

- Jour 1-2: bootstrap + dry-run
- Jour 3: webhooks MR/issues
- Jour 4: trigger pipeline + gestion "At Risk"
- Jour 5: generation next steps + issues standardisees
- Jour 6: ouverture MR automatiques + audit log
- Jour 7: revue KPI, calibrage des seuils, stabilisation
