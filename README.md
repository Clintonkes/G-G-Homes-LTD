# RentEase Nigeria

WhatsApp-native rental platform built with FastAPI, async SQLAlchemy, Redis-backed conversation state, Cloudinary media uploads, and Paystack payment orchestration.

## Structure

- `main.py`: FastAPI entrypoint.
- `api/`: Versioned endpoint routers.
- `core/`: Settings, auth, and dependency guards.
- `database/`: Base, session, `models.py`, `schema.py`, and admin bootstrap.
- `services/`: Bot engine, WhatsApp, payments, media, notifications, and property search.
- `utils/`: Scheduler and helper utilities.

## Quick Start

1. Copy `.env.example` to `.env`.
2. Install dependencies with `pip install -r requirements.txt`.
3. Run `uvicorn main:app --reload --port 8000`.
4. Open `/docs`.
