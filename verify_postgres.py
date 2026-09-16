"""Real PostgreSQL storage regression checks in a disposable, unique schema.

Run inside the Compose app via stdin (see README). No mocks or data resets.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url


def verify():
    url = make_url(os.environ["RELAY_DATABASE_URL"])
    assert url.drivername == "postgresql+psycopg"
    schema = "relay_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    os.environ["RELAY_DATABASE_URL"] = url.update_query_dict(
        {"options": f"-csearch_path={schema}"}
    ).render_as_string(hide_password=False)
    try:
        from database import Attempt, Task, db_session, engine, init_db, utcnow, as_db_time
        from errors import RelayError
        from storage import (register_agent, create_task, claim_one, commit_terminal,
                             heartbeat, recover_expired, task_for_participant)
        init_db()
        sender = register_agent("pg-sender", None)["agent_id"]
        recipient = register_agent("pg-recipient", None)["agent_id"]

        def expect_error(code, operation):
            try:
                operation()
            except RelayError as error:
                assert error.code == code
            else:
                raise AssertionError(f"Expected {code}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            sent = list(pool.map(lambda _: create_task(sender, recipient, "hello", "same-key"), range(16)))
        assert len({t["task_id"] for t in sent}) == 1
        expect_error("idempotency_conflict", lambda: create_task(sender, recipient, "different", "same-key"))
        print("PASS concurrent sender-scoped idempotency and conflicting payload")

        ids = {sent[0]["task_id"]}
        ids.update(create_task(sender, recipient, "hello", None)["task_id"] for _ in range(15))
        # A held oldest-row lock must not block a second worker's claim.
        with db_session() as db:
            oldest = db.scalar(select(Task).order_by(Task.created_at, Task.id).limit(1).with_for_update())
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(claim_one, recipient, "skip-locked").result(timeout=5)
            assert first["task_id"] != oldest.id
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = [first] + list(pool.map(lambda i: claim_one(recipient, str(i)), range(15)))
        assert {c["task_id"] for c in claims} == ids
        assert len(claims) == len(ids)
        assert claim_one(recipient, "empty") is None
        print("PASS SKIP LOCKED and 16 concurrent claims without overlap")

        claim = claims[0]
        task_id, token = claim["task_id"], claim["claim_token"]
        heartbeat(task_id, recipient, token)
        def complete():
            return commit_terminal(task_id, recipient, token, action="complete", value="HELLO")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: complete(), range(8)))
        assert all(r["status"] == "completed" for r in results)
        expect_error("conflicting_terminal", lambda: commit_terminal(
            task_id, recipient, token, action="fail", value="conflict"))
        assert task_for_participant(task_id, sender).output == "HELLO"
        print("PASS heartbeat, concurrent terminal retries, conflicting terminal action")

        stale = claims[1]
        with db_session() as db:
            attempt = db.scalar(select(Attempt).where(Attempt.task_id == stale["task_id"]))
            attempt.lease_expires_at = as_db_time(utcnow() - timedelta(seconds=1))
        expect_error("stale_claim", lambda: commit_terminal(
            stale["task_id"], recipient, stale["claim_token"], action="complete", value="late"))
        expect_error("stale_claim", lambda: heartbeat(stale["task_id"], recipient, stale["claim_token"]))
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(lambda _: recover_expired(), range(4))) == 1
        replacement = claim_one(recipient, "replacement")
        assert replacement["task_id"] == stale["task_id"]
        assert replacement["attempt"] == 2
        assert replacement["claim_token"] != stale["claim_token"]
        print("PASS expired-token rejection, concurrent recovery, redelivery")
        print("PostgreSQL storage regression checks: 4 passed")
    finally:
        if "engine" in locals():
            engine.dispose()
        # The identifier is generated above, never supplied by the environment.
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


if __name__ == "__main__":
    verify()
