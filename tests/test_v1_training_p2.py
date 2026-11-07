"""学習証跡、未見課題の分離、予算、反復停止を実データで検証する。"""
from copy import deepcopy

import pytest

from ai_yori_murasaki import p2, rsi_train
from ai_yori_murasaki.jsonio import DataError
from tests.test_v1_rsi import setup_case, _dataset


def trained(tmp_path):
    rsi, candidate, *_ = setup_case(tmp_path)
    frozen = rsi.load_freeze(candidate['freeze_id'])
    dataset = _dataset()
    # 非trainの回答は学習側の処理対象にならない。
    dataset['tasks'][2]['cases'] = 'HOLDOUT-CANARY-NOT-READ'
    ref = rsi._store.put_object(dataset)
    return rsi, rsi_train.train(rsi, frozen['baseline_ref'], ref)


def test_proposal_and_memory_provenance(tmp_path):
    rsi, result = trained(tmp_path)
    proposal = rsi_train.propose(rsi, result['train_ref'])
    assert proposal['changed'] is True
    assert rsi._store.get_object(proposal['proposal_ref']) == {'require_admin': True}
    assert proposal['promotion_ready'] is False
    card = proposal['experience_card']
    ref = rsi.add_experience(card, train_ref=result['train_ref'])['memory_ref']
    assert rsi.retrieve_experience([ref], 'search') == [card]
    assert 'HOLDOUT-CANARY' not in str(proposal)
    with pytest.raises(DataError):
        rsi.add_experience({**card, 'evidence_count': 99}, train_ref=result['train_ref'])
    # 証拠を改変して再保存したCASでも、独立再計算で拒否される。
    fake = deepcopy(result['report']); fake['results'][0]['success'] = True
    forged_ref = rsi._store.put_object(fake)
    with pytest.raises(DataError):
        rsi_train.verify_train(rsi, forged_ref)
    rsi._trusted_path(ref).write_text('{}')
    with pytest.raises(DataError):
        rsi.retrieve_experience([ref], 'search')


def test_training_requires_nonempty_train(tmp_path):
    rsi, candidate, *_ = setup_case(tmp_path)
    frozen = rsi.load_freeze(candidate['freeze_id'])
    dataset = _dataset(); dataset['tasks'] = dataset['tasks'][1:]
    with pytest.raises(DataError):
        rsi_train.train(rsi, frozen['baseline_ref'], rsi._store.put_object(dataset))


def test_purple_loop_stops_repeated_failure_and_enforces_budget():
    calls = []
    def no_progress(i, remaining):
        calls.append((i, remaining))
        return {'progress': False, 'success': False, 'calls': 1,
                'failure_class': 'tool', 'evidence_refs': []}
    result = p2.purple_loop(no_progress, max_iterations=10, max_calls=10)
    assert result['stop_reason'] == 'repeat_failure' and len(calls) == 2
    result = p2.purple_loop(no_progress, max_iterations=10, max_calls=1)
    assert result['stop_reason'] == 'no_budget' and result['used_calls'] == 1
    with pytest.raises(DataError):
        p2.purple_loop(no_progress, max_iterations=True, max_calls=3)


def test_model_routing_and_real_resource_measurement():
    assert p2.route_model('purple') == 'deepseek-v4.1-flash:cloud'
    assert p2.route_model('reviewer') == 'glm-5.3:cloud'
    assert p2.route_model('blue', {'blue': 'registered-model'}) == 'registered-model'
    with pytest.raises(DataError):
        p2.route_model('oracle')
    measured = p2.rss_bytes()
    assert measured is None or measured > 0


def test_sigma_candidate_does_not_apply_and_reports_fp_fn():
    rule = {'title': 'local authz', 'id': 'local-authz', 'logsource': {'category': 'webserver'},
            'detection': {'selection': {'status': 200, 'path': '/health'}, 'condition': 'selection'}}
    before = deepcopy(rule)
    observed = [{'status': 200, 'path': '/record?role=guest', 'expected_detection': True},
                {'status': 200, 'path': '/health', 'expected_detection': False},
                {'status': 403, 'path': '/record', 'expected_detection': False}]
    result = p2.tune_sigma(rule, observed)
    assert result['matrix'] == {'tp': 1, 'fp': 0, 'fn': 0, 'tn': 2}
    assert result['baseline_matrix']['fp'] == 1
    assert result['applied'] is False and rule == before
    with pytest.raises(DataError):
        p2.tune_sigma(rule, observed + [{'status': 200, 'path': '/other', 'expected_detection': True}])
