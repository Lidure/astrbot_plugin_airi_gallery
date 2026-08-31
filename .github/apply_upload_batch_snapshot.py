from pathlib import Path

path = Path("main.py")
text = path.read_text(encoding="utf-8")

marker = "    def _store_unique_image(\n"
if text.count(marker) != 1:
    raise SystemExit(f"unexpected store helper anchor count: {text.count(marker)}")

batch_helper = '''    def _store_unique_image_batch(
        self,
        category_dir: Path,
        category: str,
        candidates: list[tuple[str, bytes]],
        *,
        remote_records: tuple[IndexedImage, ...] = (),
        remote_checked: bool = True,
        min_index: int = 1,
        stop_on_similar: bool = False,
    ) -> list[tuple[Path | None, IndexedUploadDecision]]:
        """Store one upload batch from a single local dedup/number snapshot."""
        if not candidates:
            return []

        with self._gallery_write_lock:
            local_records = list(self._indexed_local_images())
            next_index = max(self._next_index(), max(1, int(min_index)))
            outcomes: list[tuple[Path | None, IndexedUploadDecision]] = []
            try:
                for ext, image_bytes in candidates:
                    candidate = compute_image_fingerprint(image_bytes)
                    decision = evaluate_indexed_upload(
                        candidate,
                        local_records=local_records,
                        remote_records=remote_records,
                        remote_checked=remote_checked,
                        perceptual_max_distance=PERCEPTUAL_MAX_DISTANCE,
                        force_similar=False,
                    )
                    if not decision.allowed:
                        outcomes.append((None, decision))
                        if stop_on_similar and decision.reason == "similar":
                            break
                        continue

                    target_path = category_dir / f"{next_index}{ext}"
                    while target_path.exists():
                        next_index += 1
                        target_path = category_dir / f"{next_index}{ext}"

                    target_path.write_bytes(image_bytes)
                    self._invalidate_category_hash_cache(category)
                    self._remember_file_hash(
                        target_path,
                        candidate.content_hash,
                        category=category,
                        save=False,
                        perceptual_hash=candidate.perceptual_hash,
                    )
                    git_path = self._hash_index_key(target_path)
                    if not git_path:
                        raise RuntimeError(f"无法建立上传图片索引路径：{target_path}")
                    local_records.append(
                        IndexedImage(
                            path=git_path,
                            content_hash=candidate.content_hash,
                            blob_sha=candidate.blob_sha,
                            perceptual_hash=candidate.perceptual_hash,
                        )
                    )
                    outcomes.append((target_path, decision))
                    next_index += 1
            finally:
                self._save_hash_index()
            return outcomes

'''
text = text.replace(marker, batch_helper + marker, 1)

old_web_loop = '''            uploaded: list[str] = []
            staged_paths: list[Path] = []
            rejected: list[dict] = []
            for name, validated in validated_images:
                image_bytes = validated.content
                ext = validated.extension
                fingerprint = compute_image_fingerprint(image_bytes)
                target, decision = self._store_unique_image(
                    category_dir,
                    category,
                    ext,
                    image_bytes,
                    remote_records=remote_records,
                    remote_checked=True,
                    min_index=remote_max_index + 1,
                    force_similar=False,
                    fingerprint=fingerprint,
                )
                if target is None:
                    detail = self._upload_decision_json(decision)
                    detail["name"] = name
                    if decision.reason == "similar":
                        detail["force_token"] = self._cache_api_similar_upload(
                            category=category,
                            suffix=ext,
                            image_bytes=image_bytes,
                            fingerprint=fingerprint,
                        )
                    rejected.append(detail)
                    continue
                staged_paths.append(target)
                remote_max_index = max(remote_max_index, int(target.stem))
'''
new_web_loop = '''            uploaded: list[str] = []
            staged_paths: list[Path] = []
            rejected: list[dict] = []
            batch_candidates = [
                (validated.extension, validated.content)
                for _, validated in validated_images
            ]
            outcomes = self._store_unique_image_batch(
                category_dir,
                category,
                batch_candidates,
                remote_records=remote_records,
                remote_checked=True,
                min_index=remote_max_index + 1,
            )
            for (name, validated), (target, decision) in zip(
                validated_images, outcomes
            ):
                image_bytes = validated.content
                ext = validated.extension
                if target is None:
                    detail = self._upload_decision_json(decision)
                    detail["name"] = name
                    if decision.reason == "similar":
                        detail["force_token"] = self._cache_api_similar_upload(
                            category=category,
                            suffix=ext,
                            image_bytes=image_bytes,
                            fingerprint=decision.fingerprint,
                        )
                    rejected.append(detail)
                    continue
                staged_paths.append(target)
'''
if text.count(old_web_loop) != 2:
    raise SystemExit(f"unexpected web upload loop count: {text.count(old_web_loop)}")
text = text.replace(old_web_loop, new_web_loop)

old_chat_loop = '''        uploaded: list[str] = []
        staged_paths: list[Path] = []
        exact_count = 0
        similar_count = 0
        invalid_count = 0
        for source_path, image_bytes in all_images:
            try:
                validated = validate_image_payload(image_bytes)
            except (UploadPayloadTooLarge, ValueError):
                invalid_count += 1
                continue
            image_bytes = validated.content
            suffix = validated.extension
            fingerprint = compute_image_fingerprint(image_bytes)
            target_path, decision = self._store_unique_image(
                category_dir,
                category_name,
                suffix,
                image_bytes,
                remote_records=remote_records,
                remote_checked=True,
                min_index=remote_max_index + 1,
                fingerprint=fingerprint,
            )
            if target_path is None:
                if decision.reason == "exact_duplicate":
                    exact_count += 1
                    await self._send_upload_decision_hint(event, decision)
                    continue
                if decision.reason == "similar":
                    similar_count += 1
                    self._cache_similar_upload(
                        event,
                        category=category_name,
                        suffix=suffix,
                        image_bytes=image_bytes,
                        fingerprint=fingerprint,
                    )
                    await self._send_upload_decision_hint(event, decision)
                    # One pending candidate per user/session keeps /强制上传 unambiguous.
                    break
                continue

            staged_paths.append(target_path)
            remote_max_index = max(remote_max_index, int(target_path.stem))
'''
new_chat_loop = '''        uploaded: list[str] = []
        staged_paths: list[Path] = []
        exact_count = 0
        similar_count = 0
        invalid_count = 0
        batch_candidates: list[tuple[str, bytes]] = []
        for _, image_bytes in all_images:
            try:
                validated = validate_image_payload(image_bytes)
            except (UploadPayloadTooLarge, ValueError):
                invalid_count += 1
                continue
            batch_candidates.append((validated.extension, validated.content))

        outcomes = self._store_unique_image_batch(
            category_dir,
            category_name,
            batch_candidates,
            remote_records=remote_records,
            remote_checked=True,
            min_index=remote_max_index + 1,
            stop_on_similar=True,
        )
        for (suffix, image_bytes), (target_path, decision) in zip(
            batch_candidates, outcomes
        ):
            if target_path is None:
                if decision.reason == "exact_duplicate":
                    exact_count += 1
                    await self._send_upload_decision_hint(event, decision)
                    continue
                if decision.reason == "similar":
                    similar_count += 1
                    self._cache_similar_upload(
                        event,
                        category=category_name,
                        suffix=suffix,
                        image_bytes=image_bytes,
                        fingerprint=decision.fingerprint,
                    )
                    await self._send_upload_decision_hint(event, decision)
                    # One pending candidate per user/session keeps /强制上传 unambiguous.
                    break
                continue

            staged_paths.append(target_path)
'''
if text.count(old_chat_loop) != 1:
    raise SystemExit(f"unexpected chat upload loop count: {text.count(old_chat_loop)}")
text = text.replace(old_chat_loop, new_chat_loop, 1)

path.write_text(text, encoding="utf-8")
