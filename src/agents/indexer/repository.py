"""
Repository Manager

Handles git operations: cloning, pulling, file discovery,
reading files, and computing diffs between commits.

Uses GitPython for all git operations (deployment-friendly).
"""

import asyncio
import os
import logging
import shutil
import tempfile
from pathlib import Path

import git

logger = logging.getLogger("indexer-agent.repository")

# Directories and files to skip during indexing
SKIP_DIRS = {
    "__pycache__", ".git", ".tox", ".mypy_cache", ".pytest_cache",
    "node_modules", ".eggs", "*.egg-info", "venv", ".venv", "env",
    "build", "dist", ".nox",
    "docs_src",  "docs",
    "tests",
}

SKIP_FILES = {
    "setup.py", "conftest.py", "noxfile.py",
}


class RepositoryManager:
    """Manages git repository operations for the indexer via GitPython."""

    def __init__(self, clone_dir: str | None = None):
        if clone_dir:
            self._clone_dir = Path(clone_dir)
            self._is_temp = False
        else:
            self._clone_dir = Path(tempfile.mkdtemp(prefix="indexer-repos-"))
            self._is_temp = True
        self._clone_dir.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        """Remove the cloned repository directory."""
        if self._clone_dir.exists():
            shutil.rmtree(self._clone_dir, ignore_errors=True)
            logger.info("Cleaned up temporary clone directory: %s", self._clone_dir)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._is_temp:
            self.cleanup()

    async def clone(self, repo_url: str, branch: str = "main") -> Path:
        """
        Clone a repository. If already cloned, fetch and reset to latest.

        Returns:
            Path to the cloned repository.
        """
        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        repo_path = self._clone_dir / repo_name

        if repo_path.exists():
            logger.info("Repository exists at %s, pulling latest...", repo_path)
            repo = git.Repo(repo_path)

            def _fetch_and_reset():
                repo.remotes.origin.fetch()
                logger.info("Checked out branch %s and reset to origin", branch)
                repo.git.checkout(branch)
                repo.git.reset("--hard", f"origin/{branch}")

            await asyncio.to_thread(_fetch_and_reset)
            logger.info("Repository updated successfully")
        else:
            logger.info("Cloning %s (branch: %s) to %s...", repo_url, branch, repo_path)
            await asyncio.to_thread(
                git.Repo.clone_from,
                repo_url,
                str(repo_path),
                branch=branch,
                multi_options=["--single-branch"],
            )
            logger.info("Repository cloned successfully")

        return repo_path

    async def get_head_commit(self, repo_path: Path) -> str:
        """Get the HEAD commit hash."""
        repo = git.Repo(repo_path)
        return repo.head.commit.hexsha

    async def discover_python_files(self, repo_path: Path) -> list[str]:
        """
        Find all Python files in the repository.
        Returns paths relative to the repo root.
        """
        python_files = []

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [
                d for d in dirs
                if d not in SKIP_DIRS and not d.endswith(".egg-info")
            ]

            for filename in files:
                if not filename.endswith(".py"):
                    continue
                if filename in SKIP_FILES:
                    continue

                full_path = Path(root) / filename
                rel_path = str(full_path.relative_to(repo_path)).replace("\\", "/")
                python_files.append(rel_path)

        python_files.sort()
        logger.info("Discovered %d Python files", len(python_files))
        return python_files

    async def read_file(self, repo_path: Path, file_path: str) -> str:
        """Read file contents from the cloned repo."""
        full_path = repo_path / file_path
        return full_path.read_text(encoding="utf-8", errors="replace")

    async def read_file_from_working_dir(self, file_path: str) -> str:
        """
        Read file from the current working repo directory.
        Used during incremental updates.
        """
        repos = list(self._clone_dir.iterdir())
        if not repos:
            raise FileNotFoundError("No repository cloned")

        repo_path = repos[0]
        return await self.read_file(repo_path, file_path)

    async def get_changed_files(
        self, repo_path: Path, from_commit: str, to_commit: str = "HEAD"
    ) -> dict[str, list[str]]:
        """
        Get files changed between two commits.

        Returns:
            Dict with keys 'added', 'modified', 'deleted', 'renamed'.
            Each value is a list of file paths.
        """
        repo = git.Repo(repo_path)

        def _diff() -> dict[str, list]:
            output = repo.git.diff(
                "--name-status", "-M", from_commit, to_commit, "--", "*.py"
            )

            changes: dict[str, list] = {
                "added": [],
                "modified": [],
                "deleted": [],
                "renamed": [],
            }

            if not output:
                return changes

            for line in output.splitlines():
                parts = line.split("\t")
                status = parts[0]

                if status == "A":
                    changes["added"].append(parts[1])
                elif status == "M":
                    changes["modified"].append(parts[1])
                elif status == "D":
                    changes["deleted"].append(parts[1])
                elif status.startswith("R"):
                    changes["renamed"].append((parts[1], parts[2]))

            return changes

        changes = await asyncio.to_thread(_diff)

        logger.info(
            "Changes: %d added, %d modified, %d deleted, %d renamed",
            len(changes["added"]),
            len(changes["modified"]),
            len(changes["deleted"]),
            len(changes["renamed"]),
        )
        return changes

    async def get_repo_path(self) -> Path:
        """Get the path of the cloned repository."""
        repos = list(self._clone_dir.iterdir())
        if not repos:
            raise FileNotFoundError("No repository cloned")
        return repos[0]
