import unittest

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.migrations import migrate_agent_constraints
from app import models  # register fresh schema


class AgentConstraintMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)

    def legacy_schema(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE agent_runs (id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, canvas_id TEXT)"))
            connection.execute(text("CREATE TABLE agent_tasks (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_id TEXT NOT NULL, canvas_node_id TEXT)"))
            connection.execute(text("INSERT INTO agent_runs VALUES ('run-1', 'turn-1', 'canvas-1')"))
            connection.execute(text("INSERT INTO agent_tasks VALUES ('task-1', 'run-1', 'shot-1', 'node-1')"))

    def test_old_database_preserves_rows_and_rejects_duplicate_identity(self):
        self.legacy_schema()
        migrate_agent_constraints(self.engine)
        migrate_agent_constraints(self.engine)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM agent_runs")).scalar(), 1)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM agent_tasks")).scalar(), 1)
        for sql in (
            "INSERT INTO agent_runs VALUES ('run-2', 'turn-1', 'canvas-1')",
            "INSERT INTO agent_tasks VALUES ('task-2', 'run-1', 'shot-1', 'node-1')",
        ):
            with self.assertRaises(IntegrityError), self.engine.begin() as connection:
                connection.execute(text(sql))

    def test_duplicates_stop_migration_without_deleting_or_partial_indexes(self):
        self.legacy_schema()
        with self.engine.begin() as connection:
            connection.execute(text("INSERT INTO agent_tasks VALUES ('task-2', 'run-1', 'shot-1', 'node-1')"))
        with self.assertRaisesRegex(RuntimeError, "重复执行记录"):
            migrate_agent_constraints(self.engine)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM agent_tasks")).scalar(), 2)
        self.assertEqual(inspect(self.engine).get_indexes("agent_runs"), [])

    def test_fresh_database_can_run_compatibility_migration_repeatedly(self):
        Base.metadata.create_all(self.engine)
        migrate_agent_constraints(self.engine)
        migrate_agent_constraints(self.engine)
        self.assertIn("uq_agent_run_turn", {item["name"] for item in inspect(self.engine).get_unique_constraints("agent_runs")})
