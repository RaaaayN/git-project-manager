# API_SPEC.md

## Authentication

POST /api/auth/login

Request:
{
  "email": "string",
  "password": "string"
}

Response:
{
  "access_token": "string",
  "refresh_token": "string"
}

---

## User Profile

GET /api/users/me

Headers:
Authorization: Bearer <access_token>

Response:
{
  "id": "uuid",
  "email": "string",
  "role": "user"
}
