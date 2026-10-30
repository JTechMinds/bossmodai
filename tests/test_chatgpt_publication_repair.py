"""Host-publication lifecycle regressions with a live extension session."""
import pytest
import tests.test_chatgpt as base
import db
from core import config
from core.extensions.registry import set_enabled
from db.crud import execute
from db.managed_connections import image_capability, metadata
from db.model_capabilities import set_supports_images

isolated = base.isolated


def test_metadata_loss_repairs_without_remote_polling(monkeypatch):
    cid = base.saved_account()
    base.auth.set_image_policy('test-model', 'enabled')
    monkeypatch.setattr(base.auth, 'model_catalog', lambda _: pytest.fail('remote poll'))
    execute('DELETE FROM managed_connections')
    set_supports_images('test-model', True)
    assert not image_capability(db.get_connection_by_id(cid))
    assert base.auth.account_status()['connected']
    assert metadata(cid)['image_policy'] == 'enabled'
    assert image_capability(db.get_connection_by_id(cid))


async def test_live_session_actual_reseed_repairs_publication(monkeypatch, tmp_path):
    import db.connection as connection
    from core.bm_cli import filesystem
    from core.runtime.services import RuntimeServices
    monkeypatch.setattr(filesystem, '_AGENTS_ROOT', tmp_path / 'agents')
    monkeypatch.setenv('BOSSMOD_COMPANY_ROOT', str(tmp_path / 'company'))
    monkeypatch.setattr(connection, '_PROJECT_ROOT', tmp_path)
    cid = base.saved_account()
    base.auth.set_image_policy('test-model', 'enabled')
    monkeypatch.setattr(base.auth, 'model_catalog', lambda _: pytest.fail('remote poll'))
    services = RuntimeServices()
    async def noop():
        pass
    monkeypatch.setattr(services, '_start_unlocked', noop)
    monkeypatch.setattr(services, '_stop_unlocked', noop)
    await services.reseed_application_data()
    config.reload()
    set_enabled('chatgpt', True)
    assert db.get_connection_by_id(cid) is not None
    assert metadata(cid) is None
    assert base.auth.account_status()['connected']
    assert base.auth.account_status()['connection_id'] == cid
    assert metadata(cid)['image_policy'] == 'enabled'
    assert image_capability(db.get_connection_by_id(cid))


def test_partial_publication_and_deleted_row_repaired(monkeypatch):
    base.saved_account(models=[{'slug': 'test-model', 'display_name': 'Test'}, {'slug': 'second', 'display_name': 'Second'}])
    before = {c.model: c.id for c in db.list_connections()}
    execute('DELETE FROM managed_connections WHERE connection_id = $1', [before['second']])
    assert base.auth.account_status()['connected']
    assert metadata(before['second'])['available']
    db.delete_connection(before['second'])
    assert base.auth.account_status()['connected']
    after = {c.model: c.id for c in db.list_connections()}
    assert after['test-model'] == before['test-model']
    assert metadata(after['second'])['available']
