"""
engine/packs.py

Manages gesture "packs" — curated gesture→action bundles that override
the default active-app detection when active.

GitHub Pack: 6 fully automated git workflow chains + hold_center to exit.
No dialogs, no user input — every chain runs start to finish silently.

Results are logged to the terminal and shown via the status banner.
"""

from __future__ import annotations
import os
import subprocess
import sys
import time
import threading
from dataclasses import dataclass


# ── CHAIN RUNNER ──────────────────────────────────────────────────────────────

class ChainResult:
    """Holds the outcome of a multi-step git chain."""
    def __init__(self):
        self.steps: list[dict] = []
        self.failed_at: int | None = None

    def add(self, cmd: str, ok: bool, output: str):
        self.steps.append({"cmd": cmd, "ok": ok, "output": output})
        if not ok and self.failed_at is None:
            self.failed_at = len(self.steps) - 1

    @property
    def success(self) -> bool:
        return self.failed_at is None

    @property
    def summary(self) -> str:
        if self.success:
            return f"✓ {len(self.steps)} steps completed"
        step = self.steps[self.failed_at]
        return f"✗ failed at step {self.failed_at + 1}: {step['cmd']}"


def _find_git_repo() -> str | None:
    """
    Finds git repo root from VS Code's open workspace via AppleScript,
    falling back to current working directory.
    """
    if sys.platform == "darwin":
        script = 'tell application "Visual Studio Code" to set ws to path of front document'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            candidate = r.stdout.strip()
            r2 = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=candidate, capture_output=True, text=True
            )
            if r2.returncode == 0:
                return r2.stdout.strip()

    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=os.getcwd(), capture_output=True, text=True
    )
    return r.stdout.strip() if r.returncode == 0 else None


def _git(args: list[str], repo: str) -> tuple[bool, str]:
    r = subprocess.run(["git"] + args, cwd=repo, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _gh(args: list[str], repo: str) -> tuple[bool, str]:
    r = subprocess.run(["gh"] + args, cwd=repo, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _run_chain(steps: list[tuple[list[str], str]], repo: str) -> ChainResult:
    """
    Runs a sequence of (args, tool) steps where tool is "git" or "gh".
    Stops on first failure.
    """
    result = ChainResult()
    for args, tool in steps:
        cmd_str = f"{tool} {' '.join(args)}"
        ok, out = _gh(args, repo) if tool == "gh" else _git(args, repo)
        print(f"[github_pack] {cmd_str} → {'OK' if ok else 'FAIL'}")
        if out:
            print(f"[github_pack]   {out[:300]}")
        result.add(cmd_str, ok, out)
        if not ok:
            break
    return result


def _open_url(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", url])
    elif sys.platform == "win32":
        subprocess.Popen(["start", url], shell=True)
    else:
        subprocess.Popen(["xdg-open", url])


def _github_url(repo: str) -> str | None:
    ok, remote = _git(["remote", "get-url", "origin"], repo)
    if not ok:
        return None
    if remote.startswith("git@"):
        path = remote.split("github.com:")[-1].removesuffix(".git")
        return f"https://github.com/{path}"
    return remote.removesuffix(".git")


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _has_changes(repo: str) -> bool:
    ok, out = _git(["status", "--porcelain"], repo)
    return ok and bool(out.strip())


# ── CHAIN ACTIONS ─────────────────────────────────────────────────────────────

def _chain_quick_push(root) -> None:
    """
    swipe_right — Quick Push
    1. git add -A
    2. git status --short          (verify what's staged)
    3. git commit -m "wip: <ts>"
    4. git push origin HEAD
    5. open GitHub repo in browser
    """
    from ui.review_dialog import show_review_banner
    repo = _find_git_repo()
    if not repo:
        show_review_banner(root, "No git repo found", False)
        return

    ts = _timestamp()
    result = _run_chain([
        (["add", "-A"],                        "git"),
        (["status", "--short"],                "git"),
        (["commit", "-m", f"wip: {ts}"],       "git"),
        (["push", "origin", "HEAD"],           "git"),
    ], repo)

    if result.success:
        url = _github_url(repo)
        if url:
            _open_url(url)
        show_review_banner(root, f"Pushed ✓  wip: {ts}", True)
    else:
        show_review_banner(root, f"Push failed — {result.summary}", False)


def _chain_sync_main(root) -> None:
    """
    swipe_left — Morning Sync
    1. git stash push -m gesturepuck-sync  (if dirty)
    2. git checkout main
    3. git fetch --all --prune
    4. git pull origin main
    5. git stash pop                       (if we stashed)
    6. open GitHub PRs page in browser
    """
    from ui.review_dialog import show_review_banner
    repo = _find_git_repo()
    if not repo:
        show_review_banner(root, "No git repo found", False)
        return

    had_changes = _has_changes(repo)
    result = ChainResult()

    if had_changes:
        ok, out = _git(["stash", "push", "-m", "gesturepuck-sync"], repo)
        result.add("git stash push", ok, out)
        if not ok:
            show_review_banner(root, "Could not stash changes", False)
            return

    chain = _run_chain([
        (["checkout", "main"],          "git"),
        (["fetch", "--all", "--prune"], "git"),
        (["pull", "origin", "main"],    "git"),
    ], repo)
    for s in chain.steps:
        result.add(s["cmd"], s["ok"], s["output"])

    if had_changes and chain.success:
        ok, out = _git(["stash", "pop"], repo)
        result.add("git stash pop", ok, out)

    if result.success:
        url = _github_url(repo)
        if url:
            _open_url(f"{url}/pulls")
        show_review_banner(root, "Synced with main ✓", True)
    else:
        show_review_banner(root, f"Sync failed — {result.summary}", False)


def _chain_new_feature_branch(root) -> None:
    """
    swipe_up — New Feature Branch
    1. git stash push -m gesturepuck-feature  (if dirty)
    2. git checkout main
    3. git fetch --all --prune
    4. git pull origin main
    5. git checkout -b feature/<timestamp>
    6. git stash pop                          (restore work onto new branch)
    """
    from ui.review_dialog import show_review_banner
    repo = _find_git_repo()
    if not repo:
        show_review_banner(root, "No git repo found", False)
        return

    had_changes = _has_changes(repo)
    branch_name = f"feature/{_timestamp()}"
    result = ChainResult()

    if had_changes:
        ok, out = _git(["stash", "push", "-m", "gesturepuck-feature"], repo)
        result.add("git stash push", ok, out)
        if not ok:
            show_review_banner(root, "Could not stash changes", False)
            return

    chain = _run_chain([
        (["checkout", "main"],            "git"),
        (["fetch", "--all", "--prune"],   "git"),
        (["pull", "origin", "main"],      "git"),
        (["checkout", "-b", branch_name], "git"),
    ], repo)
    for s in chain.steps:
        result.add(s["cmd"], s["ok"], s["output"])

    if had_changes and chain.success:
        ok, out = _git(["stash", "pop"], repo)
        result.add("git stash pop", ok, out)

    if result.success:
        show_review_banner(root, f"Branch ready: {branch_name} ✓", True)
    else:
        show_review_banner(root, f"Branch failed — {result.summary}", False)


def _chain_stash_and_clean(root) -> None:
    """
    swipe_down — Stash & Clean
    1. git stash push -m gesturepuck-clean
    2. git clean -fd                          (remove untracked files/dirs)
    3. git status                             (verify clean)
    4. git fetch --all --prune
    5. git remote prune origin
    6. delete all local branches merged into main
    """
    from ui.review_dialog import show_review_banner
    repo = _find_git_repo()
    if not repo:
        show_review_banner(root, "No git repo found", False)
        return

    result = _run_chain([
        (["stash", "push", "-m", "gesturepuck-clean"], "git"),
        (["clean", "-fd"],                              "git"),
        (["status"],                                    "git"),
        (["fetch", "--all", "--prune"],                 "git"),
        (["remote", "prune", "origin"],                 "git"),
    ], repo)

    # Delete merged branches separately since it needs two commands
    if result.success:
        ok, merged = _git(["branch", "--merged", "main"], repo)
        if ok and merged:
            branches = [
                b.strip() for b in merged.splitlines()
                if b.strip()
                and b.strip() not in ("main", "master")
                and not b.strip().startswith("*")
            ]
            for branch in branches:
                ok2, out2 = _git(["branch", "-d", branch], repo)
                result.add(f"git branch -d {branch}", ok2, out2)
                print(f"[github_pack] deleted merged branch: {branch}")

    if result.success:
        show_review_banner(root, "Stashed & cleaned ✓", True)
    else:
        show_review_banner(root, f"Clean failed — {result.summary}", False)


def _chain_pr_status(root) -> None:
    """
    push — PR Status
    1. gh pr status          (PRs involving you)
    2. gh pr list --limit 10 (open PRs in repo)
    3. open GitHub PRs page
    """
    from ui.review_dialog import show_review_banner
    repo = _find_git_repo()
    if not repo:
        show_review_banner(root, "No git repo found", False)
        return

    result = _run_chain([
        (["pr", "status"],                "gh"),
        (["pr", "list", "--limit", "10"], "gh"),
    ], repo)

    url = _github_url(repo)
    if url:
        _open_url(f"{url}/pulls")

    if result.success:
        show_review_banner(root, "PR status ✓  — see terminal", True)
    else:
        show_review_banner(root, "gh not found — brew install gh", False)


def _chain_undo_last_commit(root) -> None:
    """
    pull — Undo Last Commit (soft reset — keeps changes staged)
    1. git log --oneline -3     (show context before)
    2. git reset --soft HEAD~1  (undo commit, keep changes)
    3. git status               (confirm staged)
    4. git log --oneline -3     (show new state)
    """
    from ui.review_dialog import show_review_banner
    repo = _find_git_repo()
    if not repo:
        show_review_banner(root, "No git repo found", False)
        return

    result = _run_chain([
        (["log", "--oneline", "-3"],      "git"),
        (["reset", "--soft", "HEAD~1"],   "git"),
        (["status"],                      "git"),
        (["log", "--oneline", "-3"],      "git"),
    ], repo)

    if result.success:
        show_review_banner(root, "Last commit undone — changes staged ✓", True)
    else:
        show_review_banner(root, f"Undo failed — {result.summary}", False)


# ── PACK DEFINITION ────────────────────────────────────────────────────────────

@dataclass
class GesturePack:
    id: str
    name: str
    description: str
    icon: str
    gestures: dict[str, dict]
    purchased: bool = True


def _make_github_pack() -> GesturePack:
    return GesturePack(
        id="github",
        name="GitHub Pack",
        description="Automated git workflow chains — push, sync, branch, clean, PR status, undo",
        icon="🐙",
        gestures={
            "swipe_right": {
                "label": "Quick Push",
                "description": "add -A → commit wip:timestamp → push → open repo",
                "action": _chain_quick_push,
            },
            "swipe_left": {
                "label": "Sync Main",
                "description": "stash → checkout main → fetch --prune → pull → pop → open PRs",
                "action": _chain_sync_main,
            },
            "swipe_up": {
                "label": "New Feature Branch",
                "description": "stash → main → fetch → pull → checkout -b feature/ts → pop",
                "action": _chain_new_feature_branch,
            },
            "swipe_down": {
                "label": "Stash & Clean",
                "description": "stash → clean -fd → fetch --prune → delete merged branches",
                "action": _chain_stash_and_clean,
            },
            "push": {
                "label": "PR Status",
                "description": "gh pr status → gh pr list → open GitHub PRs in browser",
                "action": _chain_pr_status,
            },
            "pull": {
                "label": "Undo Last Commit",
                "description": "reset --soft HEAD~1 — keeps your changes staged",
                "action": _chain_undo_last_commit,
            },
            "hold_center": {
                "label": "Exit Pack Mode",
                "description": "Switch back to Default mode",
                "action": None,  # handled by ModeManager
            },
        },
        purchased=True,
    )


# ── REGISTRY ──────────────────────────────────────────────────────────────────

def build_all_packs() -> dict[str, GesturePack]:
    github = _make_github_pack()
    return {
        github.id: github,
        # future: "figma": _make_figma_pack(),
    }


# ── MODE MANAGER ──────────────────────────────────────────────────────────────

class ModeManager:
    """
    Tracks whether the app is in Default mode or a Pack mode.

    Default mode → detect active app → fire mapped macro
    Pack mode    → fire pack chain   → ignore active app
    """

    def __init__(self):
        self._packs = build_all_packs()
        self._active_id: str | None = None

    def set_default(self):
        self._active_id = None

    def set_pack(self, pack_id: str) -> bool:
        if pack_id in self._packs:
            self._active_id = pack_id
            return True
        return False

    def toggle_pack(self, pack_id: str) -> bool:
        """Toggle: if already active → Default. Otherwise → activate."""
        if self._active_id == pack_id:
            self.set_default()
            return False
        self.set_pack(pack_id)
        return True

    def is_default(self) -> bool:
        return self._active_id is None

    def active_pack(self) -> GesturePack | None:
        return self._packs.get(self._active_id) if self._active_id else None

    def active_pack_id(self) -> str | None:
        return self._active_id

    def all_packs(self) -> list[GesturePack]:
        return list(self._packs.values())

    def handle(self, gesture_name: str, root) -> bool:
        """
        If a pack is active and defines this gesture, fires its action
        in a background thread so the UI never freezes during git ops.

        Returns True if handled — caller should skip default macro logic.
        Returns False if Default mode or gesture not in pack.

        hold_center always exits pack mode.
        """
        if gesture_name == "hold_center" and not self.is_default():
            self.set_default()
            return True

        pack = self.active_pack()
        if pack is None:
            return False

        gesture_def = pack.gestures.get(gesture_name)
        if gesture_def is None:
            return False

        action = gesture_def.get("action")
        if action is not None:
            try:
                threading.Thread(
                    target=action,
                    args=(root,),
                    daemon=True,
                ).start()
            except Exception as exc:
                print(f"[packs] action error: {exc}")
        return True