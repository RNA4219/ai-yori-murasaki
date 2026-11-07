"""CLIから実ストアへ接続し、終了値と永続的な証跡を確認する。"""
import json
from pathlib import Path

import pytest

from ai_yori_murasaki.cli import main
from ai_yori_murasaki.jsonio import digest
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


@pytest.fixture
def cli_env(tmp_path, capsys):
    ws, tr = tmp_path / 'workspace', tmp_path / 'trusted'
    ws.mkdir(); tr.mkdir()
    rt = Runtime(Store(ws, tr))
    scope = {'targets': [{'kind': 'fixture', 'identifier': 'cli'}], 'max_targets': 1}
    auth = {'operator_id': 'tester', 'granted_at': '2026-01-01T00:00:00Z',
            'expires_at': '2099-01-01T00:00:00Z', 'scope_digest': digest(scope)}
    c = rt.create_campaign('INT-CLI', scope, auth, 'create')
    def call(*args):
        rc = main(['--workspace', str(ws), '--trusted-root', str(tr), *args])
        out = capsys.readouterr()
        assert out.err == ''
        return rc, json.loads(out.out)
    return rt, c['campaign_id'], call


def test_usage_and_validate_without_storage(tmp_path, capsys):
    assert main(['unknown']) == 2
    assert json.loads(capsys.readouterr().out)['reason_code'] == 'usage_error'
    fixtures = json.loads((Path(__file__).parents[1] / 'fixtures/v1/contracts.json').read_text())
    f = tmp_path / 'finding.json'
    f.write_text(json.dumps(fixtures['findings'][0]))
    assert main(['validate', 'finding', str(f)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'ok'
    f.write_text('{"token":"secret-test-value","token":"duplicate"}')
    assert main(['validate', 'finding', str(f)]) == 2
    assert 'secret-test-value' not in capsys.readouterr().out


def test_lifecycle_checkpoint_and_verify(cli_env):
    rt, cid, call = cli_env
    rc, result = call('run', 'start', '--campaign-id', cid, '--phase', 'verify', '--idempotency-key', 'v1')
    assert rc == 0, result
    rid = result['run_id']
    assert call('run', 'finish', '--run-id', rid, '--completion', 'completed')[0] == 0
    assert call('run', 'seal', '--campaign-id', cid, '--run-id', rid)[0] == 0
    rc, result = call('verify', 'checkpoint', '--campaign-id', cid, '--run-id', rid)
    assert rc == 3 and result['status'] == 'hold', result
    assert call('checkpoint', 'seal', '--campaign-id', cid, '--run-id', rid, '--operator-id', 'tester')[0] == 0
    for kind in ('chain', 'checkpoint'):
        rc, result = call('verify', kind, '--campaign-id', cid, '--run-id', rid)
        assert rc == 0 and result['data']['result'] == 'PASS', result
    assert (rt.store.workspace / 'verification' / (rid + '.jsonl')).exists()


def test_denied_and_invalid_bind(cli_env):
    _, cid, call = cli_env
    rc, result = call('run', 'start', '--campaign-id', cid, '--phase', 'purple', '--idempotency-key', 'deny')
    assert rc == 4 and result['status'] == 'denied', result
    rc, result = call('monitor', 'serve', '--bind', '0.0.0.0:8766')
    assert rc == 2 and result['reason_code'] == 'bind_not_loopback', result


@pytest.mark.parametrize('expected,code,status', [(0, 0, 'ok'), (1, 1, 'fail')])
def test_oracle_exit_codes(cli_env, tmp_path, expected, code, status):
    from ai_yori_murasaki.oracle import Oracle
    rt, cid, call = cli_env
    rid = rt.start_run(cid, 'verify', 0, 'oracle')['run_id']
    ref = rt.store.put_object({'code': 0})
    predicate = Oracle().make_predicate('cli-oracle', 'exit_code', ref, {'code': expected})
    f = tmp_path / 'predicate.json'; f.write_text(json.dumps(predicate))
    rc, result = call('oracle', 'evaluate', '--run-id', rid, '--predicate-file', str(f))
    assert (rc, result['status']) == (code, status), result


def test_rsi_cli_comparison_pins_manifest_and_records_holdout(tmp_path, capsys):
    from tests.test_v1_rsi import setup_case, _config
    rsi, _, root, manifest, bindings = setup_case(tmp_path)
    rt = Runtime(rsi._store)
    scope = {'targets': [{'kind': 'fixture', 'identifier': 'rsi'}], 'max_targets': 1}
    auth = {'operator_id': 'tester', 'granted_at': '2026-01-01T00:00:00Z',
            'expires_at': '2099-01-01T00:00:00Z', 'scope_digest': digest(scope)}
    cid = rt.create_campaign('INT-CLI-RSI', scope, auth, 'cli-rsi')['campaign_id']
    frozen = rsi.freeze(cid, _config(), manifest, bindings, root)
    candidate = rsi.create_candidate(frozen['freeze_id'])
    prefix = ['--workspace', str(rt.store.workspace), '--trusted-root', str(rsi._trusted)]
    rc = main([*prefix, 'rsi', 'compare', '--candidate-id', candidate['candidate_id']])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0, data
    rid = data['data']['audit_run_id']
    run = rt.get_run(rid)
    pinned = rt.store.get_object(run['manifest_ref'])
    assert pinned['evaluation_manifest_id'] == manifest['manifest_id']
    assert rt.store.get_object(pinned['evaluation_manifest_ref']) == manifest
    events = rt.store.read_events(rid)
    access = next(i for i, e in enumerate(events) if e.get('extensions', {}).get('record_type') == 'holdout_access')
    compared = next(i for i, e in enumerate(events) if e['event_type'] == 'rsi_compared')
    assert access < compared
    assert rt.store.verify_checkpoint(cid, rid)['result'] == 'PASS'
    context = {k: pinned[k] for k in ('evaluation_manifest_ref', 'evaluation_manifest_id')}
    crashing = rt.start_run(cid, 'verify', 9, 'crash-identity', evaluation_context=context)
    resumed = rt.resume_after_crash(crashing['run_id'])
    assert resumed['run_id'] != crashing['run_id']
    assert rt.resume_after_crash(crashing['run_id']) == resumed
    resumed_manifest = rt.store.get_object(resumed['manifest_ref'])
    assert all(resumed_manifest[k] == context[k] for k in context)
