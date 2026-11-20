.PHONY: backend frontend demo test

backend:
	cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8050

frontend:
	cd frontend && npm run dev

demo:
	cd backend && python -c "from app.db import SessionLocal, init_db; from app.services import reset_demo; init_db(); db=SessionLocal(); print(reset_demo(db)); db.close()"

test:
	cd backend && PYTHONPATH=. pytest -q
