let currentCaseId = null;
let currentDataType = 'cdr';
let cy = null;
let imeiCy = null;          // second graph instance for the IMEI / SIM-swap view
let profileChart = null;
let movementMap = null;
let lastRecords = [];       // most recent /cdrs result (for data-preview table)

// Cache of visuals captured AT RENDER TIME (while the view is visible & sized).
// Graphs/maps can't be snapshotted while their container is hidden on the
// Reports page, so we grab them the moment they are drawn and reuse them.
const visualCache = { network: null, imei: null, movement: null, profile: null };
// Data needed to redraw the movement markers onto the captured map canvas
// (leaflet-image rasterizes tiles + vector lines but NOT HTML divIcon pins).
let movementRenderCtx = null;

const API_BASE = (window.location.protocol === 'file:' || window.location.port === '5500')
    ? 'http://localhost:8001/api/v1' : '/api/v1';

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function toast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transition = 'opacity 0.4s';
        setTimeout(() => el.remove(), 400);
    }, 4000);
}

function displayTZ() {
    return document.getElementById('global-timezone')?.value || 'UTC';
}

// All backend times are UTC-normalized naive ISO strings — format them in the selected display TZ.
function fmtTime(isoStr) {
    if (!isoStr) return 'N/A';
    let s = String(isoStr);
    if (!s.endsWith('Z') && !s.includes('+')) s += 'Z'; // treat naive as UTC
    const d = new Date(s);
    if (isNaN(d)) return isoStr;
    return new Intl.DateTimeFormat('en-GB', {
        timeZone: displayTZ(),
        year: 'numeric', month: 'short', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    }).format(d);
}

function esc(str) {
    const div = document.createElement('div');
    div.textContent = str ?? '';
    return div.innerHTML;
}

function geoSourceLabel(source) {
    return {
        reference_csv: 'Reference CSV',
        opencellid: 'OpenCellID',
        roaming_centroid: 'Approx. (city)',
    }[source] || 'Unknown';
}

async function api(path, options = {}) {
    const res = await fetch(`${API_BASE}${path}`, options);
    if (!res.ok) {
        let detail = `Request failed (${res.status})`;
        try { detail = (await res.json()).detail || detail; } catch (e) {}
        throw new Error(detail);
    }
    return res.json();
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', initApp);

async function initApp() {
    setupTabNav();
    setupDropdowns();
    setupUpload();
    setupFilters();
    setupAdvancedTools();
    setupNewFeatures();
    initGraph();
    setupGraphInteractions();
    setupDataPreview();
    setupDetailModal();
    setupCrossAnalysis();
    setupImeiGraph();
    await loadCases();

    document.getElementById('toggle-create-case-btn').addEventListener('click', () => {
        const form = document.getElementById('create-case-form');
        form.style.display = form.style.display === 'none' ? 'flex' : 'none';
    });

    document.getElementById('submit-new-case-btn').addEventListener('click', async () => {
        const name = document.getElementById('new-case-name').value.trim();
        const number = document.getElementById('new-case-number').value.trim();
        const officer = document.getElementById('new-case-officer').value.trim() || 'Investigator';
        if (!name || !number) return toast('Case name and number are required', 'error');

        try {
            const newCase = await api('/cases', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ case_name: name, case_number: number, created_by: officer }),
            });
            toast(`Case "${name}" created`, 'success');
            selectCase(newCase);
        } catch (e) {
            toast(e.message, 'error');
        }
    });
}

// Distinct, high-contrast palette for community clusters (Gephi-style).
const COMMUNITY_COLORS = [
    '#F5C518', // gold
    '#3B82F6', // blue
    '#22C55E', // green
    '#A855F7', // purple
    '#EF4444', // red
    '#EC4899', // pink
    '#F97316', // orange
    '#14B8A6', // teal
    '#8B5CF6', // violet
    '#84CC16', // lime
    '#06B6D4', // cyan
    '#EAB308', // amber
];
const DEFAULT_NODE_COLOR = '#9CA3AF'; // grey for unclustered singletons

// Register the fcose force layout if the extension loaded.
if (window.cytoscape && window.cytoscapeFcose) {
    try { cytoscape.use(window.cytoscapeFcose); } catch (e) { /* already registered */ }
}

function initGraph() {
    cy = cytoscape({
        container: document.getElementById('cy-container'),
        wheelSensitivity: 0.25,
        minZoom: 0.15,
        maxZoom: 3,
        style: [
            {
                selector: 'node',
                style: {
                    // Colour = the community/cluster this entity belongs to.
                    'background-color': 'data(color)',
                    'background-opacity': 0.95,
                    'border-width': 1,
                    'border-color': 'rgba(255,255,255,0.85)',
                    'width': 'data(size)',
                    'height': 'data(size)',
                    'label': 'data(label)',
                    'color': '#111827',
                    'font-size': 'data(fontSize)',
                    'font-weight': 600,
                    'font-family': 'Inter, system-ui, sans-serif',
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'text-outline-color': '#ffffff',
                    'text-outline-width': 2,
                    'text-outline-opacity': 0.85,
                    'min-zoomed-font-size': 7,
                    'transition-property': 'background-color, border-color, width, height',
                    'transition-duration': '150ms',
                },
            },
            {
                // Emphasise the biggest hubs with a bold ring + white label.
                selector: 'node.hub',
                style: {
                    'border-width': 3,
                    'border-color': '#ffffff',
                    'color': '#0B1220',
                    'font-weight': 700,
                    'z-index': 50,
                },
            },
            {
                selector: 'node.highlight',
                style: {
                    'border-width': 5,
                    'border-color': '#111827',
                    'z-index': 999,
                },
            },
            {
                selector: 'node.selected',
                style: {
                    'border-width': 5,
                    'border-color': '#111827',
                    'z-index': 999,
                },
            },
            {
                selector: '.faded',
                style: { 'opacity': 0.06, 'text-opacity': 0.04 },
            },
            {
                selector: 'edge',
                style: {
                    'width': 'mapData(weight, 1, 12, 0.6, 5)',
                    // Edge inherits the source node's community colour, translucent —
                    // exactly the layered look of the reference graph.
                    'line-color': 'data(color)',
                    'target-arrow-color': 'data(color)',
                    'target-arrow-shape': 'triangle',
                    'arrow-scale': 0.7,
                    'curve-style': 'bezier',
                    'opacity': 0.35,
                    'label': 'data(weight)',
                    'font-size': '9px',
                    'font-family': 'JetBrains Mono, monospace',
                    'color': '#111827',
                    'text-background-color': '#ffffff',
                    'text-background-opacity': 0.85,
                    'text-background-padding': '1px',
                    'text-opacity': 0,
                },
            },
            {
                selector: 'edge.highlight',
                style: {
                    'opacity': 0.95,
                    'width': 'mapData(weight, 1, 12, 2.5, 9)',
                    'text-opacity': 1,
                    'z-index': 99,
                },
            },
        ],
        layout: fcoseLayout(),
    });

    cy.on('mouseover', 'edge', (e) => e.target.addClass('highlight'));
    cy.on('mouseout', 'edge', (e) => {
        if (!e.target.data('_pinned')) e.target.removeClass('highlight');
    });
}

// Organic force-directed layout (Gephi ForceAtlas-style). Falls back to a
// spaced cose layout if the fcose extension didn't load.
function fcoseLayout() {
    if (window.cytoscapeFcose) {
        return {
            name: 'fcose',
            quality: 'proof',
            animate: false,
            randomize: true,
            padding: 40,
            nodeSeparation: 90,
            idealEdgeLength: 90,
            nodeRepulsion: 9000,
            gravity: 0.25,
            gravityRange: 3.8,
            numIter: 2500,
            packComponents: true,
        };
    }
    return {
        name: 'cose', animate: false, padding: 40,
        nodeRepulsion: 12000, idealEdgeLength: 95, edgeElasticity: 120,
        gravity: 0.25, componentSpacing: 130, nodeOverlap: 20, randomize: true,
    };
}

// ---------------------------------------------------------------------------
// Cases
// ---------------------------------------------------------------------------

async function loadCases() {
    try {
        const cases = await api('/cases');
        const grid = document.getElementById('case-grid');
        grid.innerHTML = '';

        if (!cases.length) {
            grid.innerHTML = '<p class="muted" style="grid-column:1/-1;">No cases yet — create your first case above.</p>';
            return;
        }

        cases.forEach(c => {
            const card = document.createElement('div');
            card.className = 'case-card';
            card.innerHTML = `
                <button class="case-delete-btn" title="Delete case" data-id="${c.id}" data-name="${esc(c.case_name)}">&times;</button>
                <h3>${esc(c.case_name)}</h3>
                <p class="meta">FIR/ID: ${esc(c.case_number)}</p>
                <p class="meta">Opened ${new Date(c.created_at).toLocaleDateString()} · ${esc(c.created_by || '')}</p>
            `;
            card.querySelector('.case-delete-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                deleteCase(c.id, c.case_name);
            });
            card.addEventListener('click', () => selectCase(c));
            grid.appendChild(card);
        });
    } catch (e) {
        toast('Could not load cases — is the backend running?', 'error');
    }
}

function selectCase(caseObj) {
    currentCaseId = caseObj.id;
    document.getElementById('case-selection-overlay').style.display = 'none';
    document.getElementById('main-sidebar').style.display = 'flex';
    document.getElementById('main-content').style.display = 'flex';

    document.getElementById('sidebar-case-name').textContent = caseObj.case_name;
    document.getElementById('sidebar-case-number').textContent = `FIR/ID: ${caseObj.case_number}`;

    refreshSummary();
    const activeTab = document.querySelector('.nav-links li.active a');
    if (activeTab) activeTab.click();
}

async function deleteCase(caseId, caseName) {
    if (!confirm(`Delete case "${caseName}"?\n\nThis permanently removes all its CDRs, cell towers, anomalies and evidence logs. This cannot be undone.`)) {
        return;
    }
    try {
        const r = await api(`/cases/${caseId}`, { method: 'DELETE' });
        const removed = r.removed || {};
        toast(`Deleted "${caseName}" — ${removed.cdrs || 0} records removed`, 'success');
        // If the deleted case was open, return to the case-selection screen.
        if (currentCaseId === caseId) {
            currentCaseId = null;
            document.getElementById('main-sidebar').style.display = 'none';
            document.getElementById('main-content').style.display = 'none';
            document.getElementById('case-selection-overlay').style.display = 'flex';
        }
        loadCases();
    } catch (e) {
        toast(`Could not delete case: ${e.message}`, 'error');
    }
}

async function refreshSummary() {
    if (!currentCaseId) return;
    try {
        const s = await api(`/cases/${currentCaseId}/summary`);
        document.getElementById('stat-cdrs').textContent = s.cdr_count ?? 0;

        document.getElementById('stat-nodes').textContent = s.unique_entities ?? 0;
        document.getElementById('stat-anomalies').textContent = s.anomaly_count ?? 0;
    } catch (e) { /* non-fatal */ }
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

function setupDropdowns() {
    document.querySelectorAll('.dropdown-toggle').forEach(toggle => {
        toggle.addEventListener('click', (e) => {
            e.preventDefault();
            toggle.closest('.dropdown').classList.toggle('open');
        });
    });
}

const PAGE_TITLES = {
    dashboard: 'Investigation Dashboard',
    timeline: 'Unified Activity Timeline',
    keyactors: 'Key Actor Analysis',
    ingestion: 'Data Ingestion & Analysis',
    graph: 'Network Graph',
    crossanalysis: 'Cross-CDR Insights',
    imeigraph: 'IMEI / SIM-Swap Graph',
    reports: 'Reports & Export',
    evidence: 'Chain of Custody',
    anomalies: 'Anomaly Detection',
    profile: 'Behavior Profile',

    movement: 'Tower Movement Reconstruction',
    cellactivity: 'Cell Site Activity',
    query: 'Smart Query',
};

function setupTabNav() {
    const navLinks = document.querySelectorAll('.nav-links a[data-target]');
    const cards = {
        'dashboard': ['card-stats', 'card-graph'],
        'timeline': ['card-timeline'],
        'keyactors': ['card-keyactors'],
        'ingestion': ['card-filters', 'card-ingestion', 'card-data-preview', 'card-analysis'],
        'graph': ['card-graph'],
        'crossanalysis': ['card-crossanalysis'],
        'imeigraph': ['card-imeigraph'],
        'reports': ['card-reports'],
        'evidence': ['card-evidence'],
        'anomalies': ['card-anomalies'],
        'profile': ['card-profile'],

        'movement': ['card-movement'],
        'cellactivity': ['card-cellactivity'],
        'query': ['card-query'],
    };

    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            document.querySelectorAll('.nav-links li').forEach(li => li.classList.remove('active'));
            link.parentElement.classList.add('active');

            const type = link.getAttribute('data-type');
            if (type) {
                currentDataType = type;
                const headerEntity = document.getElementById('analysis-header-entity');
                if (headerEntity) headerEntity.textContent = type === 'cdr' ? 'Phone Number' : 'IP Address';
                document.getElementById('ingestion-type-label').textContent = type.toUpperCase();
            }

            document.querySelectorAll('.dashboard-grid .card').forEach(card => card.style.display = 'none');

            const target = link.getAttribute('data-target');
            document.getElementById('page-title').textContent = PAGE_TITLES[target] || 'Investigation Dashboard';

            if (cards[target]) {
                cards[target].forEach(cardId => {
                    const el = document.getElementById(cardId);
                    if (el) el.style.display = '';
                });

                if (target === 'dashboard' || target === 'graph') {
                    fetchDataAndRender();
                    setTimeout(() => cy.resize(), 60);
                } else if (target === 'ingestion') fetchDataAndRender();
                else if (target === 'evidence') loadEvidence();
                else if (target === 'anomalies') loadAnomalies();
                else if (target === 'imeigraph') setTimeout(() => imeiCy && imeiCy.resize(), 60);
                else if (target === 'cellactivity') loadCellList();
            }
        });
    });
}

// ---------------------------------------------------------------------------
// Upload
// ---------------------------------------------------------------------------

function setupUpload() {
    const fileInput = document.getElementById('file-input');
    const fileNameDisplay = document.getElementById('file-name');
    const uploadBtn = document.getElementById('upload-btn');
    const statusMsg = document.getElementById('upload-status');
    const zone = document.getElementById('upload-zone');

    fileInput.addEventListener('change', (e) => {
        const n = e.target.files.length;
        if (n > 0) {
            fileNameDisplay.textContent = n === 1 ? e.target.files[0].name : `${n} files selected`;
            uploadBtn.disabled = false;
        } else {
            fileNameDisplay.textContent = 'No file chosen';
            uploadBtn.disabled = true;
        }
    });

    // Drag & drop support
    ['dragover', 'dragenter'].forEach(ev => zone.addEventListener(ev, e => {
        e.preventDefault();
        zone.classList.add('dragover');
    }));
    ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, e => {
        e.preventDefault();
        zone.classList.remove('dragover');
    }));
    zone.addEventListener('drop', e => {
        if (e.dataTransfer.files.length) {
            fileInput.files = e.dataTransfer.files;
            fileInput.dispatchEvent(new Event('change'));
        }
    });

    uploadBtn.addEventListener('click', async () => {
        if (!currentCaseId) return toast('No case selected', 'error');
        const files = Array.from(fileInput.files || []);
        if (!files.length) return;

        const tz = document.getElementById('upload-timezone').value;
        uploadBtn.disabled = true;
        statusMsg.textContent = '';
        statusMsg.className = 'status-message';

        let totalIngested = 0, totalQuarantined = 0, failed = 0;
        const lines = [];

        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            uploadBtn.textContent = `Uploading ${i + 1}/${files.length}…`;
            const formData = new FormData();
            formData.append('file', file);
            formData.append('case_id', currentCaseId);
            if (tz) formData.append('timezone', tz);

            try {
                const result = await api('/upload/cdr', { method: 'POST', body: formData });
                totalIngested += result.ingested_count || 0;
                totalQuarantined += result.quarantined_count || 0;
                let line = `✓ ${file.name}: ${result.ingested_count} records`;
                if (result.quarantined_count > 0) line += ` (${result.quarantined_count} quarantined)`;
                lines.push(line);
            } catch (error) {
                failed++;
                lines.push(`✗ ${file.name}: ${error.message}`);
            }
        }

        statusMsg.innerHTML = lines.map(l => esc(l)).join('<br>') +
            `<br><strong>Total: ${totalIngested} records from ${files.length - failed}/${files.length} file(s).</strong>`;
        statusMsg.classList.add(failed ? 'error' : 'success');
        toast(`Upload complete — ${totalIngested} records from ${files.length} file(s)`, failed ? 'info' : 'success');
        refreshSummary();
        fetchDataAndRender();

        uploadBtn.textContent = 'Upload & Normalize';
        uploadBtn.disabled = false;
        fileInput.value = '';
        fileNameDisplay.textContent = 'No file chosen';
    });
}

function setupFilters() {
    document.getElementById('apply-filters-btn').addEventListener('click', fetchDataAndRender);
    document.getElementById('global-timezone').addEventListener('change', () => {
        toast(`Display timezone set to ${displayTZ()}`);
        fetchDataAndRender();
    });
}

// ---------------------------------------------------------------------------
// Graph & analysis table
// ---------------------------------------------------------------------------

async function fetchDataAndRender() {
    if (!currentCaseId) return;
    try {
        const startDate = document.getElementById('start-date')?.value;
        const endDate = document.getElementById('end-date')?.value;

        const params = new URLSearchParams({ case_id: currentCaseId });
        if (startDate) params.append('start_date', startDate);
        if (endDate) params.append('end_date', endDate);

        const path = '/cdrs';
        const data = await api(`${path}?${params.toString()}`);
        const records = data.cdrs;
        lastRecords = records;
        renderDataPreview();

        const elements = [];
        const degree = {};
        const edgeWeights = {};
        const entityStats = {};

        records.forEach(record => {
            let source, target;
            source = record.caller; target = record.callee;

            if (source && target) {
                const key = `${source}→${target}`;
                edgeWeights[key] = (edgeWeights[key] || 0) + 1;
                degree[source] = (degree[source] || 0) + 1;
                degree[target] = (degree[target] || 0) + 1;
            }

            for (const ent of [source, target]) {
                if (!ent) continue;
                if (!entityStats[ent]) entityStats[ent] = { count: 0, calls: [] };
                entityStats[ent].count++;
                entityStats[ent].calls.push(record);
            }
        });

        const nodes = Object.keys(degree);
        const maxDeg = Math.max(1, ...Object.values(degree));

        // Fetch modularity communities so we can colour nodes by cluster
        // (the "Gephi" look). Falls back gracefully if metrics are unavailable.
        let nodeColor = {};
        try {
            const metrics = await api(`/case/${currentCaseId}/graph-metrics?data_type=cdr`);
            (metrics.communities || []).forEach((members, idx) => {
                const color = COMMUNITY_COLORS[idx % COMMUNITY_COLORS.length];
                members.forEach(m => { nodeColor[m] = color; });
            });
        } catch (e) { /* colouring is best-effort */ }

        nodes.forEach(n => {
            const d = degree[n];
            const size = 16 + Math.sqrt(d / maxDeg) * 52; // 16–68 px, sqrt so hubs don't dwarf everything
            const isHub = d >= maxDeg * 0.55;
            const color = nodeColor[n] || DEFAULT_NODE_COLOR;
            // Full number as the label — the force layout gives room for it, and
            // it matches the reference (named nodes).
            const fontSize = (isHub ? 13 : 10) + 'px';
            elements.push({
                data: { id: n, label: n, size, degreeVal: d, color, fontSize },
                classes: isHub ? 'hub' : '',
            });
        });
        Object.entries(edgeWeights).forEach(([key, weight]) => {
            const [source, target] = key.split('→');
            // Edge takes its source node's community colour (translucent).
            const color = nodeColor[source] || DEFAULT_NODE_COLOR;
            elements.push({ data: { id: key, source, target, weight, color } });
        });

        cy.elements().remove();
        cy.add(elements);
        const netLayout = cy.layout(fcoseLayout());
        netLayout.one('layoutstop', () => {
            setTimeout(() => {
                cy.fit(undefined, 30);
                // Snapshot the graph now, while it is visible & laid out.
                try { visualCache.network = cy.png({ full: true, scale: 2, bg: '#0e1626' }); }
                catch (e) { /* ignore */ }
            }, 80);
        });
        netLayout.run();

        renderAnalysisTable(entityStats);
    } catch (error) {
        console.error('Error fetching data:', error);
    }
}

function renderAnalysisTable(entityStats) {
    const tbody = document.getElementById('aggregation-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    const sortedEntities = Object.keys(entityStats).sort((a, b) => entityStats[b].count - entityStats[a].count);

    const detailsPanel = document.getElementById('details-panel');

    if (sortedEntities.length === 0) {
        tbody.innerHTML = '<tr><td colspan="2" class="empty-cell">No data available</td></tr>';
        detailsPanel.innerHTML = '<h4>Details</h4><p class="muted">Select an entity to view details.</p>';
        return;
    }

    sortedEntities.forEach(entity => {
        const tr = document.createElement('tr');
        tr.className = 'clickable';
        tr.innerHTML = `<td class="mono">${esc(entity)}</td><td>${entityStats[entity].count}</td>`;

        tr.addEventListener('click', () => {
            const calls = entityStats[entity].calls;
            let totalDuration = 0;
            let rowsHtml = '';

            calls.forEach(call => {
                let type, timeStr, dur;
                type = call.caller === entity ? 'Outgoing' : 'Incoming';
                timeStr = fmtTime(call.normalized_time || call.start_time);
                dur = call.duration || 0;
                totalDuration += dur;
                rowsHtml += `<tr><td>${timeStr}</td><td>${type}</td><td>${parseInt(dur)}s</td></tr>`;
            });

            detailsPanel.innerHTML = `
                <h4>Details for <span class="mono">${esc(entity)}</span></h4>
                <p style="margin:0.25rem 0;"><strong>Total Count:</strong> ${entityStats[entity].count}
                &nbsp;·&nbsp; <strong>Total Duration:</strong> ${parseInt(totalDuration)}s</p>
                <div class="table-scroll" style="margin-top:0.75rem;">
                    <table class="data-table">
                        <thead><tr><th>Time (${displayTZ()})</th><th>Type</th><th>Duration</th></tr></thead>
                        <tbody>${rowsHtml}</tbody>
                    </table>
                </div>`;
        });
        tbody.appendChild(tr);
    });
}

// ---------------------------------------------------------------------------
// Ingested-data preview table
// ---------------------------------------------------------------------------

function setupDataPreview() {
    const filter = document.getElementById('data-preview-filter');
    if (filter) filter.addEventListener('input', renderDataPreview);
}

function renderDataPreview() {
    const tbody = document.getElementById('data-preview-tbody');
    const countEl = document.getElementById('data-preview-count');
    if (!tbody) return;

    const q = (document.getElementById('data-preview-filter')?.value || '').trim().toLowerCase();
    let rows = lastRecords;
    if (q) {
        rows = rows.filter(r =>
            [r.caller, r.callee, r.imei, r.imsi, r.cell_id, r.last_cell_id, r.operator, r.roaming_center, r.direction, r.event_type]
                .some(v => String(v ?? '').toLowerCase().includes(q))
        );
    }

    if (countEl) countEl.textContent = `${rows.length} record${rows.length === 1 ? '' : 's'}${q ? ` (filtered from ${lastRecords.length})` : ''}`;

    if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="11" class="empty-cell">${lastRecords.length ? 'No records match the filter.' : 'No data — upload a CDR file above.'}</td></tr>`;
        return;
    }

    // Cap the DOM to the first 500 rows for responsiveness.
    const capped = rows.slice(0, 500);
    tbody.innerHTML = capped.map(r => {
        const imeiCell = r.imei
            ? `<td><a href="#" class="imei-link mono" data-imei="${esc(r.imei)}">${esc(r.imei)}</a></td>`
            : '<td class="muted">—</td>';
        return `<tr>
            <td>${fmtTime(r.normalized_time || r.start_time)}</td>
            <td>${esc(r.event_type || '')}</td>
            <td>${esc(r.direction || '')}</td>
            <td class="mono clickable-num" data-num="${esc(r.caller || '')}">${esc(r.caller || '—')}</td>
            <td class="mono clickable-num" data-num="${esc(r.callee || '')}">${esc(r.callee || '—')}</td>
            <td>${r.duration != null ? parseInt(r.duration) : '—'}</td>
            <td>${esc(r.operator || 'Unknown')}</td>
            <td class="mono">${esc(r.cell_id || '—')}</td>
            ${imeiCell}
            <td class="mono">${esc(r.imsi || '—')}</td>
            <td>${esc(r.roaming_center || '—')}</td>
        </tr>`;
    }).join('');

    if (rows.length > capped.length) {
        tbody.innerHTML += `<tr><td colspan="11" class="muted" style="text-align:center;">Showing first ${capped.length} of ${rows.length} — refine the filter to see more.</td></tr>`;
    }

    // Wire IMEI links and number links.
    tbody.querySelectorAll('.imei-link').forEach(a =>
        a.addEventListener('click', e => { e.preventDefault(); openImeiModal(a.dataset.imei); }));
    tbody.querySelectorAll('.clickable-num').forEach(td =>
        td.addEventListener('click', () => { if (td.dataset.num) openEntityDetails(td.dataset.num); }));
}

// ---------------------------------------------------------------------------
// Graph interactions: node click details, search / highlight
// ---------------------------------------------------------------------------

function setupGraphInteractions() {
    // Node click -> load full entity details into side panel.
    cy.on('tap', 'node', evt => {
        const number = evt.target.id();
        cy.nodes().removeClass('selected');
        evt.target.addClass('selected');
        loadNodeDetails(number);
    });

    // Click on empty canvas clears highlight/fade.
    cy.on('tap', evt => {
        if (evt.target === cy) clearGraphHighlight();
    });

    const searchBtn = document.getElementById('btn-graph-search');
    const resetBtn = document.getElementById('btn-graph-reset');
    const input = document.getElementById('graph-search');
    if (searchBtn) searchBtn.addEventListener('click', () => graphSearch(input.value));
    if (resetBtn) resetBtn.addEventListener('click', () => { input.value = ''; clearGraphHighlight(); });
    if (input) input.addEventListener('keydown', e => { if (e.key === 'Enter') graphSearch(input.value); });
}

function clearGraphHighlight() {
    cy.elements().removeClass('faded highlight');
}

function graphSearch(term) {
    term = (term || '').trim();
    if (!term) { clearGraphHighlight(); return; }

    // Exact match first, else substring.
    let node = cy.getElementById(term);
    if (!node || node.empty()) {
        node = cy.nodes().filter(n => n.id().includes(term));
    }

    if (!node || node.empty()) {
        toast(`No node matching "${term}" in the current graph`, 'error');
        return;
    }
    node = node.first ? node.first() : node;

    const neighborhood = node.closedNeighborhood();
    cy.elements().addClass('faded');
    neighborhood.removeClass('faded');
    cy.nodes().removeClass('highlight selected');
    node.addClass('highlight');

    cy.animate({ center: { eles: node }, zoom: 1.4 }, { duration: 400 });
    loadNodeDetails(node.id());
}

async function loadNodeDetails(number) {
    const panel = document.getElementById('node-details');
    if (!panel) return;
    panel.innerHTML = `<h4>Node Details</h4><p class="muted">Loading ${esc(number)}…</p>`;

    try {
        const d = await api(`/case/${currentCaseId}/entity/${encodeURIComponent(number)}/details`);
        panel.innerHTML = renderNodeDetails(d);
        // Wire IMEI links inside the panel.
        panel.querySelectorAll('.imei-link').forEach(a =>
            a.addEventListener('click', e => { e.preventDefault(); openImeiModal(a.dataset.imei); }));
        panel.querySelectorAll('.contact-link').forEach(a =>
            a.addEventListener('click', e => { e.preventDefault(); graphSearch(a.dataset.num); }));
    } catch (e) {
        panel.innerHTML = `<h4>Node Details</h4><p class="error-text">${esc(e.message)}</p>`;
    }
}

function renderNodeDetails(d) {
    if (d.error) return `<h4>Node Details</h4><p class="muted">${esc(d.error)}</p>`;

    const opList = (d.operators || []).length
        ? d.operators.map(o => `${esc(o.operator)} <span class="muted">(${o.records})</span>`).join(', ')
        : 'Unknown';

    const imeiRows = (d.imeis || []).length
        ? d.imeis.map(i => `<a href="#" class="imei-link mono" data-imei="${esc(i.imei)}">${esc(i.imei)}</a> <span class="muted">(${i.records})</span>`).join('<br>')
        : '<span class="muted">None recorded</span>';

    const cellRows = (d.cells || []).length
        ? d.cells.map(c => `${esc(c.cell_id)}`).join(', ') : '<span class="muted">None</span>';

    const roaming = (d.roaming_centers || []).length
        ? d.roaming_centers.map(c => esc(c.location)).join(', ') : '<span class="muted">—</span>';

    const contactRows = (d.top_contacts || []).length
        ? d.top_contacts.map(c => `<tr>
              <td><a href="#" class="contact-link mono" data-num="${esc(c.number)}">${esc(c.number)}</a></td>
              <td>${c.interactions}</td></tr>`).join('')
        : '<tr><td colspan="2" class="muted">No contacts</td></tr>';

    return `
        <h4>Details for <span class="mono accent">${esc(d.number)}</span></h4>
        <div class="stats-grid four" style="margin:0.5rem 0;">
            <div class="stat-box compact"><span class="stat-value small">${d.total_interactions ?? 0}</span><span class="stat-label">Total</span></div>
            <div class="stat-box compact"><span class="stat-value small">${d.outgoing ?? 0}</span><span class="stat-label">Outgoing</span></div>
            <div class="stat-box compact"><span class="stat-value small">${d.incoming ?? 0}</span><span class="stat-label">Incoming</span></div>
            <div class="stat-box compact"><span class="stat-value small">${d.total_duration_sec ?? 0}s</span><span class="stat-label">Talk Time</span></div>
        </div>
        <div class="detail-kv"><strong>Network Operator:</strong> ${opList}${d.primary_operator ? ` <span class="badge badge-medium">${esc(d.primary_operator)}</span>` : ''}</div>
        <div class="detail-kv"><strong>Voice / SMS:</strong> ${d.voice ?? 0} voice · ${d.sms ?? 0} SMS</div>
        <div class="detail-kv"><strong>Unique contacts:</strong> ${d.unique_contacts ?? 0}</div>
        <div class="detail-kv"><strong>Roaming centres:</strong> ${roaming}</div>
        <div class="detail-kv"><strong>Cells used:</strong> ${cellRows}</div>
        <div class="detail-kv"><strong>IMEI(s):</strong><br>${imeiRows}</div>
        <div class="detail-kv"><strong>First seen:</strong> ${fmtTime(d.first_seen)}</div>
        <div class="detail-kv"><strong>Last seen:</strong> ${fmtTime(d.last_seen)}</div>
        <h5 style="margin:0.75rem 0 0.35rem;">Top Contacts</h5>
        <div class="table-scroll">
            <table class="data-table"><thead><tr><th>Number</th><th>Interactions</th></tr></thead>
            <tbody>${contactRows}</tbody></table>
        </div>`;
}

function openEntityDetails(number) {
    // Reuse the modal for a quick entity view (from the data table).
    openModal(`Entity ${number}`, '<p class="muted">Loading…</p>');
    api(`/case/${currentCaseId}/entity/${encodeURIComponent(number)}/details`)
        .then(d => setModalBody(renderNodeDetails(d), () => {
            document.querySelectorAll('#detail-modal-body .imei-link').forEach(a =>
                a.addEventListener('click', e => { e.preventDefault(); openImeiModal(a.dataset.imei); }));
        }))
        .catch(e => setModalBody(`<p class="error-text">${esc(e.message)}</p>`));
}

// ---------------------------------------------------------------------------
// IMEI handset modal
// ---------------------------------------------------------------------------

async function openImeiModal(imei) {
    openModal(`IMEI ${imei}`, '<p class="muted">Looking up handset…</p>');
    try {
        const d = await api(`/case/${currentCaseId}/imei/${encodeURIComponent(imei)}/details`);
        setModalBody(renderImeiDetails(d));
    } catch (e) {
        setModalBody(`<p class="error-text">${esc(e.message)}</p>`);
    }
}

function renderImeiDetails(d) {
    if (d.error) return `<p class="muted">${esc(d.error)}</p>`;

    const validBadge = d.valid
        ? '<span class="badge badge-high">Valid (Luhn OK)</span>'
        : '<span class="badge badge-low">Check digit mismatch</span>';

    const simRows = (d.sims_used || []).length
        ? d.sims_used.map(s => `<tr>
              <td class="mono">${esc(s.imsi)}</td>
              <td>${esc(s.operator || 'Unknown')}</td>
              <td>${s.records ?? 0}</td></tr>`).join('')
        : '<tr><td colspan="3" class="muted">No SIMs recorded</td></tr>';

    const subjRows = (d.used_by_subjects || []).length
        ? d.used_by_subjects.map(s => `<a href="#" class="contact-link mono" data-num="${esc(s.number)}">${esc(s.number)}</a> <span class="muted">(${s.records})</span>`).join(', ')
        : '<span class="muted">—</span>';

    const swap = d.sim_swap_suspected
        ? '<div class="detail-kv warn-text"><strong>⚠ SIM swap suspected</strong> — this handset was seen with multiple SIMs.</div>'
        : '';

    return `
        <div class="detail-kv"><strong>Make / Model:</strong> ${esc(d.make_model || 'Unknown handset')} ${validBadge}</div>
        <div class="detail-kv"><strong>TAC:</strong> <span class="mono">${esc(d.tac || '—')}</span>
            &nbsp;·&nbsp; <strong>Serial:</strong> <span class="mono">${esc(d.serial || '—')}</span>
            &nbsp;·&nbsp; <strong>Check:</strong> <span class="mono">${esc(d.check_digit ?? '—')}</span></div>
        ${d.note ? `<div class="detail-kv muted">${esc(d.note)}</div>` : ''}
        ${swap}
        <div class="detail-kv"><strong>Usage records:</strong> ${d.usage_records ?? 0}</div>
        <div class="detail-kv"><strong>Used by:</strong> ${subjRows}</div>
        <div class="detail-kv"><strong>First seen:</strong> ${fmtTime(d.first_seen)} &nbsp;·&nbsp; <strong>Last seen:</strong> ${fmtTime(d.last_seen)}</div>
        <h5 style="margin:0.75rem 0 0.35rem;">SIMs used in this handset</h5>
        <div class="table-scroll">
            <table class="data-table"><thead><tr><th>IMSI</th><th>Operator</th><th>Records</th></tr></thead>
            <tbody>${simRows}</tbody></table>
        </div>`;
}

// ---------------------------------------------------------------------------
// Generic modal helpers
// ---------------------------------------------------------------------------

function setupDetailModal() {
    const overlay = document.getElementById('detail-modal');
    const close = document.getElementById('detail-modal-close');
    if (close) close.addEventListener('click', closeModal);
    if (overlay) overlay.addEventListener('click', e => { if (e.target === overlay) closeModal(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
}

function openModal(title, bodyHtml) {
    document.getElementById('detail-modal-title').textContent = title;
    document.getElementById('detail-modal-body').innerHTML = bodyHtml;
    document.getElementById('detail-modal').style.display = 'flex';
}

function setModalBody(html, afterWire) {
    const body = document.getElementById('detail-modal-body');
    body.innerHTML = html;
    // Wire any contact links to jump to the graph.
    body.querySelectorAll('.contact-link').forEach(a =>
        a.addEventListener('click', e => { e.preventDefault(); closeModal(); graphSearch(a.dataset.num); }));
    if (typeof afterWire === 'function') afterWire();
}

function closeModal() {
    const overlay = document.getElementById('detail-modal');
    if (overlay) overlay.style.display = 'none';
}

// ---------------------------------------------------------------------------
// Cross-CDR insights
// ---------------------------------------------------------------------------

function setupCrossAnalysis() {
    const btn = document.getElementById('btn-run-crossanalysis');
    if (btn) btn.addEventListener('click', runCrossAnalysis);
}

async function runCrossAnalysis() {
    if (!currentCaseId) return toast('No case selected', 'error');
    const box = document.getElementById('cross-results');
    box.innerHTML = '<p class="muted">Analyzing all CDRs in this case…</p>';
    try {
        const d = await api(`/case/${currentCaseId}/cross-analysis`);
        if (d.error) { box.innerHTML = `<p class="muted">${esc(d.error)}</p>`; return; }

        document.getElementById('cross-summary').style.display = '';
        document.getElementById('cx-records').textContent = d.record_count;
        document.getElementById('cx-subjects').textContent = d.subject_count;
        document.getElementById('cx-common').textContent = d.totals.common_numbers;
        document.getElementById('cx-swaps').textContent = d.totals.shared_handsets;

        box.innerHTML = [
            crossBlock('📞 Common Numbers (contacted by multiple subjects)',
                d.common_numbers,
                ['Number', 'Shared by', 'Total contacts'],
                r => [numLink(r.number), `${r.subject_count} subjects`, r.total_contacts],
                'These numbers link otherwise-separate suspects — likely coordinators or shared contacts.'),
            crossBlock('📱 Shared Handsets — same IMEI, different numbers (SIM swap)',
                d.shared_handsets,
                ['IMEI', 'Numbers used', 'Count'],
                r => [imeiLink(r.imei), r.numbers.map(numLink).join(', '), r.number_count],
                'One physical phone used with several SIMs — classic SIM-swapping to evade tracking.'),
            crossBlock('💳 Shared SIMs — same IMSI in different handsets',
                d.shared_sims,
                ['IMSI', 'Operator', 'Handsets', 'Count'],
                r => [`<span class="mono">${esc(r.imsi)}</span>`, esc(r.operator), r.imeis.map(x => `<span class="mono">${esc(x)}</span>`).join(', '), r.imei_count],
                'A single SIM moved between multiple phones.'),
            crossBlock('📡 Shared Cell Towers (possible co-location)',
                d.shared_cells,
                ['Cell ID', 'Subjects seen there', 'Count'],
                r => [`<span class="mono">${esc(r.cell_id)}</span>`, r.subjects.map(numLink).join(', '), r.subject_count],
                'Multiple subjects using the same tower — they may have been in the same place.'),
            crossBlock('🔝 Busiest Talkers',
                d.busiest_talkers,
                ['Number', 'Records'],
                r => [numLink(r.number), r.records]),
            crossBlock('🎯 Most-Contacted Numbers',
                d.most_contacted,
                ['Number', 'Times contacted'],
                r => [numLink(r.number), r.contacts]),
        ].join('');

        // Wire links.
        box.querySelectorAll('.imei-link').forEach(a =>
            a.addEventListener('click', e => { e.preventDefault(); openImeiModal(a.dataset.imei); }));
        box.querySelectorAll('.contact-link').forEach(a =>
            a.addEventListener('click', e => { e.preventDefault(); openEntityDetails(a.dataset.num); }));
    } catch (e) {
        box.innerHTML = `<p class="error-text">${esc(e.message)}</p>`;
    }
}

function numLink(n) {
    return `<a href="#" class="contact-link mono" data-num="${esc(n)}">${esc(n)}</a>`;
}
function imeiLink(i) {
    return `<a href="#" class="imei-link mono" data-imei="${esc(i)}">${esc(i)}</a>`;
}

function crossBlock(title, rows, headers, rowFn, note) {
    if (!rows || !rows.length) {
        return `<div class="cross-block"><h4>${title}</h4><p class="muted">None found.</p></div>`;
    }
    const head = headers.map(h => `<th>${h}</th>`).join('');
    const body = rows.map(r => `<tr>${rowFn(r).map(c => `<td>${c}</td>`).join('')}</tr>`).join('');
    return `
        <div class="cross-block">
            <h4>${title} <span class="badge badge-medium">${rows.length}</span></h4>
            ${note ? `<p class="muted small">${note}</p>` : ''}
            <div class="table-scroll bordered">
                <table class="data-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
            </div>
        </div>`;
}

// ---------------------------------------------------------------------------
// IMEI / SIM-swap graph
// ---------------------------------------------------------------------------

function setupImeiGraph() {
    imeiCy = cytoscape({
        container: document.getElementById('imei-cy-container'),
        style: [
            { selector: 'node', style: {
                'label': 'data(label)', 'color': '#E8F1FF', 'font-size': '11px',
                'font-weight': 600, 'font-family': 'JetBrains Mono, monospace',
                'text-outline-color': '#1F2937', 'text-outline-width': 2.5,
                'text-valign': 'bottom', 'text-margin-y': 5,
                'width': 'data(size)', 'height': 'data(size)',
                'text-max-width': '120px', 'text-wrap': 'ellipsis',
            }},
            { selector: 'node.number', style: { 'background-color': '#B3D4FD', 'shape': 'ellipse', 'border-width': 2, 'border-color': '#60A5FA' } },
            { selector: 'node.imei', style: { 'background-color': '#60A5FA', 'shape': 'round-rectangle', 'border-width': 2, 'border-color': '#B3D4FD' } },
            // A swapped handset is the headline finding — make it pop.
            { selector: 'node.swap', style: {
                'background-color': '#F87171', 'border-color': '#fecaca', 'border-width': 5,
                'shape': 'round-rectangle', 'color': '#fff', 'font-size': '12px',
            }},
            // A number that appears on 2+ handsets = "multi-handset" user.
            { selector: 'node.multi', style: { 'border-color': '#FBBF24', 'border-width': 4 } },
            { selector: 'node.selected', style: { 'background-color': '#34D399', 'border-color': '#6ee7b7', 'border-width': 5 } },
            { selector: 'node.faded', style: { 'opacity': 0.12 } },
            { selector: 'edge', style: {
                'width': 'mapData(weight, 1, 20, 1.5, 7)', 'line-color': '#3B4A61',
                'curve-style': 'bezier', 'opacity': 0.5,
                'target-arrow-shape': 'none',
            }},
            // Edges touching a swap handset are highlighted red.
            { selector: 'edge.swap-edge', style: { 'line-color': '#F87171', 'opacity': 0.85, 'width': 3 } },
            { selector: 'edge.faded', style: { 'opacity': 0.06 } },
        ],
        // Grid-friendly force layout with real spacing so it isn't a hairball.
        layout: {
            name: 'cose', animate: false, padding: 40,
            nodeRepulsion: 12000, idealEdgeLength: 90, edgeElasticity: 120,
            nestingFactor: 1.2, gravity: 0.3, componentSpacing: 120,
            nodeOverlap: 20, randomize: true,
        },
    });

    imeiCy.on('tap', 'node', evt => {
        const node = evt.target;
        imeiCy.elements().removeClass('selected faded');
        // Spotlight: dim everything except this node and its direct neighbours.
        const hood = node.closedNeighborhood();
        imeiCy.elements().not(hood).addClass('faded');
        node.addClass('selected');
        const t = node.data('type');
        if (t === 'imei') openImeiModal(node.data('label'));
        else openEntityDetails(node.data('label'));
    });
    // Tap empty canvas to clear the spotlight.
    imeiCy.on('tap', evt => { if (evt.target === imeiCy) imeiCy.elements().removeClass('selected faded'); });

    const btn = document.getElementById('btn-load-imeigraph');
    if (btn) btn.addEventListener('click', loadImeiGraph);
    const swapOnly = document.getElementById('imei-swaps-only');
    if (swapOnly) swapOnly.addEventListener('change', applyImeiFilter);
}

// Show only SIM-swap handsets + the numbers attached to them, or the full graph.
function applyImeiFilter() {
    if (!imeiCy) return;
    const on = document.getElementById('imei-swaps-only');
    imeiCy.elements().removeClass('faded');
    if (!on || !on.checked) { imeiCy.layout({ name: 'cose', animate: false, padding: 40, nodeRepulsion: 12000, idealEdgeLength: 90 }).run(); return; }
    const swapNodes = imeiCy.nodes('.swap');
    const keep = swapNodes.closedNeighborhood();
    imeiCy.elements().not(keep).addClass('faded');
}

async function loadImeiGraph() {
    if (!currentCaseId) return toast('No case selected', 'error');
    const panel = document.getElementById('imei-swap-panel');
    panel.innerHTML = '<h4>SIM-Swap Clusters</h4><p class="muted">Loading…</p>';
    try {
        const d = await api(`/case/${currentCaseId}/imei-graph`);
        if (!d.nodes.length) {
            panel.innerHTML = '<h4>SIM-Swap Clusters</h4><p class="muted">No IMEI data in this case.</p>';
            imeiCy.elements().remove();
            return;
        }

        const els = [];
        const swapImeiIds = new Set();
        d.nodes.forEach(n => {
            const isImei = n.type === 'imei';
            const size = isImei ? 30 + Math.min((n.number_count || 1) * 8, 44) : 24 + Math.min((n.handset_count || 1) * 6, 28);
            let cls = n.type;
            if (n.swap) { cls += ' swap'; swapImeiIds.add(n.id); }
            // A number appearing on 2+ handsets = multi-handset user (amber ring).
            if (!isImei && (n.handset_count || 0) >= 2) cls += ' multi';
            const label = isImei
                ? `${n.label}${n.make_model ? '\n' + n.make_model : ''}`
                : n.label;
            els.push({ data: { id: n.id, label, type: n.type, size }, classes: cls });
        });
        d.edges.forEach(e => {
            // Flag edges that touch a swap handset so the swap path glows red.
            const cls = swapImeiIds.has(e.target) || swapImeiIds.has(e.source) ? 'swap-edge' : '';
            els.push({ data: { id: e.id, source: e.source, target: e.target, weight: e.weight }, classes: cls });
        });

        imeiCy.elements().remove();
        imeiCy.add(els);
        imeiCy.layout({
            name: 'cose', animate: false, padding: 40,
            nodeRepulsion: 12000, idealEdgeLength: 90, edgeElasticity: 120,
            gravity: 0.3, componentSpacing: 120, nodeOverlap: 20, randomize: true,
        }).run();
        setTimeout(() => {
            imeiCy.resize(); imeiCy.fit(null, 40);
            try { visualCache.imei = imeiCy.png({ full: true, scale: 2, bg: '#0e1626' }); }
            catch (e) { /* ignore */ }
        }, 120);
        applyImeiFilter();

        // Cluster side panel.
        const clusters = d.swap_clusters || [];
        let html = `<h4>SIM-Swap Clusters <span class="badge badge-danger">${clusters.length}</span></h4>`;
        html += `<p class="muted small">${d.stats.numbers} numbers · ${d.stats.handsets} handsets. Red nodes = one handset shared by multiple numbers.</p>`;
        if (!clusters.length) {
            html += '<p class="muted">No SIM-swap handsets detected — every handset maps to a single number.</p>';
        } else {
            clusters.forEach(c => {
                html += `
                    <div class="cross-block">
                        <div class="detail-kv"><strong>IMEI</strong> ${imeiLink(c.imei)}</div>
                        <div class="detail-kv muted small">${esc(c.make_model || '')} · ${c.sim_count} SIM(s)</div>
                        <div class="detail-kv"><strong>${c.number_count} numbers:</strong><br>${c.numbers.map(numLink).join(', ')}</div>
                    </div>`;
            });
        }
        panel.innerHTML = html;
        panel.querySelectorAll('.imei-link').forEach(a =>
            a.addEventListener('click', e => { e.preventDefault(); openImeiModal(a.dataset.imei); }));
        panel.querySelectorAll('.contact-link').forEach(a =>
            a.addEventListener('click', e => { e.preventDefault(); openEntityDetails(a.dataset.num); }));

        toast(`IMEI graph: ${d.stats.handsets} handsets, ${d.stats.swap_handsets} SIM-swap`, 'success');
    } catch (e) {
        panel.innerHTML = `<h4>SIM-Swap Clusters</h4><p class="error-text">${esc(e.message)}</p>`;
    }
}

// ---------------------------------------------------------------------------
// New features: timeline, key actors, movement, exports
// ---------------------------------------------------------------------------

function setupNewFeatures() {
    // --- Unified timeline ---
    document.getElementById('btn-load-timeline').addEventListener('click', async () => {
        const subject = document.getElementById('timeline-subject').value.trim();
        const container = document.getElementById('timeline-container');
        container.innerHTML = '<p class="muted empty-pad">Loading…</p>';
        try {
            let path = `/case/${currentCaseId}/timeline?limit=500`;
            if (subject) path += `&subject_id=${encodeURIComponent(subject)}`;
            const data = await api(path);

            if (!data.events.length) {
                container.innerHTML = '<p class="muted empty-pad">No events found. Upload CDR data first.</p>';
                return;
            }
            container.innerHTML = data.events.map(ev => `
                <div class="timeline-event">
                    <span class="timeline-time">${fmtTime(ev.time)}</span>
                    <span class="timeline-tag ${ev.source.toLowerCase()}">${ev.source}</span>
                    <span class="timeline-summary">${esc(ev.summary)} <span class="muted small">(${esc(ev.type)})</span></span>
                </div>`).join('');
            toast(`Timeline loaded — ${data.events.length} of ${data.count} events`);
        } catch (e) {
            container.innerHTML = `<p class="muted empty-pad">${esc(e.message)}</p>`;
        }
    });

    // --- Key actors ---
    document.getElementById('btn-load-keyactors').addEventListener('click', async () => {
        const type = document.getElementById('keyactors-type').value;
        const tbody = document.getElementById('keyactors-tbody');
        tbody.innerHTML = '<tr><td colspan="7" class="empty-cell">Computing centrality measures…</td></tr>';
        try {
            const data = await api(`/case/${currentCaseId}/graph-metrics?data_type=${type}`);

            document.getElementById('keyactors-summary').style.display = 'grid';
            document.getElementById('ka-nodes').textContent = data.nodes;
            document.getElementById('ka-edges').textContent = data.edges;
            document.getElementById('ka-density').textContent = data.density ?? 0;
            document.getElementById('ka-communities').textContent = (data.communities || []).length;

            if (!data.key_actors.length) {
                tbody.innerHTML = '<tr><td colspan="7" class="empty-cell">No interaction data available.</td></tr>';
                return;
            }
            tbody.innerHTML = data.key_actors.map((a, i) => `
                <tr>
                    <td><strong>#${i + 1}</strong></td>
                    <td class="mono">${esc(a.entity)}</td>
                    <td>${a.degree}</td>
                    <td>${a.total_interactions}</td>
                    <td>${a.degree_centrality}</td>
                    <td>${a.betweenness}</td>
                    <td>${a.eigenvector}</td>
                </tr>`).join('');

            const commDiv = document.getElementById('communities-container');
            if (data.communities && data.communities.length) {
                commDiv.innerHTML = '<h4 style="margin-bottom:0.5rem;">Detected Communities</h4>' +
                    data.communities.map((c, i) =>
                        `<p style="margin-bottom:0.5rem;"><span class="badge badge-medium">Cluster ${i + 1}</span> ` +
                        c.map(m => `<span class="chip">${esc(m)}</span>`).join(' ') + '</p>').join('');
            } else {
                commDiv.innerHTML = '';
            }
        } catch (e) {
            tbody.innerHTML = `<tr><td colspan="7" class="empty-cell">${esc(e.message)}</td></tr>`;
        }
    });

    // --- Tower movement ---
    document.getElementById('btn-load-movement').addEventListener('click', async () => {
        const subject = document.getElementById('movement-subject').value.trim();
        if (!subject) return toast('Enter a subject number', 'error');
        const results = document.getElementById('movement-results');
        const list = document.getElementById('movement-list');
        const mapDiv = document.getElementById('movement-map');
        results.style.display = 'block';
        list.innerHTML = '<p class="muted empty-pad">Reconstructing…</p>';

        try {
            const data = await api(`/case/${currentCaseId}/subject/${encodeURIComponent(subject)}/movement`);

            if (!data.dwell_periods.length) {
                list.innerHTML = `<p class="muted empty-pad">${esc(data.message || 'No tower data for this subject.')}</p>`;
                mapDiv.style.display = 'none';
                return;
            }

            list.innerHTML = data.dwell_periods.map((d, i) => {
                const geo = d.geo_source
                    ? `<span class="badge ${d.geo_confidence === 'high' ? 'badge-high' : d.geo_confidence === 'medium' ? 'badge-medium' : 'badge-low'}" title="Geolocation source">${geoSourceLabel(d.geo_source)}</span>`
                    : '';
                const loc = d.address ? `· ${esc(d.address)}` : (d.roaming_center ? `· ${esc(d.roaming_center)}` : '');
                // Direction breakdown: ↓ incoming, ↑ outgoing.
                const dir = [];
                if (d.incoming) dir.push(`<span class="dir-in" title="Incoming">↓ ${d.incoming} in</span>`);
                if (d.outgoing) dir.push(`<span class="dir-out" title="Outgoing">↑ ${d.outgoing} out</span>`);
                const dirHtml = dir.length ? `<span class="dir-badges">${dir.join(' ')}</span>` : '';
                return `
                <div class="timeline-event">
                    <span class="timeline-time">${fmtTime(d.start)}</span>
                    <span class="timeline-tag cdr">Stop ${i + 1}</span>
                    <span class="timeline-summary">
                        <strong>${esc(d.cell_id)}</strong> — ${d.event_count} event(s), dwell ${d.duration_minutes} min
                        ${dirHtml} ${loc} ${geo}
                    </span>
                </div>`;
            }).join('');

            // Map view if coordinates available
            if (data.has_coordinates) {
                mapDiv.style.display = 'block';
                if (movementMap) { movementMap.remove(); movementMap = null; }
                movementMap = L.map('movement-map');
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '© OpenStreetMap contributors',
                    crossOrigin: true,
                }).addTo(movementMap);

                const points = data.dwell_periods.filter(d => d.lat != null);
                const latlngs = points.map(d => [d.lat, d.lon]);

                // Draw the travel path first (under the markers): an ice-blue
                // dashed route with a soft glow so the sequence reads clearly.
                if (latlngs.length > 1) {
                    L.polyline(latlngs, { color: '#1F2937', weight: 7, opacity: 0.5 }).addTo(movementMap);
                    L.polyline(latlngs, { color: '#B3D4FD', weight: 3, dashArray: '2 9', lineCap: 'round' }).addTo(movementMap);
                }

                // Markers encode BOTH sequence and call direction:
                //   colour  = dominant direction at that tower
                //             blue = mostly outgoing, green = mostly incoming,
                //             amber = mixed
                //   ring    = green (first stop) / red (last stop)
                //   glyph   = stop number; a small ↑/↓ shows the direction
                const DIR_COLOR = { outgoing: '#60A5FA', incoming: '#34D399', mixed: '#FBBF24' };
                const DIR_ARROW = { outgoing: '↑', incoming: '↓', mixed: '↕' };
                points.forEach((d, i) => {
                    const isFirst = i === 0, isLast = i === points.length - 1;
                    const dirn = d.primary_direction || 'mixed';
                    const bg = DIR_COLOR[dirn] || '#B3D4FD';
                    const ring = isFirst ? '#10B981' : isLast ? '#EF4444' : '#ffffff';
                    const arrow = DIR_ARROW[dirn] || '';
                    const icon = L.divIcon({
                        className: 'move-pin',
                        html: `<div style="background:${bg};color:#1F2937;width:30px;height:30px;border-radius:50%;
                               display:flex;align-items:center;justify-content:center;font:700 12px 'JetBrains Mono',monospace;
                               border:3px solid ${ring};box-shadow:0 2px 7px rgba(0,0,0,0.55);position:relative;">
                               ${i + 1}<span style="position:absolute;top:-8px;right:-6px;font-size:13px;color:${bg};
                               text-shadow:0 0 2px #1F2937,0 0 2px #1F2937;">${arrow}</span></div>`,
                        iconSize: [30, 30], iconAnchor: [15, 15],
                    });
                    const tag = isFirst ? ' (start)' : isLast ? ' (end)' : '';
                    L.marker([d.lat, d.lon], { icon }).addTo(movementMap)
                        .bindPopup(
                            `<b>Stop ${i + 1}${tag} · ${d.cell_id}</b><br>${fmtTime(d.start)}<br>` +
                            `<span style="color:#059669">↓ ${d.incoming || 0} incoming</span> · ` +
                            `<span style="color:#2563eb">↑ ${d.outgoing || 0} outgoing</span><br>` +
                            `${d.voice || 0} voice · ${d.sms || 0} SMS · dwell ${d.duration_minutes} min<br>` +
                            `<small>${geoSourceLabel(d.geo_source)} (${(d.geo_confidence || 'n/a')})</small>`);
                });

                // On-map legend so the colour/arrow coding is self-explanatory.
                const legend = L.control({ position: 'bottomright' });
                legend.onAdd = function () {
                    const div = L.DomUtil.create('div', 'move-legend');
                    div.innerHTML =
                        `<div><span class="lg-dot" style="background:#34D399"></span>↓ mostly incoming</div>` +
                        `<div><span class="lg-dot" style="background:#60A5FA"></span>↑ mostly outgoing</div>` +
                        `<div><span class="lg-dot" style="background:#FBBF24"></span>↕ mixed</div>` +
                        `<div><span class="lg-dot" style="background:#fff;border-color:#10B981"></span>start</div>` +
                        `<div><span class="lg-dot" style="background:#fff;border-color:#EF4444"></span>end</div>`;
                    return div;
                };
                legend.addTo(movementMap);

                movementMap.fitBounds(L.latLngBounds(latlngs).pad(0.3));
                setTimeout(() => movementMap.invalidateSize(), 100);
                // Remember what to overlay so the PDF map shows every numbered stop.
                movementRenderCtx = { points, subject };
                // Cache the map for the PDF once tiles have painted. leaflet-image
                // rasterizes the tiles + vector overlays to a canvas.
                cacheMovementMap();
                if ((data.geo_sources || []).includes('roaming_centroid')) {
                    toast('Some coordinates are approximate (roaming-centre city fallback)', 'info');
                }
            } else {
                mapDiv.style.display = 'none';
                toast('No tower coordinates — try "Auto-Geolocate Towers" or upload a tower reference CSV', 'info');
            }
        } catch (e) {
            list.innerHTML = `<p class="muted empty-pad">${esc(e.message)}</p>`;
        }
    });

    // --- Tower reference upload ---
    document.getElementById('tower-file-input').addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file || !currentCaseId) return;
        const formData = new FormData();
        formData.append('file', file);
        formData.append('case_id', currentCaseId);
        try {
            const result = await api('/upload/towers', { method: 'POST', body: formData });
            toast(`Tower reference loaded — ${result.ingested_count} towers`, 'success');
        } catch (err) {
            toast(err.message, 'error');
        } finally {
            e.target.value = '';
        }
    });

    // --- Auto-geolocate all towers in the case (OpenCellID + roaming fallback) ---
    document.getElementById('btn-geolocate-towers').addEventListener('click', async () => {
        if (!currentCaseId) return toast('No case selected', 'error');
        const status = document.getElementById('geolocate-status');
        status.className = 'status-message';
        status.textContent = 'Resolving cell-tower coordinates (OpenCellID)…';
        try {
            const s = await api(`/case/${currentCaseId}/geolocate-towers`, { method: 'POST' });
            const parts = [];
            if (s.opencellid) parts.push(`${s.opencellid} via OpenCellID`);
            if (s.roaming_centroid) parts.push(`${s.roaming_centroid} via roaming-centre`);
            if (s.reference) parts.push(`${s.reference} from reference CSV`);
            if (s.unresolved) parts.push(`${s.unresolved} unresolved`);
            status.textContent = `Resolved ${s.total_cells} cell ID(s): ${parts.join(' · ') || 'none'}.`;
            status.classList.add('success');
            toast(`Geolocation complete — ${s.newly_resolved} newly resolved`, 'success');
            // Refresh the map if a subject is already loaded.
            const subj = document.getElementById('movement-subject').value.trim();
            if (subj) document.getElementById('btn-load-movement').click();
        } catch (e) {
            status.textContent = e.message;
            status.classList.add('error');
            toast(e.message, 'error');
        }
    });

    // --- CSV exports ---
    document.getElementById('export-cdr-csv-btn').addEventListener('click', () => {
        window.open(`${API_BASE}/export/csv?case_id=${currentCaseId}&data_type=cdr`, '_blank');
    });

    // --- Cell Site Activity ---
    document.getElementById('btn-refresh-cells').addEventListener('click', () => loadCellList(true));
    document.getElementById('btn-load-cellactivity').addEventListener('click', loadCellActivity);
    document.getElementById('cellact-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') loadCellActivity();
    });

}

// ---------------------------------------------------------------------------
// Cell Site Activity — who was on a tower, and which cells they connect to
// ---------------------------------------------------------------------------

let _cellListLoaded = false;

async function loadCellList(force) {
    if (!currentCaseId) return;
    if (_cellListLoaded && !force) return;
    try {
        const data = await api(`/case/${currentCaseId}/cells`);
        const dl = document.getElementById('cellact-list');
        dl.innerHTML = (data.cells || []).map(c =>
            `<option value="${esc(c.cell_id)}">${c.events} events${c.location ? ' · ' + esc(c.location) : ''}</option>`
        ).join('');
        _cellListLoaded = true;
        if (force) toast(`Loaded ${data.total} cell ID(s)`, 'success');
    } catch (e) {
        if (force) toast(e.message, 'error');
    }
}

async function loadCellActivity() {
    if (!currentCaseId) return toast('No case selected', 'error');
    const cell = document.getElementById('cellact-input').value.trim();
    if (!cell) return toast('Enter or pick a cell ID', 'error');
    const results = document.getElementById('cellact-results');
    try {
        const d = await api(`/case/${currentCaseId}/cell/${encodeURIComponent(cell)}/activity`);
        if (d.error) {
            results.style.display = 'none';
            return toast(d.error, 'error');
        }
        results.style.display = 'block';

        document.getElementById('cellact-events').textContent = d.total_events;
        document.getElementById('cellact-numbers').textContent = d.unique_numbers;
        document.getElementById('cellact-connected').textContent = (d.connected_cells || []).length;
        document.getElementById('cellact-handovers').textContent = (d.handovers || []).length;

        const win = (d.first_seen && d.last_seen)
            ? ` · Active ${fmtTime(d.first_seen)} → ${fmtTime(d.last_seen)}` : '';
        document.getElementById('cellact-loc').innerHTML =
            `<strong>${esc(d.cell_id)}</strong>${d.location ? ' — ' + esc(d.location) : ''}${win}`;

        // Numbers table
        const nbody = document.getElementById('cellact-numbers-tbody');
        nbody.innerHTML = (d.numbers || []).length ? d.numbers.map(n => {
            const awin = (n.first_seen && n.last_seen)
                ? `${fmtTime(n.first_seen)} → ${fmtTime(n.last_seen)}` : '—';
            return `<tr>
                <td><a href="#" class="link-cell" data-number="${esc(n.number)}">${esc(n.number)}</a></td>
                <td>${n.events}</td>
                <td><span class="dir-in">↓ ${n.incoming}</span></td>
                <td><span class="dir-out">↑ ${n.outgoing}</span></td>
                <td>${n.voice}</td>
                <td>${n.sms}</td>
                <td>${n.unique_contacts}</td>
                <td class="small muted">${awin}</td>
            </tr>`;
        }).join('') : '<tr><td colspan="8" class="empty-cell">No numbers</td></tr>';

        // Connected cells table
        const cbody = document.getElementById('cellact-connected-tbody');
        cbody.innerHTML = (d.connected_cells || []).length ? d.connected_cells.map(c => {
            const nums = c.numbers.slice(0, 6).map(esc).join(', ') + (c.numbers.length > 6 ? ` +${c.numbers.length - 6} more` : '');
            return `<tr>
                <td><a href="#" class="link-cellid" data-cell="${esc(c.cell_id)}">${esc(c.cell_id)}</a></td>
                <td>${c.location ? esc(c.location) : '<span class="muted">—</span>'}</td>
                <td>${c.events}</td>
                <td>${c.number_count}</td>
                <td class="small">${nums}</td>
            </tr>`;
        }).join('') : '<tr><td colspan="5" class="empty-cell">No connected cells</td></tr>';

        // Handovers table
        const hbody = document.getElementById('cellact-handovers-tbody');
        hbody.innerHTML = (d.handovers || []).length ? d.handovers.map(h =>
            `<tr>
                <td>${esc(h.from)}${h.from_location ? '<br><span class="small muted">' + esc(h.from_location) + '</span>' : ''}</td>
                <td>${esc(h.to)}${h.to_location ? '<br><span class="small muted">' + esc(h.to_location) + '</span>' : ''}</td>
                <td>${h.count}</td>
            </tr>`
        ).join('') : '<tr><td colspan="3" class="empty-cell">No direct handovers recorded</td></tr>';

        // Drill: click a connected cell to analyze it
        cbody.querySelectorAll('.link-cellid').forEach(a => {
            a.addEventListener('click', (e) => {
                e.preventDefault();
                document.getElementById('cellact-input').value = a.getAttribute('data-cell');
                loadCellActivity();
                document.getElementById('card-cellactivity').scrollIntoView({ behavior: 'smooth' });
            });
        });
        // Drill: click a number to open its entity details (if that modal exists)
        nbody.querySelectorAll('.link-cell').forEach(a => {
            a.addEventListener('click', (e) => {
                e.preventDefault();
                if (typeof openEntityDetails === 'function') {
                    openEntityDetails(a.getAttribute('data-number'));
                }
            });
        });
    } catch (e) {
        toast(e.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// Advanced tools
// ---------------------------------------------------------------------------

// --- Report builder: reuse visuals captured at render time and POST a custom PDF ---

// leaflet-image rasterizes tiles + polylines but NOT the HTML divIcon pins or
// the legend. So after rasterizing we re-draw every numbered stop, the
// start/end rings, direction arrows and a legend directly onto the canvas so
// the PDF map carries the full information the on-screen map shows.
function cacheMovementMap() {
    if (!movementMap || typeof window.leafletImage !== 'function') return;
    const map = movementMap;
    const ctx = movementRenderCtx;
    // Give tiles a moment to finish loading before rasterizing.
    setTimeout(() => {
        try {
            window.leafletImage(map, (err, canvas) => {
                if (err || !canvas) return;
                try {
                    if (ctx && ctx.points && ctx.points.length) {
                        drawMovementOverlay(canvas, map, ctx.points);
                    }
                    visualCache.movement = canvas.toDataURL('image/png');
                } catch (e) { /* ignore */ }
            });
        } catch (e) { /* ignore */ }
    }, 900);
}

function drawMovementOverlay(canvas, map, points) {
    const g = canvas.getContext('2d');
    const DIR_COLOR = { outgoing: '#60A5FA', incoming: '#34D399', mixed: '#FBBF24' };
    const DIR_ARROW = { outgoing: '↑', incoming: '↓', mixed: '↕' };

    points.forEach((d, i) => {
        const pt = map.latLngToContainerPoint([d.lat, d.lon]);
        const x = pt.x, y = pt.y;
        const isFirst = i === 0, isLast = i === points.length - 1;
        const dirn = d.primary_direction || 'mixed';
        const bg = DIR_COLOR[dirn] || '#B3D4FD';
        const ring = isFirst ? '#10B981' : isLast ? '#EF4444' : '#ffffff';

        g.beginPath();
        g.arc(x, y, 15, 0, Math.PI * 2);
        g.fillStyle = bg;
        g.fill();
        g.lineWidth = 3;
        g.strokeStyle = ring;
        g.stroke();

        g.fillStyle = '#1F2937';
        g.font = "700 13px 'JetBrains Mono', monospace";
        g.textAlign = 'center';
        g.textBaseline = 'middle';
        g.fillText(String(i + 1), x, y);

        const arrow = DIR_ARROW[dirn] || '';
        if (arrow) {
            g.fillStyle = bg;
            g.font = "700 13px sans-serif";
            g.fillText(arrow, x + 13, y - 12);
        }
    });

    // Legend, drawn bottom-right.
    const legendItems = [
        ['#34D399', '↓ mostly incoming'],
        ['#60A5FA', '↑ mostly outgoing'],
        ['#FBBF24', '↕ mixed'],
        ['#10B981', 'start (ring)'],
        ['#EF4444', 'end (ring)'],
    ];
    const lw = 168, lh = legendItems.length * 20 + 14;
    const lx = canvas.width - lw - 12, ly = canvas.height - lh - 12;
    g.fillStyle = 'rgba(255,255,255,0.92)';
    g.strokeStyle = '#cbd5e1';
    g.lineWidth = 1;
    g.fillRect(lx, ly, lw, lh);
    g.strokeRect(lx, ly, lw, lh);
    g.textAlign = 'left';
    g.textBaseline = 'middle';
    g.font = "600 12px 'Inter', sans-serif";
    legendItems.forEach(([c, label], i) => {
        const yy = ly + 16 + i * 20;
        g.beginPath();
        g.arc(lx + 14, yy, 6, 0, Math.PI * 2);
        g.fillStyle = c;
        g.fill();
        g.strokeStyle = '#64748b';
        g.lineWidth = 1;
        g.stroke();
        g.fillStyle = '#1f2937';
        g.fillText(label, lx + 28, yy);
    });
}

function collectVisuals() {
    const pick = (v) => {
        const el = document.querySelector(`.report-check input[data-visual="${v}"]`);
        return el ? el.checked : false;
    };
    const visuals = [];

    if (pick('network') && visualCache.network) {
        visuals.push({ title: 'Entity Interaction Network Graph', image: visualCache.network,
            caption: 'Interaction network of numbers; node size reflects connections, colour reflects community.' });
    }
    if (pick('imei') && visualCache.imei) {
        visuals.push({ title: 'IMEI / SIM-Swap Graph', image: visualCache.imei,
            caption: 'Handsets (IMEI) linked to the subscriber numbers that used them; shared handsets indicate SIM swapping.' });
    }
    if (pick('profile') && visualCache.profile) {
        visuals.push({ title: 'Behaviour Profile (Activity by Hour)', image: visualCache.profile,
            caption: 'Distribution of a subject’s activity across the hours of the day, with odd-hour activity highlighted.' });
    }
    if (pick('movement') && visualCache.movement) {
        visuals.push({ title: 'Cell-Tower Movement Reconstruction Map', image: visualCache.movement,
            caption: 'Reconstructed path of the subject across serving cell towers, in chronological order — numbered stops with start (green) and end (red) rings.' });
    }
    return visuals;
}

async function generateCustomReport() {
    if (!currentCaseId) return toast('Open a case first', 'error');

    const sections = {};
    document.querySelectorAll('.report-check input[data-section]').forEach((el) => {
        sections[el.getAttribute('data-section')] = el.checked;
    });

    toast('Building report…');
    let visuals = [];
    try {
        visuals = collectVisuals();
    } catch (e) {
        visuals = [];
    }
    // Tell the backend whether the visuals section should render at all.
    sections.visuals = visuals.length > 0;

    // Figure out which selected visuals had no cached image, to warn the user.
    const wantedNames = { network: 'Network graph', imei: 'IMEI graph',
                          movement: 'Movement map', profile: 'Behaviour chart' };
    const missing = [];
    Object.keys(wantedNames).forEach((k) => {
        const el = document.querySelector(`.report-check input[data-visual="${k}"]`);
        if (el && el.checked && !visualCache[k]) missing.push(wantedNames[k]);
    });
    if (missing.length) {
        toast(`Open these views once so they can be included: ${missing.join(', ')}.`, 'error');
    }

    try {
        const resp = await fetch(`${API_BASE}/report/pdf`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                case_id: currentCaseId,
                sections,
                visuals,
            }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
        setTimeout(() => URL.revokeObjectURL(url), 60000);
        toast('Report generated.');
    } catch (e) {
        toast('Report generation failed. Please try again.', 'error');
    }
}

function setupAdvancedTools() {
    document.getElementById('generate-cdr-report-btn').addEventListener('click', generateCustomReport);



    // --- Behavior profile ---
    document.getElementById('btn-load-profile').addEventListener('click', async () => {
        const sub = document.getElementById('profile-subject-input').value.trim();
        if (!sub) return toast('Enter a subject number/IP', 'error');
        try {
            const data = await api(`/case/${currentCaseId}/subject/${encodeURIComponent(sub)}/profile`);
            if (data.error) return toast(data.error, 'error');

            document.getElementById('profile-results').style.display = 'block';
            document.getElementById('prof-total').textContent = data.total_calls;
            document.getElementById('prof-odd-hours').textContent = `${data.odd_hours_percentage}%`;
            document.getElementById('prof-avg-dur').textContent = `${data.avg_duration}s`;
            document.getElementById('prof-median-dur').textContent = `${data.median_duration}s`;

            const burstsDiv = document.getElementById('prof-bursts');
            if (data.burst_days && data.burst_days.length) {
                burstsDiv.innerHTML = '<h4 style="margin-bottom:0.5rem;">Burst Days (statistical outliers)</h4>' +
                    data.burst_days.map(b =>
                        `<span class="badge badge-danger" style="margin-right:0.5rem;">${b.date} — ${b.count} events (z=${b.z_score})</span>`).join('');
            } else {
                burstsDiv.innerHTML = '';
            }

            if (profileChart) profileChart.destroy();
            const ctx = document.getElementById('profileChart').getContext('2d');
            const labels = Array.from({ length: 24 }, (_, i) => i);
            const vals = labels.map(l => data.hourly_distribution[l] || 0);

            profileChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels.map(l => `${l}:00`),
                    datasets: [{
                        label: 'Interactions per Hour (UTC)',
                        data: vals,
                        backgroundColor: labels.map(l => (l >= 23 || l <= 5)
                            ? 'rgba(245, 158, 11, 0.55)' : 'rgba(59, 130, 246, 0.55)'),
                        borderColor: labels.map(l => (l >= 23 || l <= 5)
                            ? 'rgba(245, 158, 11, 1)' : 'rgba(59, 130, 246, 1)'),
                        borderWidth: 1,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: {
                        onComplete: () => {
                            // Snapshot the chart on a solid background for the PDF.
                            try {
                                const src = profileChart.canvas;
                                const off = document.createElement('canvas');
                                off.width = src.width; off.height = src.height;
                                const octx = off.getContext('2d');
                                octx.fillStyle = '#0e1626';
                                octx.fillRect(0, 0, off.width, off.height);
                                octx.drawImage(src, 0, 0);
                                visualCache.profile = off.toDataURL('image/png');
                            } catch (e) { /* ignore */ }
                        },
                    },
                    plugins: { legend: { labels: { color: '#94a3b8' } } },
                    scales: {
                        y: { beginAtZero: true, ticks: { color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.06)' } },
                        x: { ticks: { color: '#94a3b8' }, grid: { display: false } },
                    },
                },
            });
        } catch (e) {
            toast(e.message, 'error');
        }
    });


    // --- Smart query (with live sentence preview) ---
    const updateSentence = () => {
        const subject = document.getElementById('q-subject').value.trim();
        const start = document.getElementById('q-start').value;
        const end = document.getElementById('q-end').value;
        let s = 'Show all records';
        if (subject) s += ` involving ${subject}`;
        if (start) s += ` after ${start.replace('T', ' ')}`;
        if (end) s += ` and before ${end.replace('T', ' ')}`;
        document.getElementById('query-sentence').textContent = s + ' in this case.';
    };
    ['q-subject', 'q-start', 'q-end'].forEach(id =>
        document.getElementById(id).addEventListener('input', updateSentence));

    document.getElementById('btn-run-query').addEventListener('click', async () => {
        const subject = document.getElementById('q-subject').value.trim();
        const start = document.getElementById('q-start').value;
        const end = document.getElementById('q-end').value;

        const payload = {};
        if (subject) payload.subject_id = subject;
        if (start) payload.start_date = start;
        if (end) payload.end_date = end;

        const tableDiv = document.getElementById('q-results-table');
        tableDiv.innerHTML = '<p class="muted empty-pad">Searching…</p>';

        try {
            const data = await api(`/case/${currentCaseId}/query`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            if (data.length > 0) {
                let html = `<table class="data-table">
                    <thead><tr><th>Time (${displayTZ()})</th><th>Type</th><th>Caller</th><th>Callee</th><th>Duration</th><th>Cell</th></tr></thead><tbody>`;
                data.forEach(r => {
                    html += `<tr>
                        <td>${fmtTime(r.normalized_time)}</td>
                        <td>${esc((r.event_type || '').toUpperCase())}</td>
                        <td class="mono">${esc(r.caller)}</td>
                        <td class="mono">${esc(r.callee)}</td>
                        <td>${r.duration}s</td>
                        <td>${esc(r.cell_id || '—')}</td>
                    </tr>`;
                });
                html += '</tbody></table>';
                tableDiv.innerHTML = html;
                toast(`Query returned ${data.length} record(s)`);
            } else {
                tableDiv.innerHTML = '<p class="muted empty-pad">No results found for this query.</p>';
            }
        } catch (e) {
            tableDiv.innerHTML = `<p class="muted empty-pad">${esc(e.message)}</p>`;
        }
    });
}

// ---------------------------------------------------------------------------
// Evidence & anomalies
// ---------------------------------------------------------------------------

async function loadEvidence() {
    if (!currentCaseId) return;
    try {
        const logs = await api(`/case/${currentCaseId}/custody-log`);
        const tbody = document.getElementById('custody-log-tbody');
        tbody.innerHTML = '';

        if (logs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="empty-cell">No custody entries yet — upload a file to begin the trail.</td></tr>';
            return;
        }

        logs.forEach(log => {
            const hash = (log.sha256_hash && log.sha256_hash !== 'N/A')
                ? `<br><span class="mono muted small">${log.sha256_hash.substring(0, 32)}…</span>` : '';
            tbody.innerHTML += `<tr>
                <td>${fmtTime(log.upload_timestamp)}</td>
                <td><span class="badge badge-high">${esc(log.action.toUpperCase())}</span></td>
                <td class="mono">${esc(log.file_name)}${hash}</td>
                <td>${log.record_count ?? 0}</td>
                <td>${esc(log.uploaded_by)}</td>
            </tr>`;
        });
    } catch (e) {
        toast(e.message, 'error');
    }
}

async function loadAnomalies() {
    if (!currentCaseId) return;
    const container = document.getElementById('anomalies-container');
    container.innerHTML = '<p class="muted">Running detection rules…</p>';

    try {
        const anomalies = await api(`/case/${currentCaseId}/anomalies`);
        container.innerHTML = '';

        if (anomalies.length === 0) {
            container.innerHTML = '<p class="muted">No anomalies detected in the current dataset.</p>';
            refreshSummary();
            return;
        }

        anomalies.forEach(a => {
            const sevBadge = a.severity === 'high'
                ? '<span class="badge badge-danger">High Severity</span>'
                : a.severity === 'medium'
                    ? '<span class="badge badge-medium">Medium Severity</span>'
                    : '<span class="badge badge-low">Low Severity</span>';

            container.innerHTML += `<div class="anomaly-card ${esc(a.severity)}">
                <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:1rem;">
                    <h4>${esc(a.flag_type.replace(/_/g, ' ').toUpperCase())} — <span class="mono">${esc(a.subject_id)}</span></h4>
                    <div>${sevBadge}</div>
                </div>
                <p>${esc(a.description)}</p>
                <p class="anomaly-meta">Window: ${fmtTime(a.start_time)} → ${fmtTime(a.end_time)}
                    · Confidence: ${esc((a.confidence || 'N/A').toUpperCase())}</p>
            </div>`;
        });
        refreshSummary();
    } catch (e) {
        container.innerHTML = `<p class="muted">${esc(e.message)}</p>`;
    }
}
