from datetime import datetime, timezone, timedelta

from app.models import AgentSession, AgentTurn
from app.models import AgentChatMessage, User
from app.routers import agent
from unittest.mock import patch
import asyncio
from test_agent_v3 import AgentV3TestBase


class WorkspaceHistoryTests(AgentV3TestBase):
    def test_reconnect_after_turn_queue_cleanup_returns_reply_and_finishes(self):
        now = datetime.now(timezone.utc)
        with self.Session() as db:
            db.add(AgentSession(id='reconnect-session', user_id=self.admin_id, surface='canvas', target_id='canvas-a'))
            db.add(AgentTurn(id='reconnect-turn', session_id='reconnect-session', user_input='你好', status='answered', expires_at=now))
            db.add(AgentChatMessage(session_id='reconnect-session', turn_id='reconnect-turn', role='assistant', content='你好，开始创作吧'))
            db.commit()

        async def replay():
            with self.Session() as db:
                response = await agent.turn_events('reconnect-turn', 0, db, db.get(User, self.admin_id))
                iterator = response.body_iterator
                try:
                    first = await asyncio.wait_for(anext(iterator), timeout=0.3)
                    second = await asyncio.wait_for(anext(iterator), timeout=0.3)
                finally:
                    await iterator.aclose()
                self.assertIn('你好，开始创作吧', first)
                self.assertIn('event: done', second)

        with patch.object(agent, 'SessionLocal', self.Session):
            asyncio.run(replay())

    def test_history_keeps_old_sessions_and_isolates_user_and_canvas(self):
        now = datetime.now(timezone.utc)
        with self.Session() as db:
            for session_id, user_id, target, age in [
                ('old', self.admin_id, 'canvas-a', 2),
                ('new', self.admin_id, 'canvas-a', 1),
                ('other-canvas', self.admin_id, 'canvas-b', 0),
                ('other-user', self.other_user_id, 'canvas-a', 0),
            ]:
                db.add(AgentSession(id=session_id, user_id=user_id, surface='canvas', target_id=target))
                db.add(AgentTurn(session_id=session_id, user_input=f'创意 {session_id}', status='answered', created_at=now-timedelta(days=age), expires_at=now))
            db.commit()
        response = self.client.get('/api/agent/sessions', params={'surface': 'canvas', 'target_id': 'canvas-a'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.json()], ['new', 'old'])
        self.assertEqual(response.json()[1]['title'], '创意 old')
