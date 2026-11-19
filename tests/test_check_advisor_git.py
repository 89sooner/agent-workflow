import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow.check_advisor_git import DIFF_BYTES, PATH_LIMIT, change_context
from agent_workflow.core import WorkflowError


class ChangeContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "fixture@example.test")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", self.repo, *args], stderr=subprocess.DEVNULL)

    def put(self, name, content):
        path = self.repo / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(content)
        return path

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD").strip().decode()

    def test_two_layers_and_untracked_names_from_subdirectory(self):
        self.put("src/app.py", "original\n")
        self.put(".gitignore", "ignored/\n")
        commit = self.commit()
        self.put("src/app.py", "saved in index\n")
        self.git("add", "src/app.py")
        self.put("src/app.py", "original\n")
        self.put("e2e/새검사.ts", "PRIVATE_UNTRACKED_CONTENT")
        self.put("ignored/build", "generated")
        with patch.dict(os.environ, {"GIT_DIR": "/does/not/exist"}):
            got = change_context(self.repo / "src")
        self.assertEqual(got["base"], commit)
        self.assertEqual(got["paths"], ["src/app.py", "e2e/새검사.ts"])
        self.assertEqual(got["untracked_paths"], ["e2e/새검사.ts"])
        self.assertIn("[base -> index]", got["diff"])
        self.assertIn("[index -> worktree]", got["diff"])
        self.assertIn("+saved in index", got["diff"])
        self.assertIn("-saved in index", got["diff"])
        self.assertNotIn("PRIVATE_UNTRACKED_CONTENT", str(got))
        self.assertFalse(got["truncated"])

    def test_base_can_include_committed_changes_and_works_in_linked_tree(self):
        self.put("app", "one\n")
        first = self.commit()
        self.put("app", "two\n")
        self.commit()
        self.assertEqual(change_context(self.repo)["paths"], [])
        tree = self.repo.parent / "linked"
        self.git("worktree", "add", "--detach", str(tree), "HEAD")
        got = change_context(tree, "HEAD~1")
        self.assertEqual(got["base"], first)
        self.assertEqual(got["paths"], ["app"])
        self.assertIn("+two", got["diff"])
        for ref in ("not-a-ref", "--help", "HEAD\0"):
            with self.subTest(ref=ref), self.assertRaises(WorkflowError):
                change_context(tree, ref)

    def test_unborn_index_and_non_git(self):
        self.put("app", "staged first\n")
        self.git("add", "app")
        output = change_context(self.repo)
        self.assertIsNone(output["base"])
        self.assertIn("+staged first", output["diff"])
        with self.assertRaises(WorkflowError):
            change_context(self.repo.parent)

    def test_excluded_paths_secret_renames_and_inline_credentials(self):
        self.put("restricted/private.txt", "sensitive internal notes, formerly protected\n")
        self.put("old.py", "existing\n")
        self.commit()
        self.git("mv", "restricted/private.txt", "public.txt")
        self.put("public.txt", "sensitive internal notes, formerly protected\nrevised\n")
        self.put("restricted/notes", "hidden")
        self.put(".env.stage", "secret data")
        self.git("add", ".env.stage")
        self.put("sub/.ssh/config", "should not appear")
        self.put("old.py", 'normal\nPASSWORD="leaked123"\nSECRET_KEY = "leaked456"\nAuthorization: Bearer AAAA\n')
        got = change_context(self.repo, excluded_paths=["restricted"])
        self.assertEqual(got["paths"], ["old.py"])
        self.assertGreaterEqual(got["omitted_sensitive"], 5)
        self.assertIn("+normal", got["diff"])
        self.assertIn("+[REDACTED]", got["diff"])
        for secret in ("public.txt", ".env.stage", ".ssh", "restricted", "leaked123", "leaked456", "Bearer AAAA"):
            self.assertNotIn(secret, str(got))
        self.assertEqual(change_context(self.repo, excluded_paths=["."])["paths"], [])

    def test_modified_move_from_protected_file_without_git_rename_match(self):
        self.put("vault/data", "internal-private-fragment\n" + "old source\n" * 100)
        self.commit()
        self.git("rm", "vault/data")
        self.put("moved", "internal-private-fragment\n" + "new destination\n" * 100)
        self.git("add", "moved")
        got = change_context(self.repo, excluded_paths=["vault"])
        self.assertNotIn("internal-private-fragment", str(got))
        self.assertEqual(got["omitted_diff_files"], 1)

    def test_no_external_filter_or_diff_driver_and_git_index_readonly(self):
        self.put(".gitattributes", "app filter=fixture diff=fixture\n")
        self.put("app", "before\n")
        self.commit()
        marker = self.repo.parent / "invoked"
        self.git("config", "filter.fixture.clean", f"touch '{marker}' && cat")
        self.git("config", "filter.fixture.required", "true")
        self.git("config", "diff.fixture.textconv", f"touch '{marker}'")
        self.git("config", "diff.external", f"touch '{marker}'")
        self.git("config", "core.fsmonitor", str(marker))
        self.put("app", "after before\n")
        index = self.repo / ".git" / "index"
        before = index.stat().st_mtime_ns, index.read_bytes()
        output = change_context(self.repo)
        self.assertIn("+after before", output["diff"])
        self.assertFalse(marker.exists())
        self.assertEqual(before, (index.stat().st_mtime_ns, index.read_bytes()))

    def test_binary_symlink_and_private_key_omit_patch(self):
        self.put("binary", "before\n")
        self.put("notes", "before\n")
        self.commit()
        (self.repo / "binary").write_bytes(b"\0BINARY_PAYLOAD")
        self.put("notes", "-----BEGIN PRIVATE KEY-----\nRAW_KEY_CONTENT\n-----END PRIVATE KEY-----\n")
        external = self.repo.parent / "private-location"
        external.write_text("OUTSIDE_FILE_BODY")
        (self.repo / "link").symlink_to(external)
        self.git("add", "link")
        got = change_context(self.repo)
        self.assertEqual(set(got["paths"]), {"binary", "link", "notes"})
        self.assertEqual(got["omitted_diff_files"], 3)
        self.assertEqual(got["diff"], "")
        self.assertTrue(got["truncated"])

    def test_payload_limits_remain_visible(self):
        for i in range(5):
            self.put(f"tracked{i}", "old\n")
        self.commit()
        for i in range(5):
            self.put(f"tracked{i}", "new " * 3000 + "\nadditional\n")
        for i in range(PATH_LIMIT + 5):
            self.put(f"extra{i}", "do not read me")
        got = change_context(self.repo)
        self.assertEqual(len(got["paths"]), PATH_LIMIT)
        self.assertLessEqual(len(got["diff"].encode()), DIFF_BYTES)
        self.assertTrue(got["truncated"])
        self.assertGreater(got["omitted_diff_files"], 0)
        self.assertNotIn("do not read me", str(got))
