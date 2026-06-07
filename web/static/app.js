const state = {
  page: 1,
  pageSize: 50,
  debounceTimer: null,
  view: "production",
};

const workflowLabels = {
  digitized: "Numérisée",
  to_review: "À visionner",
  transcribed: "Transcrite",
  ready_edit: "Prête montage",
  published: "Publiée",
};

const assetLabels = {
  raw: "Brute",
  cut: "Découpée",
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
  assetTypeFilter: document.getElementById("assetTypeFilter"),
  workflowStageFilter: document.getElementById("workflowStageFilter"),
  labelFilter: document.getElementById("labelFilter"),
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
  productionOverview: document.getElementById("productionOverview"),
  workflowCards: document.getElementById("workflowCards"),
  workflowAll: document.getElementById("workflowAll"),
  workflowRaw: document.getElementById("workflowRaw"),
  workflowCut: document.getElementById("workflowCut"),
  workflowCutLinked: document.getElementById("workflowCutLinked"),
  workflowDigitized: document.getElementById("workflowDigitized"),
  workflowToReview: document.getElementById("workflowToReview"),
  workflowTranscribed: document.getElementById("workflowTranscribed"),
  workflowReadyEdit: document.getElementById("workflowReadyEdit"),
  workflowPublished: document.getElementById("workflowPublished"),
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
  add("asset_type", els.assetTypeFilter.value);
  add("workflow_stage", els.workflowStageFilter.value);
  add("label", els.labelFilter.value);
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

async function loadWorkflowStats() {
  const response = await fetch("/api/workflow/stats");
  const data = await response.json();
  const assets = data.assets || {};
  const stages = data.stages || {};
  els.workflowAll.textContent = Object.values(assets).reduce((sum, value) => sum + value, 0).toLocaleString();
  els.workflowRaw.textContent = (assets.raw || 0).toLocaleString();
  els.workflowCut.textContent = (assets.cut || 0).toLocaleString();
  els.workflowCutLinked.textContent = `${(data.linked_cuts || 0).toLocaleString()} associée(s)`;
  els.workflowDigitized.textContent = (stages.digitized || 0).toLocaleString();
  els.workflowToReview.textContent = (stages.to_review || 0).toLocaleString();
  els.workflowTranscribed.textContent = (stages.transcribed || 0).toLocaleString();
  els.workflowReadyEdit.textContent = (stages.ready_edit || 0).toLocaleString();
  els.workflowPublished.textContent = (stages.published || 0).toLocaleString();
}

async function loadFilters() {
  const response = await fetch("/api/filters");
  const data = await response.json();
  fillSelect(els.folderFilter, data.folders, "All folders");
  fillSelect(els.extensionFilter, data.extensions, "All formats");
  fillSelect(els.resolutionFilter, data.resolutions, "All resolutions");
  fillSelect(els.yearFilter, data.years, "All years");
  fillSelect(els.sharedDriveFilter, data.shared_drives, "All drives");
  const selectedLabel = els.labelFilter.value;
  els.labelFilter.innerHTML = `<option value="">Tous les labels</option>`;
  (data.labels || []).forEach((label) => {
    const option = document.createElement("option");
    option.value = label.name;
    option.textContent = `${label.name} (${label.video_count})`;
    els.labelFilter.appendChild(option);
  });
  els.labelFilter.value = selectedLabel;
}

function renderRows(items) {
  if (!items.length) {
    els.resultsBody.innerHTML = `<tr><td colspan="9" class="empty">Aucune vidéo ne correspond à ces filtres.</td></tr>`;
    return;
  }

  els.resultsBody.innerHTML = items
    .map(
      (item) => `
      <tr>
        <td>
          <div class="file-name">${escapeHtml(item.editorial_title || item.clean_title || item.file_name)}</div>
          ${item.editorial_title || item.clean_title ? `<div class="folder-path">${escapeHtml(item.file_name)}</div>` : ""}
          <div class="folder-path">${escapeHtml(item.owner || "Unknown owner")}</div>
          ${renderLabelBadges(item.labels || [])}
        </td>
        <td><div class="folder-path">${escapeHtml(item.folder_path || "—")}</div></td>
        <td>
          <span class="status-badge asset-${escapeHtml(item.asset_type)}">${escapeHtml(assetLabels[item.asset_type] || item.asset_type)}</span>
          <span class="status-badge stage-${escapeHtml(item.workflow_stage)}">${escapeHtml(workflowLabels[item.workflow_stage] || item.workflow_stage)}</span>
        </td>
        <td>${escapeHtml(item.file_extension || "—")}</td>
        <td>
          <div>${escapeHtml(item.resolution || "—")}</div>
          ${item.main_theme ? `<div class="folder-path">${escapeHtml(item.main_theme)}</div>` : ""}
        </td>
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

  els.detailContent.dataset.fileId = fileId;
  els.detailContent.innerHTML = rows
    .map(
      ([label, value]) => `
      <div class="detail-row">
        <span>${escapeHtml(label)}</span>
        <div>${typeof value === "string" && value.startsWith("<a") ? value : escapeHtml(String(value))}</div>
      </div>`
    )
    .join("") + renderLabelEditor(item) + await renderWorkflowEditor(item) + renderChristianMetadataEditor(item);

  els.detailDialog.showModal();
}

function renderLabelBadges(labels) {
  if (!labels.length) return "";
  return `<div class="video-labels">${renderLabelChips(labels)}</div>`;
}

function renderLabelChips(labels) {
  return labels.map((label) => `<span class="video-label">${escapeHtml(label.name)}</span>`).join("");
}

function renderLabelEditor(item) {
  const value = (item.labels || []).map((label) => label.name).join("; ");
  return `
    <section class="label-editor">
      <div class="metadata-header">
        <h3>Labels manuels</h3>
        <button id="saveLabelsButton" type="button">Enregistrer les labels</button>
      </div>
      <p class="metadata-hint">Ajoute plusieurs labels séparés par des points-virgules.</p>
      <label class="metadata-field">
        <span>Labels</span>
        <input id="videoLabelsInput" type="text" value="${escapeAttr(value)}" placeholder="prioritaire; audio à nettoyer; chant">
      </label>
      <div id="videoLabelPreview" class="video-labels">${renderLabelChips(item.labels || [])}</div>
      <p id="labelsSaveStatus" class="filters-status"></p>
    </section>`;
}

async function saveLabels() {
  const fileId = els.detailContent.dataset.fileId;
  const labels = document.getElementById("videoLabelsInput").value
    .split(";")
    .map((label) => label.trim())
    .filter(Boolean);
  const status = document.getElementById("labelsSaveStatus");
  const button = document.getElementById("saveLabelsButton");
  status.textContent = "Enregistrement...";
  button.disabled = true;
  try {
    const response = await fetch(`/api/videos/${fileId}/labels`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ labels }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    const data = await response.json();
    document.getElementById("videoLabelPreview").innerHTML = renderLabelChips(data.labels);
    status.textContent = "Labels enregistrés.";
    await Promise.all([loadVideos(), loadFilters()]);
  } catch (error) {
    status.textContent = `Échec : ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function renderWorkflowEditor(item) {
  const response = await fetch(`/api/workflow/raw-videos?exclude_file_id=${encodeURIComponent(item.file_id)}`);
  const data = await response.json();
  const sourceOptions = (data.items || [])
    .map((source) => `
      <option value="${escapeAttr(source.file_id)}" ${source.file_id === item.source_file_id ? "selected" : ""}>
        ${escapeHtml(source.label)} · ${escapeHtml(source.folder_path)}
      </option>`)
    .join("");
  const related = item.related_videos || {};
  const sourceLink = related.source
    ? `<button class="related-video-btn" type="button" data-related-id="${escapeAttr(related.source.file_id)}">Source : ${escapeHtml(related.source.editorial_title || related.source.file_name)}</button>`
    : "";
  const cutLinks = (related.cuts || [])
    .map((cut) => `<button class="related-video-btn" type="button" data-related-id="${escapeAttr(cut.file_id)}">${escapeHtml(cut.editorial_title || cut.file_name)}</button>`)
    .join("");

  return `
    <section class="workflow-editor">
      <div class="metadata-header">
        <h3>Suivi de production</h3>
        <button id="saveWorkflowButton" type="button">Enregistrer le suivi</button>
      </div>
      <div class="workflow-editor-grid">
        <label class="metadata-field">
          <span>Type de fichier</span>
          <select id="workflowAssetType">
            <option value="raw" ${item.asset_type === "raw" ? "selected" : ""}>Vidéo brute</option>
            <option value="cut" ${item.asset_type === "cut" ? "selected" : ""}>Vidéo découpée</option>
          </select>
        </label>
        <label class="metadata-field">
          <span>Étape actuelle</span>
          <select id="workflowStage">
            ${Object.entries(workflowLabels).map(([value, label]) => `<option value="${value}" ${item.workflow_stage === value ? "selected" : ""}>${label}</option>`).join("")}
          </select>
        </label>
        <label class="metadata-field metadata-field-wide" id="sourceVideoField" ${item.asset_type !== "cut" ? "hidden" : ""}>
          <span>Vidéo brute source</span>
          <select id="workflowSourceFile">
            <option value="">Aucune source associée</option>
            ${sourceOptions}
          </select>
        </label>
        <label class="metadata-field metadata-field-wide">
          <span>Notes de suivi</span>
          <textarea id="workflowNotes" rows="3">${escapeHtml(item.workflow_notes || "")}</textarea>
        </label>
      </div>
      ${(sourceLink || cutLinks) ? `<div class="related-videos"><h4>Fichiers associés</h4>${sourceLink}${cutLinks}</div>` : ""}
      <p id="workflowSaveStatus" class="filters-status"></p>
    </section>`;
}

async function saveWorkflow() {
  const fileId = els.detailContent.dataset.fileId;
  const assetType = document.getElementById("workflowAssetType").value;
  const payload = {
    asset_type: assetType,
    workflow_stage: document.getElementById("workflowStage").value,
    source_file_id: assetType === "cut" ? document.getElementById("workflowSourceFile").value : "",
    workflow_notes: document.getElementById("workflowNotes").value,
  };
  const status = document.getElementById("workflowSaveStatus");
  const button = document.getElementById("saveWorkflowButton");
  status.textContent = "Enregistrement...";
  button.disabled = true;
  try {
    const response = await fetch(`/api/videos/${fileId}/workflow`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    status.textContent = "Suivi enregistré.";
    await Promise.all([loadVideos(), loadStats(), loadWorkflowStats()]);
  } catch (error) {
    status.textContent = `Échec : ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

const metadataFields = [
  ["editorial_title", "Titre éditorial", "input"],
  ["original_title", "Titre original", "input"],
  ["alternate_titles", "Autres titres", "textarea"],
  ["content_type", "Type de contenu", "input"],
  ["main_theme", "Thème principal", "input"],
  ["spiritual_themes", "Thèmes spirituels", "textarea"],
  ["doctrine_topics", "Doctrine", "textarea"],
  ["biblical_topics", "Sujets bibliques", "textarea"],
  ["bible_references", "Références bibliques", "textarea"],
  ["songs", "Chants", "textarea"],
  ["speaker", "Orateur", "input"],
  ["preacher", "Prédicateur", "input"],
  ["worship_leaders", "Conducteurs de louange", "input"],
  ["ministry", "Ministère", "input"],
  ["event_name", "Événement", "input"],
  ["event_date", "Date événement", "input"],
  ["location", "Lieu", "input"],
  ["language", "Langue", "input"],
  ["audience", "Public", "input"],
  ["series_name", "Série", "input"],
  ["session_number", "Session", "input"],
  ["teaching_type", "Format d'enseignement", "input"],
  ["keywords", "Mots-clés", "textarea"],
  ["semantic_tags", "Tags sémantiques", "textarea"],
  ["transcript_status", "Statut transcription", "input"],
  ["transcript_text_path", "Chemin transcription", "input"],
  ["transcript_summary", "Résumé transcription", "textarea"],
  ["ai_summary", "Résumé IA", "textarea"],
  ["manual_notes", "Notes manuelles", "textarea"],
  ["metadata_source", "Source métadonnées", "input"],
  ["metadata_confidence", "Confiance 0-1", "number"],
];

function renderChristianMetadataEditor(item) {
  const terms = item.lexicon_terms || [];
  const termBadges = terms.length
    ? terms.map((term) => `<span class="term-badge">${escapeHtml(term.category)} · ${escapeHtml(term.term)}</span>`).join("")
    : `<span class="empty-inline">Aucun terme normalisé pour l'instant.</span>`;

  const fields = metadataFields
    .map(([key, label, type]) => {
      const value = item[key] ?? "";
      if (type === "textarea") {
        return `
          <label class="metadata-field metadata-field-wide">
            <span>${escapeHtml(label)}</span>
            <textarea data-meta="${escapeHtml(key)}" rows="3">${escapeHtml(value)}</textarea>
          </label>`;
      }
      return `
        <label class="metadata-field">
          <span>${escapeHtml(label)}</span>
          <input data-meta="${escapeHtml(key)}" type="${type}" ${type === "number" ? 'min="0" max="1" step="0.01"' : ""} value="${escapeAttr(value)}">
        </label>`;
    })
    .join("");

  return `
    <section class="metadata-editor">
      <div class="metadata-header">
        <h3>Métadonnées chrétiennes</h3>
        <button id="saveMetadataButton" type="button">Save metadata</button>
      </div>
      <p class="metadata-hint">Sépare les listes par des points-virgules : foi; repentance; grâce.</p>
      <div class="metadata-grid">${fields}</div>
      <div class="metadata-terms">
        <h4>Lexique associé</h4>
        <div class="term-list">${termBadges}</div>
      </div>
      <p id="metadataSaveStatus" class="filters-status"></p>
    </section>`;
}

async function saveMetadata() {
  const fileId = els.detailContent.dataset.fileId;
  if (!fileId) return;
  const payload = {};
  els.detailContent.querySelectorAll("[data-meta]").forEach((field) => {
    payload[field.dataset.meta] = field.value;
  });

  const status = document.getElementById("metadataSaveStatus");
  const button = document.getElementById("saveMetadataButton");
  status.textContent = "Saving metadata...";
  button.disabled = true;
  try {
    const response = await fetch(`/api/videos/${fileId}/metadata`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    const item = await response.json();
    status.textContent = "Metadata saved.";
    await Promise.all([loadVideos(), loadStats(), loadFilters()]);
    const termsContainer = els.detailContent.querySelector(".term-list");
    if (termsContainer) {
      termsContainer.innerHTML = (item.lexicon_terms || [])
        .map((term) => `<span class="term-badge">${escapeHtml(term.category)} · ${escapeHtml(term.term)}</span>`)
        .join("") || `<span class="empty-inline">Aucun terme normalisé pour l'instant.</span>`;
    }
  } catch (error) {
    status.textContent = `Save failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value);
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
    els.assetTypeFilter,
    els.workflowStageFilter,
    els.labelFilter,
  ].forEach((select) => {
    select.value = "";
  });
  [els.minSizeFilter, els.maxSizeFilter, els.minDurationFilter, els.maxDurationFilter].forEach((input) => {
    input.value = "";
  });
  els.sortBy.value = "file_name";
  els.sortDir.value = "asc";
  document.querySelectorAll(".workflow-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.filterKind === "all");
  });
  state.page = 1;
  loadVideos();
}

function syncWorkflowCardSelection() {
  document.querySelectorAll(".workflow-card").forEach((card) => {
    const matchesAsset = card.dataset.filterKind === "asset_type"
      && card.dataset.filterValue === els.assetTypeFilter.value
      && !els.workflowStageFilter.value;
    const matchesStage = card.dataset.filterKind === "workflow_stage"
      && card.dataset.filterValue === els.workflowStageFilter.value
      && !els.assetTypeFilter.value;
    const matchesAll = card.dataset.filterKind === "all"
      && !els.assetTypeFilter.value
      && !els.workflowStageFilter.value;
    card.classList.toggle("active", matchesAsset || matchesStage || matchesAll);
  });
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
  els.assetTypeFilter,
  els.workflowStageFilter,
  els.labelFilter,
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

els.assetTypeFilter.addEventListener("change", syncWorkflowCardSelection);
els.workflowStageFilter.addEventListener("change", syncWorkflowCardSelection);

document.querySelectorAll(".view-tab").forEach((button) => {
  button.addEventListener("click", () => {
    state.view = button.dataset.view;
    document.querySelectorAll(".view-tab").forEach((tab) => tab.classList.toggle("active", tab === button));
    els.productionOverview.hidden = state.view !== "production";
  });
});

els.workflowCards.addEventListener("click", (event) => {
  const card = event.target.closest(".workflow-card");
  if (!card) return;
  const kind = card.dataset.filterKind;
  els.assetTypeFilter.value = kind === "asset_type" ? card.dataset.filterValue : "";
  els.workflowStageFilter.value = kind === "workflow_stage" ? card.dataset.filterValue : "";
  document.querySelectorAll(".workflow-card").forEach((item) => item.classList.toggle("active", item === card));
  state.page = 1;
  loadVideos();
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

els.detailContent.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.id === "saveMetadataButton") {
    saveMetadata();
  }
  if (target.id === "saveWorkflowButton") {
    saveWorkflow();
  }
  if (target.id === "saveLabelsButton") {
    saveLabels();
  }
  if (target.classList.contains("related-video-btn")) {
    showDetails(target.dataset.relatedId);
  }
});

els.detailContent.addEventListener("change", (event) => {
  if (event.target.id === "workflowAssetType") {
    document.getElementById("sourceVideoField").hidden = event.target.value !== "cut";
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
  await Promise.all([loadStats(), loadWorkflowStats(), loadFilters(), loadVideos()]);
}

init();
