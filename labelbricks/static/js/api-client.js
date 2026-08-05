/**
 * Centralized API client for all backend calls.
 */

// Surface the backend's Unity Catalog denial message instead of a generic failure.
async function throwApiError(res, fallback) {
  const err = await res.json().catch(() => ({}));
  throw new Error(err.error || fallback);
}

const API = {
  async getCatalogs() {
    const res = await fetch('/api/catalogs');
    if (!res.ok) await throwApiError(res, 'Failed to fetch catalogs');
    return res.json();
  },

  async getSchemas(catalog) {
    const res = await fetch(`/api/schemas/${encodeURIComponent(catalog)}`);
    if (!res.ok) await throwApiError(res, 'Failed to fetch schemas');
    return res.json();
  },

  async getVolumes(catalog, schema) {
    const res = await fetch(`/api/volumes/${encodeURIComponent(catalog)}/${encodeURIComponent(schema)}`);
    if (!res.ok) await throwApiError(res, 'Failed to fetch volumes');
    return res.json();
  },

  async getDirectories(volumePath) {
    const cleanPath = volumePath.replace(/^\/Volumes\//, '');
    const res = await fetch(`/api/directories/${cleanPath}`);
    if (!res.ok) await throwApiError(res, 'Failed to fetch directories');
    return res.json();
  },

  async setVolume(catalog, schema, volume, directory) {
    const res = await fetch('/api/set-volume', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ catalog, schema, volume, directory }),
    });
    if (!res.ok) await throwApiError(res, 'Failed to set volume');
    return res.json();
  },

  getImageUrl(filePath) {
    return `/api/image?file_path=${encodeURIComponent(filePath)}`;
  },

  getThumbnailUrl(filePath, size = 48) {
    return `/api/thumbnail?file_path=${encodeURIComponent(filePath)}&size=${size}`;
  },

  async loadAnnotations(filePath, volumePath) {
    let url = `/api/annotations?file_path=${encodeURIComponent(filePath)}`;
    if (volumePath) url += `&volume_path=${encodeURIComponent(volumePath)}`;
    const res = await fetch(url);
    if (!res.ok) return null;
    const data = await res.json();
    return data;
  },

  async saveAnnotations({ filePath, annotations, status, notes, compositeImage, volumePath, imageWidth, imageHeight, displayScale }) {
    const res = await fetch('/api/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filePath, annotations, status, notes, compositeImage, volumePath, imageWidth, imageHeight, displayScale }),
    });
    if (!res.ok) throw new Error(`Save failed (${res.status})`);
    return res.json();
  },

  async getImageStatuses(volumePath, filePaths) {
    const res = await fetch('/api/image-statuses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ volumePath, filePaths }),
    });
    if (!res.ok) return {};
    return res.json();
  },

  async getAISuggestions({ filePath, prompt }) {
    const res = await fetch('/api/ai-suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filePath, prompt }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'AI request failed' }));
      throw new Error(err.error || `AI suggest failed (${res.status})`);
    }
    return res.json();
  },
};

export default API;
