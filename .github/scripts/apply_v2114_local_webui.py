from pathlib import Path


def one(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 occurrence, found {count}")
    return source.replace(old, new, 1)


# Fail closed on unknown future index versions.
safety_path = Path("gallery_safety.py")
safety = safety_path.read_text(encoding="utf-8")
safety = one(
    safety,
    "    preserve_remote = version_number >= 2\n    preserve_perceptual = version_number >= 3",
    "    preserve_remote = version_number in (2, HASH_INDEX_VERSION)\n    preserve_perceptual = version_number == HASH_INDEX_VERSION",
    "future index fail-closed",
)
safety_path.write_text(safety, encoding="utf-8")

# Add server-side force tokens so WebUI similarity confirmation reuses the already
# computed candidate fingerprint instead of hashing/decoding the same image again.
main_path = Path("main.py")
main = main_path.read_text(encoding="utf-8")
main = one(main, "import re\nimport shutil", "import re\nimport secrets\nimport shutil", "secrets import")
main = one(
    main,
    "        self._pending_similar_uploads: dict[str, dict] = {}\n        self._pending_similar_upload_lock = threading.RLock()",
    "        self._pending_similar_uploads: dict[str, dict] = {}\n        self._pending_similar_upload_lock = threading.RLock()\n        self._pending_api_similar_uploads: dict[str, dict] = {}\n        self._pending_api_similar_upload_lock = threading.RLock()",
    "api pending state",
)

api_anchor = '''    @staticmethod
    def _upload_decision_json(decision: IndexedUploadDecision) -> dict:
'''
api_helpers = r'''    def _cache_api_similar_upload(
        self,
        *,
        category: str,
        suffix: str,
        image_bytes: bytes,
        fingerprint: ImageFingerprint,
    ) -> str:
        token = secrets.token_urlsafe(24)
        with self._pending_api_similar_upload_lock:
            now = time.time()
            expired = [
                key
                for key, value in self._pending_api_similar_uploads.items()
                if now - float(value.get("created_at", 0)) > SIMILAR_UPLOAD_CONFIRM_TTL
            ]
            for key in expired:
                self._pending_api_similar_uploads.pop(key, None)
            self._pending_api_similar_uploads[token] = {
                "created_at": now,
                "category": category,
                "suffix": suffix,
                "image_bytes": image_bytes,
                "fingerprint": fingerprint,
            }
        return token

    def _get_api_similar_upload(self, token: str) -> dict | None:
        if not token:
            return None
        with self._pending_api_similar_upload_lock:
            pending = self._pending_api_similar_uploads.get(token)
            if pending is None:
                return None
            if time.time() - float(pending.get("created_at", 0)) > SIMILAR_UPLOAD_CONFIRM_TTL:
                self._pending_api_similar_uploads.pop(token, None)
                return None
            return dict(pending)

    def _forget_api_similar_upload(self, token: str) -> None:
        with self._pending_api_similar_upload_lock:
            self._pending_api_similar_uploads.pop(token, None)

    async def _force_api_similar_upload(
        self, category: str, force_token: str
    ) -> tuple[dict, int]:
        pending = self._get_api_similar_upload(force_token)
        if pending is None:
            return {"ok": False, "error": "相似图片确认已过期，请重新选择图片上传"}, 410
        if str(pending.get("category", "")) != category:
            return {"ok": False, "error": "相似图片确认与当前分类不匹配"}, 400
        category_dir = resolve_gallery_category_dir(self.gallery_root, category)
        if category_dir is None:
            return {"ok": False, "error": "invalid category"}, 400
        category_dir.mkdir(parents=True, exist_ok=True)

        remote_checked, remote_records, remote_max_index = await asyncio.to_thread(
            self._prepare_remote_upload_guard, category
        )
        if not remote_checked:
            return {"ok": False, "error": "远程查重失败，本次强制上传未执行"}, 503

        target, decision = self._store_unique_image(
            category_dir,
            category,
            str(pending["suffix"]),
            bytes(pending["image_bytes"]),
            remote_records=remote_records,
            remote_checked=True,
            min_index=remote_max_index + 1,
            force_similar=True,
            fingerprint=pending["fingerprint"],
        )
        if target is None:
            self._forget_api_similar_upload(force_token)
            return {
                "ok": True,
                "count": 0,
                "files": [],
                "rejected": [self._upload_decision_json(decision)],
            }, 200

        if self._git_sync_enabled:
            pushed = await asyncio.to_thread(self._git_push_file, str(target))
            manifest_ok = pushed and await asyncio.to_thread(self._publish_gallery_manifest)
            if not manifest_ok:
                if pushed:
                    await asyncio.to_thread(self._git_delete_remote_file, str(target))
                self._rollback_stored_image(target, category)
                return {"ok": False, "error": "远程上传或感知索引更新失败，本地写入已回滚"}, 502
        self._forget_api_similar_upload(force_token)
        return {"ok": True, "count": 1, "files": [target.name], "rejected": []}, 200

'''
main = one(main, api_anchor, api_helpers + api_anchor, "api force helpers")

# Both authenticated and public APIs now use a one-time force token. The initial
# request computes the fingerprint once and caches it only for perceptual matches.
for func_name in ("_api_upload_images", "_api_pub_upload"):
    start = main.index(f"    async def {func_name}(self):")
    if func_name == "_api_upload_images":
        end = main.index("\n    async def _api_category_image", start)
    else:
        end = main.index("\n    def _resolve_view_command_mode", start)
    block = main[start:end]
    block = block.replace(
        '            images = data.get("images", [])\n            force_similar = data.get("force_similar") is True',
        '            images = data.get("images", [])\n            force_token = str(data.get("force_token", "")).strip()',
        1,
    )
    block = block.replace(
        '            if not category:\n                return jsonify({"ok": False, "error": "请选择分类"}), 400\n            if not images:\n                return jsonify({"ok": False, "error": "请选择要上传的图片"}), 400',
        '            if not category:\n                return jsonify({"ok": False, "error": "请选择分类"}), 400\n            category = _sanitize_component(category)\n            if force_token:\n                payload, status = await self._force_api_similar_upload(category, force_token)\n                return jsonify(payload), status\n            if not images:\n                return jsonify({"ok": False, "error": "请选择要上传的图片"}), 400',
        1,
    )
    # category was sanitized above now; remove the old duplicate line after validation.
    block = block.replace('            category = _sanitize_component(category)\n            category_dir = resolve_gallery_category_dir', '            category_dir = resolve_gallery_category_dir', 1)
    block = block.replace('                    force_similar=force_similar,', '                    force_similar=False,', 1)
    block = block.replace(
        '''                    detail = self._upload_decision_json(decision)
                    detail["name"] = name
                    rejected.append(detail)''',
        '''                    detail = self._upload_decision_json(decision)
                    detail["name"] = name
                    if decision.reason == "similar":
                        detail["force_token"] = self._cache_api_similar_upload(
                            category=category,
                            suffix=ext,
                            image_bytes=image_bytes,
                            fingerprint=fingerprint,
                        )
                    rejected.append(detail)''',
        1,
    )
    main = main[:start] + block + main[end:]

main_path.write_text(main, encoding="utf-8")

# Local plugin WebUI: show duplicate/similar source image, exact sequence number,
# and use the force token to retry a similar image without recomputing its hash.
app_path = Path("pages/gallery/app.js")
app = app_path.read_text(encoding="utf-8")

upload_start = app.index('upBtn.addEventListener("click", async () => {')
upload_end = app.index("\nfunction parseAliasEntry", upload_start)
replacement = r'''function parseGalleryMatchPath(path) {
  const parts = String(path || "").split("/");
  if (parts.length !== 3 || parts[0] !== "gallery") return null;
  return { category: parts[1], name: parts[2] };
}

function matchText(match, includeSimilarity = true) {
  if (!match) return "未知图片";
  const number = match.number ? `#${match.number}` : String(match.path || "未知图片");
  if (!includeSimilarity) return number;
  const similarity = Number(match.similarity);
  return Number.isFinite(similarity) ? `${number} ${(similarity * 100).toFixed(1)}%` : number;
}

async function showMatchPreview(match) {
  const location = parseGalleryMatchPath(match?.path);
  if (!location) return;
  try {
    const data = await apiGet("category_image", location);
    const url = makeBlobUrl(data.data, data.content_type);
    if (!url) return;
    modalImage.src = url;
    modalImage.alt = location.name;
    mask.classList.add("show");
  } catch (error) {
    console.warn("[gallery] failed to load dedup preview", error);
  }
}

async function encodeUploadFile(file) {
  return { name: file.name, data: await fileToBase64(file) };
}

upBtn.addEventListener("click", async () => {
  const category = upInput.value.trim() || upSel.value;
  if (!category) { showMsg("请选择或输入分类", false); return; }
  if (!pendingFiles.length) { showMsg("请选择图片", false); return; }
  upBtn.disabled = true;
  upBtn.textContent = "上传中...";
  try {
    const byName = new Map(pendingFiles.map(file => [file.name, file]));
    const images = [];
    for (const file of pendingFiles) images.push(await encodeUploadFile(file));
    const result = await apiPost("upload", { category, images });
    if (result?.ok === false) throw new Error(result.error || "上传失败");

    let uploaded = Number(result.count) || 0;
    const keepNames = new Set();
    const rejected = Array.isArray(result.rejected) ? result.rejected : [];

    for (const item of rejected) {
      if (item.reason === "exact_duplicate") {
        await showMatchPreview(item.exact_match);
        window.alert(`发现完全重复图片：${matchText(item.exact_match, false)}。\n这张图片已被拦截，不能强制上传。`);
        continue;
      }
      if (item.reason !== "similar" || !item.force_token) {
        if (item.name) keepNames.add(item.name);
        continue;
      }

      const matches = Array.isArray(item.similar_matches) ? item.similar_matches : [];
      if (matches.length) await showMatchPreview(matches[0]);
      const labels = matches.map(match => matchText(match, true)).join("、") || "已有图片";
      const force = window.confirm(`发现相似图片：${labels}\n\n确认不是同一张图并仍然上传吗？`);
      if (!force) {
        if (item.name) keepNames.add(item.name);
        continue;
      }

      const forced = await apiPost("upload", { category, force_token: item.force_token });
      if (forced?.ok === false) throw new Error(forced.error || "强制上传失败");
      uploaded += Number(forced.count) || 0;
      const forcedRejected = Array.isArray(forced.rejected) ? forced.rejected : [];
      if (forcedRejected.length) {
        const exact = forcedRejected.find(entry => entry.reason === "exact_duplicate");
        if (exact) {
          await showMatchPreview(exact.exact_match);
          window.alert(`强制上传前发现完全重复图片：${matchText(exact.exact_match, false)}。\n完全重复不能绕过。`);
        } else if (item.name) {
          keepNames.add(item.name);
        }
      }
    }

    pendingFiles = pendingFiles.filter(file => keepNames.has(file.name));
    renderPreview();
    clearImageCache();
    await loadCats();
    if (categories.includes(category)) {
      currentCat = category;
      currentPage = 1;
      renderTabs();
      await loadImgs();
    }
    showMsg(`成功上传 ${uploaded} 张到“${category}”${pendingFiles.length ? `，${pendingFiles.length} 张相似图片已保留` : ""}`);
  } catch (error) {
    showMsg(error.message || "上传失败", false);
  } finally {
    upBtn.disabled = false;
    upBtn.textContent = `上传 ${pendingFiles.length} 张`;
  }
});
'''
app = app[:upload_start] + replacement + app[upload_end:]
app_path.write_text(app, encoding="utf-8")

# Extend migration tests to assert unknown future versions fail closed.
test_path = Path("tests/test_gallery_safety.py")
test = test_path.read_text(encoding="utf-8")
anchor = '''def test_v3_preserves_valid_perceptual_hash_and_remote_baseline():
'''
insert = '''def test_unknown_future_index_version_does_not_trust_advanced_fields():
    files = normalize_hash_index({
        "version": 4,
        "files": {
            "gallery/airi/1.png": {
                "hash": "sha256-old",
                "git_blob_sha": "matching-blob",
                "remote_sha": "matching-blob",
                "perceptual_hash": "0123456789abcdef",
            }
        },
    })
    assert files == {"gallery/airi/1.png": {"hash": "sha256-old"}}


'''
test = one(test, anchor, insert + anchor, "future version test")
test_path.write_text(test, encoding="utf-8")
