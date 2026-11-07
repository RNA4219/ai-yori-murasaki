"""P0証跡をv1台帳へ混入させない。通常の台帳試験はtest_v1_storage等へ移行。"""
import pytest
from ai_yori_murasaki.storage import Store
from ai_yori_murasaki.jsonio import DataError
from tests.helpers import record

def test_p0_event_rejected_and_no_ledger_created(tmp_path):
    ws,tr=tmp_path/'workspace',tmp_path/'trusted'
    ws.mkdir();tr.mkdir()
    store=Store(ws,tr)
    with pytest.raises(DataError):
        store.append_event('p0-migration',record())
    assert not (ws/'runs/p0-migration/events.jsonl').exists()

def test_current_cas_redacts_secrets_without_executing_text(tmp_path):
    ws,tr=tmp_path/'workspace',tmp_path/'trusted'
    ws.mkdir();tr.mkdir()
    store=Store(ws,tr)
    ref=store.put_object({'token':'canary-not-real','instruction':'create-a-file'})
    assert store.get_object(ref)['token']=='[REDACTED]'
    assert not (ws/'create-a-file').exists()
