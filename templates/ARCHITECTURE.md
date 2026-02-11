# ARCHITECTURE.md

## Technology Stack

Frontend:
- Next.js
- React
- TailwindCSS

Backend:
- Node.js / FastAPI
- PostgreSQL
- Redis

Infrastructure:
- Docker
- GitLab CI/CD
- Cloud provider (GCP / AWS / etc.)

---

## High-Level Architecture

Client
  ↓
Frontend (SSR / SPA)
  ↓
Backend API
  ↓
Database

---

## Key Design Decisions

- REST API architecture
- Stateless backend
- Token-based authentication (JWT)
- Environment-based configuration

---

## Scalability Strategy

- Horizontal scaling of API
- Database indexing strategy
- Caching layer (Redis)
- Background workers for async tasks
