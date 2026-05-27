const state = {
  page: 1,
  pageSize: 50,
  debounceTimer: null,
};

const els = {
  searchInput: document.getElementById("searchInput"),
  sortBy: document.getElementById("sortBy"),
  sortDir: document.getElementById("sortDir"),
  resetFilters: document.getElementById("resetFilters"),
  folderFilter: document.getElementById("folderFilter"),
  extensionFilter: document.getElementById("extensionFilter"),
  resolutionFilter: document.getElementById("resolutionFilter"),
  yearFilter: document.getElementById("yearFilter"),
  sharedDriveFilter: document.getElementById("sharedDriveFilter"),
  audioFilter: document.getElementById("audioFilter"),
  minSizeFilter: document.getElementById("minSizeFilter"),
  maxSizeFilter: document.getElementById("maxSizeFilter"),
  minDurationFilter: document.getElementById("minDurationFilter"),
  maxDurationFilter: document.getElementById("maxDurationFilter"),
  semanticToggle: document.getElementById("semanticToggle"),
  resultsBody: document.getElementById("resultsBody"),
  resultsSummary: document.getElementById("resultsSummary"),
  prevPage: document.getElementById("prevPage"),
  nextPage: document.getElementById("nextPage"),
  pageInfo: document.getElementById("pageInfo"),
  statVideos: document.getElementById("statVideos"),
  statStorage: document.getElementById("statStorage"),
  statDuration: document.getElementById("statDuration"),
  detailDialog: document.getElementById("detailDialog"),
  detailContent: document.getElementById("detailContent"),
  scanFolderInput: document.getElementById("scanFolderInput"),
  scanFolderButton: document.getElementById("scanFolderButton"),
  scanFolderStatus: document.getElementById("scanFolderStatus"),
};

function buildQueryParams() {
  const params = new URLSearchParams();
  const add = (key, value) => {
    if (value !== null && value !== undefined && String(value).trim() !== "") {
      params.set(key, value);
    }
  };

  add("q", els.searchInput.value.trim());
  add("folder", els.folderFilter.value);
  add("extension", els.extensionFilter.value);
  add("resolution", els.resolutionFilter.value);
  add("year", els.yearFilter.value);
  add("shared_drive", els.sharedDriveFilter.value);
  add("semantic", els.semanticToggle && els.semanticToggle.checked ? "true" : "");
  add("has_audio", els.audioFilter.value);
  add("min_size_mb", els.minSizeFilter.value);
  add("max_size_mb", els.maxSizeFilter.value);
  add("min_duration_sec", els.minDurationFilter.value);
  add("max_duration_sec", els.maxDurationFilter.value);
  add("sort_by", els.sortBy.value);
  add("sort_dir", els.sortDir.value);
  params.set("page", String(state.page));
  params.set("page_size", String(state.pageSize));
  return params;
}

function fillSelect(select, values, placeholder) {
  select.innerHTML = `<option value="">${placeholder}</option>`;
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
}

async function loadStats() {
  const response = await fetch("/api/stats");
  const data = await response.json();
  els.statVideos.textContent = data.total_videos.toLocaleString();
  els.statStorage.textContent = data.total_size_human;
  els.statDuration.textContent = data.total_duration_human;
}

async function loadFilters() {
  const response = await fetch("/api/filters");
  const data = await response.json();
  fillSelect(els.folderFilter, data.folders, "All folders");
  fillSelect(els.extensionFilter, data.extensions, "All formats");
  fillSelect(els.resolutionFilter, data.resolutions, "All resolutions");
  fillSelect(els.yearFilter, data.years, "All years");
  fillSelect(els.sharedDriveFilter, data.shared_drives, "All drives");
}

function renderRows(items) {
  if (!items.length) {
    els.resultsBody.innerHTML = `<tr><td colspan="8" class="empty">No videos match your search. Try broader filters.</td></tr>`;
    return;
  }

  els.resultsBody.innerHTML = items
    .map(
      (item) => `
      <tr>
        <td>
          <div class="file-name">${escapeHtml(item.file_name)}</div>
          <div class="folder-path">${escapeHtml(item.owner || "Unknown owner")}</div>
        </td>
        <td><div class="folder-path">${escapeHtml(item.folder_path || "—")}</div></td>
        <td>${escapeHtml(item.file_extension || "—")}</td>
        <td>${escapeHtml(item.resolution || "—")}</td>
        <td>${escapeHtml(item.duration_human)}</td>
        <td>${escapeHtml(item.file_size_human)}</td>
        <td>${escapeHtml(formatDate(item.modified_at))}</td>
        <td>
          ${item.drive_url ? `<a class="link-btn" href="${item.drive_url}" target="_blank" rel="noopener">Open</a>` : ""}
          <button class="detail-btn" data-id="${item.file_id}" type="button">Details</button>
        </td>
      </tr>`
    )
    .join("");
}

async function loadVideos() {
  const params = buildQueryParams();
  const response = await fetch(`/api/videos?${params.toString()}`);
  const data = await response.json();

  renderRows(data.items);
  els.resultsSummary.textContent = `${data.total.toLocaleString()} video(s) found`;
  els.pageInfo.textContent = `Page ${data.page} of ${data.total_pages}`;
  els.prevPage.disabled = data.page <= 1;
  els.nextPage.disabled = data.page >= data.total_pages;
}

async function showDetails(fileId) {
  const response = await fetch(`/api/videos/${fileId}`);
  const item = await response.json();
  const rows = [
    ["Name", item.file_name],
    ["Folder", item.folder_path || "—"],
    ["Parent folder", item.parent_folder || "—"],
    ["Format", item.file_extension || "—"],
    ["Resolution", item.resolution || "—"],
    ["Duration", item.duration_human],
    ["Size", item.file_size_human],
    ["Video codec", item.video_codec || "—"],
    ["Audio codec", item.audio_codec || "—"],
    ["FPS", item.fps ?? "—"],
    ["Bitrate", item.bitrate ?? "—"],
    ["Owner", item.owner || "—"],
    ["Shared drive", item.shared_drive_name || "—"],
    ["Created", formatDate(item.created_at)],
    ["Modified", formatDate(item.modified_at)],
    ["Drive link", item.drive_url ? `<a href="${item.drive_url}" target="_blank" rel="noopener">Open in Google Drive</a>` : "—"],
  ];

  els.detailContent.innerHTML = rows
    .map(
      ([label, value]) => `
      <div class="detail-row">
        <span>${escapeHtml(label)}</span>
        <div>${typeof value === "string" && value.startsWith("<a") ? value : escapeHtml(String(value))}</div>
      </div>`
    )
    .join("");

  els.detailDialog.showModal();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function scheduleSearch() {
  clearTimeout(state.debounceTimer);
  state.debounceTimer = setTimeout(() => {
    state.page = 1;
    loadVideos();
  }, 250);
}

function resetFilters() {
  els.searchInput.value = "";
  [
    els.folderFilter,
    els.extensionFilter,
    els.resolutionFilter,
    els.yearFilter,
    els.sharedDriveFilter,
    els.audioFilter,
  ].forEach((select) => {
    select.value = "";
  });
  [els.minSizeFilter, els.maxSizeFilter, els.minDurationFilter, els.maxDurationFilter].forEach((input) => {
    input.value = "";
  });
  els.sortBy.value = "file_name";
  els.sortDir.value = "asc";
  state.page = 1;
  loadVideos();
}

[
  els.searchInput,
  els.sortBy,
  els.sortDir,
  els.folderFilter,
  els.extensionFilter,
  els.resolutionFilter,
  els.yearFilter,
  els.sharedDriveFilter,
  els.audioFilter,
  els.minSizeFilter,
  els.maxSizeFilter,
  els.minDurationFilter,
  els.maxDurationFilter,
  els.semanticToggle,
].forEach((element) => {
  if (!element) return;
  element.addEventListener("input", scheduleSearch);
  element.addEventListener("change", scheduleSearch);
});

els.resetFilters.addEventListener("click", resetFilters);
els.prevPage.addEventListener("click", () => {
  if (state.page > 1) {
    state.page -= 1;
    loadVideos();
  }
});
els.nextPage.addEventListener("click", () => {
  state.page += 1;
  loadVideos();
});

els.resultsBody.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.classList.contains("detail-btn")) {
    showDetails(target.dataset.id);
  }
});

async function triggerFolderScan() {
  const value = els.scanFolderInput.value.trim();
  if (!value) {
    els.scanFolderStatus.textContent = "Please paste a folder URL or ID.";
    return;
  }
  els.scanFolderStatus.textContent = "Scanning folder... This may take a while.";
  els.scanFolderButton.disabled = true;
  try {
    const response = await fetch("/api/scan-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_url_or_id: value }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    els.scanFolderStatus.textContent = `Scan complete. Indexed ${data.videos_indexed} videos (skipped ${data.videos_skipped}, errors ${data.errors}).`;
    // Refresh stats and list so new videos show up.
    await Promise.all([loadStats(), loadVideos(), loadFilters()]);
  } catch (error) {
    els.scanFolderStatus.textContent = `Scan failed: ${error.message}`;
  } finally {
    els.scanFolderButton.disabled = false;
  }
}

if (els.scanFolderButton) {
  els.scanFolderButton.addEventListener("click", triggerFolderScan);
}

async function init() {
  await Promise.all([loadStats(), loadFilters(), loadVideos()]);
}

init();
