from __future__ import annotations

from pathlib import Path

from ops.pytest_shards import (
    ShardAssignment,
    build_shard_command,
    discover_test_targets,
    partition_targets,
    run_shards,
)


def test_discover_test_targets_defaults_to_tests_tree(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_alpha.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tests_dir / "nested").mkdir()
    (tests_dir / "nested" / "test_beta.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (repo_root / "other.py").write_text("", encoding="utf-8")

    targets = discover_test_targets(repo_root=repo_root)

    assert targets == ("tests/nested/test_beta.py", "tests/test_alpha.py")


def test_partition_targets_preserves_all_targets_once(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    tests_dir = repo_root / "tests"
    tests_dir.mkdir(parents=True)
    for name, size in (("test_a.py", 10), ("test_b.py", 100), ("test_c.py", 30), ("test_d.py", 80)):
        (tests_dir / name).write_text("#" * size, encoding="utf-8")

    assignments = partition_targets(
        tuple(f"tests/{name}" for name in ("test_a.py", "test_b.py", "test_c.py", "test_d.py")),
        repo_root=repo_root,
        workers=2,
    )

    flattened = [target for assignment in assignments for target in assignment.targets]
    assert sorted(flattened) == [
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_c.py",
        "tests/test_d.py",
    ]
    assert len(assignments) == 2


def test_build_shard_command_keeps_pytest_q_and_targets() -> None:
    assignment = ShardAssignment(index=1, targets=("tests/test_alpha.py", "tests/test_beta.py"), estimated_weight=123)

    command = build_shard_command(assignment, pytest_args=("--tb=short",))

    assert command == ("pytest", "-q", "tests/test_alpha.py", "tests/test_beta.py", "--tb=short")


def test_run_shards_assigns_unique_runtime_homes(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    seen_homes: list[str] = []
    seen_shards: list[str] = []

    class _FakeProc:
        def wait(self) -> int:
            return 0

        def poll(self) -> int:
            return 0

    def fake_launcher(command, cwd, env, stdout, stderr, text):  # type: ignore[no-untyped-def]
        seen_homes.append(env["NULLA_HOME"])
        seen_shards.append(env["NULLA_TEST_SHARD"])
        return _FakeProc()

    assignments = (
        ShardAssignment(index=1, targets=("tests/test_alpha.py",), estimated_weight=10),
        ShardAssignment(index=2, targets=("tests/test_beta.py",), estimated_weight=20),
    )

    rc = run_shards(
        assignments,
        repo_root=repo_root,
        pytest_args=("--tb=short",),
        shard_label="ci",
        launcher=fake_launcher,
    )

    assert rc == 0
    assert len(seen_homes) == 2
    assert seen_homes[0] != seen_homes[1]
    assert seen_shards == ["1", "2"]
