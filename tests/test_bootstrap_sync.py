from __future__ import annotations

from unittest import mock

from core.bootstrap_sync import publish_local_presence_snapshots, sync_from_bootstrap_topics


def test_publish_local_presence_snapshots_uses_active_record_helper() -> None:
    adapter = mock.Mock()
    adapter.publish_snapshot.return_value = True

    with mock.patch("core.bootstrap_sync._get_active_peer_records", return_value=[]):
        written = publish_local_presence_snapshots(topic_names=["topic_a"], adapter=adapter)

    assert written == 1
    adapter.publish_snapshot.assert_called_once()


def test_sync_from_bootstrap_topics_returns_complete_result_fields() -> None:
    adapter = mock.Mock()
    adapter.fetch_snapshot.side_effect = [{"records": []}, None]

    with mock.patch("core.bootstrap_sync._merge_snapshot", return_value=2):
        result = sync_from_bootstrap_topics(topic_names=["topic_a", "topic_b"], adapter=adapter)

    assert result.topics_written == 0
    assert result.topics_read == 1
    assert result.records_merged == 2
