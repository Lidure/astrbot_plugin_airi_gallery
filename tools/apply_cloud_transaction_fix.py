from pathlib import Path

path = Path('pages/zz_cloud/app.js')
text = path.read_text(encoding='utf-8')

old_put = """  const body = { message, content: contentB64 };\n\n  if (config.platform === 'gitee') {\n"""
new_put = """  const body = { message, content: contentB64, branch };\n\n  if (config.platform === 'gitee') {\n"""
if old_put not in text:
    raise SystemExit('putFile body anchor not found')
text = text.replace(old_put, new_put, 1)

old_github_branch = """  } else {\n    body.branch = branch;\n    if (existingSha) body.sha = existingSha;\n"""
new_github_branch = """  } else {\n    if (existingSha) body.sha = existingSha;\n"""
if old_github_branch not in text:
    raise SystemExit('github branch anchor not found')
text = text.replace(old_github_branch, new_github_branch, 1)

old_delete = """  const body = { message, sha };\n  if (config.platform !== 'gitee') body.branch = branch;\n  await ghRequest('DELETE', `/repos/${config.owner}/${config.repo}/contents/${path}`, { body });\n"""
new_delete = """  const body = { message, sha, branch };\n  await ghRequest('DELETE', `/repos/${config.owner}/${config.repo}/contents/${path}`, { body });\n"""
if old_delete not in text:
    raise SystemExit('delete body anchor not found')
text = text.replace(old_delete, new_delete, 1)

marker = """// ──────────────────────────────────────────────\n// Upload\n// ──────────────────────────────────────────────\n"""
helper = """async function rollbackUploadedResults(uploadedResults, galleryIndex) {\n  const rollbackFailures = [];\n  for (const result of [...uploadedResults].reverse()) {\n    try {\n      await deleteFile(result.gitPath, `Rollback ${result.fileName}: gallery index update failed`);\n      delete galleryIndex[result.gitPath];\n    } catch (error) {\n      rollbackFailures.push({ path: result.gitPath, error });\n      console.error(`[Gallery] 补偿删除失败: ${result.gitPath}`, error);\n    }\n  }\n  return rollbackFailures;\n}\n\n"""
if marker not in text:
    raise SystemExit('upload marker not found')
text = text.replace(marker, marker + helper, 1)

old_rollback = """      } catch (indexError) {\n        // Perceptual state is part of the upload transaction. Roll back new images\n        // rather than leave GitHub and the Bot with different similarity knowledge.\n        for (const result of [...uploadedResults].reverse()) {\n          try { await deleteFile(result.gitPath, `Rollback ${result.fileName}: gallery index update failed`); } catch {}\n          delete galleryIndex[result.gitPath];\n        }\n        throw new Error(`感知查重索引更新失败，新上传图片已回滚：${indexError.message}`);\n      }\n"""
new_rollback = """      } catch (indexError) {\n        // Perceptual state is part of the upload transaction. Compensate every new image,\n        // but never claim a full rollback when any remote delete could not be confirmed.\n        const rollbackFailures = await rollbackUploadedResults(uploadedResults, galleryIndex);\n        if (rollbackFailures.length) {\n          const failedPaths = rollbackFailures.map(item => item.path).join('、');\n          throw new Error(\n            `感知查重索引更新失败；部分远端图片补偿删除失败（${failedPaths}），请立即同步核对：${indexError.message}`\n          );\n        }\n        throw new Error(`感知查重索引更新失败；远端新增图片补偿删除已完成：${indexError.message}`);\n      }\n"""
if old_rollback not in text:
    raise SystemExit('rollback anchor not found')
text = text.replace(old_rollback, new_rollback, 1)

path.write_text(text, encoding='utf-8')
