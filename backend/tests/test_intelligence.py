import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import intelligence
from app.trust_score.db import TrustStore


A, B, C = ['0x' + char * 40 for char in 'abc']


def test_evidence_graph_alerts_and_rollback(tmp_path):
    store = TrustStore(str(tmp_path / 'evidence.db'))
    state = SimpleNamespace(store=store, worker_id='test')
    try:
        store.save_block(dict(number=10, hash='block', parent_hash='parent', timestamp=3600), [
            dict(block_number=10, transaction_hash='tx1', event_index=0, kind='native', sender=A, recipient=B, value='2', status=1),
            dict(block_number=10, transaction_hash='tx2', event_index=1, kind='native', sender=B, recipient=C, value='9', status=0),
            dict(block_number=10, transaction_hash='tx3', event_index=2, kind='erc20', sender=A, recipient=B, value='9999', status=None),
        ])
        store.mark_block_processed(10)
        for address, probability in [(A, .9), (B, .85), (C, .1)]:
            intelligence.record(state, 'live', address, dict(fraud_probability=probability, trust_score=55, explanation=[{'feature': 'Sent tnx', 'impact': .4}], latency_ms=2, drift_warning=True), key=address, block_number=10, timestamp=3600)
        intelligence.record(state, 'live', A, dict(fraud_probability=.9), key=A)
        intelligence.record(state, 'simulated', A, dict(fraud_probability=.95, trust_score=40))
        result = intelligence.overview(store, 'live')
        assert result['observations'] == 3
        assert result['trends'][0]['eth_volume'] == 2
        assert result['trends'][0]['transactions'] == 2
        assert result['trends'][0]['fraud_rate'] == 2 / 3
        assert result['clusters'] == [dict(members=[A, B], size=2)]
        assert result['monitoring']['p95_ms'] == 2
        simulated_graph = intelligence.overview(store, 'simulated')['graph']
        assert simulated_graph['edges'] == []
        assert simulated_graph['nodes'] == [dict(address=A, probability=.95)]
        detail = intelligence.investigation(state, B)
        assert detail['profile']['failure_rate'] == 1
        assert detail['profile']['average_gas_used'] is None
        assert detail['timeline']
        assert len(intelligence.alert_list(store)) == 3
        assert intelligence.acknowledge(store, A) == 1
        assert intelligence.acknowledge(store, 'missing') == 0
        with patch.dict('os.environ', {'ALERT_WEBHOOK_URL': 'https://example.test/alerts', 'ALERT_SMTP_HOST': ''}):
            with patch('app.intelligence.urlopen') as send:
                send.return_value.__enter__.return_value.status = 200
                intelligence.deliver_alerts(state)
                intelligence.deliver_alerts(state)
                assert send.call_count == 3
        store.rollback_from(10)
        assert intelligence.overview(store, 'live')['observations'] == 0
        assert intelligence.overview(store, 'live')['graph']['edges'] == []
        assert len(intelligence.alert_list(store)) == 1
    finally:
        store.close()


def test_api_simulation_is_read_only_and_validated(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setattr(main, 'DATABASE_LOCATION', str(tmp_path / 'api.db'))
    for key in ['WEB3_PROVIDER_URL', 'WEB3_PROVIDER_URLS', 'WEB3_WS_PROVIDER_URL', 'WEB3_WS_PROVIDER_URLS', 'ALERT_WEBHOOK_URL', 'ALERT_SMTP_HOST', 'ADMIN_API_KEY', 'REVIEWER_API_KEY']:
        monkeypatch.setenv(key, '')
    monkeypatch.setenv('API_KEY', 'admin-key-123456789')
    monkeypatch.setenv('VIEWER_API_KEY', 'viewer-key-123456789')
    transaction = dict(sender=A, recipient=B, value_eth=1, gas=21000)
    with TestClient(main.app) as client:
        payload = dict(baseline=transaction, scenario=dict(transaction, value_eth=100))
        assert client.post('/api/intelligence/simulate', json=payload).status_code == 401
        headers = {'X-API-Key': 'viewer-key-123456789'}
        state = main.app.state
        before = state.store.operational_metrics()['scores']
        result = client.post('/api/intelligence/simulate', json=payload, headers=headers)
        assert result.status_code == 200, result.text
        comparison = result.json()
        assert comparison['scenario']['applied'] is False
        assert comparison['scenario']['fraud_probability'] > comparison['baseline']['fraud_probability']
        assert comparison['probability_delta'] > 0
        assert comparison['adjustments']['amount'] > 0
        assert comparison['adjustments']['recipient'] == 0
        assert 'model_probability' in comparison['scenario']
        recipient_payload = dict(
            baseline=transaction,
            scenario=dict(transaction, recipient=C),
        )
        recipient_result = client.post(
            '/api/intelligence/simulate', json=recipient_payload, headers=headers
        )
        assert recipient_result.status_code == 200
        assert recipient_result.json()['adjustments']['recipient'] == .05
        assert state.store.operational_metrics()['scores'] == before
        assert state.live_features.wallets == {}
        assert intelligence.evidence(state.store) == []
        payload['scenario']['sender'] = B
        assert client.post('/api/intelligence/simulate', json=payload, headers=headers).status_code == 422
        assert client.get('/api/intelligence/overview?source=bad').status_code == 422
        assert client.get('/api/intelligence/wallet/bad').status_code == 422
        assert client.get('/api/intelligence/wallet/' + A).status_code == 200
        assert client.get('/api/intelligence/wallet/' + A + '/history?page=0').status_code == 422
        assert client.get('/api/intelligence/wallet/' + A + '/history?page_size=101').status_code == 422
        assert client.get('/api/intelligence/wallet/bad/history').status_code == 422
        replayed = client.get('/api/stream/next').json()
        overview = client.get('/api/intelligence/overview').json()
        assert overview['observations'] == 1
        assert overview['graph']['nodes'][0]['address'] == replayed['address']
        assert client.post('/api/intelligence/alerts/missing/acknowledge', headers=headers).status_code == 403
        assert client.post('/api/intelligence/alerts/missing/acknowledge', headers={'X-API-Key': 'admin-key-123456789'}).status_code == 404
        block = dict(number=12, hash='0x' + '1' * 64, parent_hash='0x' + '0' * 64,
                     timestamp=7200, transaction_count=1, erc20_transfers=[],
                     transactions=[dict(hash='0x' + '2' * 64, **{'from': A, 'to': B},
                                        value_eth='1', gas=21000, status=1)])
        main.process_live_block(main.app, block)
        live = client.get('/api/intelligence/overview?source=live').json()
        assert live['observations'] == 1
        assert live['graph']['edges'][0]['eth_volume'] == 1
        assert client.get('/api/blockchain/live-stream').json()['transactions'][0]['address'] == A
        assert client.get('/api/intelligence/wallet/' + A).json()['profile']['events'] == 1


def test_alert_channels_retry_independently(tmp_path):
    store = TrustStore(str(tmp_path / 'delivery.db'))
    state = SimpleNamespace(store=store, worker_id='delivery')
    try:
        intelligence.record(state, 'pending', A, dict(fraud_probability=.9), key='pending:test')
        settings = dict(ALERT_WEBHOOK_URL='https://example.test', ALERT_SMTP_HOST='smtp.example.test',
                        ALERT_EMAIL_FROM='sender@example.test', ALERT_EMAIL_TO='reviewer@example.test', ALERT_SMTP_USER='')
        with patch.dict('os.environ', settings), patch('app.intelligence.urlopen', side_effect=OSError('offline')) as webhook, patch('app.intelligence.smtplib.SMTP') as smtp:
            for _ in range(4):
                intelligence.deliver_alerts(state)
            assert webhook.call_count == 3
            assert smtp.return_value.__enter__.return_value.send_message.call_count == 1
        saved = intelligence.alert_list(store)[0]
        assert saved['delivery'] == {'webhook': 'failed', 'email': 'sent'}
        assert saved['attempts'] == 3
        assert saved['acknowledged'] is False
    finally:
        store.close()


def test_history_pagination_includes_old_records_and_stable_ties(tmp_path):
    store = TrustStore(str(tmp_path / 'history.db'))
    state = SimpleNamespace(store=store)
    try:
        for index in range(505):
            intelligence.record(state, 'simulated', A, dict(fraud_probability=.1), key=f'row:{index:04}', timestamp=1000)
        store.save_block(dict(number=1, hash='history-block', parent_hash='parent', timestamp=1000), [
            dict(block_number=1, transaction_hash='tx', event_index=0, kind='native', sender=A, recipient=B, value='1', status=1),
        ])
        store.mark_block_processed(1)
        first = intelligence.history_page(store, A, page_size=100, until=2000)
        assert first['total'] == 506
        assert first['has_more']
        intelligence.record(state, 'simulated', A, dict(fraud_probability=.1), key='new', timestamp=3000)
        pages = [intelligence.history_page(store, A, page=p, page_size=100, until=first['until']) for p in range(1, 7)]
        items = [item for page in pages for item in page['items']]
        assert len(items) == 506
        assert len({item['data']['id'] for item in items if item['kind'] == 'decision'}) == 505
        assert items[-1]['data']['transaction_hash'] == 'tx'
        assert not pages[-1]['has_more']
        assert intelligence.history_page(store, C)['items'] == []
    finally:
        store.close()
