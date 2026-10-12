// Pure helpers for the authenticated AstrBot WebUI's opt-in quick uploader.
// The backend still enforces 20 MiB/file, 100 MiB/request and 100 files/request.
export const FAST_UPLOAD_MAX_BATCH_BYTES = 48 * 1024 * 1024;
export const FAST_UPLOAD_MAX_BATCH_FILES = 30;
export const FAST_UPLOAD_MAX_FILE_BYTES = 20 * 1024 * 1024;

export function planFastUploadBatches(
  files,
  { maxBatchBytes = FAST_UPLOAD_MAX_BATCH_BYTES,
    maxBatchFiles = FAST_UPLOAD_MAX_BATCH_FILES,
    maxFileBytes = FAST_UPLOAD_MAX_FILE_BYTES } = {},
) {
  if (!Array.isArray(files) || !files.length) throw new Error("请先选择图片");
  if (![maxBatchBytes, maxBatchFiles, maxFileBytes].every(Number.isSafeInteger) ||
      maxBatchBytes < 1 || maxBatchFiles < 1 || maxFileBytes < 1 ||
      maxFileBytes > maxBatchBytes) {
    throw new Error("快速上传批次配置无效");
  }

  const batches = [];
  let batch = [];
  let bytes = 0;
  for (const file of files) {
    const size = file?.size;
    if (!Number.isSafeInteger(size) || size < 1) {
      throw new Error(`图片“${file?.name || "未知文件"}”的大小无效`);
    }
    if (size > maxFileBytes) {
      throw new Error(`图片“${file.name}”超过单张 20 MiB 限制，请先压缩`);
    }
    if (size > maxBatchBytes) {
      throw new Error(`图片“${file.name}”超过当前批次大小上限`);
    }
    if (batch.length && (bytes + size > maxBatchBytes || batch.length >= maxBatchFiles)) {
      batches.push(batch);
      batch = [];
      bytes = 0;
    }
    batch.push(file);
    bytes += size;
  }
  if (batch.length) batches.push(batch);
  return batches;
}

export async function uploadFastBatches(
  files,
  { category, encodeFile, sendBatch, onProgress = () => {}, batchOptions } = {},
) {
  // Plan the *whole* selection before sending batch 1. An oversized file
  // later in the selection must not cause accidental partial submission.
  const batches = planFastUploadBatches(files, batchOptions);
  const uploadedFiles = [];
  for (let index = 0; index < batches.length; index++) {
    const batch = batches[index];
    const progress = {
      batchNumber: index + 1,
      totalBatches: batches.length,
      uploaded: uploadedFiles.length,
      total: files.length,
    };
    onProgress({ ...progress, stage: "sending" });
    let requestStarted = false;
    try {
      // Encode only the current <=48 MiB batch; do not retain base64 strings
      // for all selected images, even if the user selects hundreds.
      const images = [];
      for (const file of batch) images.push(await encodeFile(file));
      requestStarted = true;
      const result = await sendBatch({ category, images });
      if (result?.ok !== true ||
          Number(result.count) !== batch.length ||
          !Array.isArray(result.files) || result.files.length !== batch.length) {
        throw new Error(result?.error || "服务器返回的上传数量不一致，请核对图库");
      }
      uploadedFiles.push(...batch);
      onProgress({ ...progress, uploaded: uploadedFiles.length, stage: "completed" });
    } catch (error) {
      return {
        ok: false,
        uploadedFiles,
        totalBatches: batches.length,
        failedBatch: index + 1,
        // Once a request is dispatched, a network failure or malformed reply
        // is NOT proof the remote transaction was rolled back.
        uncertain: requestStarted,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }
  return { ok: true, uploadedFiles, totalBatches: batches.length };
}
