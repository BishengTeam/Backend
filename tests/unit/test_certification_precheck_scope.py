"""Precheck must account for blocked rows too; eligibility checks still decide export access."""
import sqlite3

import pytest
from sqlalchemy.dialects import sqlite

from app.domain.h3c.src.index import H3cRegistration
from app.schemas.h3c_registration import H3cExportPrecheckRequest
from app.schemas.nisp import NispExportPrecheckRequest
from app.services.h3c_export import H3cExportService


@pytest.mark.parametrize('schema', [H3cExportPrecheckRequest, NispExportPrecheckRequest])
def test_no_implicit_approved_filter(schema):
    request = schema(batch_id=14)
    assert request.status is None
    assert schema(batch_id=14, status='approved').status == 'approved'
    assert schema(batch_id=14, payment_state='closed', has_valid_export=False).has_valid_export is False


def test_h3c_precheck_query_keeps_closed_rows_and_applies_explicit_filters():
    db = sqlite3.connect(':memory:')
    table = H3cRegistration.__tablename__
    db.execute(f'CREATE TABLE {table} (id INTEGER, batch_id INTEGER, status TEXT, order_id INTEGER)')
    db.executemany(f'INSERT INTO {table} VALUES (?, ?, ?, ?)', [(1,14,'approved',1),(2,14,'refunded_closed',2),(3,15,'approved',3)])
    db.execute('CREATE TABLE "order" (id INTEGER, status TEXT)')
    db.executemany('INSERT INTO "order" VALUES (?, ?)', [(1,'paid'),(2,'refunded'),(3,'paid')])

    def ids(**kwargs):
        stmt = H3cExportService._precheck_stmt(H3cExportPrecheckRequest(batch_id=14, **kwargs)).with_only_columns(H3cRegistration.id)
        sql = str(stmt.compile(dialect=sqlite.dialect(), compile_kwargs={'literal_binds': True}))
        return [r[0] for r in db.execute(sql)]

    try:
        assert ids() == [1, 2]
        assert ids(status='approved') == [1]
        assert ids(payment_state='closed') == [2]
        assert ids(registration_ids=[2], status='approved') == [2]
    finally:
        db.close()
