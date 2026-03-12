from __future__ import annotations

from unittest import mock

from apps.nulla_daemon import DaemonConfig, NullaDaemon


def test_daemon_marks_limited_but_stays_online_when_hive_task_intake_is_disabled() -> None:
    daemon = NullaDaemon(DaemonConfig())

    with mock.patch("apps.nulla_daemon.hive_task_intake_enabled", return_value=False), mock.patch.object(
        daemon,
        "_active_assignment_count",
        return_value=0,
    ):
        accepts = daemon._refresh_assist_status()
        idle_config = daemon._idle_assist_config()

    assert accepts is False
    assert daemon.config.assist_status == "limited"
    assert idle_config.mode == "off"
