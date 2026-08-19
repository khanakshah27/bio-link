/* =========================================================================
   BioLink frontend logic (vanilla JS, no framework).
   Talks to the FastAPI backend at /api/papers/*.
   ========================================================================= */

const API_BASE = "/api/papers";

const TYPE_COLORS = {
  gene: "#9B87C4", protein: "#E39FC2", disease: "#E7A3A3",
  pathway: "#EFC583", organism: "#8FC7B8", chemical: "#8FB6D9",
  snp: "#C9A6E5", database: "#C7C0D6",
};
const TYPE_LABELS = {
  gene: "Gene", protein: "Protein", disease: "Disease", pathway: "Pathway",
  organism: "Organism", chemical: "Chemical", snp: "SNP", database: "Database source",
};

let state = {
  paper: null,      // full PaperOut from API
  selectedType: "all",
  selectedEntityId: null,
  cy: null,
  demo: false,
};

// ---------------------------------------------------------------------
// Sample dataset — lets the Dashboard / Graph / Databases / Summary tabs
// be explored without a PDF upload or a running backend.
// ---------------------------------------------------------------------
const DEMO_PAPER = {
  id: "demo-brca1",
  filename: "brca1_tp53_dna_damage_review.pdf",
  status: "complete",
  error_message: null,
  summary: "This review synthesizes recent findings on BRCA1 and TP53 in hereditary breast and ovarian cancer. Pathogenic BRCA1 variants, including the loss-of-function allele rs80357382, impair homologous recombination within the DNA damage response pathway and are linked to substantially elevated lifetime risk of both breast and ovarian cancer. Downstream, TP53-encoded p53 interacts with MDM2 in a negative feedback loop and induces CDKN1A (p21) to enforce cell-cycle arrest after DNA damage. BRCA1-deficient tumors also showed increased sensitivity to cisplatin, suggesting a therapeutic window for platinum-based chemotherapy in this patient population.",
  entities: [
    { id: "e1", text: "BRCA1", entity_type: "gene", normalized_id: "NCBI:672", confidence: 0.97,
      sentence: "Mutations in BRCA1 are strongly associated with hereditary breast and ovarian cancer syndrome.",
      records: [
        { source: "ncbi_gene", status: "ok", payload: { description: "BRCA1 DNA repair associated" } },
        { source: "clinvar", status: "ok", payload: { variant_count_reported: 6120 } },
      ] },
    { id: "e2", text: "TP53", entity_type: "gene", normalized_id: "NCBI:7157", confidence: 0.95,
      sentence: "TP53 mutations were detected in 38% of tumor samples, consistent with its role as a tumor suppressor.",
      records: [
        { source: "ncbi_gene", status: "ok", payload: { description: "tumor protein p53" } },
        { source: "kegg", status: "offline_fallback", payload: { pathways: [{ name: "p53 signaling pathway" }] } },
      ] },
    { id: "e11", text: "CDKN1A", entity_type: "gene", normalized_id: "NCBI:1026", confidence: 0.88,
      sentence: "Downstream, CDKN1A (p21) expression was upregulated 4.2-fold following p53 stabilization.",
      records: [
        { source: "ncbi_gene", status: "ok", payload: { description: "cyclin dependent kinase inhibitor 1A" } },
      ] },
    { id: "e3", text: "p53", entity_type: "protein", normalized_id: "UniProt:P04637", confidence: 0.94,
      sentence: "The p53 protein accumulates in response to DNA damage and triggers apoptosis or cell-cycle arrest.",
      records: [
        { source: "uniprot", status: "ok", payload: { protein_name: "Cellular tumor antigen p53" } },
        { source: "pdb", status: "ok", payload: { structures: ["1TUP", "2XWR", "4HJE"] } },
        { source: "string", status: "ok", payload: { partners: ["MDM2", "CDKN1A", "BAX"] } },
      ] },
    { id: "e4", text: "MDM2", entity_type: "protein", normalized_id: "UniProt:Q00987", confidence: 0.9,
      sentence: "MDM2 binds p53 and targets it for proteasomal degradation, forming a key negative feedback loop.",
      records: [
        { source: "uniprot", status: "ok", payload: { protein_name: "E3 ubiquitin-protein ligase Mdm2" } },
        { source: "string", status: "ok", payload: { partners: ["TP53", "CDKN1A"] } },
      ] },
    { id: "e5", text: "breast cancer", entity_type: "disease", normalized_id: "MESH:D001943", confidence: 0.93,
      sentence: "Patients carrying pathogenic BRCA1 variants have up to a 72% lifetime risk of developing breast cancer.",
      records: [
        { source: "clinvar", status: "ok", payload: { variant_count_reported: 6120 } },
      ] },
    { id: "e6", text: "ovarian cancer", entity_type: "disease", normalized_id: "MESH:D010051", confidence: 0.91,
      sentence: "The same cohort showed a 44% lifetime risk of ovarian cancer among BRCA1 carriers.",
      records: [
        { source: "clinvar", status: "offline_fallback", payload: { variant_count_reported: 1830 } },
      ] },
    { id: "e7", text: "DNA damage response", entity_type: "pathway", normalized_id: "KEGG:hsa04115", confidence: 0.86,
      sentence: "BRCA1 functions within the DNA damage response pathway, coordinating homologous recombination repair.",
      records: [
        { source: "kegg", status: "ok", payload: { pathways: [{ name: "p53 signaling pathway" }, { name: "Homologous recombination" }] } },
      ] },
    { id: "e8", text: "Homo sapiens", entity_type: "organism", normalized_id: "NCBITaxon:9606", confidence: 0.99,
      sentence: "All samples were derived from primary tumor tissue in Homo sapiens patients enrolled at three clinical sites.",
      records: [] },
    { id: "e9", text: "cisplatin", entity_type: "chemical", normalized_id: "CHEBI:27899", confidence: 0.82,
      sentence: "Tumors with BRCA1 loss-of-function showed increased sensitivity to cisplatin in vitro.",
      records: [
        { source: "go", status: "unavailable", payload: null },
      ] },
    { id: "e10", text: "rs80357382", entity_type: "snp", normalized_id: "dbSNP:rs80357382", confidence: 0.89,
      sentence: "The pathogenic variant rs80357382 introduces a premature stop codon in exon 11 of BRCA1.",
      records: [
        { source: "clinvar", status: "ok", payload: { variant_count_reported: 412 } },
      ] },
  ],
  relationships: [
    { source_entity_id: "e1", target_entity_id: "e5", relation_type: "associated_with", confidence: 0.93 },
    { source_entity_id: "e1", target_entity_id: "e7", relation_type: "participates_in", confidence: 0.88 },
    { source_entity_id: "e2", target_entity_id: "e3", relation_type: "encodes", confidence: 0.95 },
    { source_entity_id: "e3", target_entity_id: "e4", relation_type: "interacts_with", confidence: 0.9 },
    { source_entity_id: "e3", target_entity_id: "e11", relation_type: "regulates", confidence: 0.85 },
    { source_entity_id: "e1", target_entity_id: "e6", relation_type: "associated_with", confidence: 0.9 },
    { source_entity_id: "e10", target_entity_id: "e1", relation_type: "variant_of", confidence: 0.97 },
    { source_entity_id: "e9", target_entity_id: "e1", relation_type: "sensitizes", confidence: 0.8 },
  ],
};

// ---------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------
function goToTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tabName));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("active", v.id === `view-${tabName}`));
  if (tabName === "graph" && state.paper) renderGraph();
}

document.getElementById("tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab-btn");
  if (!btn || btn.disabled) return;
  goToTab(btn.dataset.tab);
});

function enableTabs(enabled) {
  document.querySelectorAll(".tab-btn").forEach(b => {
    if (b.dataset.tab !== "upload") b.disabled = !enabled;
  });
}

// ---------------------------------------------------------------------
// Upload + dropzone
// ---------------------------------------------------------------------
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("fileInput");
const browseBtn = document.getElementById("browseBtn");
const fileNameEl = document.getElementById("fileName");
const uploadError = document.getElementById("uploadError");
const pipelineEl = document.getElementById("pipeline");
const demoBtn = document.getElementById("demoBtn");

demoBtn.addEventListener("click", () => loadDemoPaper());

function loadDemoPaper() {
  uploadError.hidden = true;
  state.demo = true;
  state.paper = JSON.parse(JSON.stringify(DEMO_PAPER));
  state.selectedType = "all";
  state.selectedEntityId = null;
  document.getElementById("paperSelect").value = "";
  loadPaperIntoUI(state.paper);
  enableTabs(true);
  goToTab("dashboard");
}

browseBtn.addEventListener("click", (e) => { e.stopPropagation(); fileInput.click(); });
dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => { if (fileInput.files[0]) handleFile(fileInput.files[0]); });

["dragover", "dragenter"].forEach(evt =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); })
);
["dragleave", "drop"].forEach(evt =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});

function setPipelineStep(stepName, statusClass) {
  document.querySelectorAll(".pipeline-step").forEach(s => {
    if (s.dataset.step === stepName) {
      s.classList.remove("active", "done");
      s.classList.add(statusClass);
    } else if (statusClass === "done") {
      // steps before this one, if not already done, become done too
    }
  });
}

async function animatePipeline() {
  const steps = ["upload", "extract", "ner", "link", "graph"];
  pipelineEl.hidden = false;
  for (let i = 0; i < steps.length; i++) {
    steps.forEach((s, j) => {
      const el = document.querySelector(`.pipeline-step[data-step="${s}"]`);
      el.classList.remove("active", "done");
      if (j < i) el.classList.add("done");
      if (j === i) el.classList.add("active");
    });
    await new Promise(r => setTimeout(r, 550));
  }
}

async function handleFile(file) {
  uploadError.hidden = true;
  state.demo = false;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showUploadError("Only PDF files are supported.");
    return;
  }
  fileNameEl.textContent = `Selected: ${file.name}`;
  browseBtn.disabled = true;

  const pipelineAnim = animatePipeline();

  const formData = new FormData();
  formData.append("file", file);

  try {
    const resp = await fetch(`${API_BASE}/upload`, { method: "POST", body: formData });
    await pipelineAnim; // let the animation finish so it doesn't feel jumpy
    document.querySelectorAll(".pipeline-step").forEach(s => { s.classList.remove("active"); s.classList.add("done"); });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: "Upload failed." }));
      throw new Error(err.detail || "Upload failed.");
    }
    const paper = await resp.json();
    if (paper.status === "error") {
      throw new Error(paper.error_message || "Processing failed.");
    }
    state.paper = paper;
    await refreshPaperList(paper.id);
    loadPaperIntoUI(paper);
    enableTabs(true);
    setTimeout(() => goToTab("dashboard"), 400);
  } catch (err) {
    showUploadError(err.message);
    pipelineEl.hidden = true;
  } finally {
    browseBtn.disabled = false;
  }
}

function showUploadError(msg) {
  uploadError.textContent = msg;
  uploadError.hidden = false;
}

// ---------------------------------------------------------------------
// Past papers dropdown
// ---------------------------------------------------------------------
async function refreshPaperList(selectId) {
  try {
    const resp = await fetch(API_BASE);
    const papers = await resp.json();
    const select = document.getElementById("paperSelect");
    select.innerHTML = '<option value="">— past papers —</option>';
    papers.forEach(p => {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = `${p.filename} (${p.status})`;
      if (p.id === selectId) opt.selected = true;
      select.appendChild(opt);
    });
  } catch (e) { /* non-fatal */ }
}

document.getElementById("paperSelect").addEventListener("change", async (e) => {
  const id = e.target.value;
  if (!id) return;
  const resp = await fetch(`${API_BASE}/${id}`);
  if (!resp.ok) return;
  const paper = await resp.json();
  state.demo = false;
  state.paper = paper;
  loadPaperIntoUI(paper);
  enableTabs(true);
  goToTab("dashboard");
});

refreshPaperList();

// ---------------------------------------------------------------------
// Dashboard rendering
// ---------------------------------------------------------------------
function loadPaperIntoUI(paper) {
  document.getElementById("paperTitle").textContent = paper.filename;
  document.getElementById("nerBadge").textContent = state.demo
    ? "Demo dataset — sample entities & database records for illustration, not the paper's real content."
    : describeExtraction(paper.entities);
  renderStatGrid(paper.entities);
  renderEntityFilter(paper.entities);
  renderEntityList(paper.entities);
  renderSummary(paper.summary);
  renderDatabaseTable(paper.entities);
  state.cy = null; // force graph rebuild next time graph tab opens
}

function describeExtraction(entities) {
  const n = entities.length;
  if (n === 0) return "No entities were extracted from this paper.";
  const avgConf = Math.round((entities.reduce((sum, e) => sum + (e.confidence || 0), 0) / n) * 100);
  return `${n} entit${n === 1 ? "y" : "ies"} extracted · avg NER confidence ${avgConf}%`;
}

function renderStatGrid(entities) {
  const counts = {};
  entities.forEach(e => { counts[e.entity_type] = (counts[e.entity_type] || 0) + 1; });
  const grid = document.getElementById("statGrid");
  grid.innerHTML = "";
  Object.keys(TYPE_LABELS).filter(t => t !== "database").forEach(type => {
    const card = document.createElement("div");
    card.className = "stat-card";
    card.style.setProperty("--accent", TYPE_COLORS[type]);
    card.innerHTML = `<div class="num">${counts[type] || 0}</div><div class="lbl">${TYPE_LABELS[type]}${(counts[type] || 0) === 1 ? "" : "s"}</div>`;
    grid.appendChild(card);
  });
}

function renderEntityFilter(entities) {
  const types = ["all", ...new Set(entities.map(e => e.entity_type))];
  const wrap = document.getElementById("entityFilter");
  wrap.innerHTML = "";
  types.forEach(type => {
    const chip = document.createElement("button");
    chip.className = "filter-chip" + (type === state.selectedType ? " active" : "");
    chip.textContent = type === "all" ? "All" : TYPE_LABELS[type];
    chip.style.color = type === "all" ? "" : TYPE_COLORS[type];
    chip.addEventListener("click", () => {
      state.selectedType = type;
      renderEntityFilter(entities);
      renderEntityList(entities);
    });
    wrap.appendChild(chip);
  });
}

function renderEntityList(entities) {
  const list = document.getElementById("entityList");
  list.innerHTML = "";
  const filtered = state.selectedType === "all" ? entities : entities.filter(e => e.entity_type === state.selectedType);
  if (filtered.length === 0) {
    list.innerHTML = '<p class="placeholder">No entities of this type were found.</p>';
    return;
  }
  filtered.forEach(e => {
    const row = document.createElement("div");
    row.className = "entity-row" + (e.id === state.selectedEntityId ? " selected" : "");
    row.innerHTML = `
      <span class="entity-swatch" style="background:${TYPE_COLORS[e.entity_type]}"></span>
      <span class="txt">${escapeHtml(e.text)}</span>
      <span class="type">${TYPE_LABELS[e.entity_type]}</span>
    `;
    row.addEventListener("click", () => {
      state.selectedEntityId = e.id;
      renderEntityList(entities);
      showEvidence(e);
    });
    list.appendChild(row);
  });
}

function showEvidence(entity) {
  const el = document.getElementById("evidenceText");
  if (!entity.sentence) {
    el.innerHTML = '<p class="placeholder">No sentence-level evidence recorded for this entity.</p>';
    return;
  }
  const re = new RegExp(escapeRegExp(entity.text), "gi");
  const highlighted = escapeHtml(entity.sentence).replace(
    new RegExp(escapeRegExp(escapeHtml(entity.text)), "gi"),
    (m) => `<mark>${m}</mark>`
  );
  el.innerHTML = `<p>…${highlighted}…</p>`;
}

function renderSummary(summary) {
  document.getElementById("summaryText").textContent = summary || "No summary was generated for this paper.";
}

// ---------------------------------------------------------------------
// Database records table
// ---------------------------------------------------------------------
function renderDatabaseTable(entities) {
  const body = document.getElementById("dbTableBody");
  body.innerHTML = "";
  let rows = 0;
  entities.forEach(e => {
    (e.records || []).forEach(r => {
      rows++;
      const tr = document.createElement("tr");
      const resultText = r.payload ? summarizePayload(r.payload) : "—";
      tr.innerHTML = `
        <td class="gene-cell">${escapeHtml(e.text)}</td>
        <td><span style="color:${TYPE_COLORS[e.entity_type]}">${TYPE_LABELS[e.entity_type]}</span></td>
        <td>${escapeHtml(sourceLabel(r.source))}</td>
        <td>${statusPill(r.status)}</td>
        <td>${escapeHtml(resultText)}</td>
      `;
      body.appendChild(tr);
    });
  });
  if (rows === 0) {
    body.innerHTML = '<tr><td colspan="5" class="placeholder" style="padding:20px;">No database records were retrieved.</td></tr>';
  }
}

function sourceLabel(source) {
  const map = { ncbi_gene: "NCBI Gene", uniprot: "UniProt", pdb: "PDB", string: "STRING", go: "Gene Ontology", kegg: "KEGG", clinvar: "ClinVar" };
  return map[source] || source;
}

function statusPill(status) {
  const label = { ok: "live", offline_fallback: "cached", unavailable: "unavailable" }[status] || status;
  return `<span class="status-pill status-${status}"><span class="dot" style="background:currentColor"></span>${label}</span>`;
}

function summarizePayload(payload) {
  if (payload.protein_name) return payload.protein_name;
  if (payload.description) return payload.description;
  if (payload.structures) return `${payload.structures.length} structure(s): ${payload.structures.join(", ")}`;
  if (payload.partners) return `Interacts with: ${payload.partners.join(", ")}`;
  if (payload.pathways) return payload.pathways.map(p => p.name).join(", ");
  if (payload.biological_process) return payload.biological_process.join(", ");
  if (payload.variant_count_reported) return `${payload.variant_count_reported} variant(s) reported`;
  return JSON.stringify(payload).slice(0, 90);
}

// ---------------------------------------------------------------------
// Cytoscape knowledge graph
// ---------------------------------------------------------------------

// Mirrors the backend's build_graph() so the graph can be drawn from data
// already loaded on the page, without a second round trip to the API
// (and so it also works for the client-only demo dataset).
function buildGraphElements(entities, relationships) {
  const nodes = [];
  const edges = [];
  const seenDbNodes = new Set();

  (entities || []).forEach(e => {
    nodes.push({ data: { id: e.id, label: e.text, type: e.entity_type, normalized_id: e.normalized_id, confidence: e.confidence } });
    (e.records || []).forEach(rec => {
      if (rec.status !== "ok" && rec.status !== "offline_fallback") return;
      const dbNodeId = `db::${rec.source}::${e.normalized_id || e.text}`;
      if (!seenDbNodes.has(dbNodeId)) {
        seenDbNodes.add(dbNodeId);
        nodes.push({ data: { id: dbNodeId, label: sourceLabel(rec.source), type: "database", source: rec.source } });
      }
      edges.push({ data: { id: `edge::${e.id}::${dbNodeId}`, source: e.id, target: dbNodeId, label: "linked in", edge_type: "database_link" } });
    });
  });

  (relationships || []).forEach((r, i) => {
    edges.push({ data: {
      id: r.id || `rel::${i}`,
      source: r.source_entity_id,
      target: r.target_entity_id,
      label: (r.relation_type || "").replace(/_/g, " "),
      edge_type: "relationship",
    } });
  });

  return [...nodes, ...edges];
}

function renderGraph() {
  if (!state.paper) return;
  const legend = document.getElementById("graphLegend");
  legend.innerHTML = Object.keys(TYPE_LABELS).map(t =>
    `<div class="legend-item"><span class="dot" style="background:${TYPE_COLORS[t]}"></span>${TYPE_LABELS[t]}</div>`
  ).join("");

  const elements = buildGraphElements(state.paper.entities, state.paper.relationships);

  if (state.cy) state.cy.destroy();

  state.cy = cytoscape({
    container: document.getElementById("cy"),
    elements,
    style: [
      {
        selector: "node",
        style: {
          "background-color": (n) => TYPE_COLORS[n.data("type")] || "#C7C0D6",
          "label": "data(label)",
          "font-family": "Karla, sans-serif",
          "font-size": 11,
          "font-weight": 600,
          "color": "#3A3247",
          "text-valign": "bottom",
          "text-margin-y": 6,
          "width": (n) => n.data("type") === "database" ? 22 : 34,
          "height": (n) => n.data("type") === "database" ? 22 : 34,
          "border-width": 2,
          "border-color": "#ffffff",
          "text-outline-width": 2,
          "text-outline-color": "#FBF7F1",
        },
      },
      {
        selector: "edge",
        style: {
          "width": 1.6,
          "line-color": "#D9CCEF",
          "target-arrow-color": "#D9CCEF",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          "label": "data(label)",
          "font-size": 8,
          "color": "#9B87C4",
          "text-background-color": "#FBF7F1",
          "text-background-opacity": 1,
          "text-background-padding": 2,
        },
      },
      {
        selector: "edge[edge_type = 'database_link']",
        style: { "line-style": "dashed", "line-color": "#E4D9F7", "target-arrow-color": "#E4D9F7", "width": 1 },
      },
      {
        selector: "node:selected",
        style: { "border-color": "#5B3E8C", "border-width": 3 },
      },
    ],
    layout: { name: "cose", animate: true, padding: 40, nodeRepulsion: 9000, idealEdgeLength: 90 },
  });

  state.cy.on("tap", "node", (evt) => showGraphDetail(evt.target));
}

function showGraphDetail(node) {
  const d = node.data();
  const detail = document.getElementById("graphDetail");
  if (d.type === "database") {
    detail.innerHTML = `<h4>${escapeHtml(d.label)}</h4><div class="kv">External database source node.</div>`;
    return;
  }
  const entity = (state.paper.entities || []).find(e => e.id === d.id);
  let html = `<h4>${escapeHtml(d.label)}</h4>
    <div class="kv"><b>Type:</b> ${TYPE_LABELS[d.type] || d.type}</div>
    <div class="kv"><b>Normalized:</b> ${escapeHtml(d.normalized_id || "—")}</div>`;
  if (entity && entity.records && entity.records.length) {
    html += `<div class="kv" style="margin-top:10px;"><b>Linked databases:</b></div>`;
    entity.records.forEach(r => {
      html += `<div class="kv">${sourceLabel(r.source)} — ${statusPill(r.status)}</div>`;
    });
  }
  detail.innerHTML = html;
}

// ---------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------
function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeRegExp(str) {
  return String(str).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
