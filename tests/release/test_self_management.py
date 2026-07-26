from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
from pathlib import Path
from typing import Any, cast

import pytest

from adaptive_harness.cli import main
from adaptive_harness.distribution import manager as distribution_manager


def _standalone_release(
    tmp_path: Path, version: str, *, executable_version: str | None = None
) -> Path:
    release_root = tmp_path / "releases"
    version_root = release_root / f"v{version}"
    version_root.mkdir(parents=True)
    archive_name = f"adaptive-harness-v{version}-macos-arm64.tar.gz"
    archive = version_root / archive_name
    content = (
        f"#!/bin/sh\nprintf '{executable_version or version}\\n'\n".encode()
    )
    member = tarfile.TarInfo("runtime/harness")
    member.mode = 0o755
    member.size = len(content)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.addfile(member, io.BytesIO(content))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (version_root / "SHA256SUMS").write_text(
        f"{digest}  {archive_name}\n", encoding="utf-8"
    )
    return release_root


def _installation(
    tmp_path: Path, release_root: Path, *, version: str = "0.1.0"
) -> tuple[Path, Path]:
    runtime_parent = tmp_path / "data/harness/runtime"
    runtime = runtime_parent / "current"
    slot = runtime_parent / f"slots/{version}-initial"
    slot.mkdir(parents=True)
    runtime.symlink_to(Path("slots") / slot.name)
    runtime_binary = slot / "harness"
    runtime_binary.write_text(
        f"#!/bin/sh\nprintf '{version}\\n'\n", encoding="utf-8"
    )
    runtime_binary.chmod(0o755)
    binary = tmp_path / "bin/harness"
    binary.parent.mkdir()
    binary.write_text(
        f"#!/bin/sh\nexec '{runtime_binary}' \"$@\"\n", encoding="utf-8"
    )
    binary.chmod(0o755)
    manifest = tmp_path / "data/harness/installation.json"
    slot_manifest = slot / "installation.json"
    slot_manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "channel": "standalone",
                "version": version,
                "binary_path": str(binary),
                "data_root": str(manifest.parent),
                "runtime_path": str(runtime),
                "release_base": release_root.as_uri(),
                "release_repository": "hilt21/Adaptive-Harness",
                "path_profile": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.symlink_to(Path("runtime/current/installation.json"))
    return binary, manifest


def test_self_update_verifies_and_atomically_replaces_standalone_binary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = _standalone_release(tmp_path, "0.2.0")
    binary, manifest = _installation(tmp_path, release_root)
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    monkeypatch.setenv("HARNESS_TARGET", "macos-arm64")

    assert main(["self", "update", "--version", "0.2.0", "--json"]) == 0

    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "updated"
    assert document["previous_version"] == "0.1.0"
    assert document["version"] == "0.2.0"
    runtime = manifest.parent / "runtime/current/harness"
    assert runtime.parent.is_symlink()
    assert manifest.is_symlink()
    assert runtime.read_text(encoding="utf-8").endswith("'0.2.0\\n'\n")
    assert (manifest.parent / "runtime/previous/harness").is_file()
    assert json.loads(manifest.read_text(encoding="utf-8"))["version"] == "0.2.0"


def test_self_management_refuses_concurrent_installation_operation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = _standalone_release(tmp_path, "0.2.0")
    binary, manifest = _installation(tmp_path, release_root)
    lock = manifest.parent.with_name(f"{manifest.parent.name}.install.lock")
    lock.write_text(f"{os.getpid()}\n", encoding="ascii")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    monkeypatch.setenv("HARNESS_TARGET", "macos-arm64")

    assert main(["self", "update", "--version", "0.2.0", "--json"]) == 1

    error = json.loads(capsys.readouterr().out)
    assert "operation is in progress" in error["message"]
    assert binary.is_file()
    assert lock.is_file()


def test_self_management_recovers_stale_installation_lock(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = _standalone_release(tmp_path, "0.2.0")
    _, manifest = _installation(tmp_path, release_root)
    lock = manifest.parent.with_name(f"{manifest.parent.name}.install.lock")
    lock.write_text("99999999\n", encoding="ascii")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    monkeypatch.setenv("HARNESS_TARGET", "macos-arm64")

    assert main(["self", "update", "--version", "0.2.0", "--json"]) == 0

    capsys.readouterr()
    assert not lock.exists()


def test_python_installation_lock_is_atomically_published_with_owner(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    lock = data_root.with_name(f"{data_root.name}.install.lock")

    with distribution_manager._installation_lock(data_root):
        assert lock.is_file()
        assert lock.read_text(encoding="ascii") == f"{os.getpid()}\n"


def test_self_update_supports_verbose_localized_human_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = _standalone_release(tmp_path, "0.2.0")
    binary, manifest = _installation(tmp_path, release_root)
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    monkeypatch.setenv("HARNESS_TARGET", "macos-arm64")

    assert main(
        [
            "--locale",
            "zh-CN",
            "self",
            "update",
            "--version",
            "0.2.0",
            "--verbose",
        ]
    ) == 0

    output = capsys.readouterr().out
    assert "已将 Adaptive Harness 从 0.1.0 更新到 0.2.0。" in output
    assert f"启动器：{binary}" in output
    assert "上一 Runtime：" in output


def test_self_uninstall_removes_managed_files_but_preserves_records(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = tmp_path / "releases"
    binary, manifest = _installation(tmp_path, release_root)
    backup = binary.with_name("harness.previous")
    backup.write_text("previous", encoding="utf-8")
    record = manifest.parent / "projects/repo/tasks/task/record.json"
    record.parent.mkdir(parents=True)
    record.write_text("preserve me\n", encoding="utf-8")
    profile = tmp_path / "home/.zshrc"
    profile.parent.mkdir()
    profile.write_text(
        "# user content\n"
        "# >>> adaptive-harness PATH >>>\n"
        f"export PATH='{binary.parent}':\"$PATH\"\n"
        "# <<< adaptive-harness PATH <<<\n",
        encoding="utf-8",
    )
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["path_profile"] = str(profile)
    manifest.write_text(json.dumps(document) + "\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))

    assert main(["self", "uninstall", "--yes", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "uninstalled"
    assert result["data_purged"] is False
    assert not binary.exists()
    assert not backup.exists()
    assert not (manifest.parent / "runtime").exists()
    assert not manifest.exists()
    assert record.read_text(encoding="utf-8") == "preserve me\n"
    assert profile.read_text(encoding="utf-8") == "# user content\n"
    assert stat.S_IMODE(profile.stat().st_mode) == 0o644


def test_self_uninstall_previews_profile_diff_and_cancels_without_confirmation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, manifest = _installation(tmp_path, tmp_path / "releases")
    profile = tmp_path / "home/.zshrc"
    profile.parent.mkdir()
    original = (
        "export SECRET_TOKEN='do-not-print-this'\n"
        "# >>> adaptive-harness PATH >>>\n"
        f"export PATH='{binary.parent}':\"$PATH\"\n"
        "# <<< adaptive-harness PATH <<<\n"
    )
    profile.write_text(original, encoding="utf-8")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["path_profile"] = str(profile)
    manifest.write_text(json.dumps(document) + "\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))

    assert main(["self", "uninstall"], input_fn=lambda _: "n") == 1

    output = capsys.readouterr().out
    assert f"--- {profile}" in output
    assert "-# >>> adaptive-harness PATH >>>" in output
    assert "do-not-print-this" not in output
    assert "cancelled" in output
    assert binary.is_file()
    assert manifest.is_file()
    assert profile.read_text(encoding="utf-8") == original


def test_self_uninstall_refuses_noncanonical_managed_profile_block(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, manifest = _installation(tmp_path, tmp_path / "releases")
    profile = tmp_path / "home/.zshrc"
    profile.parent.mkdir()
    secret = "SECRET_TOKEN=must-not-be-deleted"
    profile.write_text(
        "# >>> adaptive-harness PATH >>>\n"
        f"{secret}\n"
        "# <<< adaptive-harness PATH <<<\n",
        encoding="utf-8",
    )
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["path_profile"] = str(profile)
    manifest.write_text(json.dumps(document) + "\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))

    assert main(["self", "uninstall"]) == 1

    output = capsys.readouterr().out
    assert secret not in output
    assert profile.read_text(encoding="utf-8").count(secret) == 1


def test_self_uninstall_shows_complete_plan_without_a_managed_profile(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, manifest = _installation(tmp_path, tmp_path / "releases")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    output_before_prompt: list[str] = []

    def decline(_: str) -> str:
        output_before_prompt.append(capsys.readouterr().out)
        return "n"

    assert main(["self", "uninstall"], input_fn=decline) == 1

    review = output_before_prompt[0]
    assert "Standalone uninstall plan." in review
    assert f"Launcher: {binary}" in review
    assert f"Runtime: {manifest.parent / 'runtime/current'}" in review
    assert f"Runtime root: {manifest.parent / 'runtime'}" in review
    assert f"Previous launcher: {binary.with_name('harness.previous')}" in review
    assert f"Manifest: {manifest}" in review
    assert "Purge data: False" in review
    assert binary.is_file()
    assert manifest.is_file()


def test_self_uninstall_restores_staged_files_when_launcher_cannot_move(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, manifest = _installation(tmp_path, tmp_path / "releases")
    profile = tmp_path / "home/.zshrc"
    profile.parent.mkdir()
    original = (
        "# user content\n"
        "# >>> adaptive-harness PATH >>>\n"
        f"export PATH='{binary.parent}':\"$PATH\"\n"
        "# <<< adaptive-harness PATH <<<\n"
    )
    profile.write_text(original, encoding="utf-8")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["path_profile"] = str(profile)
    manifest.write_text(json.dumps(document) + "\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    binary.parent.chmod(0o555)
    try:
        assert main(["self", "uninstall", "--yes", "--json"]) == 1
    finally:
        binary.parent.chmod(0o755)

    capsys.readouterr()
    assert binary.is_file()
    assert manifest.is_file()
    assert (manifest.parent / "runtime/current/harness").is_file()
    assert profile.read_text(encoding="utf-8") == original


def test_self_uninstall_requires_explicit_confirmation_to_purge_data(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, manifest = _installation(tmp_path, tmp_path / "releases")
    record = manifest.parent / "projects/repo/record.json"
    record.parent.mkdir(parents=True)
    record.write_text("record\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))

    assert main(["self", "uninstall", "--purge-data", "--json"]) == 1
    assert "requires --yes" in json.loads(capsys.readouterr().out)["message"]
    assert binary.is_file()
    assert record.is_file()

    assert main(
        ["self", "uninstall", "--purge-data", "--yes", "--json"]
    ) == 0
    assert json.loads(capsys.readouterr().out)["data_purged"] is True
    assert not binary.exists()
    assert not manifest.parent.exists()


def test_self_management_refuses_unknown_installation_channel(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing-installation.json"
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(missing))

    assert main(["self", "update", "--version", "0.2.0", "--json"]) == 1

    error = json.loads(capsys.readouterr().out)
    assert "original package manager" in error["message"]


def test_self_update_restores_previous_binary_when_smoke_check_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = _standalone_release(
        tmp_path, "0.2.0", executable_version="wrong-version"
    )
    binary, manifest = _installation(tmp_path, release_root)
    original = (manifest.parent / "runtime/current/harness").read_bytes()
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    monkeypatch.setenv("HARNESS_TARGET", "macos-arm64")

    assert main(["self", "update", "--version", "0.2.0", "--json"]) == 1

    assert "version check" in json.loads(capsys.readouterr().out)["message"]
    assert (manifest.parent / "runtime/current/harness").read_bytes() == original
    assert json.loads(manifest.read_text(encoding="utf-8"))["version"] == "0.1.0"


def test_self_update_reports_a_checksum_valid_malformed_archive(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = tmp_path / "releases"
    version_root = release_root / "v0.2.0"
    version_root.mkdir(parents=True)
    archive_name = "adaptive-harness-v0.2.0-macos-arm64.tar.gz"
    archive = version_root / archive_name
    archive.write_bytes(b"not a tar archive")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (version_root / "SHA256SUMS").write_text(
        f"{digest}  {archive_name}\n", encoding="utf-8"
    )
    _, manifest = _installation(tmp_path, release_root)
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    monkeypatch.setenv("HARNESS_TARGET", "macos-arm64")

    assert main(["self", "update", "--version", "0.2.0", "--json"]) == 1

    error = json.loads(capsys.readouterr().out)
    assert "archive is invalid" in error["message"]


def test_self_update_restores_both_runtime_pointers_when_current_switch_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = _standalone_release(tmp_path, "0.2.0")
    _, manifest = _installation(tmp_path, release_root)
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    monkeypatch.setenv("HARNESS_TARGET", "macos-arm64")
    assert main(["self", "update", "--version", "0.2.0", "--json"]) == 0
    capsys.readouterr()
    _standalone_release(tmp_path, "0.3.0")
    runtime_parent = manifest.parent / "runtime"
    current = runtime_parent / "current"
    previous = runtime_parent / "previous"
    original_current = current.readlink()
    original_previous = previous.readlink()
    original_slots = sorted(path.name for path in (runtime_parent / "slots").iterdir())
    manager_os = cast(Any, distribution_manager).os
    real_replace = manager_os.replace
    failed = False

    def fail_after_current_switch(source: Path | str, target: Path | str) -> None:
        nonlocal failed
        real_replace(source, target)
        if Path(target) == current and not failed:
            failed = True
            raise OSError("simulated current pointer failure")

    monkeypatch.setattr(manager_os, "replace", fail_after_current_switch)

    assert main(["self", "update", "--version", "0.3.0", "--json"]) == 1

    assert "simulated current pointer failure" in json.loads(
        capsys.readouterr().out
    )["message"]
    assert current.readlink() == original_current
    assert previous.readlink() == original_previous
    assert sorted(path.name for path in (runtime_parent / "slots").iterdir()) == (
        original_slots
    )


def test_self_update_restores_previous_pointer_when_replace_reports_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = _standalone_release(tmp_path, "0.2.0")
    _, manifest = _installation(tmp_path, release_root)
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    monkeypatch.setenv("HARNESS_TARGET", "macos-arm64")
    assert main(["self", "update", "--version", "0.2.0", "--json"]) == 0
    capsys.readouterr()
    _standalone_release(tmp_path, "0.3.0")
    runtime_parent = manifest.parent / "runtime"
    current = runtime_parent / "current"
    previous = runtime_parent / "previous"
    original_current = current.readlink()
    original_previous = previous.readlink()
    manager_os = cast(Any, distribution_manager).os
    real_replace = manager_os.replace
    failed = False

    def fail_after_previous_switch(source: Path | str, target: Path | str) -> None:
        nonlocal failed
        real_replace(source, target)
        if Path(target) == previous and not failed:
            failed = True
            raise OSError("simulated previous pointer failure")

    monkeypatch.setattr(manager_os, "replace", fail_after_previous_switch)

    assert main(["self", "update", "--version", "0.3.0", "--json"]) == 1

    assert current.readlink() == original_current
    assert previous.readlink() == original_previous


def test_self_uninstall_restores_path_when_staging_replace_reports_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, manifest = _installation(tmp_path, tmp_path / "releases")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    manager_os = cast(Any, distribution_manager).os
    real_replace = manager_os.replace
    failed = False

    def fail_after_staging(source: Path | str, target: Path | str) -> None:
        nonlocal failed
        real_replace(source, target)
        if Path(source) == binary and not failed:
            failed = True
            raise OSError("simulated staging failure")

    monkeypatch.setattr(manager_os, "replace", fail_after_staging)

    assert main(["self", "uninstall", "--yes", "--json"]) == 1

    assert "simulated staging failure" in json.loads(
        capsys.readouterr().out
    )["message"]
    assert binary.is_file()
    assert manifest.is_file()


def test_self_uninstall_restores_profile_when_profile_replace_reports_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, manifest = _installation(tmp_path, tmp_path / "releases")
    profile = tmp_path / "home/.zshrc"
    profile.parent.mkdir()
    original = (
        "# user content\n"
        "# >>> adaptive-harness PATH >>>\n"
        f"export PATH='{binary.parent}':\"$PATH\"\n"
        "# <<< adaptive-harness PATH <<<\n"
    )
    profile.write_text(original, encoding="utf-8")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["path_profile"] = str(profile)
    manifest.write_text(json.dumps(document) + "\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    manager_os = cast(Any, distribution_manager).os
    real_replace = manager_os.replace
    failed = False

    def fail_after_profile_replace(source: Path | str, target: Path | str) -> None:
        nonlocal failed
        real_replace(source, target)
        if Path(target) == profile and not failed:
            failed = True
            raise OSError("simulated profile replacement failure")

    monkeypatch.setattr(manager_os, "replace", fail_after_profile_replace)

    assert main(["self", "uninstall", "--yes", "--json"]) == 1

    assert "simulated profile replacement failure" in json.loads(
        capsys.readouterr().out
    )["message"]
    assert binary.is_file()
    assert manifest.is_file()
    assert (manifest.parent / "runtime/current/harness").is_file()
    assert profile.read_text(encoding="utf-8") == original


def test_self_uninstall_reports_concurrent_profile_recovery_conflict(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, manifest = _installation(tmp_path, tmp_path / "releases")
    profile = tmp_path / "home/.zshrc"
    profile.parent.mkdir()
    profile.write_text(
        "# user content\n"
        "# >>> adaptive-harness PATH >>>\n"
        f"export PATH='{binary.parent}':\"$PATH\"\n"
        "# <<< adaptive-harness PATH <<<\n",
        encoding="utf-8",
    )
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["path_profile"] = str(profile)
    manifest.write_text(json.dumps(document) + "\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    manager_os = cast(Any, distribution_manager).os
    real_replace = manager_os.replace
    failed = False

    def fail_with_concurrent_change(source: Path | str, target: Path | str) -> None:
        nonlocal failed
        real_replace(source, target)
        if Path(target) == profile and not failed:
            failed = True
            with profile.open("a", encoding="utf-8") as handle:
                handle.write("# concurrent profile change\n")
            raise OSError("simulated profile replacement failure")

    monkeypatch.setattr(manager_os, "replace", fail_with_concurrent_change)

    assert main(["self", "uninstall", "--yes", "--json"]) == 1

    assert "recovery was incomplete" in json.loads(
        capsys.readouterr().out
    )["message"]
    assert "concurrent profile change" in profile.read_text(encoding="utf-8")
    assert binary.is_file()
    assert manifest.is_file()


def test_self_uninstall_reports_recovery_artifacts_when_cleanup_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, manifest = _installation(tmp_path, tmp_path / "releases")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))

    def retain_recovery(path: Path) -> None:
        raise OSError(f"simulated cleanup failure: {path}")

    monkeypatch.setattr(
        distribution_manager, "_remove_staged_path", retain_recovery
    )

    assert main(["self", "uninstall", "--yes", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "uninstalled"
    assert len(result["cleanup_pending"]) == 3
    assert not binary.exists()
    assert not manifest.exists()
    assert not (manifest.parent / "runtime").exists()
    assert all(
        Path(path).exists() or Path(path).is_symlink()
        for path in result["cleanup_pending"]
    )


def test_self_uninstall_does_not_report_purge_when_cleanup_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, manifest = _installation(tmp_path, tmp_path / "releases")
    record = manifest.parent / "projects/repo/record.json"
    record.parent.mkdir(parents=True)
    record.write_text("record\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    monkeypatch.setattr(
        distribution_manager,
        "_remove_staged_path",
        lambda path: (_ for _ in ()).throw(OSError(f"retain {path}")),
    )

    assert main(
        ["self", "uninstall", "--purge-data", "--yes", "--json"]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["data_purged"] is False
    assert result["cleanup_pending"]


def test_self_uninstall_tracks_data_purge_separately_from_launcher_cleanup(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary, manifest = _installation(tmp_path, tmp_path / "releases")
    record = manifest.parent / "projects/repo/record.json"
    record.parent.mkdir(parents=True)
    record.write_text("record\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    remove_staged_path = distribution_manager._remove_staged_path

    def retain_launcher_recovery(path: Path) -> None:
        if path.parent == binary.parent:
            raise OSError(f"retain launcher recovery: {path}")
        remove_staged_path(path)

    monkeypatch.setattr(
        distribution_manager,
        "_remove_staged_path",
        retain_launcher_recovery,
    )

    assert main(
        ["self", "uninstall", "--purge-data", "--yes", "--json"]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["data_purged"] is True
    assert len(result["cleanup_pending"]) == 1
    assert not manifest.parent.exists()


def test_self_uninstall_human_output_warns_about_pending_cleanup(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, manifest = _installation(tmp_path, tmp_path / "releases")
    monkeypatch.setenv("HARNESS_INSTALL_MANIFEST", str(manifest))
    monkeypatch.setattr(
        distribution_manager,
        "_remove_staged_path",
        lambda path: (_ for _ in ()).throw(OSError(f"retain {path}")),
    )

    assert main(["self", "uninstall", "--yes"]) == 0

    assert "Cleanup pending:" in capsys.readouterr().out
