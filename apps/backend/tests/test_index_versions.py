import pytest
from app.modules.ingest import index_versions


class Indices:
    def __init__(self):
        self.current = {'v1'}
        self.actions = []
    def get_alias(self, name):
        return {value: {} for value in self.current}
    def update_aliases(self, actions):
        self.actions = actions
        self.current = {actions[-1]['add']['index']}


def test_alias_promote_rollback_and_stale_review_guard(monkeypatch):
    class Client:
        indices = Indices()
    client = Client()
    manifest = {'documents': 3, 'sources': {'ocr': 1, 'asr': 1, 'caption': 1}}
    monkeypatch.setattr(index_versions, 'manifest', lambda c,i: manifest)
    result = index_versions.promote(client, 'read', 'v2', 'v1', manifest)
    assert result['previous'] == 'v1' and client.indices.current == {'v2'}
    assert client.indices.actions[0]['remove']['must_exist'] is True
    with pytest.raises(ValueError, match='Alias changed'):
        index_versions.promote(client, 'read', 'v3', 'v1', manifest)
    index_versions.promote(client, 'read', 'v1', 'v2', manifest)
    assert client.indices.current == {'v1'}
    with pytest.raises(ValueError, match='Target changed'):
        index_versions.promote(client, 'read', 'v2', 'v1', {})
