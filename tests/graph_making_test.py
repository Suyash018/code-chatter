"""
Repository Manager

Handles git operations: cloning, pulling, file discovery,
reading files, and computing diffs between commits.
"""

import asyncio
import os
import logging
from pathlib import Path

logger = logging.getLogger("indexer-agent.repository")

# Directories and files to skip during indexing
SKIP_DIRS = {
    "__pycache__", ".git", ".tox", ".mypy_cache", ".pytest_cache",
    "node_modules", ".eggs", "*.egg-info", "venv", ".venv", "env",
    "build", "dist", ".nox",
}

SKIP_FILES = {
    "setup.py", "conftest.py", "noxfile.py",
}


class RepositoryManager:
    """Manages git repository operations for the indexer."""

    def __init__(self, clone_dir: str = "/tmp/indexer-repos"):
        self._clone_dir = Path(clone_dir)
        self._clone_dir.mkdir(parents=True, exist_ok=True)

    async def _run_git(self, *args: str, cwd: str | Path | None = None) -> str:
        """Run a git command and return stdout."""
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed: {stderr.decode().strip()}"
            )
        return stdout.decode().strip()

    async def clone(self, repo_url: str, branch: str = "main") -> Path:
        """
        Clone a repository. If already cloned, fetch and reset to latest.

        Returns:
            Path to the cloned repository.
        """
        # Derive repo name from URL
        repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
        repo_path = self._clone_dir / repo_name

        if repo_path.exists():
            # Already cloned — fetch and reset
            logger.info(f"Repository exists at {repo_path}, pulling latest...")
            await self._run_git("fetch", "--all", cwd=repo_path)
            await self._run_git("checkout", branch, cwd=repo_path)
            await self._run_git("reset", "--hard", f"origin/{branch}", cwd=repo_path)
        else:
            # Fresh clone
            logger.info(f"Cloning {repo_url} to {repo_path}...")
            await self._run_git(
                "clone", "--branch", branch, "--single-branch",
                repo_url, str(repo_path),
            )

        return repo_path

    async def get_head_commit(self, repo_path: Path) -> str:
        """Get the HEAD commit hash."""
        return await self._run_git("rev-parse", "HEAD", cwd=repo_path)

    async def discover_python_files(self, repo_path: Path) -> list[str]:
        """
        Find all Python files in the repository.
        Returns paths relative to the repo root.
        """
        python_files = []

        for root, dirs, files in os.walk(repo_path):
            # Skip unwanted directories
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
                rel_path = str(full_path.relative_to(repo_path))
                python_files.append(rel_path)

        python_files.sort()
        logger.info(f"Discovered {len(python_files)} Python files")
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
        # Find the repo in our clone directory
        repos = list(self._clone_dir.iterdir())
        if not repos:
            raise FileNotFoundError("No repository cloned")

        repo_path = repos[0]  # Use the first (and typically only) repo
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
        # Get diff with rename detection
        output = await self._run_git(
            "diff", "--name-status", "-M", from_commit, to_commit,
            "--", "*.py",
            cwd=repo_path,
        )

        changes = {
            "added": [],
            "modified": [],
            "deleted": [],
            "renamed": [],  # List of (old_path, new_path) tuples
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
                # Rename: R100\told_path\tnew_path
                changes["renamed"].append((parts[1], parts[2]))

        logger.info(
            f"Changes: {len(changes['added'])} added, "
            f"{len(changes['modified'])} modified, "
            f"{len(changes['deleted'])} deleted, "
            f"{len(changes['renamed'])} renamed"
        )
        return changes

    async def get_repo_path(self) -> Path:
        """Get the path of the cloned repository."""
        repos = list(self._clone_dir.iterdir())
        if not repos:
            raise FileNotFoundError("No repository cloned")
        return repos[0]