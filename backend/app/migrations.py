"""Compatibility migrations which create_all cannot apply to existing tables."""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def migrate_agent_constraints(engine: Engine) -> None:
    """Enforce run/task identity without discarding historical execution records."""
    constraints = (
        ("agent_runs", "uq_agent_run_turn", ("turn_id",)),
        ("agent_tasks", "uq_agent_task_node", ("run_id", "node_id")),
    )
    with engine.begin() as connection:
        inspector = inspect(connection)
        missing = []
        for table, name, columns in constraints:
            unique_columns = {
                tuple(item["column_names"])
                for item in inspector.get_unique_constraints(table)
            } | {
                tuple(item["column_names"])
                for item in inspector.get_indexes(table) if item.get("unique")
            }
            if columns in unique_columns:
                continue
            grouped = ", ".join(columns)
            duplicate = connection.execute(text(
                f"SELECT {grouped} FROM {table} GROUP BY {grouped} HAVING COUNT(*) > 1 LIMIT 1"
            )).first()
            if duplicate is not None:
                raise RuntimeError(
                    f"无法迁移 {name}：{table} 存在重复执行记录，请核对并处理后重试；原始记录未删除"
                )
            missing.append((table, name, grouped))
        # Preflight both tables before issuing any DDL (SQLite DDL may autocommit).
        for table, name, columns in missing:
            connection.execute(text(f"CREATE UNIQUE INDEX {name} ON {table} ({columns})"))

        for table, name, column in (
            ("agent_runs", "ix_agent_runs_canvas_id", "canvas_id"),
            ("agent_tasks", "ix_agent_tasks_canvas_node_id", "canvas_node_id"),
        ):
            if name not in {item["name"] for item in inspector.get_indexes(table)}:
                connection.execute(text(f"CREATE INDEX {name} ON {table} ({column})"))
