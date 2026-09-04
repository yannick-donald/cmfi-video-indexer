const state = {
  page: 1,
  pageSize: 50,
  debounceTimer: null,
  view: "production",
  labels: [],
};

const appMode = {
  publicDemo: document.body.dataset.publicDemo === "true",
  readOnly: document.body.dataset.readOnly === "true",
  superAdmin: document.body.dataset.superAdmin === "true",
};

const workflowLabels = {
  digitized: "Numérisée",
  to_review: "À visionner",
  watched: "Visionnée",
  transcribed: "Transcrite",
  treated: "Traitée",
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
  trackingFilter: document.getElementById("trackingFilter"),
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
  workflowWatched: document.getElementById("workflowWatched"),
  workflowTranscribed: document.getElementById("workflowTranscribed"),
  workflowTreated: document.getElementById("workflowTreated"),
  workflowReadyEdit: document.getElementById("workflowReadyEdit"),
  workflowPublished: document.getElementById("workflowPublished"),
  trackingNew: document.getElementById("trackingNew"),
  trackingIncomplete: document.getElementById("trackingIncomplete"),
  trackingMissingLabels: document.getElementById("trackingMissingLabels"),
  trackingUnlinkedCuts: document.getElementById("trackingUnlinkedCuts"),
  trackingCards: document.getElementById("trackingCards"),
  logoutButton: document.getElementById("logoutButton"),
  helpButton: document.getElementById("helpButton"),
  helpDialog: document.getElementById("helpDialog"),
  openDriveConfigButton: document.getElementById("openDriveConfigButton"),
  driveConfigDialog: document.getElementById("driveConfigDialog"),
  driveCurrentFolder: document.getElementById("driveCurrentFolder"),
  driveFolderInput: document.getElementById("driveFolderInput"),
  testDriveFolderButton: document.getElementById("testDriveFolderButton"),
  saveDriveFolderButton: document.getElementById("saveDriveFolderButton"),
  driveConfigStatus: document.getElementById("driveConfigStatus"),
  driveFolderSearchInput: document.getElementById("driveFolderSearchInput"),
  searchDriveFoldersButton: document.getElementById("searchDriveFoldersButton"),
  driveFolderSearchResults: document.getElementById("driveFolderSearchResults"),
  labelSuggestions: document.getElementById("labelSuggestions"),
  assigneeFilter: document.getElementById("assigneeFilter"),
  openUsersButton: document.getElementById("openUsersButton"),
  usersDialog: document.getElementById("usersDialog"),
  usersContent: document.getElementById("usersContent"),
  openRelinkQueue: document.getElementById("openRelinkQueue"),
  relinkDialog: document.getElementById("relinkDialog"),
  relinkContent: document.getElementById("relinkContent"),
  fillTitlesButton: document.getElementById("fillTitlesButton"),
  fillTitlesStatus: document.getElementById("fillTitlesStatus"),
  exportExcelButton: document.getElementById("exportExcelButton"),
  exportCsvButton: document.getElementById("exportCsvButton"),
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
  add("tracking", els.trackingFilter.value);
  add("assignee", els.assigneeFilter ? els.assigneeFilter.value : "");
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

// Pagination, sorting and the semantic toggle shape the on-screen page, not the
// export: a report always covers every row matching the active filters.
const EXPORT_IGNORED_PARAMS = ["page", "page_size", "sort_by", "sort_dir", "semantic"];

function buildExportUrl(path) {
  const params = buildQueryParams();
  EXPORT_IGNORED_PARAMS.forEach((key) => params.delete(key));
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

async function downloadExport(button, path, fallbackName) {
  if (!button) return;
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "Préparation...";
  try {
    const response = await fetch(buildExportUrl(path));
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const disposition = response.headers.get("content-disposition") || "";
    const match = disposition.match(/filename="?([^"]+)"?/);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = match ? match[1] : fallbackName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    window.alert(`L'export a échoué : ${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
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
  els.workflowWatched.textContent = (stages.watched || 0).toLocaleString();
  els.workflowTranscribed.textContent = (stages.transcribed || 0).toLocaleString();
  els.workflowTreated.textContent = (stages.treated || 0).toLocaleString();
  els.workflowReadyEdit.textContent = (stages.ready_edit || 0).toLocaleString();
  els.workflowPublished.textContent = (stages.published || 0).toLocaleString();
  const tracking = data.tracking || {};
  els.trackingNew.textContent = (tracking.new_count || 0).toLocaleString();
  els.trackingIncomplete.textContent = (tracking.incomplete || 0).toLocaleString();
  els.trackingMissingLabels.textContent = (tracking.missing_labels || 0).toLocaleString();
  els.trackingUnlinkedCuts.textContent = (tracking.unlinked_cuts || 0).toLocaleString();
}

async function populateAssigneeFilter() {
  if (!els.assigneeFilter) return;
  const team = await loadTeam();
  if (!team.length) return;
  const current = els.assigneeFilter.value;
  els.assigneeFilter.innerHTML =
    `<option value="">Tout le monde</option><option value="__none__">Non affectées</option>` +
    team.map((user) => `<option value="${escapeAttr(user.email)}">${escapeHtml(user.display_name)}</option>`).join("");
  els.assigneeFilter.value = current;
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
  state.labels = data.labels || [];
  els.labelFilter.innerHTML = `<option value="">Tous les labels</option>`;
  (data.labels || []).forEach((label) => {
    const option = document.createElement("option");
    option.value = label.name;
    option.textContent = `${label.name} (${label.video_count})`;
    els.labelFilter.appendChild(option);
  });
  els.labelFilter.value = selectedLabel;
  if (els.labelSuggestions) {
    els.labelSuggestions.innerHTML = state.labels
      .map((label) => `<option value="${escapeAttr(label.name)}"></option>`)
      .join("");
  }
}

// L'ID interne est la clé stable d'une vidéo : il survit au renommage et au
// déplacement dans Drive. On l'affiche partout et on le rend copiable en un clic.
function renderInternalId(internalId) {
  if (!internalId) {
    return '<span class="internal-id internal-id-missing" title="Aucun ID interne : relancez un scan">—</span>';
  }
  const safe = escapeHtml(internalId);
  return `<span class="internal-id"><code>${safe}</code><button type="button" class="copy-id-btn" data-copy-id="${escapeAttr(internalId)}" title="Copier ${safe}" aria-label="Copier ${safe}">⧉</button></span>`;
}

// Repli sans API Clipboard : celle-ci exige un contexte sécurisé (https), ce qui
// n'est pas garanti en local ni dans un navigateur embarqué.
function legacyCopy(value) {
  try {
    const field = document.createElement("textarea");
    field.value = value;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.appendChild(field);
    field.select();
    const copied = document.execCommand("copy");
    field.remove();
    return copied;
  } catch (error) {
    return false;
  }
}

async function copyInternalId(button) {
  const value = button.dataset.copyId;
  const original = button.textContent;
  let copied = false;
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(value);
      copied = true;
    }
  } catch (error) {
    copied = false;
  }
  if (!copied) {
    copied = legacyCopy(value);
  }
  button.textContent = copied ? "✓" : "!";
  button.title = copied ? `${value} copié` : `Copie impossible — sélectionnez ${value}`;
  window.setTimeout(() => {
    button.textContent = original;
    button.title = `Copier ${value}`;
  }, 1200);
}

async function callFillTitles(apply) {
  const response = await fetch("/api/titles/fill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apply, include_partial: true }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

// Écriture de masse : toujours un aperçu d'abord, jamais d'application directe.
async function previewFillTitles() {
  els.fillTitlesButton.disabled = true;
  els.fillTitlesStatus.hidden = false;
  els.fillTitlesStatus.textContent = "Analyse des vidéos sans titre...";
  try {
    const data = await callFillTitles(false);
    if (!data.eligible) {
      els.fillTitlesStatus.textContent =
        `Aucun titre à proposer. ${data.skipped_existing_title} vidéo(s) ont déjà un titre, ` +
        `${data.skipped_no_proposal} n'ont aucun titre déductible de leur nom de fichier.`;
      return;
    }
    const apercu = (data.sample || [])
      .slice(0, 4)
      .map((item) => `<li>${escapeHtml(item.title)}</li>`)
      .join("");
    els.fillTitlesStatus.innerHTML =
      `<strong>${data.eligible} titre(s)</strong> seront écrits ` +
      `(${data.high} exploitables, ${data.partial} à relire). ` +
      `${data.skipped_existing_title} titre(s) existants ne seront pas touchés, ` +
      `${data.skipped_no_proposal} vidéo(s) resteront sans titre en attendant leurs métadonnées.` +
      `<ul class="fill-titles-sample">${apercu}</ul>` +
      `<button type="button" id="confirmFillTitles">Écrire les ${data.eligible} titres</button> ` +
      `<button type="button" id="cancelFillTitles" class="btn-secondary">Annuler</button>`;
  } catch (error) {
    els.fillTitlesStatus.textContent = `Analyse impossible : ${error.message}`;
  } finally {
    els.fillTitlesButton.disabled = false;
  }
}

async function applyFillTitles() {
  els.fillTitlesStatus.textContent = "Écriture en cours...";
  try {
    const data = await callFillTitles(true);
    els.fillTitlesStatus.textContent =
      `${data.eligible} titre(s) écrits. Les titres marqués « à relire » demandent une vérification.`;
    await Promise.all([loadVideos(), loadStats()]);
  } catch (error) {
    els.fillTitlesStatus.textContent = `Écriture impossible : ${error.message}`;
  }
}

// Liste des comptes, chargée une fois : elle sert au sélecteur d'affectation.
let teamCache = null;

async function loadTeam(force = false) {
  if (teamCache && !force) return teamCache;
  if (!appMode.superAdmin) return [];
  try {
    const response = await fetch("/api/admin/users");
    if (!response.ok) return [];
    const data = await response.json();
    teamCache = data.items || [];
    return teamCache;
  } catch (error) {
    return [];
  }
}

function renderAssignee(item, team) {
  const current = item.assigned_user_email || "";
  const meta = current
    ? `<div class="folder-path">Désignée le ${escapeHtml(formatDate(item.assigned_at))} par ${escapeHtml(item.assigned_by_email || "—")}</div>`
    : "";
  if (!appMode.superAdmin) {
    return `
      <section class="assignee-block">
        <h3>Responsable</h3>
        <p class="assignee-current">${current ? escapeHtml(item.assigned_user_name || current) : "Personne n'est encore désigné."}</p>
        ${meta}
      </section>`;
  }
  const options = [`<option value="">Personne</option>`]
    .concat(
      team
        .filter((user) => user.is_active || user.email === current)
        .map(
          (user) =>
            `<option value="${escapeAttr(user.id)}" ${user.email === current ? "selected" : ""}>${escapeHtml(user.display_name)}${user.is_active ? "" : " (désactivé)"}</option>`
        )
    )
    .join("");
  return `
    <section class="assignee-block">
      <h3>Responsable</h3>
      <div class="assignee-row">
        <select id="assigneeSelect" ${appMode.readOnly ? "disabled" : ""}>${options}</select>
        ${appMode.readOnly ? "" : '<button type="button" id="saveAssigneeButton">Désigner</button>'}
      </div>
      ${meta}
      <p id="assigneeStatus" class="filters-status"></p>
    </section>`;
}

async function saveAssignee() {
  const select = document.getElementById("assigneeSelect");
  const status = document.getElementById("assigneeStatus");
  const fileId = els.detailContent.dataset.fileId;
  if (!select || !fileId) return;
  status.textContent = "Enregistrement...";
  try {
    const response = await fetch(`/api/videos/${encodeURIComponent(fileId)}/assignee`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: select.value ? Number(select.value) : null }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    status.textContent = data.assigned_user_email
      ? `Vidéo désignée à ${data.assigned_user_email}.`
      : "Affectation retirée.";
    await loadVideos();
  } catch (error) {
    status.textContent = `Échec : ${error.message}`;
  }
}

async function loadUsersPanel() {
  els.usersContent.innerHTML = '<p class="source-empty">Chargement...</p>';
  try {
    const response = await fetch("/api/admin/users");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    teamCache = data.items || [];
    const charge = {};
    (data.assignments?.per_user || []).forEach((row) => {
      charge[row.email] = row.total;
    });
    const rows = teamCache
      .map(
        (user) => `
        <tr>
          <td>
            <input class="user-name-input" type="text" value="${escapeAttr(user.full_name)}" placeholder="Nom" data-user-id="${escapeAttr(user.id)}">
            <div class="folder-path">${escapeHtml(user.email)}${user.is_super_admin ? ' · admin' : ""}</div>
          </td>
          <td>${charge[user.email] || 0}</td>
          <td>${user.email_verified ? "oui" : "non"}</td>
          <td>${user.last_login_at ? escapeHtml(formatDate(user.last_login_at)) : "jamais"}</td>
          <td>${
            user.is_super_admin
              ? "—"
              : `<button type="button" class="toggle-user-btn btn-secondary" data-user-id="${escapeAttr(user.id)}" data-active="${user.is_active}">${user.is_active ? "Désactiver" : "Réactiver"}</button>`
          }</td>
        </tr>`
      )
      .join("");
    els.usersContent.innerHTML = `
      <div class="users-table-wrap">
      <table class="users-table">
        <thead><tr><th>Nom et compte</th><th>Vidéos</th><th>Vérifié</th><th>Dernière connexion</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      </div>
      <p class="metadata-hint">${data.assignments?.unassigned ?? 0} vidéo(s) sans responsable désigné.</p>
      <p id="usersStatus" class="filters-status"></p>`;
  } catch (error) {
    els.usersContent.innerHTML = `<p class="source-empty">Chargement impossible : ${escapeHtml(error.message)}</p>`;
  }
}

async function saveUserName(input) {
  const status = document.getElementById("usersStatus");
  try {
    const response = await fetch(`/api/admin/users/${input.dataset.userId}/name`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ full_name: input.value }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    teamCache = null;
    if (status) status.textContent = `Nom enregistré : ${data.display_name}.`;
    await Promise.all([populateAssigneeFilter(), loadVideos()]);
  } catch (error) {
    if (status) status.textContent = `Échec : ${error.message}`;
  }
}

async function toggleUser(button) {
  const status = document.getElementById("usersStatus");
  button.disabled = true;
  try {
    const response = await fetch(`/api/admin/users/${button.dataset.userId}/status`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: button.dataset.active !== "true" }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    await loadUsersPanel();
  } catch (error) {
    button.disabled = false;
    if (status) status.textContent = `Échec : ${error.message}`;
  }
}

function renderRelinkItem(item) {
  const suggestions = item.suggestions || [];
  const options = suggestions.length
    ? suggestions
        .map(
          (source) => `
          <button type="button" class="relink-option" data-cut-id="${escapeAttr(item.file_id)}" data-source-id="${escapeAttr(source.file_id)}">
            <span class="relink-option-main">
              ${source.internal_video_id ? `<code class="source-id">${escapeHtml(source.internal_video_id)}</code>` : ""}
              ${escapeHtml(source.label)}
            </span>
            <span class="folder-path">${escapeHtml(source.reasons.join(" · "))}</span>
          </button>`
        )
        .join("")
    : `<p class="source-empty">Aucune source plausible trouvée. Ouvrez la fiche pour la rechercher manuellement.</p>`;

  return `
    <section class="relink-item" data-cut-row="${escapeAttr(item.file_id)}">
      <div class="relink-cut">
        ${item.internal_video_id ? `<code class="source-id">${escapeHtml(item.internal_video_id)}</code>` : ""}
        <strong>${escapeHtml(item.editorial_title || item.file_name)}</strong>
        <div class="folder-path">${escapeHtml(item.folder_path || "—")}</div>
      </div>
      <div class="relink-options">${options}</div>
    </section>`;
}

async function loadRelinkQueue() {
  els.relinkContent.innerHTML = '<p class="source-empty">Chargement...</p>';
  try {
    const response = await fetch("/api/workflow/relink-queue?limit=25");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data.total) {
      els.relinkContent.innerHTML =
        `<p class="source-empty">Aucune vidéo à rattacher : toutes les vidéos découpées ont une source.</p>`;
      return;
    }
    els.relinkContent.innerHTML = data.items.map(renderRelinkItem).join("");
  } catch (error) {
    els.relinkContent.innerHTML = `<p class="source-empty">Chargement impossible : ${escapeHtml(error.message)}</p>`;
  }
}

async function confirmRelink(button) {
  const cutId = button.dataset.cutId;
  const row = els.relinkContent.querySelector(`[data-cut-row="${CSS.escape(cutId)}"]`);
  button.disabled = true;
  try {
    const response = await fetch(`/api/videos/${encodeURIComponent(cutId)}/link-source`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_file_id: button.dataset.sourceId }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `HTTP ${response.status}`);
    }
    if (row) {
      row.innerHTML = `<p class="relink-done">Rattachée.</p>`;
    }
    await Promise.all([loadWorkflowStats(), loadVideos()]);
  } catch (error) {
    button.disabled = false;
    if (row) {
      const status = document.createElement("p");
      status.className = "source-empty";
      status.textContent = `Rattachement impossible : ${error.message}`;
      row.appendChild(status);
    }
  }
}

function renderRows(items) {
  if (!items.length) {
    els.resultsBody.innerHTML = `<tr><td colspan="10" class="empty">Aucune vidéo ne correspond à ces filtres.</td></tr>`;
    return;
  }

  els.resultsBody.innerHTML = items
    .map(
      (item) => `
      <tr>
        <td>${renderInternalId(item.internal_video_id)}</td>
        <td>
          <div class="file-name">${escapeHtml(item.editorial_title || item.clean_title || item.file_name)}</div>
          <div class="row-badges">
            ${item.is_new ? '<span class="new-badge">Nouvelle</span>' : ""}
            <span class="completion-badge completion-${escapeHtml(item.completeness.status)}">${escapeHtml(item.completeness.label)}</span>
          </div>
          ${item.editorial_title || item.clean_title ? `<div class="folder-path">${escapeHtml(item.file_name)}</div>` : ""}
          <div class="folder-path">${escapeHtml(item.owner || "Unknown owner")}</div>
          ${item.assigned_user_email ? `<div class="assignee-badge" title="Désignée pour travailler dessus : ${escapeAttr(item.assigned_user_email)}">👤 ${escapeHtml(item.assigned_user_name || item.assigned_user_email)}</div>` : ""}
          ${renderLabelBadges(item.labels || [])}
          ${item.last_label_edit ? `<div class="label-edit-by">Labels : ${escapeHtml(item.last_label_edit.user_email)} · ${escapeHtml(formatDate(item.last_label_edit.created_at))}</div>` : ""}
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
    ["ID interne", renderInternalId(item.internal_video_id)],
    ["Titre", item.editorial_title || item.clean_title || item.file_name],
    ["Nom du fichier", item.file_name],
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
        <div>${typeof value === "string" && (value.startsWith("<a") || value.startsWith("<span class=\"internal-id")) ? value : escapeHtml(String(value))}</div>
      </div>`
    )
    .join("") +
    renderAssignee(item, await loadTeam()) +
    renderTranscription(item, await loadTranscriptionState(fileId)) +
    renderLabelEditor(item) +
    (await renderWorkflowEditor(item)) +
    renderChristianMetadataEditor(item);

  els.detailDialog.showModal();
}

function renderLabelBadges(labels) {
  if (!labels.length) return "";
  return `<div class="video-labels">${renderLabelChips(labels)}</div>`;
}

function renderLabelChips(labels) {
  return labels.map((label) => `<span class="video-label">${escapeHtml(label.name)}</span>`).join("");
}

const ETATS_TRANSCRIPTION = {
  absent: "Pas encore transcrite",
  pending: "En file d'attente",
  downloading: "Téléchargement",
  downloaded: "Téléchargée",
  extracting_audio: "Extraction de l'audio",
  transcribing: "Transcription en cours",
  generating_metadata: "Métadonnées",
  chunking: "Découpage",
  embedding: "Plongements",
  indexing: "Indexation",
  completed: "Terminée",
  done: "Transcrite",
  failed: "Échec",
};

function renderTranscription(item, etat) {
  const cle = etat && etat.state ? etat.state : "absent";
  const libelle = ETATS_TRANSCRIPTION[cle] || cle;
  const enCours = !["absent", "done", "completed", "failed"].includes(cle);
  const transcrite = Boolean(etat && etat.transcript);

  let detail = "";
  if (transcrite) {
    const t = etat.transcript;
    detail = `<p class="metadata-hint">${t.segment_count} segments · modèle ${escapeHtml(String(t.model || "?"))}.</p>`;
  } else if (cle === "failed") {
    detail = `<p class="metadata-hint">Dernière erreur : ${escapeHtml(String(etat.error || "inconnue"))} (${etat.attempts} tentative(s)).</p>`;
  } else if (enCours) {
    detail = `<p class="metadata-hint">Étape en cours : ${escapeHtml(String(etat.step || libelle))}.</p>`;
  } else {
    detail = `<p class="metadata-hint">Le bouton met la vidéo en file. La transcription tourne sur un worker séparé, pas sur ce serveur : compter environ une heure de calcul pour trois heures de vidéo.</p>`;
  }

  const bouton = appMode.readOnly
    ? ""
    : `<button id="transcribeButton" type="button" ${enCours ? "disabled" : ""}>${
        transcrite ? "Retranscrire" : "Lancer la transcription"
      }</button>`;

  return `
    <section class="transcription-panel">
      <div class="metadata-header">
        <h3>Transcription</h3>
        ${bouton}
      </div>
      <p class="transcription-state" data-state="${escapeAttr(cle)}"><strong>${escapeHtml(libelle)}</strong></p>
      ${detail}
      <p id="transcribeStatus" class="filters-status"></p>
    </section>`;
}

async function loadTranscriptionState(fileId) {
  try {
    const response = await fetch(`/api/videos/${encodeURIComponent(fileId)}/transcription`);
    if (!response.ok) return null;
    return await response.json();
  } catch (error) {
    return null;
  }
}

async function queueTranscription() {
  const fileId = els.detailContent.dataset.fileId;
  const status = document.getElementById("transcribeStatus");
  const button = document.getElementById("transcribeButton");
  button.disabled = true;
  status.textContent = "Mise en file...";
  try {
    const response = await fetch(`/api/videos/${encodeURIComponent(fileId)}/transcribe`, {
      method: "POST",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    status.textContent = data.message || "Mise en file.";
    const panneau = document.querySelector(".transcription-panel");
    if (panneau) panneau.outerHTML = renderTranscription(null, data);
  } catch (error) {
    status.textContent = `Échec : ${error.message}`;
    button.disabled = false;
  }
}

function renderLabelEditor(item) {
  const value = (item.labels || []).map((label) => label.name).join("; ");
  return `
    <section class="label-editor">
      <div class="metadata-header">
        <h3>Labels manuels</h3>
        ${appMode.readOnly ? "" : '<button id="saveLabelsButton" type="button">Enregistrer les labels</button>'}
      </div>
      <p class="metadata-hint">Ajoute plusieurs labels séparés par des points-virgules.</p>
      <label class="metadata-field">
        <span>Labels</span>
        <input id="videoLabelsInput" list="labelSuggestions" type="text" value="${escapeAttr(value)}" placeholder="prioritaire; audio à nettoyer; chant" ${appMode.readOnly ? "disabled" : ""}>
      </label>
      <div id="videoLabelPreview" class="video-labels">${renderLabelChips(item.labels || [])}</div>
      <div id="labelHistoryContainer">${renderLabelHistory(item.label_history || [])}</div>
      <p id="labelsSaveStatus" class="filters-status"></p>
    </section>`;
}

function renderLabelHistory(history) {
  if (!history.length) {
    return `<p class="metadata-hint">Aucune modification de labels enregistrée.</p>`;
  }
  return `
    <div class="label-history">
      <h4>Historique des labels</h4>
      ${history.map((entry) => {
        const changes = [
          entry.added_labels.length ? `Ajout : ${entry.added_labels.join(", ")}` : "",
          entry.removed_labels.length ? `Retrait : ${entry.removed_labels.join(", ")}` : "",
        ].filter(Boolean).join(" · ");
        return `
          <div class="label-history-entry">
            <strong>${escapeHtml(entry.user_email)}</strong>
            <span>${escapeHtml(formatDate(entry.created_at))}</span>
            <p>${escapeHtml(changes || "Liste enregistrée")}</p>
          </div>`;
      }).join("")}
    </div>`;
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
    document.getElementById("labelHistoryContainer").innerHTML = renderLabelHistory(data.label_history || []);
    status.textContent = "Labels enregistrés.";
    await Promise.all([loadVideos(), loadFilters(), loadWorkflowStats()]);
  } catch (error) {
    status.textContent = `Échec : ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

let sourceSearchTimer = null;

function currentDetailFileId() {
  return els.detailContent.dataset.fileId || "";
}

async function runSourceSearch(term) {
  const results = document.getElementById("sourceSearchResults");
  if (!results) return;
  const params = new URLSearchParams({
    exclude_file_id: currentDetailFileId(),
    limit: "12",
  });
  if (term.trim()) params.set("q", term.trim());

  try {
    const response = await fetch(`/api/workflow/raw-videos?${params}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const items = data.items || [];
    if (!items.length) {
      results.innerHTML = '<p class="source-empty">Aucune vidéo brute ne correspond.</p>';
    } else {
      results.innerHTML = items
        .map(
          (source) => `
          <button type="button" class="source-result" data-source-id="${escapeAttr(source.file_id)}">
            ${source.internal_video_id ? `<code class="source-id">${escapeHtml(source.internal_video_id)}</code>` : ""}
            <span class="source-result-label">${escapeHtml(source.label)}</span>
            <span class="folder-path">${escapeHtml(source.folder_path || "—")}</span>
          </button>`
        )
        .join("");
    }
    results.hidden = false;
  } catch (error) {
    results.innerHTML = `<p class="source-empty">Recherche impossible : ${escapeHtml(error.message)}</p>`;
    results.hidden = false;
  }
}

function selectSourceVideo(button) {
  const hidden = document.getElementById("workflowSourceFile");
  const selected = document.getElementById("sourceSelected");
  const results = document.getElementById("sourceSearchResults");
  const search = document.getElementById("sourceSearchInput");
  if (!hidden || !selected) return;

  hidden.value = button.dataset.sourceId;
  selected.innerHTML = renderSelectedSource({
    internal_video_id: button.querySelector(".source-id")?.textContent || "",
    label: button.querySelector(".source-result-label")?.textContent || "",
    folder_path: button.querySelector(".folder-path")?.textContent || "",
  });
  if (results) {
    results.hidden = true;
    results.innerHTML = "";
  }
  if (search) search.value = "";
  // Le choix n'est pas persisté tant que "Enregistrer le suivi" n'est pas cliqué.
  const status = document.getElementById("workflowSaveStatus");
  if (status) status.textContent = "Source sélectionnée — cliquez sur « Enregistrer le suivi » pour valider.";
}

function clearSourceVideo() {
  const hidden = document.getElementById("workflowSourceFile");
  const selected = document.getElementById("sourceSelected");
  if (hidden) hidden.value = "";
  if (selected) selected.innerHTML = renderSelectedSource(null);
  const status = document.getElementById("workflowSaveStatus");
  if (status) status.textContent = "Source retirée — cliquez sur « Enregistrer le suivi » pour valider.";
}

// Le rattachement d'une découpe à sa source se fait par recherche : l'ancienne
// liste déroulante chargeait toute la bibliothèque, ce qui la rendait
// inutilisable dès quelques centaines de vidéos.
function renderSelectedSource(source) {
  if (!source) {
    return '<p class="source-empty">Aucune source associée. Recherchez la vidéo brute ci-dessus.</p>';
  }
  const id = source.internal_video_id
    ? `<code class="source-id">${escapeHtml(source.internal_video_id)}</code>`
    : "";
  const name = escapeHtml(source.editorial_title || source.label || source.file_name || "");
  const folder = escapeHtml(source.folder_path || "");
  const remove = appMode.readOnly
    ? ""
    : '<button type="button" id="clearSourceButton" class="btn-secondary" title="Retirer la source">Retirer</button>';
  return `
    <div class="source-selected">
      <div class="source-selected-main">${id}<strong>${name}</strong></div>
      ${folder ? `<div class="folder-path">${folder}</div>` : ""}
      ${remove}
    </div>`;
}

async function renderWorkflowEditor(item) {
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
        ${appMode.readOnly ? "" : '<button id="saveWorkflowButton" type="button">Enregistrer le suivi</button>'}
      </div>
      <div class="workflow-editor-grid">
        <label class="metadata-field">
          <span>Type de fichier</span>
          <select id="workflowAssetType" ${appMode.readOnly ? "disabled" : ""}>
            <option value="raw" ${item.asset_type === "raw" ? "selected" : ""}>Vidéo brute</option>
            <option value="cut" ${item.asset_type === "cut" ? "selected" : ""}>Vidéo découpée</option>
          </select>
        </label>
        <label class="metadata-field">
          <span>Étape actuelle</span>
          <select id="workflowStage" ${appMode.readOnly ? "disabled" : ""}>
            ${Object.entries(workflowLabels).map(([value, label]) => `<option value="${value}" ${item.workflow_stage === value ? "selected" : ""}>${label}</option>`).join("")}
          </select>
        </label>
        <div class="metadata-field metadata-field-wide" id="sourceVideoField" ${item.asset_type !== "cut" ? "hidden" : ""}>
          <span class="metadata-field-label">Vidéo brute source</span>
          <input type="hidden" id="workflowSourceFile" value="${escapeAttr(item.source_file_id || "")}">
          ${appMode.readOnly ? "" : `
            <input id="sourceSearchInput" type="search" autocomplete="off"
                   placeholder="Rechercher par ID interne (CHR-VID-000123), titre ou nom de fichier">
            <div id="sourceSearchResults" class="source-results" hidden></div>
            <p class="source-hint">Astuce : nommez la découpe <code>CHR-VID-000123 - Titre.mp4</code> dans Drive et elle sera rattachée automatiquement au prochain scan.</p>`}
          <div id="sourceSelected">${renderSelectedSource(related.source)}</div>
        </div>
        <label class="metadata-field metadata-field-wide">
          <span>Notes de suivi</span>
          <textarea id="workflowNotes" rows="3" ${appMode.readOnly ? "disabled" : ""}>${escapeHtml(item.workflow_notes || "")}</textarea>
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
            <textarea data-meta="${escapeHtml(key)}" rows="3" ${appMode.readOnly ? "disabled" : ""}>${escapeHtml(value)}</textarea>
          </label>`;
      }
      return `
        <label class="metadata-field">
          <span>${escapeHtml(label)}</span>
          <input data-meta="${escapeHtml(key)}" type="${type}" ${type === "number" ? 'min="0" max="1" step="0.01"' : ""} value="${escapeAttr(value)}" ${appMode.readOnly ? "disabled" : ""}>
        </label>`;
    })
    .join("");

  return `
    <section class="metadata-editor">
      <div class="metadata-header">
        <h3>Métadonnées chrétiennes</h3>
        <div class="metadata-actions">
          ${appMode.readOnly ? "" : '<button id="suggestTitleButton" type="button" class="btn-secondary" title="Construit un titre publiable à partir du nom de fichier et des champs déjà renseignés">Proposer un titre</button>'}
          ${appMode.readOnly ? "" : '<button id="saveMetadataButton" type="button">Save metadata</button>'}
        </div>
      </div>
      <p id="titleSuggestionStatus" class="filters-status"></p>
      <p class="metadata-hint">Sépare les listes par des points-virgules : foi; repentance; grâce.</p>
      <div class="metadata-grid">${fields}</div>
      <div class="metadata-terms">
        <h4>Lexique associé</h4>
        <div class="term-list">${termBadges}</div>
      </div>
      <p id="metadataSaveStatus" class="filters-status"></p>
    </section>`;
}

async function suggestTitle() {
  const button = document.getElementById("suggestTitleButton");
  const status = document.getElementById("titleSuggestionStatus");
  const field = els.detailContent.querySelector('[data-meta="editorial_title"]');
  const fileId = els.detailContent.dataset.fileId;
  if (!button || !field || !fileId) return;

  button.disabled = true;
  status.textContent = "Génération...";
  try {
    const response = await fetch(`/api/videos/${encodeURIComponent(fileId)}/title-suggestion`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (!data.title) {
      status.textContent =
        "Aucun titre proposable depuis le nom du fichier. Renseignez le thème, l'intervenant ou la date, puis relancez.";
      return;
    }
    // On remplit le champ sans enregistrer : la validation reste humaine.
    field.value = data.title;
    const labels = { high: "exploitable", partial: "à relire", none: "incomplet" };
    const extra = data.is_cut && data.source_title ? ` Contexte hérité de : « ${data.source_title} ».` : "";
    const notes = (data.notes || []).length ? ` ${data.notes.join(" ")}` : "";
    status.textContent =
      `Proposition ${labels[data.confidence] || data.confidence} (${data.title.length} caractères).` +
      `${extra}${notes} Rien n'est enregistré tant que vous n'avez pas cliqué sur « Save metadata ».`;
  } catch (error) {
    status.textContent = `Proposition impossible : ${error.message}`;
  } finally {
    button.disabled = false;
  }
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
    await Promise.all([loadVideos(), loadStats(), loadWorkflowStats(), loadFilters()]);
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
    els.trackingFilter,
    els.assigneeFilter,
  ].filter(Boolean).forEach((select) => {
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
  document.querySelectorAll(".tracking-card").forEach((card) => card.classList.remove("active"));
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
  els.trackingFilter,
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

els.trackingCards.addEventListener("click", (event) => {
  const card = event.target.closest(".tracking-card");
  if (!card) return;
  const nextValue = els.trackingFilter.value === card.dataset.tracking ? "" : card.dataset.tracking;
  els.trackingFilter.value = nextValue;
  document.querySelectorAll(".tracking-card").forEach((item) => {
    item.classList.toggle("active", item.dataset.tracking === nextValue);
  });
  state.page = 1;
  loadVideos();
});

els.trackingFilter.addEventListener("change", () => {
  document.querySelectorAll(".tracking-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.tracking === els.trackingFilter.value);
  });
});

els.resetFilters.addEventListener("click", resetFilters);
if (els.openUsersButton) {
  els.openUsersButton.addEventListener("click", () => {
    els.usersDialog.showModal();
    loadUsersPanel();
  });
  els.usersContent.addEventListener("change", (event) => {
    if (event.target.classList.contains("user-name-input")) saveUserName(event.target);
  });
  els.usersContent.addEventListener("click", (event) => {
    const button = event.target.closest(".toggle-user-btn");
    if (button) {
      event.preventDefault();
      toggleUser(button);
    }
  });
}
if (els.assigneeFilter) {
  els.assigneeFilter.addEventListener("change", () => {
    state.page = 1;
    loadVideos();
  });
}
if (els.openRelinkQueue) {
  els.openRelinkQueue.addEventListener("click", () => {
    els.relinkDialog.showModal();
    loadRelinkQueue();
  });
  els.relinkContent.addEventListener("click", (event) => {
    const option = event.target.closest(".relink-option");
    if (option) {
      event.preventDefault();
      confirmRelink(option);
    }
  });
}
if (els.fillTitlesButton) {
  els.fillTitlesButton.addEventListener("click", previewFillTitles);
  els.fillTitlesStatus.addEventListener("click", (event) => {
    if (event.target.id === "confirmFillTitles") applyFillTitles();
    if (event.target.id === "cancelFillTitles") {
      els.fillTitlesStatus.hidden = true;
      els.fillTitlesStatus.innerHTML = "";
    }
  });
}
els.exportExcelButton.addEventListener("click", () =>
  downloadExport(els.exportExcelButton, "/api/export/inventory.xlsx", "inventaire.xlsx")
);
els.exportCsvButton.addEventListener("click", () =>
  downloadExport(els.exportCsvButton, "/api/export/videos.csv", "inventaire.csv")
);
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
  if (target.classList.contains("copy-id-btn")) {
    copyInternalId(target);
    return;
  }
  if (target.classList.contains("detail-btn")) {
    showDetails(target.dataset.id);
  }
});

els.detailContent.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.id === "saveAssigneeButton") {
    saveAssignee();
    return;
  }
  if (target.id === "suggestTitleButton") {
    suggestTitle();
    return;
  }
  if (target.id === "saveMetadataButton") {
    saveMetadata();
  }
  if (target.id === "saveWorkflowButton") {
    saveWorkflow();
  }
  if (target.id === "saveLabelsButton") {
    saveLabels();
  }
  if (target.id === "transcribeButton") {
    queueTranscription();
  }
  if (target.classList.contains("copy-id-btn")) {
    copyInternalId(target);
    return;
  }
  const sourceResult = target.closest(".source-result");
  if (sourceResult) {
    selectSourceVideo(sourceResult);
    return;
  }
  if (target.id === "clearSourceButton") {
    clearSourceVideo();
    return;
  }
  if (target.classList.contains("related-video-btn")) {
    showDetails(target.dataset.relatedId);
  }
});

// Recherche de source : saisie décalée pour ne pas requêter à chaque frappe.
els.detailContent.addEventListener("input", (event) => {
  if (event.target.id !== "sourceSearchInput") return;
  const term = event.target.value;
  window.clearTimeout(sourceSearchTimer);
  sourceSearchTimer = window.setTimeout(() => runSourceSearch(term), 250);
});

els.detailContent.addEventListener("focusin", (event) => {
  if (event.target.id !== "sourceSearchInput") return;
  const results = document.getElementById("sourceSearchResults");
  if (results && !results.innerHTML) runSourceSearch("");
});

els.detailContent.addEventListener("change", (event) => {
  if (event.target.id === "workflowAssetType") {
    document.getElementById("sourceVideoField").hidden = event.target.value !== "cut";
  }
});

async function triggerFolderScan() {
  els.scanFolderStatus.textContent = "Démarrage de la synchronisation...";
  els.scanFolderButton.disabled = true;
  try {
    const response = await fetch("/api/scan-folder", {
      method: "POST",
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${response.status}`);
    }
    await pollFolderScan();
  } catch (error) {
    els.scanFolderStatus.textContent = `Échec du scan : ${error.message}`;
    els.scanFolderButton.disabled = false;
  }
}

async function pollFolderScan() {
  const response = await fetch("/api/scan-folder/status");
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${response.status}`);
  }
  const data = await response.json();
  if (data.status === "running") {
    els.scanFolderStatus.textContent = "Synchronisation en cours...";
    window.setTimeout(() => pollFolderScan().catch(handleScanPollingError), 2000);
    return;
  }
  if (data.status === "succeeded") {
    const linked = data.cuts_linked
      ? ` ${data.cuts_linked} découpe(s) rattachée(s) automatiquement.`
      : "";
    els.scanFolderStatus.textContent = `Terminé : ${data.videos_indexed} ajoutées ou mises à jour, ${data.videos_skipped} inchangées, ${data.errors} erreurs.${linked}`;
    els.scanFolderButton.disabled = false;
    await Promise.all([loadStats(), loadWorkflowStats(), loadVideos(), loadFilters()]);
    return;
  }
  if (data.status === "failed") {
    throw new Error(data.message || "Le scan Drive a échoué");
  }
  if (data.status === "interrupted") {
    els.scanFolderStatus.textContent =
      data.message || "Le scan précédent a été interrompu. Relancez-le pour terminer l'indexation.";
    els.scanFolderButton.disabled = false;
    return;
  }
  els.scanFolderButton.disabled = false;
}

// Surface a scan that a restart killed, without waiting for the user to click.
async function showLastScanState() {
  try {
    const response = await fetch("/api/scan-folder/status");
    if (!response.ok) return;
    const data = await response.json();
    if (data.status === "interrupted" || data.status === "failed") {
      els.scanFolderStatus.textContent =
        data.message || "Le dernier scan Drive ne s'est pas terminé.";
    }
  } catch (error) {
    // A missing or forbidden status endpoint is not worth surfacing on load.
  }
}

function handleScanPollingError(error) {
  els.scanFolderStatus.textContent = `Échec du scan : ${error.message}`;
  els.scanFolderButton.disabled = false;
}

if (els.scanFolderButton && !appMode.readOnly) {
  els.scanFolderButton.addEventListener("click", triggerFolderScan);
  showLastScanState();
  pollFolderScan().catch(handleScanPollingError);
}

if (els.logoutButton) {
  els.logoutButton.addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  });
}

if (els.helpButton && els.helpDialog) {
  els.helpButton.addEventListener("click", () => els.helpDialog.showModal());
}

async function loadDriveConfig() {
  const response = await fetch("/api/admin/drive-folder");
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  const folderName = data.folder_name || data.folder_id || "Aucun dossier configuré";
  els.driveCurrentFolder.innerHTML = `
    <strong>${escapeHtml(folderName)}</strong>
    ${data.folder_url ? `<a href="${escapeAttr(data.folder_url)}" target="_blank" rel="noopener">Ouvrir dans Drive</a>` : ""}
    <span>${data.updated_by_email ? `Configuré par ${escapeHtml(data.updated_by_email)} · ${escapeHtml(formatDate(data.updated_at))}` : "Aucune configuration enregistrée dans l’application"}</span>
    ${data.last_scan_at ? `<span>Dernière synchronisation : ${escapeHtml(formatDate(data.last_scan_at))} · ${escapeHtml(data.last_scan_status || "")}</span>` : ""}
  `;
  els.driveFolderInput.value = data.folder_url || data.folder_id || "";
}

async function testDriveFolder(value) {
  const response = await fetch("/api/admin/drive-folder/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ folder_url_or_id: value }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

if (appMode.superAdmin && els.openDriveConfigButton) {
  els.openDriveConfigButton.addEventListener("click", async () => {
    els.driveConfigDialog.showModal();
    els.driveConfigStatus.textContent = "";
    try {
      await loadDriveConfig();
    } catch (error) {
      els.driveConfigStatus.textContent = error.message;
    }
  });

  els.testDriveFolderButton.addEventListener("click", async () => {
    els.driveConfigStatus.textContent = "Vérification de l’accès...";
    try {
      const folder = await testDriveFolder(els.driveFolderInput.value);
      els.driveConfigStatus.textContent = `Accès confirmé : ${folder.folder_name}`;
    } catch (error) {
      els.driveConfigStatus.textContent = error.message;
    }
  });

  els.saveDriveFolderButton.addEventListener("click", async () => {
    const value = els.driveFolderInput.value.trim();
    if (!value) {
      els.driveConfigStatus.textContent = "Saisis une URL ou un identifiant Drive.";
      return;
    }
    if (!window.confirm("Remplacer le dossier Drive principal par ce dossier ?")) return;
    els.driveConfigStatus.textContent = "Vérification et enregistrement...";
    const response = await fetch("/api/admin/drive-folder", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_url_or_id: value }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      els.driveConfigStatus.textContent = data.detail || `HTTP ${response.status}`;
      return;
    }
    els.driveConfigStatus.textContent = `Dossier principal enregistré : ${data.folder_name}`;
    await loadDriveConfig();
    window.setTimeout(() => window.location.reload(), 700);
  });

  els.searchDriveFoldersButton.addEventListener("click", async () => {
    const query = els.driveFolderSearchInput.value.trim();
    if (!query) return;
    els.driveFolderSearchResults.innerHTML = `<p class="filters-status">Recherche...</p>`;
    const response = await fetch(`/api/admin/drive-folders/search?q=${encodeURIComponent(query)}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      els.driveFolderSearchResults.innerHTML = `<p class="filters-status">${escapeHtml(data.detail || `HTTP ${response.status}`)}</p>`;
      return;
    }
    els.driveFolderSearchResults.innerHTML = (data.items || []).map((folder) => `
      <button type="button" class="drive-folder-result" data-folder-url="${escapeAttr(folder.folder_url)}">
        <strong>${escapeHtml(folder.folder_name)}</strong>
        <span>${folder.shared_drive ? "Drive partagé" : "Dossier partagé"}</span>
      </button>
    `).join("") || `<p class="filters-status">Aucun dossier trouvé.</p>`;
  });

  els.driveFolderSearchResults.addEventListener("click", (event) => {
    const result = event.target.closest(".drive-folder-result");
    if (!result) return;
    els.driveFolderInput.value = result.dataset.folderUrl;
    els.driveConfigStatus.textContent = "Dossier sélectionné. Teste l’accès puis enregistre.";
  });
}

async function init() {
  await Promise.all([
    loadStats(),
    loadWorkflowStats(),
    loadFilters(),
    loadVideos(),
    populateAssigneeFilter(),
  ]);
}

init();
