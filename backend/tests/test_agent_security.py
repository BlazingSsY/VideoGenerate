import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import AgentSession, AgentTurn, Upload, User
from app.routers import agent, conversations
from app.security import current_user


class AgentSecurityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            first = User(username="first", password_hash="x", role="user")
            second = User(username="second", password_hash="x", role="user")
            db.add_all([first, second]); db.commit(); db.refresh(first); db.refresh(second)
            self.first_id, self.second_id, self.current = first.id, second.id, first.id
        app = FastAPI(); app.include_router(agent.router); app.include_router(conversations.router)
        def db_override():
            db = self.Session()
            try: yield db
            finally: db.close()
        def user_override():
            with self.Session() as db: return db.get(User, self.current)
        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[current_user] = user_override
        self.client = TestClient(app)

    def tearDown(self): self.client.close(); self.engine.dispose()

    def test_local_upload_from_another_user_is_rejected(self):
        with self.Session() as db:
            db.add(Upload(filename="private.png", user_id=self.second_id, kind="image")); db.commit()
        response = self.client.post("/api/agent/plans", json={"surface": "studio", "user_input": "让图动起来", "reference_media": [{"kind": "image", "url": "/media/uploads/private.png", "name": "private"}]})
        plan = response.json()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.client.post(f"/api/agent/plans/{plan['id']}/accept").status_code, 403)

    def test_plan_can_only_be_accepted_once(self):
        response = self.client.post("/api/agent/plans", json={"surface": "studio", "user_input": "日落海边"})
        self.assertEqual(response.status_code, 201)
        turn_id = response.json()["id"]
        self.assertEqual(self.client.post(f"/api/agent/plans/{turn_id}/accept").status_code, 200)
        self.assertEqual(self.client.post(f"/api/agent/plans/{turn_id}/accept").status_code, 409)


if __name__ == "__main__": unittest.main()
