"""Verified self-update and conservative standalone uninstall support."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from difflib import unified_diff
from pathlib import Path, PurePosixPath
from typing import Any, Self, cast

from jsonschema import ValidationError

from adaptive_harness.distribution.version import is_semver
from adaptive_harness.schemas import validator_for

_TARGETS = {
    "linux-arm64",
    "linux-x86_64",
    "macos-arm64",
    "macos-x86_64",
}
_CLI_NAME = "adp-harness"
_PRODUCT_ID = "dev.adaptive-harness.cli"
_REPAIR_COMMAND = (
    "curl --proto '=https' --tlsv1.2 -LsSf "
    "https://github.com/hilt21/Adaptive-Harness/releases/latest/download/install.sh "
    "| sh"
)


@dataclass(frozen=True, slots=True)
class InstallManifest:
    schema_version: str
    product_id: str
    channel: str
    version: str
    binary_path: Path
    data_root: Path
    runtime_path: Path
    release_base: str
    release_repository: str
    path_profile: Path | None
    launcher_sha256: str
    runtime_sha256: str
    release_archive_sha256: str
    path_block_sha256: str | None
    profile_created_by_installer: bool

    @classmethod
    def load(cls, path: Path) -> Self:
        expected_manifest_target = Path("runtime/current/installation.json")
        if not path.exists() and not path.is_symlink():
            raise ValueError(
                "standalone installation metadata is unavailable; use the original "
                "package manager"
            )
        if (
            not path.is_symlink()
            or path.readlink() != expected_manifest_target
            or not path.is_file()
        ):
            raise _unverified_installation()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise _unverified_installation() from error
        return cls.from_document(value)

    @classmethod
    def from_document(cls, value: Any) -> Self:
        if not isinstance(value, dict):
            raise _unverified_installation()
        try:
            validator_for("installation").validate(value)
        except ValidationError as error:
            raise _unverified_installation() from error
        binary = Path(cast(str, value["binary_path"]))
        data_root = Path(cast(str, value["data_root"]))
        runtime = Path(cast(str, value["runtime_path"]))
        profile_value = value["path_profile"]
        profile = Path(profile_value) if isinstance(profile_value, str) else None
        if (
            not binary.is_absolute()
            or binary.name != _CLI_NAME
            or not data_root.is_absolute()
            or not runtime.is_absolute()
            or runtime != data_root / "runtime/current"
            or data_root == Path.home()
            or data_root == Path(data_root.anchor)
        ):
            raise _unverified_installation()
        return cls(
            "2.0",
            _PRODUCT_ID,
            "standalone",
            cast(str, value["version"]),
            binary,
            data_root,
            runtime,
            cast(str, value["release_base"]),
            cast(str, value["release_repository"]),
            profile,
            cast(str, value["launcher_sha256"]),
            cast(str, value["runtime_sha256"]),
            cast(str, value["release_archive_sha256"]),
            cast(str | None, value["path_block_sha256"]),
            cast(bool, value["profile_created_by_installer"]),
        )

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "product_id": self.product_id,
            "channel": self.channel,
            "version": self.version,
            "binary_path": str(self.binary_path),
            "data_root": str(self.data_root),
            "runtime_path": str(self.runtime_path),
            "release_base": self.release_base,
            "release_repository": self.release_repository,
            "path_profile": str(self.path_profile) if self.path_profile else None,
            "launcher_sha256": self.launcher_sha256,
            "runtime_sha256": self.runtime_sha256,
            "release_archive_sha256": self.release_archive_sha256,
            "path_block_sha256": self.path_block_sha256,
            "profile_created_by_installer": self.profile_created_by_installer,
        }


@dataclass(frozen=True, slots=True)
class UpdateResult:
    previous_version: str
    version: str
    binary_path: Path
    backup_path: Path
    cleanup_pending: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class RollbackResult:
    previous_version: str
    version: str
    binary_path: Path
    backup_path: Path
    cleanup_pending: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class UninstallResult:
    binary_path: Path
    data_root: Path
    data_purged: bool
    cleanup_pending: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class UninstallPlan:
    manifest: InstallManifest
    runtime_slot: Path
    runtime_root: Path
    launcher_backup: Path
    affected_paths: tuple[Path, ...]
    binary_sha256: str
    profile_before: bytes | None
    profile_after: bytes | None
    profile_diff: str
    remove_created_profile: bool
    purge_data: bool


class SelfManager:
    def __init__(self, manifest_path: Path | None = None) -> None:
        self.manifest_path = Path(
            os.path.abspath(manifest_path or _default_manifest_path())
        )

    def _verified_manifest(self) -> InstallManifest:
        manifest = InstallManifest.load(self.manifest_path)
        if self.manifest_path != manifest.data_root / "installation.json":
            raise _unverified_installation()
        _verify_installed_files(manifest)
        return manifest

    def update(self, requested_version: str | None = None) -> UpdateResult:
        manifest = self._verified_manifest()
        with _installation_lock(manifest.data_root):
            current = self._verified_manifest()
            if current != manifest:
                raise ValueError("standalone installation changed before update")
            return self._update_locked(requested_version)

    def _update_locked(self, requested_version: str | None) -> UpdateResult:
        manifest = self._verified_manifest()
        version = requested_version or self._latest_version(manifest)
        if not is_semver(version):
            raise ValueError("self-update version must be a semantic version")
        if version == manifest.version:
            raise ValueError(f"Adaptive Harness {version} is already installed")
        target = _target()
        archive_name = f"adaptive-harness-v{version}-{target}.tar.gz"
        release_url = f"{manifest.release_base.rstrip('/')}/v{version}"
        archive = _download(f"{release_url}/{archive_name}")
        checksums = _download(f"{release_url}/SHA256SUMS").decode("utf-8")
        expected = _expected_checksum(checksums, archive_name)
        actual = hashlib.sha256(archive).hexdigest()
        if actual != expected:
            raise ValueError("release checksum verification failed")
        return self._replace(manifest, version, archive, actual)

    def rollback(self) -> RollbackResult:
        manifest = self._verified_manifest()
        with _installation_lock(manifest.data_root):
            current = self._verified_manifest()
            if current != manifest:
                raise ValueError("standalone installation changed before rollback")
            return self._rollback_locked(current)

    def plan_uninstall(self, *, purge_data: bool = False) -> UninstallPlan:
        manifest = self._verified_manifest()
        if purge_data and manifest.data_root in {
            Path.home(),
            Path(manifest.data_root.anchor),
        }:
            raise ValueError("refusing to purge an unsafe data root")
        runtime_slot = _runtime_slot(manifest.runtime_path)
        runtime_root = manifest.runtime_path.parent
        launcher_backup = manifest.binary_path.with_name(
            f"{_CLI_NAME}.previous"
        )
        if manifest.binary_path.is_symlink() or not manifest.binary_path.is_file():
            raise ValueError("installed standalone launcher is missing or unsafe")
        profile_before: bytes | None = None
        profile_after: bytes | None = None
        profile_diff = ""
        if manifest.path_profile is not None:
            if manifest.path_profile.is_relative_to(manifest.data_root):
                raise ValueError("managed shell profile overlaps the installation data")
            profile_before, profile_after = _managed_path_change(
                manifest.path_profile,
                manifest.binary_path.parent,
                manifest.path_block_sha256,
            )
            if profile_before is not None and profile_after is not None:
                profile_diff = "".join(
                    unified_diff(
                        profile_before.decode("utf-8").splitlines(keepends=True),
                        profile_after.decode("utf-8").splitlines(keepends=True),
                        fromfile=str(manifest.path_profile),
                        tofile=str(manifest.path_profile),
                        n=0,
                    )
                )
        affected_paths = _uninstall_paths(
            manifest,
            self.manifest_path,
            launcher_backup,
            purge_data=purge_data,
        )
        if manifest.path_profile is not None:
            affected_paths = (*affected_paths, manifest.path_profile)
        return UninstallPlan(
            manifest,
            runtime_slot,
            runtime_root,
            launcher_backup,
            affected_paths,
            hashlib.sha256(manifest.binary_path.read_bytes()).hexdigest(),
            profile_before,
            profile_after,
            profile_diff,
            manifest.profile_created_by_installer and profile_after == b"",
            purge_data,
        )

    def uninstall(self, plan: UninstallPlan) -> UninstallResult:
        with _installation_lock(plan.manifest.data_root):
            return self._uninstall_locked(plan)

    def _uninstall_locked(self, plan: UninstallPlan) -> UninstallResult:
        manifest = self._verified_manifest()
        if manifest != plan.manifest:
            raise ValueError("standalone installation changed after uninstall review")
        if self.manifest_path.parent != manifest.data_root:
            raise ValueError(
                "standalone installation metadata is outside its data root"
            )
        binary = manifest.binary_path
        backup = binary.with_name(f"{_CLI_NAME}.previous")
        for path in (binary, backup):
            if path.is_symlink():
                raise ValueError(f"refusing to uninstall symlink: {path}")
        if not binary.is_file():
            raise ValueError("installed standalone launcher is missing")
        if hashlib.sha256(binary.read_bytes()).hexdigest() != plan.binary_sha256:
            raise ValueError("installed standalone launcher changed after review")
        runtime_parent = manifest.runtime_path.parent
        if runtime_parent != manifest.data_root / "runtime":
            raise ValueError("standalone runtime path is unsafe")
        if _runtime_slot(manifest.runtime_path) != plan.runtime_slot:
            raise ValueError("standalone runtime changed after uninstall review")
        paths = _uninstall_paths(
            manifest,
            self.manifest_path,
            backup,
            purge_data=plan.purge_data,
        )
        staged: list[tuple[Path, Path]] = []
        profile_change_started = False
        try:
            for path in paths:
                recovery = _prepare_uninstall_recovery(path)
                staged.append((path, recovery))
                os.replace(path, recovery)
            if manifest.path_profile is not None:
                profile_change_started = True
                _apply_managed_path_change(
                    manifest.path_profile,
                    plan.profile_before,
                    plan.profile_after,
                    remove_file=plan.remove_created_profile,
                )
        except BaseException:
            restoration_error: BaseException | None = None
            if profile_change_started and manifest.path_profile is not None:
                try:
                    _restore_managed_path_change(
                        manifest.path_profile,
                        plan.profile_before,
                        plan.profile_after,
                        removed_file=plan.remove_created_profile,
                    )
                except BaseException as caught:
                    restoration_error = caught
            for original, recovery in reversed(staged):
                try:
                    if recovery.exists() or recovery.is_symlink():
                        os.replace(recovery, original)
                    elif not original.exists() and not original.is_symlink():
                        raise OSError(f"uninstall recovery is missing: {recovery}")
                except BaseException as caught:
                    restoration_error = restoration_error or caught
            if restoration_error is not None:
                raise ValueError(
                    "standalone uninstall failed and recovery was incomplete"
                ) from restoration_error
            raise
        cleanup_pending: list[Path] = []
        data_cleanup_pending = False
        for original, recovery in staged:
            try:
                _remove_staged_path(recovery)
            except (OSError, ValueError):
                cleanup_pending.append(recovery)
                if original == manifest.data_root:
                    data_cleanup_pending = True
        return UninstallResult(
            binary,
            manifest.data_root,
            plan.purge_data and not data_cleanup_pending,
            tuple(cleanup_pending),
        )

    def _replace(
        self,
        manifest: InstallManifest,
        version: str,
        archive: bytes,
        archive_sha256: str,
    ) -> UpdateResult:
        binary = manifest.binary_path
        if binary.is_symlink() or not binary.is_file():
            raise ValueError("installed standalone launcher is missing or unsafe")
        runtime = manifest.runtime_path
        runtime_parent = runtime.parent
        if runtime_parent != manifest.data_root / "runtime":
            raise ValueError("standalone runtime path is unsafe")
        _validate_runtime_directory(runtime)
        slots = runtime_parent / "slots"
        current_slot = _runtime_slot(runtime)
        backup = runtime_parent / "previous"
        previous_slot: Path | None = None
        previous_changed = False
        current_switch_started = False
        slots.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(dir=slots, prefix=f".{version}-"))
        try:
            try:
                _extract_runtime(archive, staged)
            except tarfile.TarError as error:
                raise ValueError("release archive is invalid") from error
            staged_binary = staged / _CLI_NAME
            _check_runtime_version(
                staged_binary,
                version,
                failure_message="updated runtime failed its version check",
            )
            runtime_sha256 = _sha256_file(staged_binary)
            _atomic_json(
                staged / "installation.json",
                replace(
                    manifest,
                    version=version,
                    runtime_sha256=runtime_sha256,
                    release_archive_sha256=archive_sha256,
                ).document(),
            )
            if backup.exists() or backup.is_symlink():
                previous_slot = _runtime_slot(backup)
            previous_changed = True
            _atomic_runtime_pointer(backup, current_slot)
            current_switch_started = True
            _atomic_runtime_pointer(runtime, staged)
            _check_runtime_version(binary, version)
            self._verified_manifest()
        except BaseException:
            restoration_error: BaseException | None = None
            if current_switch_started:
                try:
                    _atomic_runtime_pointer(runtime, current_slot)
                except BaseException as caught:
                    restoration_error = caught
            if previous_changed:
                try:
                    if previous_slot is None:
                        backup.unlink(missing_ok=True)
                    else:
                        _atomic_runtime_pointer(backup, previous_slot)
                except BaseException as caught:
                    restoration_error = restoration_error or caught
            staged_is_active = (
                runtime.is_symlink()
                and runtime.readlink() == staged.relative_to(runtime_parent)
            )
            if (
                not staged_is_active
                and staged.exists()
                and not staged.is_symlink()
            ):
                shutil.rmtree(staged)
            if restoration_error is not None:
                raise ValueError(
                    "standalone update failed and runtime recovery was incomplete"
                ) from restoration_error
            raise
        cleanup_pending: tuple[Path, ...] = ()
        if previous_slot is not None and previous_slot != current_slot:
            try:
                _remove_runtime_directory(previous_slot)
            except (OSError, ValueError):
                cleanup_pending = (previous_slot,)
        return UpdateResult(
            manifest.version,
            version,
            binary,
            backup,
            cleanup_pending,
        )

    def _rollback_locked(self, manifest: InstallManifest) -> RollbackResult:
        runtime = manifest.runtime_path
        runtime_parent = runtime.parent
        current_slot = _runtime_slot(runtime)
        backup = runtime_parent / "previous"
        try:
            previous_slot = _runtime_slot(backup)
        except ValueError as error:
            raise ValueError(
                "there is no verified previous Runtime to roll back to"
            ) from error
        if previous_slot == current_slot:
            raise ValueError(
                "previous and current point to the same Runtime; rollback refused"
            )
        previous_manifest = _load_slot_manifest(previous_slot)
        _verify_installed_files(previous_manifest, runtime_slot=previous_slot)
        if replace(
            previous_manifest,
            version=manifest.version,
            runtime_sha256=manifest.runtime_sha256,
            release_archive_sha256=manifest.release_archive_sha256,
        ) != manifest:
            raise _unverified_installation()

        current_changed = False
        backup_consumed = False
        try:
            _atomic_runtime_pointer(runtime, previous_slot)
            current_changed = True
            _check_runtime_version(manifest.binary_path, previous_manifest.version)
            self._verified_manifest()
            backup.unlink()
            backup_consumed = True
        except BaseException:
            restoration_error: BaseException | None = None
            if current_changed:
                try:
                    _atomic_runtime_pointer(runtime, current_slot)
                except BaseException as caught:
                    restoration_error = caught
            if backup_consumed:
                try:
                    _atomic_runtime_pointer(backup, previous_slot)
                except BaseException as caught:
                    restoration_error = restoration_error or caught
            if restoration_error is not None:
                raise ValueError(
                    "standalone rollback failed and runtime recovery was incomplete"
                ) from restoration_error
            raise

        cleanup_pending: tuple[Path, ...] = ()
        try:
            _remove_runtime_directory(current_slot)
        except (OSError, ValueError):
            cleanup_pending = (current_slot,)
        return RollbackResult(
            manifest.version,
            previous_manifest.version,
            manifest.binary_path,
            backup,
            cleanup_pending,
        )

    def _latest_version(self, manifest: InstallManifest) -> str:
        request = urllib.request.Request(
            f"https://github.com/{manifest.release_repository}/releases/latest",
            headers={"User-Agent": "adaptive-harness-self-update"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            response_url = cast(str, response.geturl())
            tag = response_url.rstrip("/").rsplit("/", 1)[-1]
        if not tag.startswith("v"):
            raise ValueError("could not resolve the latest standalone version")
        return tag[1:]


def _unverified_installation() -> ValueError:
    return ValueError(
        "Adaptive Harness installation cannot be verified. Repair it with: "
        f"{_REPAIR_COMMAND}"
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_runtime_version(
    binary: Path,
    expected_version: str,
    *,
    failure_message: str | None = None,
) -> None:
    try:
        completed = subprocess.run(
            (str(binary), "--version"),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        if failure_message is not None:
            raise ValueError(failure_message) from error
        raise _unverified_installation() from error
    if (
        completed.returncode != 0
        or completed.stdout.strip() != expected_version
    ):
        if failure_message is not None:
            raise ValueError(failure_message)
        raise _unverified_installation()


def _load_slot_manifest(slot: Path) -> InstallManifest:
    path = slot / "installation.json"
    if path.is_symlink() or not path.is_file():
        raise _unverified_installation()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _unverified_installation() from error
    return InstallManifest.from_document(value)


def _verify_installed_files(
    manifest: InstallManifest, *, runtime_slot: Path | None = None
) -> None:
    binary = manifest.binary_path
    if binary.is_symlink() or not binary.is_file():
        raise _unverified_installation()
    try:
        launcher_sha256 = _sha256_file(binary)
    except OSError as error:
        raise _unverified_installation() from error
    if launcher_sha256 != manifest.launcher_sha256:
        raise _unverified_installation()
    slot = runtime_slot or _runtime_slot(manifest.runtime_path)
    executable = slot / _CLI_NAME
    if executable.is_symlink() or not executable.is_file():
        raise _unverified_installation()
    try:
        runtime_sha256 = _sha256_file(executable)
    except OSError as error:
        raise _unverified_installation() from error
    if runtime_sha256 != manifest.runtime_sha256:
        raise _unverified_installation()
    _check_runtime_version(executable, manifest.version)


def _default_manifest_path() -> Path:
    configured = os.environ.get("HARNESS_INSTALL_MANIFEST")
    if configured:
        return Path(configured)
    xdg_data = os.environ.get("XDG_DATA_HOME")
    data_root = (
        Path(xdg_data) / "harness"
        if xdg_data
        else Path.home() / ".local/share/harness"
    )
    return data_root / "installation.json"


@contextmanager
def _installation_lock(data_root: Path) -> Iterator[None]:
    lock = data_root.with_name(f"{data_root.name}.install.lock")
    _acquire_installation_lock(lock)
    try:
        yield
    finally:
        _release_installation_lock(lock)


def _acquire_installation_lock(lock: Path) -> None:
    descriptor, candidate_name = tempfile.mkstemp(
        dir=lock.parent, prefix=f".{lock.name}.candidate-"
    )
    candidate = Path(candidate_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(candidate, lock, follow_symlinks=False)
            return
        except FileExistsError:
            raise ValueError(
                "another standalone installation operation is in progress "
                f"(lock: {lock}). If no installation operation is running, "
                "remove that exact lock file and retry."
            ) from None
    finally:
        candidate.unlink(missing_ok=True)


def _release_installation_lock(lock: Path) -> None:
    try:
        if lock.is_symlink() or not lock.is_file():
            return
        owner = lock.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return
    if owner != str(os.getpid()):
        return
    with suppress(FileNotFoundError):
        lock.unlink()


def _target() -> str:
    configured = os.environ.get("HARNESS_TARGET")
    if configured:
        if configured not in _TARGETS:
            raise ValueError(f"unsupported release target: {configured}")
        return configured
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        operating_system = "macos"
    elif system == "Linux":
        operating_system = "linux"
    else:
        raise ValueError(f"unsupported operating system: {system}")
    if machine in {"arm64", "aarch64"}:
        architecture = "arm64"
    elif machine in {"x86_64", "amd64"}:
        architecture = "x86_64"
    else:
        raise ValueError(f"unsupported CPU architecture: {machine}")
    return f"{operating_system}-{architecture}"


def _download(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "adaptive-harness-self-update"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return cast(bytes, response.read())


def _expected_checksum(checksums: str, archive_name: str) -> str:
    matches = [
        line.split()[0]
        for line in checksums.splitlines()
        if len(line.split()) == 2 and line.split()[1] == archive_name
    ]
    if len(matches) != 1 or re.fullmatch(r"[0-9a-f]{64}", matches[0]) is None:
        raise ValueError(f"release checksum is missing for {archive_name}")
    return matches[0]


def _extract_runtime(archive: bytes, destination: Path) -> None:
    seen: set[PurePosixPath] = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        for member in bundle.getmembers():
            archive_path = PurePosixPath(member.name)
            if (
                archive_path.is_absolute()
                or not archive_path.parts
                or archive_path.parts[0] != "runtime"
                or ".." in archive_path.parts
                or archive_path in seen
            ):
                raise ValueError("release archive contains an unsafe runtime path")
            seen.add(archive_path)
            relative = archive_path.relative_to("runtime")
            if not relative.parts:
                if not member.isdir():
                    raise ValueError("release archive runtime root is invalid")
                continue
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError("release archive contains a non-regular file")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise ValueError("release archive contains a duplicate runtime path")
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError("release archive contains an unreadable runtime file")
            with target.open("xb") as handle:
                shutil.copyfileobj(source, handle)
            target.chmod(member.mode & 0o777)
    _validate_runtime_contents(destination)


def _validate_runtime_directory(path: Path) -> None:
    _validate_runtime_contents(_runtime_slot(path))


def _validate_runtime_contents(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("installed standalone runtime is missing or unsafe")
    executable = path / _CLI_NAME
    if executable.is_symlink() or not executable.is_file():
        raise ValueError(
            f"standalone runtime has no safe {_CLI_NAME} executable"
        )


def _runtime_slot(pointer: Path) -> Path:
    if not pointer.is_symlink():
        raise ValueError("installed standalone runtime pointer is missing or unsafe")
    relative = pointer.readlink()
    if (
        relative.is_absolute()
        or len(relative.parts) != 2
        or relative.parts[0] != "slots"
        or relative.parts[1] in {".", ".."}
    ):
        raise ValueError("installed standalone runtime pointer target is unsafe")
    target = pointer.parent.joinpath(*relative.parts)
    slots = pointer.parent / "slots"
    if target.parent != slots or target.is_symlink() or not target.is_dir():
        raise ValueError("installed standalone runtime is missing or unsafe")
    return target


def _atomic_runtime_pointer(pointer: Path, target: Path) -> None:
    relative = target.relative_to(pointer.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=pointer.parent, prefix=f".{pointer.name}-", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        temporary.symlink_to(relative)
        os.replace(temporary, pointer)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _remove_runtime_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"refusing to remove unsafe runtime path: {path}")
    shutil.rmtree(path)


def _uninstall_paths(
    manifest: InstallManifest,
    manifest_path: Path,
    launcher_backup: Path,
    *,
    purge_data: bool,
) -> tuple[Path, ...]:
    paths: list[Path] = []
    if not purge_data or not manifest.binary_path.is_relative_to(manifest.data_root):
        paths.append(manifest.binary_path)
        if launcher_backup.exists() or launcher_backup.is_symlink():
            paths.append(launcher_backup)
    if purge_data:
        paths.append(manifest.data_root)
    else:
        paths.extend((manifest.runtime_path.parent, manifest_path))
    return tuple(paths)


def _prepare_uninstall_recovery(path: Path) -> Path:
    if not path.exists() and not path.is_symlink():
        raise ValueError(f"uninstall path changed after review: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-uninstall-", suffix=".recovery"
    )
    os.close(descriptor)
    recovery = Path(temporary_name)
    recovery.unlink()
    return recovery


def _remove_staged_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        raise ValueError(f"uninstall recovery path is unsafe: {path}")


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    content = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _managed_path_change(
    profile: Path,
    install_dir: Path,
    expected_block_sha256: str | None,
) -> tuple[bytes | None, bytes | None]:
    if not profile.exists():
        return None, None
    if profile.is_symlink() or not profile.is_file():
        raise ValueError(f"refusing to modify unsafe shell profile: {profile}")
    lines = profile.read_text(encoding="utf-8").splitlines(keepends=True)
    start_marker = "# >>> adaptive-harness PATH >>>"
    end_marker = "# <<< adaptive-harness PATH <<<"
    escaped_install_dir = str(install_dir).replace("'", "'\\''")
    expected_path_line = (
        f"fish_add_path '{escaped_install_dir}'"
        if profile.suffix == ".fish"
        else f"export PATH='{escaped_install_dir}':\"$PATH\""
    )
    expected_block = (
        f"{start_marker}\n{expected_path_line}\n{end_marker}\n"
    ).encode()
    if (
        expected_block_sha256 is None
        or hashlib.sha256(expected_block).hexdigest()
        != expected_block_sha256
    ):
        raise _unverified_installation()
    starts = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n") == start_marker
    ]
    ends = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n") == end_marker
    ]
    if not starts and not ends:
        content = profile.read_bytes()
        return content, content
    if (
        len(starts) != 1
        or len(ends) != 1
        or ends[0] != starts[0] + 2
        or lines[starts[0] + 1].rstrip("\r\n") != expected_path_line
    ):
        raise ValueError(f"managed PATH block is malformed in {profile}")
    start = starts[0]
    if start > 0 and lines[start - 1].strip() == "":
        start -= 1
    before = "".join(lines).encode("utf-8")
    after = "".join((*lines[:start], *lines[ends[0] + 1 :])).encode("utf-8")
    return before, after


def _apply_managed_path_change(
    profile: Path,
    expected_before: bytes | None,
    after: bytes | None,
    *,
    remove_file: bool = False,
) -> None:
    if expected_before is None and after is None:
        if profile.exists() or profile.is_symlink():
            raise ValueError(f"shell profile changed after uninstall review: {profile}")
        return
    if expected_before is None or after is None:
        raise ValueError("managed PATH change was not reviewed")
    if profile.read_bytes() != expected_before:
        raise ValueError(f"shell profile changed after uninstall review: {profile}")
    if expected_before == after:
        return
    if remove_file and after == b"":
        profile.unlink()
        return
    descriptor, temporary_name = tempfile.mkstemp(
        dir=profile.parent, prefix=f".{profile.name}-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            shutil.copymode(profile, temporary)
            handle.write(after)
            handle.flush()
            os.fsync(handle.fileno())
        if profile.read_bytes() != expected_before:
            raise ValueError(
                f"shell profile changed while preparing uninstall: {profile}"
            )
        os.replace(temporary, profile)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _restore_managed_path_change(
    profile: Path,
    before: bytes | None,
    after: bytes | None,
    *,
    removed_file: bool = False,
) -> None:
    if before is None and after is None:
        return
    if before is None or after is None:
        raise ValueError("managed PATH recovery was not reviewed")
    if removed_file and not profile.exists() and not profile.is_symlink():
        _atomic_bytes(profile, before)
        return
    current = profile.read_bytes()
    if current == after:
        _apply_managed_path_change(profile, after, before)
    elif current != before:
        raise ValueError(
            f"shell profile changed during uninstall recovery: {profile}"
        )


def _atomic_bytes(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "RollbackResult",
    "SelfManager",
    "UninstallPlan",
    "UninstallResult",
    "UpdateResult",
]
