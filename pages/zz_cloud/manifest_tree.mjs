const IMAGE_SUFFIXES = new Set([
  '.bmp', '.gif', '.jpeg', '.jpg', '.jfif', '.png', '.tif', '.tiff', '.webp',
]);

function isSafeGalleryImagePath(path) {
  if (typeof path !== 'string' || !path || path.includes('\\')) return false;
  const parts = path.split('/');
  if (parts.length !== 3 || parts[0] !== 'gallery') return false;
  if (parts.some(part => !part || part === '.' || part === '..')) return false;
  const fileName = parts[2];
  const dot = fileName.lastIndexOf('.');
  if (dot <= 0) return false;
  return IMAGE_SUFFIXES.has(fileName.slice(dot).toLowerCase());
}

export function manifestIndexToTree(indexData) {
  const files = indexData?.files;
  if (!files || typeof files !== 'object' || Array.isArray(files)) {
    throw new Error('图库索引格式无效');
  }

  const tree = [];
  for (const path of Object.keys(files)) {
    if (!isSafeGalleryImagePath(path)) continue;
    tree.push({ path, sha: '', size: 0 });
  }
  return tree;
}
