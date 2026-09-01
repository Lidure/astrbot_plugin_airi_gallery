from pathlib import Path

path = Path("main.py")
source = path.read_text(encoding="utf-8")

store_import = '''try:\n    from .gallery_store import GalleryStore\nexcept ImportError:\n    from gallery_store import GalleryStore\n'''
remote_import = '''try:\n    from .gallery_remote import GalleryRemote\nexcept ImportError:\n    from gallery_remote import GalleryRemote\n'''
if remote_import not in source:
    source = source.replace(store_import, store_import + "\n" + remote_import, 1)

source = source.replace('        self._sha_cache: dict[str, str] = {}\n', '', 1)
remote_init_anchor = '''        self._git_sync_enabled = False\n        self._git_push_cancelled = False\n'''
remote_init = '''        self._git_sync_enabled = False\n        self.remote = GalleryRemote(\n            self.config,\n            logger=logger,\n            mutation_lock=self._git_mutation_lock,\n            set_sync_enabled=lambda enabled: setattr(\n                self, "_git_sync_enabled", bool(enabled)\n            ),\n            request_state=_GIT_REQUEST_STATE,\n        )\n        self._git_push_cancelled = False\n'''
if remote_init not in source:
    if remote_init_anchor not in source:
        raise SystemExit("remote init anchor missing")
    source = source.replace(remote_init_anchor, remote_init, 1)

helper_anchor = '    def _git_platform(self) -> str:\n'
helper_block = '''    def _remote_service(self) -> GalleryRemote:\n        remote = self.__dict__.get("remote")\n        if remote is not None:\n            return remote\n        mutation_lock = self.__dict__.get("_git_mutation_lock")\n        if mutation_lock is None:\n            mutation_lock = threading.RLock()\n            self.__dict__["_git_mutation_lock"] = mutation_lock\n        remote = GalleryRemote(\n            getattr(self, "config", {}) or {},\n            logger=logger,\n            mutation_lock=mutation_lock,\n            set_sync_enabled=lambda enabled: setattr(\n                self, "_git_sync_enabled", bool(enabled)\n            ),\n            request_state=_GIT_REQUEST_STATE,\n        )\n        remote.sha_cache = self.__dict__.pop("_compat_sha_cache", {})\n        remote.ref_update_outcome = self.__dict__.pop(\n            "_compat_git_ref_update_outcome", None\n        )\n        self.__dict__["remote"] = remote\n        return remote\n\n    @property\n    def _sha_cache(self) -> dict[str, str]:\n        remote = self.__dict__.get("remote")\n        if remote is not None:\n            return remote.sha_cache\n        return self.__dict__.setdefault("_compat_sha_cache", {})\n\n    @_sha_cache.setter\n    def _sha_cache(self, value: dict[str, str]) -> None:\n        remote = self.__dict__.get("remote")\n        if remote is not None:\n            remote.sha_cache = value\n        else:\n            self.__dict__["_compat_sha_cache"] = value\n\n    @property\n    def _git_ref_update_outcome(self) -> str | None:\n        remote = self.__dict__.get("remote")\n        if remote is not None:\n            return remote.ref_update_outcome\n        return self.__dict__.get("_compat_git_ref_update_outcome")\n\n    @_git_ref_update_outcome.setter\n    def _git_ref_update_outcome(self, value: str | None) -> None:\n        remote = self.__dict__.get("remote")\n        if remote is not None:\n            remote.ref_update_outcome = value\n        else:\n            self.__dict__["_compat_git_ref_update_outcome"] = value\n\n'''
if helper_block not in source:
    if helper_anchor not in source:
        raise SystemExit("remote helper anchor missing")
    source = source.replace(helper_anchor, helper_block + helper_anchor, 1)


def replace_method(name: str, replacement: str) -> None:
    global source
    marker = f"    def {name}("
    start = source.find(marker)
    if start < 0:
        raise SystemExit(f"method missing: {name}")
    candidates = []
    for next_marker in ("\n    def ", "\n    async def ", "\n    @staticmethod", "\n    @property", "\n    @filter."):
        pos = source.find(next_marker, start + len(marker))
        if pos >= 0:
            candidates.append(pos + 1)
    if not candidates:
        raise SystemExit(f"next method marker missing: {name}")
    end = min(candidates)
    source = source[:start] + replacement.rstrip() + "\n\n" + source[end:]


replacements = {
    "_git_platform": '''    def _git_platform(self) -> str:\n        return self._remote_service().platform()''',
    "_git_owner": '''    def _git_owner(self) -> str:\n        return self._remote_service().owner()''',
    "_git_repo": '''    def _git_repo(self) -> str:\n        return self._remote_service().repo()''',
    "_git_branch": '''    def _git_branch(self) -> str:\n        return self._remote_service().branch()''',
    "_git_token": '''    def _git_token(self) -> str:\n        return self._remote_service().token()''',
    "_git_api_base": '''    def _git_api_base(self) -> str:\n        return self._remote_service().api_base()''',
    "_git_headers": '''    def _git_headers(self) -> dict:\n        return self._remote_service().headers()''',
    "_git_auth_params": '''    def _git_auth_params(self) -> dict:\n        return self._remote_service().auth_params()''',
    "_git_request": '''    def _git_request(\n        self,\n        method: str,\n        url: str,\n        json_body: dict | None = None,\n        params: dict | None = None,\n        timeout: int = 30,\n        disable_on_auth_failure: bool = True,\n    ) -> tuple[int, dict | None]:\n        return self._remote_service().request(\n            method,\n            url,\n            json_body=json_body,\n            params=params,\n            timeout=timeout,\n            disable_on_auth_failure=disable_on_auth_failure,\n        )''',
    "_git_list_tree": '''    def _git_list_tree(self) -> list[dict] | None:\n        return self._remote_service().list_tree()''',
    "_git_list_tree_at": '''    def _git_list_tree_at(self, tree_sha: str) -> list[dict] | None:\n        return self._remote_service().list_tree_at(tree_sha)''',
    "_git_get_file": '''    def _git_get_file(self, path: str) -> bytes | None:\n        return self._remote_service().get_file(path)''',
    "_git_fetch_file_sha": '''    def _git_fetch_file_sha(self, path: str) -> str | None:\n        return self._remote_service().fetch_file_sha(path)''',
    "_git_put_file": '''    def _git_put_file(\n        self, path: str, content: bytes, message: str, *, create_only: bool = False\n    ) -> tuple[bool, str | None]:\n        return self._remote_service().put_file(\n            path, content, message, create_only=create_only\n        )''',
    "_git_get_head_commit_and_tree": '''    def _git_get_head_commit_and_tree(self) -> tuple[str, str] | None:\n        return self._remote_service().get_head_commit_and_tree()''',
    "_git_create_github_blob": '''    def _git_create_github_blob(self, content: bytes) -> str | None:\n        return self._remote_service().create_github_blob(content)''',
    "_git_verify_github_tree_exists": '''    def _git_verify_github_tree_exists(self, tree_sha: str) -> bool:\n        return self._remote_service().verify_github_tree_exists(tree_sha)''',
    "_git_create_github_tree": '''    def _git_create_github_tree(\n        self,\n        base_tree_sha: str | None,\n        entries: list[dict],\n        *,\n        context: str = "",\n    ) -> str | None:\n        return self._remote_service().create_github_tree(\n            base_tree_sha, entries, context=context\n        )''',
    "_git_create_github_tree_incrementally": '''    def _git_create_github_tree_incrementally(\n        self, entries: list[dict]\n    ) -> str | None:\n        return self._remote_service().create_github_tree_incrementally(entries)''',
    "_git_apply_category_tree_delta": '''    def _git_apply_category_tree_delta(\n        self,\n        category: str,\n        base_tree_sha: str,\n        deletes: tuple[dict[str, object], ...],\n        upserts: tuple[dict[str, object], ...],\n    ) -> str | None:\n        return self._remote_service().apply_category_tree_delta(\n            category, base_tree_sha, deletes, upserts\n        )''',
    "_git_create_github_commit": '''    def _git_create_github_commit(\n        self, message: str, tree_sha: str, parent_sha: str\n    ) -> str | None:\n        return self._remote_service().create_github_commit(\n            message, tree_sha, parent_sha\n        )''',
    "_git_update_github_ref": '''    def _git_update_github_ref(self, commit_sha: str) -> bool:\n        return self._remote_service().update_github_ref(commit_sha)''',
    "_git_github_create_only_paths_exist": '''    def _git_github_create_only_paths_exist(\n        self, tree_sha: str, paths: set[str]\n    ) -> bool | None:\n        return self._remote_service().github_create_only_paths_exist(tree_sha, paths)''',
}

for method_name, replacement in replacements.items():
    replace_method(method_name, replacement)

path.write_text(source, encoding="utf-8")
