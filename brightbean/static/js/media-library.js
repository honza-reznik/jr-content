/**
 * Media Library — Alpine.js & HTMX integrations
 *
 * This file is loaded on library pages via {% static 'js/media-library.js' %}.
 * Alpine components that need Django template values receive them through
 * markup attributes, so this file can also initialize HTMX-loaded fragments.
 */

window.tagInput = function (initialTags, saveUrl, suggestionsUrl) {
  return {
    tags: initialTags || [],
    saveUrl: saveUrl,
    suggestionsUrl: suggestionsUrl,
    inputValue: '',
    suggestions: [],

    addTag(value) {
      const tag = (value || '').trim();
      if (tag && !this.tags.includes(tag)) {
        this.tags.push(tag);
        this.saveTags();
      }
      this.inputValue = '';
      this.suggestions = [];
    },

    removeTag(index) {
      if (index >= 0 && index < this.tags.length) {
        this.tags.splice(index, 1);
        this.saveTags();
      }
    },

    saveTags() {
      const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
      fetch(this.saveUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify(this.tags),
      });
    },

    async fetchSuggestions() {
      if (this.inputValue.length < 1) {
        this.suggestions = [];
        return;
      }
      try {
        const resp = await fetch(this.suggestionsUrl + '?q=' + encodeURIComponent(this.inputValue));
        const data = await resp.json();
        this.suggestions = data.filter(s => !this.tags.includes(s)).slice(0, 8);
      } catch {
        this.suggestions = [];
      }
    }
  };
};

(function () {
  'use strict';

  // ── HTMX event: refresh grid after uploads complete ──
  document.body.addEventListener('uploadsComplete', function () {
    var grid = document.getElementById('asset-grid');
    if (grid) {
      // Trigger a page reload of the grid via HTMX
      var url = grid.getAttribute('data-refresh-url');
      if (url) {
        htmx.ajax('GET', url, { target: '#asset-grid', swap: 'innerHTML' });
      } else {
        // Fallback: reload the page
        window.location.reload();
      }
    }
  });

  // ── HTMX event: close detail panel after asset deletion ──
  document.body.addEventListener('assetDeleted', function () {
    // If Alpine is available, close the detail panel
    var lib = document.querySelector('[x-data*="mediaLibrary"]');
    if (lib && lib.__x) {
      lib.__x.$data.detailOpen = false;
    }
    // Refresh the grid
    var grid = document.getElementById('asset-grid');
    if (grid) {
      window.location.reload();
    }
  });

  // ── Keyboard shortcut: Cmd/Ctrl+U to trigger upload ──
  document.addEventListener('keydown', function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key === 'u') {
      var fileInput = document.querySelector('[x-ref="fileInput"]');
      if (fileInput) {
        e.preventDefault();
        fileInput.click();
      }
    }
  });

  // ── Format file size for display ──
  window.formatFileSize = function (bytes) {
    if (bytes === 0) return '0 B';
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
  };

  // ── Format duration for display ──
  window.formatDuration = function (seconds) {
    var mins = Math.floor(seconds / 60);
    var secs = Math.floor(seconds % 60);
    return mins + ':' + (secs < 10 ? '0' : '') + secs;
  };

})();
