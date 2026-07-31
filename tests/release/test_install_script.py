from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parents[2]
INSTALLER = REPOSITORY / "scripts/install.sh"
PACKAGER = REPOSITORY / "scripts/package_release.py"
RELEASE_WORKFLOW = REPOSITORY / ".github/workflows/release.yml"


def _release(
    tmp_path: Path,
    *,
    checksum: str | None = None,
    runtime_content: bytes | None = None,
) -> Path:
    release_root = tmp_path / "releases"
    version_root = release_root / "v0.1.0"
    version_root.mkdir(parents=True, exist_ok=True)
    archive_name = "adaptive-harness-v0.1.0-linux-x86_64.tar.gz"
    archive = version_root / archive_name
    content = runtime_content or b"#!/bin/sh\nprintf '0.1.0\\n'\n"
    info = tarfile.TarInfo("runtime/adp-harness")
    info.mode = 0o755
    info.size = len(content)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.addfile(info, io.BytesIO(content))
        support = tarfile.TarInfo("runtime/_internal/support.dat")
        support.size = len(b"support")
        bundle.addfile(support, io.BytesIO(b"support"))
    digest = checksum or hashlib.sha256(archive.read_bytes()).hexdigest()
    (version_root / "SHA256SUMS").write_text(
        f"{digest}  {archive_name}\n", encoding="utf-8"
    )
    return release_root


def _run_installer(
    tmp_path: Path,
    release_root: Path,
    *,
    environment_overrides: dict[str, str] | None = None,
    working_directory: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    install_dir = tmp_path / "bin"
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    environment = dict(os.environ)
    environment.update(
        {
            "HOME": str(home),
            "HARNESS_INSTALL_DIR": str(install_dir),
            "HARNESS_BWRAP_COMMAND": "__missing_bwrap__",
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_NONINTERACTIVE": "1",
            "HARNESS_RELEASE_BASE": release_root.as_uri(),
            "HARNESS_TARGET": "linux-x86_64",
            "HARNESS_VERSION": "0.1.0",
            "PATH": "/usr/bin:/bin",
            "SHELL": "/bin/bash",
        }
    )
    environment.update(environment_overrides or {})
    return subprocess.run(
        ("sh", str(INSTALLER)),
        cwd=working_directory or workspace,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_installer_verifies_and_exposes_release_without_touching_repository(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)

    completed = _run_installer(tmp_path, release_root)

    installed = tmp_path / "bin/adp-harness"
    assert completed.returncode == 0, completed.stderr
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111
    assert subprocess.check_output((str(installed),), text=True).strip() == "0.1.0"
    assert not (tmp_path / "workspace/.harness").exists()
    profile = tmp_path / "home/.bashrc"
    assert profile.is_file()
    manifest = json.loads(
        (tmp_path / "home/.local/share/harness/installation.json").read_text(
            encoding="utf-8"
        )
    )
    runtime_pointer = tmp_path / "home/.local/share/harness/runtime/current"
    manifest_pointer = tmp_path / "home/.local/share/harness/installation.json"
    assert runtime_pointer.is_symlink()
    assert manifest_pointer.is_symlink()
    assert manifest_pointer.readlink() == Path("runtime/current/installation.json")
    assert manifest["schema_version"] == "2.0"
    assert manifest["product_id"] == "dev.adaptive-harness.cli"
    assert manifest["channel"] == "standalone"
    assert manifest["version"] == "0.1.0"
    assert manifest["binary_path"] == str(installed)
    assert manifest["runtime_path"] == str(
        tmp_path / "home/.local/share/harness/runtime/current"
    )
    assert manifest["path_profile"] == str(profile)
    assert manifest["launcher_sha256"] == hashlib.sha256(
        installed.read_bytes()
    ).hexdigest()
    runtime_binary = runtime_pointer / "adp-harness"
    assert manifest["runtime_sha256"] == hashlib.sha256(
        runtime_binary.read_bytes()
    ).hexdigest()
    assert manifest["path_block_sha256"]
    assert manifest["profile_created_by_installer"] is True
    assert "not available in a clean new shell" in completed.stdout
    assert "Bubblewrap" in completed.stdout
    assert "observe remains available" in completed.stdout


def test_installer_verifies_a_second_directory_when_started_from_home(
    tmp_path: Path,
) -> None:
    log = tmp_path / "runtime-directories"
    runtime = (
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$PWD\" >> '{log}'\n"
        f"git rev-parse --is-inside-work-tree >> '{log}' 2>/dev/null || true\n"
        "printf '0.1.0\\n'\n"
    ).encode()
    home = tmp_path / "home"

    completed = _run_installer(
        tmp_path,
        _release(tmp_path, runtime_content=runtime),
        working_directory=home,
    )

    assert completed.returncode == 0, completed.stderr
    checked_directories = log.read_text(encoding="utf-8").splitlines()
    assert str(home) in checked_directories
    assert any(directory != str(home) for directory in checked_directories)
    assert "true" in checked_directories


def test_installer_aborts_if_noninteractive_path_change_is_not_authorized(
    tmp_path: Path,
) -> None:
    completed = _run_installer(
        tmp_path,
        _release(tmp_path),
        environment_overrides={"HARNESS_CONFIRM_PATH": "0"},
    )

    assert completed.returncode != 0
    assert "PATH update was not confirmed" in completed.stderr
    assert not (tmp_path / "bin/adp-harness").exists()
    assert not (tmp_path / "home/.bashrc").exists()
    assert not (tmp_path / "home/.local/share/harness/installation.json").exists()


def test_installer_refuses_an_unknown_default_shell(tmp_path: Path) -> None:
    completed = _run_installer(
        tmp_path,
        _release(tmp_path),
        environment_overrides={"SHELL": "/bin/sh"},
    )

    assert completed.returncode != 0
    assert "supported shells are zsh, bash, and fish" in completed.stderr
    assert not (tmp_path / "bin/adp-harness").exists()


def test_installer_refuses_an_unrelated_adp_harness_on_path(tmp_path: Path) -> None:
    unrelated_dir = tmp_path / "unrelated"
    unrelated_dir.mkdir()
    unrelated = unrelated_dir / "adp-harness"
    unrelated.write_text("#!/bin/sh\nprintf 'unrelated\\n'\n", encoding="utf-8")
    unrelated.chmod(0o755)

    completed = _run_installer(
        tmp_path,
        _release(tmp_path),
        environment_overrides={"PATH": f"{unrelated_dir}:/usr/bin:/bin"},
    )

    assert completed.returncode != 0
    assert str(unrelated) in completed.stderr
    assert "unrelated command" in completed.stderr
    assert not (tmp_path / "bin/adp-harness").exists()


def test_installer_repairs_only_a_recognized_installation_after_confirmation(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    first = _run_installer(tmp_path, release_root)
    assert first.returncode == 0, first.stderr
    launcher = tmp_path / "bin/adp-harness"
    launcher.write_text("#!/bin/sh\nprintf 'damaged\\n'\n", encoding="utf-8")
    launcher.chmod(0o755)

    declined = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "0",
            "HARNESS_CONFIRM_REPAIR": "0",
        },
    )
    assert declined.returncode != 0
    assert "repair was not confirmed" in declined.stderr
    assert launcher.read_text(encoding="utf-8").endswith("damaged\\n'\n")

    repaired = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_REPAIR": "1",
            "HARNESS_VERSION": "0.2.0",
        },
    )
    assert repaired.returncode == 0, repaired.stderr
    assert "Recognized a damaged" in repaired.stdout
    assert "Repair version: 0.1.0" in repaired.stdout
    assert subprocess.check_output(
        (str(launcher), "--version"), text=True
    ).strip() == "0.1.0"
    manifest = json.loads(
        (tmp_path / "home/.local/share/harness/installation.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["launcher_sha256"] == hashlib.sha256(
        launcher.read_bytes()
    ).hexdigest()


def test_installer_refuses_installation_with_unknown_manifest_schema(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    first = _run_installer(tmp_path, release_root)
    assert first.returncode == 0, first.stderr
    manifest = tmp_path / "home/.local/share/harness/installation.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"schema_version": "2.0"',
            '"schema_version": "1.0"',
        ),
        encoding="utf-8",
    )

    repeated = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={"HARNESS_CONFIRM_REPAIR": "1"},
    )

    assert repeated.returncode != 0
    assert "cannot be identified" in repeated.stderr
    assert json.loads(manifest.read_text(encoding="utf-8"))["schema_version"] == (
        "1.0"
    )


def test_installer_refuses_installation_with_invalid_manifest_hash(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    first = _run_installer(tmp_path, release_root)
    assert first.returncode == 0, first.stderr
    manifest = tmp_path / "home/.local/share/harness/installation.json"
    content = manifest.read_text(encoding="utf-8")
    archive_hash = json.loads(content)["release_archive_sha256"]
    manifest.write_text(
        content.replace(
            f'"release_archive_sha256": "{archive_hash}"',
            '"release_archive_sha256": "invalid"',
        ),
        encoding="utf-8",
    )

    repeated = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={"HARNESS_CONFIRM_REPAIR": "1"},
    )

    assert repeated.returncode != 0
    assert "cannot be identified" in repeated.stderr
    assert '"release_archive_sha256": "invalid"' in manifest.read_text(
        encoding="utf-8"
    )


def test_installer_refuses_manifest_with_missing_required_comma(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    first = _run_installer(tmp_path, release_root)
    assert first.returncode == 0, first.stderr
    manifest = tmp_path / "home/.local/share/harness/installation.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '"schema_version": "2.0",',
            '"schema_version": "2.0"',
        ),
        encoding="utf-8",
    )

    repeated = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={"HARNESS_CONFIRM_REPAIR": "1"},
    )

    assert repeated.returncode != 0
    assert "cannot be identified" in repeated.stderr
    assert '"schema_version": "2.0"\n' in manifest.read_text(encoding="utf-8")


def test_installer_rejects_checksum_mismatch_without_installing(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path, checksum="0" * 64)

    completed = _run_installer(tmp_path, release_root)

    assert completed.returncode != 0
    assert "checksum" in completed.stderr.lower()
    assert not (tmp_path / "bin/adp-harness").exists()


def test_installer_refuses_concurrent_installation_operation(
    tmp_path: Path,
) -> None:
    lock = tmp_path / "home/.local/share/harness.install.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(f"{os.getpid()}\n", encoding="ascii")

    completed = _run_installer(tmp_path, _release(tmp_path))

    assert completed.returncode != 0
    assert "operation is in progress" in completed.stderr
    assert str(lock) in completed.stderr
    assert "remove that exact lock file" in completed.stderr
    assert lock.is_file()


def test_installer_refuses_stale_installation_lock(tmp_path: Path) -> None:
    lock = tmp_path / "home/.local/share/harness.install.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("99999999\n", encoding="ascii")

    completed = _run_installer(tmp_path, _release(tmp_path))

    assert completed.returncode != 0
    assert "operation is in progress" in completed.stderr
    assert str(lock) in completed.stderr
    assert "remove that exact lock file" in completed.stderr
    assert lock.is_file()


def test_installer_publishes_only_complete_owner_lock() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert 'ln "$candidate_lock" "$install_lock"' in script
    assert 'mkdir "$install_lock"' not in script


def test_interactive_path_confirmation_writes_one_managed_block(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    profile = tmp_path / "home/.bashrc"

    first = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_NONINTERACTIVE": "0",
            "HARNESS_SHELL_PROFILE": str(profile),
        },
    )
    second = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_NONINTERACTIVE": "0",
            "HARNESS_SHELL_PROFILE": str(profile),
        },
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "@@" in first.stdout
    assert "+# >>> adaptive-harness PATH >>>" in first.stdout
    content = profile.read_text(encoding="utf-8")
    assert content.count("# >>> adaptive-harness PATH >>>") == 1
    assert f"export PATH='{tmp_path / 'bin'}':\"$PATH\"" in content
    manifest = json.loads(
        (tmp_path / "home/.local/share/harness/installation.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["path_profile"] == str(profile)


@pytest.mark.parametrize("trailing_newline", ["", "\n"])
def test_path_review_does_not_echo_existing_secret(
    tmp_path: Path, trailing_newline: str
) -> None:
    release_root = _release(tmp_path)
    profile = tmp_path / "home/.bashrc"
    secret = "HARNESS_TEST_TOKEN=do-not-print-this"
    profile.parent.mkdir()
    profile.write_text(f"export {secret}{trailing_newline}", encoding="utf-8")

    completed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_SHELL_PROFILE": str(profile),
        },
    )

    if trailing_newline:
        assert completed.returncode == 0, completed.stderr
    else:
        assert completed.returncode != 0
        assert "must end with a newline" in completed.stderr
    assert secret not in completed.stdout


def test_installer_refuses_noncanonical_managed_profile_block(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    profile = tmp_path / "home/.bashrc"
    profile.parent.mkdir()
    secret = "TOKEN=must-not-be-managed"
    profile.write_text(
        "# >>> adaptive-harness PATH >>>\n"
        f"{secret}\n"
        "# <<< adaptive-harness PATH <<<\n",
        encoding="utf-8",
    )

    completed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_SHELL_PROFILE": str(profile),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        },
    )

    assert completed.returncode != 0
    assert "malformed" in completed.stderr
    assert secret not in completed.stdout


def test_path_apply_refuses_profile_changed_after_review(tmp_path: Path) -> None:
    release_root = _release(tmp_path)
    profile = tmp_path / "home/.bashrc"
    profile.parent.mkdir()
    profile.write_text("# original\n", encoding="utf-8")
    wrapper_directory = tmp_path / "wrappers"
    wrapper_directory.mkdir()
    diff_wrapper = wrapper_directory / "diff"
    diff_wrapper.write_text(
        "#!/bin/sh\n"
        "/usr/bin/diff \"$@\"\n"
        "status=$?\n"
        "printf '# concurrent change\\n' >> \"$HARNESS_TEST_PROFILE\"\n"
        "exit \"$status\"\n",
        encoding="utf-8",
    )
    diff_wrapper.chmod(0o755)

    completed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_SHELL_PROFILE": str(profile),
            "HARNESS_TEST_PROFILE": str(profile),
            "PATH": f"{wrapper_directory}:/usr/bin:/bin",
        },
    )

    assert completed.returncode != 0
    assert "changed after review" in completed.stderr
    assert profile.read_text(encoding="utf-8") == (
        "# original\n# concurrent change\n"
    )
    assert not (tmp_path / "bin/adp-harness").exists()


def test_repeat_install_keeps_managed_profile_when_path_is_already_configured(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    profile = tmp_path / "home/.bashrc"
    first = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_SHELL_PROFILE": str(profile),
        },
    )
    assert first.returncode == 0, first.stderr

    second = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_SHELL_PROFILE": str(profile),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        },
    )

    assert second.returncode == 0, second.stderr
    manifest = json.loads(
        (tmp_path / "home/.local/share/harness/installation.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["path_profile"] == str(profile)


def test_repeat_install_preserves_recorded_profile_when_shell_selection_changes(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    original_profile = tmp_path / "home/.bashrc"
    first = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_SHELL_PROFILE": str(original_profile),
        },
    )
    assert first.returncode == 0, first.stderr

    second = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_SHELL_PROFILE": str(tmp_path / "home/other-profile"),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        },
    )

    assert second.returncode == 0, second.stderr
    manifest = json.loads(
        (tmp_path / "home/.local/share/harness/installation.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["path_profile"] == str(original_profile)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"HARNESS_VERSION": "not-a-version"}, "semantic version"),
        ({"HARNESS_VERSION": "01.2.3"}, "semantic version"),
        ({"HARNESS_VERSION": "1.2.3-rc..1"}, "semantic version"),
        ({"HARNESS_INSTALL_DIR": "relative-bin"}, "absolute"),
        ({"HARNESS_STATE_DIR": "relative-state"}, "absolute"),
        ({"HARNESS_SHELL_PROFILE": "relative-profile"}, "absolute"),
        ({"HARNESS_RELEASE_REPOSITORY": "invalid"}, "repository"),
    ],
)
def test_installer_rejects_invalid_manifest_inputs_before_installing(
    tmp_path: Path,
    overrides: dict[str, str],
    message: str,
) -> None:
    completed = _run_installer(
        tmp_path,
        _release(tmp_path),
        environment_overrides=overrides,
    )

    assert completed.returncode != 0
    assert message in completed.stderr
    assert not (tmp_path / "bin/adp-harness").exists()


def test_installer_accepts_semver_prerelease_and_build_syntax(
    tmp_path: Path,
) -> None:
    completed = _run_installer(
        tmp_path,
        _release(tmp_path),
        environment_overrides={"HARNESS_VERSION": "1.2.3-rc.1+build.5"},
    )

    assert "semantic version" not in completed.stderr


def test_installer_rejects_home_data_root_and_symlinked_profile_ancestor(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    home = tmp_path / "home"

    unsafe_state = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={"HARNESS_STATE_DIR": str(home)},
    )
    assert unsafe_state.returncode != 0
    assert "must not be / or HOME" in unsafe_state.stderr

    real_directory = tmp_path / "real-profile-directory"
    real_directory.mkdir()
    profile_parent = home / "linked-profile-directory"
    profile_parent.symlink_to(real_directory, target_is_directory=True)
    unsafe_profile = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_SHELL_PROFILE": str(profile_parent / ".zshrc"),
        },
    )
    assert unsafe_profile.returncode != 0
    assert "symlinked ancestor" in unsafe_profile.stderr
    assert not (real_directory / ".zshrc").exists()


def test_path_profile_update_is_all_or_nothing_when_replace_is_unavailable(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    profile_directory = tmp_path / "home/locked"
    profile_directory.mkdir(parents=True)
    profile = profile_directory / ".zshrc"
    original = "# user content\n"
    profile.write_text(original, encoding="utf-8")
    profile_directory.chmod(0o555)
    try:
        completed = _run_installer(
            tmp_path,
            release_root,
            environment_overrides={
                "HARNESS_CONFIRM_PATH": "1",
                "HARNESS_NONINTERACTIVE": "0",
                "HARNESS_SHELL_PROFILE": str(profile),
            },
        )
    finally:
        profile_directory.chmod(0o755)

    assert completed.returncode != 0
    assert profile.read_text(encoding="utf-8") == original


def test_late_install_failure_restores_launcher_and_shell_profile(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    home = tmp_path / "home"
    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    launcher = install_dir / "adp-harness"
    original_launcher = b"#!/bin/sh\nprintf 'previous-version\\n'\n"
    launcher.write_bytes(original_launcher)
    launcher.chmod(0o755)
    profile = home / ".zshrc"
    profile.parent.mkdir(parents=True)
    original_profile = "# user content\n"
    profile.write_text(original_profile, encoding="utf-8")
    manifest = home / ".local/share/harness/installation.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("unsafe pre-existing manifest\n", encoding="utf-8")

    completed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_NONINTERACTIVE": "0",
            "HARNESS_SHELL_PROFILE": str(profile),
        },
    )

    assert completed.returncode != 0
    assert launcher.read_bytes() == original_launcher
    assert profile.read_text(encoding="utf-8") == original_profile
    assert manifest.read_text(encoding="utf-8") == "unsafe pre-existing manifest\n"
    assert not (manifest.parent / "runtime").exists()


def test_repeat_installer_delegates_updates_without_replacing_runtime(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    first = _run_installer(tmp_path, release_root)
    assert first.returncode == 0, first.stderr
    runtime_parent = tmp_path / "home/.local/share/harness/runtime"
    current = runtime_parent / "current"
    previous = runtime_parent / "previous"
    original_target = current.readlink()
    original_slots = sorted(path.name for path in (runtime_parent / "slots").iterdir())
    counter = tmp_path / "smoke-counter"
    failing_content = (
        b"#!/bin/sh\n"
        b"if [ ! -e \"$HARNESS_TEST_SMOKE_COUNTER\" ]; then\n"
        b"  : > \"$HARNESS_TEST_SMOKE_COUNTER\"\n"
        b"  printf '0.1.0\\n'\n"
        b"else\n"
        b"  printf 'wrong-version\\n'\n"
        b"fi\n"
    )
    _release(tmp_path, runtime_content=failing_content)

    failed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={"HARNESS_TEST_SMOKE_COUNTER": str(counter)},
    )

    assert failed.returncode == 0, failed.stderr
    assert "Update it with: adp-harness self update" in failed.stdout
    assert f"{tmp_path / 'bin'}/adp-harness self update" not in failed.stdout
    assert current.readlink() == original_target
    assert not previous.exists()
    assert sorted(path.name for path in (runtime_parent / "slots").iterdir()) == (
        original_slots
    )
    assert not counter.exists()


def test_repeat_installer_does_not_resolve_latest_before_health_check(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    first = _run_installer(tmp_path, release_root)
    assert first.returncode == 0, first.stderr
    wrappers = tmp_path / "offline-wrappers"
    wrappers.mkdir()
    marker = tmp_path / "unexpected-curl"
    curl = wrappers / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        f"printf 'called\\n' > '{marker}'\n"
        "exit 55\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    repeated = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_VERSION": "",
            "PATH": f"{wrappers}:/usr/bin:/bin",
        },
    )

    assert repeated.returncode == 0, repeated.stderr
    assert "Update it with: adp-harness self update" in repeated.stdout
    assert not marker.exists()


def test_failed_install_preserves_concurrent_profile_change(
    tmp_path: Path,
) -> None:
    release_root = _release(
        tmp_path,
        runtime_content=(
            b"#!/bin/sh\n"
            b"if [ ! -e \"$HARNESS_TEST_SMOKE_COUNTER\" ]; then\n"
            b"  : > \"$HARNESS_TEST_SMOKE_COUNTER\"; printf '0.1.0\\n'\n"
            b"else\n"
            b"  printf '# concurrent profile change\\n' >> \"$HARNESS_TEST_PROFILE\"\n"
            b"  printf 'wrong-version\\n'\n"
            b"fi\n"
        ),
    )
    profile = tmp_path / "home/.bashrc"

    completed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_CONFIRM_PATH": "1",
            "HARNESS_NONINTERACTIVE": "0",
            "HARNESS_SHELL_PROFILE": str(profile),
            "HARNESS_TEST_PROFILE": str(profile),
            "HARNESS_TEST_SMOKE_COUNTER": str(tmp_path / "smoke-counter"),
        },
    )

    assert completed.returncode != 0
    assert "recovery was incomplete" in completed.stderr
    assert "# concurrent profile change" in profile.read_text(encoding="utf-8")


def test_repeat_installer_does_not_enter_pointer_recovery_paths(
    tmp_path: Path,
) -> None:
    release_root = _release(tmp_path)
    first = _run_installer(tmp_path, release_root)
    assert first.returncode == 0, first.stderr
    runtime_parent = tmp_path / "home/.local/share/harness/runtime"
    current = runtime_parent / "current"
    counter = tmp_path / "current-move-count"
    wrappers = tmp_path / "pointer-wrappers"
    wrappers.mkdir()
    mv_wrapper = wrappers / "mv"
    mv_wrapper.write_text(
        "#!/bin/sh\n"
        "target=\n"
        "for argument in \"$@\"; do target=$argument; done\n"
        "if [ \"$target\" = \"$HARNESS_TEST_CURRENT\" ]; then\n"
        "  if [ -e \"$HARNESS_TEST_COUNTER\" ]; then exit 1; fi\n"
        "  /bin/mv \"$@\"\n"
        "  status=$?\n"
        "  if [ \"$status\" -eq 0 ]; then : > \"$HARNESS_TEST_COUNTER\"; fi\n"
        "  exit \"$status\"\n"
        "fi\n"
        "exec /bin/mv \"$@\"\n",
        encoding="utf-8",
    )
    mv_wrapper.chmod(0o755)
    smoke_counter = tmp_path / "smoke-counter"
    _release(
        tmp_path,
        runtime_content=(
            b"#!/bin/sh\n"
            b"if [ ! -e \"$HARNESS_TEST_SMOKE_COUNTER\" ]; then\n"
            b"  : > \"$HARNESS_TEST_SMOKE_COUNTER\"; printf '0.1.0\\n'\n"
            b"else printf 'wrong-version\\n'; fi\n"
        ),
    )

    failed = _run_installer(
        tmp_path,
        release_root,
        environment_overrides={
            "HARNESS_TEST_CURRENT": str(current),
            "HARNESS_TEST_COUNTER": str(counter),
            "HARNESS_TEST_SMOKE_COUNTER": str(smoke_counter),
            "PATH": f"{wrappers}:/usr/bin:/bin",
        },
    )

    assert failed.returncode == 0, failed.stderr
    assert "adp-harness self update" in failed.stdout
    assert current.is_symlink()
    assert (current / "adp-harness").is_file()
    assert not counter.exists()


def test_release_packager_emits_installable_archive_and_checksum(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "built-harness"
    bundle.mkdir()
    binary = bundle / "adp-harness"
    binary.write_bytes(b"standalone-binary")
    binary.chmod(0o755)
    support = bundle / "_internal/support.dat"
    support.parent.mkdir()
    support.write_bytes(b"support")
    output = tmp_path / "release"

    completed = subprocess.run(
        (
            sys.executable,
            str(PACKAGER),
            "--bundle",
            str(bundle),
            "--version",
            "0.1.0",
            "--target",
            "linux-x86_64",
            "--output",
            str(output),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    archive = output / "adaptive-harness-v0.1.0-linux-x86_64.tar.gz"
    checksum = output / "adaptive-harness-v0.1.0-linux-x86_64.tar.gz.sha256"
    assert completed.returncode == 0, completed.stderr
    assert checksum.read_text(encoding="utf-8") == (
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    with tarfile.open(archive, "r:gz") as archive_bundle:
        member = archive_bundle.getmember("runtime/adp-harness")
        assert member.mode == 0o755
        extracted = archive_bundle.extractfile(member)
        assert extracted is not None
        assert extracted.read() == b"standalone-binary"
        support_member = archive_bundle.extractfile(
            "runtime/_internal/support.dat"
        )
        assert support_member is not None
        assert support_member.read() == b"support"


@pytest.mark.parametrize(
    ("version", "valid"),
    [
        ("1.2.3-rc.1+build.5", True),
        ("01.2.3", False),
        ("1.2.3-rc..1", False),
    ],
)
def test_release_packager_enforces_semver_2(
    tmp_path: Path, version: str, valid: bool
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    binary = bundle / "adp-harness"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)

    completed = subprocess.run(
        (
            sys.executable,
            str(PACKAGER),
            "--bundle",
            str(bundle),
            "--version",
            version,
            "--target",
            "linux-x86_64",
            "--output",
            str(tmp_path / "release"),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert (completed.returncode == 0) is valid



def test_release_packager_materializes_internal_symlinks(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    binary = bundle / "adp-harness"
    binary.write_bytes(b"standalone-binary")
    binary.chmod(0o755)
    target = bundle / "_internal/Python.framework/Versions/3.12/Python"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"framework-python")
    target.chmod(0o755)
    current = bundle / "_internal/Python.framework/Versions/Current"
    current.symlink_to("3.12", target_is_directory=True)
    output = tmp_path / "release"

    completed = subprocess.run(
        (
            sys.executable,
            str(PACKAGER),
            "--bundle",
            str(bundle),
            "--version",
            "0.1.0",
            "--target",
            "macos-arm64",
            "--output",
            str(output),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    archive = output / "adaptive-harness-v0.1.0-macos-arm64.tar.gz"
    with tarfile.open(archive, "r:gz") as archive_bundle:
        member = archive_bundle.getmember(
            "runtime/_internal/Python.framework/Versions/Current/Python"
        )
        assert member.isfile()
        assert not member.issym()
        extracted = archive_bundle.extractfile(member)
        assert extracted is not None
        assert extracted.read() == b"framework-python"
        assert member.mode == 0o755


def test_release_packager_rejects_external_symlinks(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    binary = bundle / "adp-harness"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (bundle / "escape").symlink_to(outside)

    completed = subprocess.run(
        (
            sys.executable,
            str(PACKAGER),
            "--bundle",
            str(bundle),
            "--version",
            "0.1.0",
            "--target",
            "macos-arm64",
            "--output",
            str(tmp_path / "release"),
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "escapes the bundle root" in completed.stderr


def test_release_pipeline_covers_supported_targets_and_attests_checksums() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    for target in (
        "linux-arm64",
        "linux-x86_64",
        "macos-arm64",
    ):
        assert target in workflow
    assert "macos-x86_64" not in workflow
    assert "actions/attest@v4" in workflow
    assert "subject-checksums: release/SHA256SUMS" in workflow
    assert 'runner: macos-14-large' not in workflow
    assert 'runner: macos-13' not in workflow
    assert "scripts/install.sh" in workflow
    assert "(cd release && sha256sum install.sh >> SHA256SUMS)" in workflow
    assert "sha256sum release/install.sh >> release/SHA256SUMS" not in workflow
    assert "--onedir" in workflow
    assert "--onefile" not in workflow
    assert "quality-gate:" in workflow
    assert "uv run pytest" in workflow
    assert "uv run ruff check ." in workflow
    assert "uv run mypy src tests" in workflow
    assert "needs: [quality-gate, build-standalone]" in workflow
    assert "HARNESS_RUN_OS_SANDBOX_E2E: \"1\"" in workflow
    assert "Standalone lifecycle gate" in workflow
    assert "HARNESS_RELEASE_BASE" in workflow
    assert "self update" in workflow
    assert "self uninstall" in workflow
    assert 'python-version: ["3.12", "3.13"]' in workflow


def test_readme_documents_download_verify_then_execute_installation() -> None:
    readme = (REPOSITORY / "README.md").read_text(encoding="utf-8")

    assert "-o install.sh" in readme
    assert "SHA256SUMS" in readme
    assert "shasum -a 256" in readme or "sha256sum" in readme
    assert "sh install.sh" in readme
