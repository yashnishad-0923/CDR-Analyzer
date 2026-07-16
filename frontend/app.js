let currentCaseId = null;
let currentDataType = 'cdr'; 
let cy = null;
let profileChart = null;

const API_BASE = (window.location.protocol === 'file:' || window.location.port === '5500') ? 'http://localhost:8001/api/v1' : '/api/v1';

document.addEventListener("DOMContentLoaded", () => {
    initApp();
});

async function initApp() {
    setupTabNav();
    setupDropdowns();
    setupUpload();
    setupFilters();
    setupAdvancedTools();
    
    // Initialize Cytoscape
    cy = cytoscape({
        container: document.getElementById('cy-container'),
        style: [
            {
                selector: 'node',
                style: {
                    'background-color': 'transparent',
                    'background-image': '/static/desktop-icon.svg',
                    'background-fit': 'contain',
                    'width': '36px',
                    'height': '36px',
                    'label': 'data(id)',
                    'color': '#fff',
                    'text-outline-color': '#0f172a',
                    'text-outline-width': 2,
                    'font-size': '12px',
                    'text-valign': 'bottom',
                    'text-margin-y': 5
                }
            },
            {
                selector: 'edge',
                style: {
                    'width': 2,
                    'line-color': '#475569',
                    'target-arrow-color': '#475569',
                    'target-arrow-shape': 'triangle',
                    'curve-style': 'bezier',
                    'opacity': 0.6
                }
            }
        ],
        layout: { name: 'cose' }
    });

    await loadCases();

    document.getElementById('submit-new-case-btn').addEventListener('click', async () => {
        const name = document.getElementById('new-case-name').value;
        const number = document.getElementById('new-case-number').value;
        if (!name || !number) return alert("Name and Number required");
        
        try {
            const res = await fetch(`${API_BASE}/cases`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({case_name: name, case_number: number, created_by: "Investigator"})
            });
            if (res.ok) {
                const newCase = await res.json();
                selectCase(newCase);
            }
        } catch (e) {
            console.error(e);
        }
    });
}

async function loadCases() {
    try {
        const res = await fetch(`${API_BASE}/cases`);
        const cases = await res.json();
        const grid = document.getElementById('case-grid');
        grid.innerHTML = '';
        
        cases.forEach(c => {
            const card = document.createElement('div');
            card.className = 'case-card';
            card.innerHTML = `
                <h3 style="margin: 0 0 0.5rem 0; font-size: 1.1rem;">${c.case_name}</h3>
                <p style="margin: 0; font-size: 0.8rem; color: #94a3b8;">ID: ${c.case_number}</p>
                <p style="margin: 0.5rem 0 0 0; font-size: 0.8rem;">Created: ${new Date(c.created_at).toLocaleDateString()}</p>
            `;
            card.onclick = () => selectCase(c);
            grid.appendChild(card);
        });
    } catch (e) {
        console.error(e);
    }
}

function selectCase(caseObj) {
    currentCaseId = caseObj.id;
    document.getElementById('case-selection-overlay').style.display = 'none';
    document.getElementById('main-sidebar').style.display = 'flex';
    document.getElementById('main-content').style.display = 'flex';
    
    document.getElementById('sidebar-case-name').textContent = caseObj.case_name;
    document.getElementById('sidebar-case-number').textContent = `FIR/ID: ${caseObj.case_number}`;
    
    const activeTab = document.querySelector('.nav-links li.active a');
    if (activeTab) activeTab.click();
}

function setupDropdowns() {
    document.querySelectorAll('.dropdown-toggle').forEach(toggle => {
        toggle.addEventListener('click', (e) => {
            e.preventDefault();
            const menu = toggle.nextElementSibling;
            const arrow = toggle.querySelector('.arrow');
            if (menu.style.display === 'none') {
                menu.style.display = 'flex';
                arrow.textContent = '▲';
            } else {
                menu.style.display = 'none';
                arrow.textContent = '▼';
            }
        });
    });
}

function setupTabNav() {
    const navLinks = document.querySelectorAll('.nav-links a[data-target]');
    const cards = {
        'dashboard': ['card-stats', 'card-graph'],
        'ingestion': ['card-filters', 'card-ingestion', 'card-analysis'],
        'graph': ['card-graph'],
        'reports': ['card-reports'],
        'evidence': ['card-evidence'],
        'anomalies': ['card-anomalies'],
        'profile': ['card-profile'],
        'correlation': ['card-correlation'],
        'intersect': ['card-intersect'],
        'query': ['card-query']
    };

    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            document.querySelectorAll('.nav-links li').forEach(li => li.classList.remove('active'));
            link.parentElement.classList.add('active');
            
            const parentDropdown = link.closest('.dropdown');
            if (parentDropdown) parentDropdown.classList.add('active');

            const type = link.getAttribute('data-type');
            if (type) {
                currentDataType = type;
                const headerEntity = document.getElementById('analysis-header-entity');
                if (headerEntity) headerEntity.textContent = type === 'cdr' ? 'Phone Number' : 'IP Address';
                document.getElementById('ingestion-type-label').textContent = type.toUpperCase();
                fetchDataAndRender();
            }

            document.querySelectorAll('.dashboard-grid .card').forEach(card => card.style.display = 'none');

            const target = link.getAttribute('data-target');
            if (cards[target]) {
                cards[target].forEach(cardId => {
                    const el = document.getElementById(cardId);
                    if (el) el.style.display = ''; 
                });
                
                if (target === 'dashboard' || target === 'graph') {
                    setTimeout(() => cy.resize(), 50);
                } else if (target === 'evidence') loadEvidence();
                else if (target === 'anomalies') loadAnomalies();
            }
        });
    });
}

function setupUpload() {
    const fileInput = document.getElementById("file-input");
    const fileNameDisplay = document.getElementById("file-name");
    const uploadBtn = document.getElementById("upload-btn");
    const statusMsg = document.getElementById("upload-status");

    fileInput.addEventListener("change", (e) => {
        if (e.target.files.length > 0) {
            fileNameDisplay.textContent = e.target.files[0].name;
            uploadBtn.disabled = false;
        } else {
            fileNameDisplay.textContent = "No file chosen";
            uploadBtn.disabled = true;
        }
    });

    uploadBtn.addEventListener("click", async () => {
        if (!currentCaseId) return alert("No case selected");
        const file = fileInput.files[0];
        if (!file) return;

        const formData = new FormData();
        formData.append("file", file);
        formData.append("case_id", currentCaseId);
        
        const tz = document.getElementById('upload-timezone').value;
        if (tz) formData.append("timezone", tz);

        uploadBtn.disabled = true;
        uploadBtn.textContent = "Uploading...";
        statusMsg.textContent = "";
        statusMsg.className = "status-message";

        try {
            const uploadUrl = currentDataType === 'cdr' ? `${API_BASE}/upload/cdr` : `${API_BASE}/upload/ipdr`;
            const response = await fetch(uploadUrl, { method: "POST", body: formData });
            if (!response.ok) throw new Error("Upload failed");

            const result = await response.json();
            statusMsg.textContent = `Success! Ingested ${result.ingested_count} records. Hash: ${result.hash.substring(0,10)}...`;
            statusMsg.classList.add("success");
            fetchDataAndRender();
        } catch (error) {
            statusMsg.textContent = error.message;
            statusMsg.classList.add("error");
        } finally {
            uploadBtn.textContent = "Upload & Normalize";
            uploadBtn.disabled = false;
            fileInput.value = "";
        }
    });
}

function setupFilters() {
    document.getElementById("apply-filters-btn").addEventListener("click", fetchDataAndRender);
    document.getElementById("global-timezone").addEventListener("change", fetchDataAndRender);
}

async function fetchDataAndRender() {
    if (!currentCaseId) return;
    try {
        const startDate = document.getElementById("start-date")?.value;
        const endDate = document.getElementById("end-date")?.value;
        let url = currentDataType === 'cdr' ? `${API_BASE}/cdrs` : `${API_BASE}/ipdrs`;
        
        const params = new URLSearchParams({ case_id: currentCaseId });
        if (startDate) params.append("start_date", startDate);
        if (endDate) params.append("end_date", endDate);
        
        url += `?${params.toString()}`;
        const response = await fetch(url);
        const data = await response.json();
        const records = currentDataType === 'cdr' ? data.cdrs : data.ipdrs;
        
        document.getElementById("stat-cdrs").textContent = data.count;
        
        const elements = [];
        const nodes = new Set();
        const entityStats = {};
        
        records.forEach(record => {
            let source, target, timeStr, dur;
            if (currentDataType === 'cdr') {
                source = record.caller; target = record.callee;
                timeStr = record.normalized_time || record.start_time;
                dur = record.duration;
            } else {
                source = record.source_ip; target = record.dest_ip;
                timeStr = record.normalized_session_start || record.session_start;
                dur = (record.session_end && record.session_start) ? (new Date(record.session_end) - new Date(record.session_start))/1000 : 0;
            }
            
            if (source) nodes.add(source);
            if (target) nodes.add(target);
            
            if (source && target) {
                elements.push({ data: { id: `${source}-${target}-${timeStr}`, source: source, target: target }});
            }
            
            if (source) {
                if (!entityStats[source]) entityStats[source] = { count: 0, calls: [] };
                entityStats[source].count++;
                entityStats[source].calls.push(record);
            }
            if (target) {
                if (!entityStats[target]) entityStats[target] = { count: 0, calls: [] };
                entityStats[target].count++;
                entityStats[target].calls.push(record);
            }
        });
        
        nodes.forEach(node => elements.push({ data: { id: node } }));
        document.getElementById("stat-nodes").textContent = nodes.size;
        
        cy.elements().remove();
        cy.add(elements);
        cy.layout({ name: 'cose' }).run();

        renderAnalysisTable(entityStats);
    } catch (error) {
        console.error("Error fetching data:", error);
    }
}

function renderAnalysisTable(entityStats) {
    const tbody = document.getElementById("aggregation-tbody");
    if (!tbody) return;
    tbody.innerHTML = '';
    const sortedEntities = Object.keys(entityStats).sort((a, b) => entityStats[b].count - entityStats[a].count);
    
    if (sortedEntities.length === 0) {
        tbody.innerHTML = '<tr><td colspan="2" style="padding: 1rem; text-align: center; color: #94a3b8;">No data available</td></tr>';
        document.getElementById("details-panel").innerHTML = '<h4 style="margin-top: 0;">Details</h4><p style="color: #94a3b8;">Select an entity to view details.</p>';
        return;
    }
    
    sortedEntities.forEach(entity => {
        const tr = document.createElement("tr");
        tr.style.cursor = "pointer";
        tr.style.borderBottom = "1px solid rgba(255,255,255,0.1)";
        tr.onmouseover = () => tr.style.background = "rgba(255,255,255,0.05)";
        tr.onmouseout = () => tr.style.background = "transparent";
        tr.innerHTML = `<td style="padding: 0.5rem;">${entity}</td><td style="padding: 0.5rem;">${entityStats[entity].count}</td>`;
        
        tr.addEventListener("click", () => {
            const detailsPanel = document.getElementById("details-panel");
            const calls = entityStats[entity].calls;
            let totalDuration = 0;
            
            let html = `<h4 style="margin-top: 0; color: #fff;">Details for ${entity}</h4>`;
            html += `<div style="max-height: 250px; overflow-y: auto; margin-top: 1rem;">`;
            html += `<table style="width: 100%; border-collapse: collapse; font-size: 0.85rem;">`;
            html += `<thead><tr><th style="text-align: left; border-bottom: 1px solid rgba(255,255,255,0.2);">Time (UTC)</th><th style="text-align: left; border-bottom: 1px solid rgba(255,255,255,0.2);">Type</th><th style="text-align: left; border-bottom: 1px solid rgba(255,255,255,0.2);">Duration</th></tr></thead><tbody>`;
            
            calls.forEach(call => {
                let type, timeStr, dur;
                if (currentDataType === 'cdr') {
                    type = call.caller === entity ? "Outgoing" : "Incoming";
                    timeStr = call.normalized_time ? new Date(call.normalized_time).toLocaleString() : 'N/A';
                    dur = call.duration || 0;
                } else {
                    type = call.source_ip === entity ? "Source" : "Dest";
                    timeStr = call.normalized_session_start ? new Date(call.normalized_session_start).toLocaleString() : 'N/A';
                    dur = (call.session_end && call.session_start) ? (new Date(call.session_end) - new Date(call.session_start))/1000 : 0;
                }
                totalDuration += dur;
                html += `<tr><td style="padding: 0.25rem 0; border-bottom: 1px solid rgba(255,255,255,0.05);">${timeStr}</td><td style="border-bottom: 1px solid rgba(255,255,255,0.05);">${type}</td><td style="border-bottom: 1px solid rgba(255,255,255,0.05);">${parseInt(dur)}s</td></tr>`;
            });
            
            html += `</tbody></table></div>`;
            const statsHtml = `<p style="margin: 0.25rem 0;"><strong>Total Count:</strong> ${entityStats[entity].count}</p><p style="margin: 0.25rem 0;"><strong>Total Duration:</strong> ${parseInt(totalDuration)}s</p>`;
            html = html.replace(`<div style="max-height: 250px`, statsHtml + `<div style="max-height: 250px`);
            detailsPanel.innerHTML = html;
        });
        tbody.appendChild(tr);
    });
}

function setupAdvancedTools() {
    document.getElementById('generate-cdr-report-btn').addEventListener('click', () => {
        window.open(`${API_BASE}/report/pdf?case_id=${currentCaseId}`, '_blank');
    });
    
    document.getElementById('generate-ipdr-report-btn').addEventListener('click', () => {
        window.open(`${API_BASE}/report/ipdr/pdf?case_id=${currentCaseId}`, '_blank');
    });

    document.getElementById('btn-load-profile').addEventListener('click', async () => {
        const sub = document.getElementById('profile-subject-input').value;
        if (!sub) return;
        const res = await fetch(`${API_BASE}/case/${currentCaseId}/subject/${sub}/profile`);
        const data = await res.json();
        
        document.getElementById('profile-results').style.display = 'block';
        document.getElementById('prof-odd-hours').textContent = `${data.odd_hours_percentage}%`;
        document.getElementById('prof-avg-dur').textContent = `${data.avg_duration}s`;
        
        if (profileChart) profileChart.destroy();
        const ctx = document.getElementById('profileChart').getContext('2d');
        const labels = Array.from({length: 24}, (_, i) => i);
        const vals = labels.map(l => data.hourly_distribution[l] || 0);
        
        profileChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels.map(l => l + ":00"),
                datasets: [{
                    label: 'Interactions per Hour',
                    data: vals,
                    backgroundColor: 'rgba(59, 130, 246, 0.5)',
                    borderColor: 'rgba(59, 130, 246, 1)',
                    borderWidth: 1
                }]
            },
            options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true } } }
        });
    });

    document.getElementById('btn-load-corr').addEventListener('click', async () => {
        const sub = document.getElementById('corr-subject-input').value;
        if (!sub) return;
        const res = await fetch(`${API_BASE}/case/${currentCaseId}/subject/${sub}/correlation`);
        const data = await res.json();
        
        document.getElementById('corr-results').style.display = 'block';
        document.getElementById('corr-pct').textContent = `${data.overlap_percentage}%`;
        
        const tl = document.getElementById('corr-timeline');
        tl.innerHTML = '';
        data.overlaps.slice(0, 20).forEach(o => {
            tl.innerHTML += `<div style="padding: 0.5rem; border-bottom: 1px solid rgba(255,255,255,0.1);">
                <strong>${new Date(o.time).toLocaleString()}</strong> - Voice call synced with data session (IPDR IDs: ${o.ipdr_ids.join(', ')})
                <span class="badge badge-medium" style="float:right;">Medium Confidence</span>
            </div>`;
        });
        if (data.overlaps.length === 0) tl.innerHTML = "No overlapping sessions found.";
    });

    document.getElementById('btn-load-intersect').addEventListener('click', async () => {
        const input = document.getElementById('intersect-subjects-input').value;
        const subs = input.split(',').map(s => s.trim()).filter(s => s);
        if (subs.length < 2) return alert("Enter at least 2 subjects");
        
        const res = await fetch(`${API_BASE}/case/${currentCaseId}/intersect`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({subject_ids: subs})
        });
        const data = await res.json();
        
        const resDiv = document.getElementById('intersect-results');
        if (data.common_contacts.length > 0) {
            resDiv.innerHTML = `<h4 style="margin:0 0 1rem 0; color:#fff;">Shared Contacts (${data.common_contacts.length}) <span class="badge badge-high" style="float:right;">High Confidence</span></h4>
            <div style="display:flex; flex-wrap:wrap; gap:0.5rem;">` + 
            data.common_contacts.map(c => `<span style="background: rgba(139, 92, 246, 0.2); padding: 0.5rem; border-radius:4px; border: 1px solid rgba(139, 92, 246, 0.5);">${c}</span>`).join('') +
            `</div>`;
        } else {
            resDiv.innerHTML = `<p style="color: #94a3b8;">No shared contacts found.</p>`;
        }
    });

    document.getElementById('btn-run-query').addEventListener('click', async () => {
        const subject = document.getElementById('q-subject').value;
        const start = document.getElementById('q-start').value;
        const end = document.getElementById('q-end').value;
        
        const payload = {};
        if (subject) payload.subject_id = subject;
        if (start) payload.start_date = start;
        if (end) payload.end_date = end;
        
        const res = await fetch(`${API_BASE}/case/${currentCaseId}/query`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        
        const tableDiv = document.getElementById('q-results-table');
        if (data.length > 0) {
            let html = `<table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
                <thead><tr><th style="padding:0.5rem; text-align:left;">Time</th><th style="padding:0.5rem; text-align:left;">Caller</th><th style="padding:0.5rem; text-align:left;">Callee</th><th style="padding:0.5rem; text-align:left;">Dur</th></tr></thead><tbody>`;
            data.forEach(r => {
                html += `<tr>
                    <td style="padding:0.25rem 0.5rem; border-bottom:1px solid rgba(255,255,255,0.1);">${new Date(r.normalized_time).toLocaleString()}</td>
                    <td style="padding:0.25rem 0.5rem; border-bottom:1px solid rgba(255,255,255,0.1);">${r.caller}</td>
                    <td style="padding:0.25rem 0.5rem; border-bottom:1px solid rgba(255,255,255,0.1);">${r.callee}</td>
                    <td style="padding:0.25rem 0.5rem; border-bottom:1px solid rgba(255,255,255,0.1);">${r.duration}s</td>
                </tr>`;
            });
            html += `</tbody></table>`;
            tableDiv.innerHTML = html;
        } else {
            tableDiv.innerHTML = `<p style="padding:1rem;">No results found.</p>`;
        }
    });
}

async function loadEvidence() {
    if (!currentCaseId) return;
    const res = await fetch(`${API_BASE}/case/${currentCaseId}/custody-log`);
    const logs = await res.json();
    
    const tbody = document.getElementById('custody-log-tbody');
    tbody.innerHTML = '';
    
    if (logs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="padding: 1rem; text-align: center; color: #94a3b8;">No logs</td></tr>';
        return;
    }
    
    logs.forEach(log => {
        tbody.innerHTML += `<tr>
            <td style="padding: 0.5rem; border-bottom: 1px solid rgba(255,255,255,0.1);">${new Date(log.upload_timestamp).toLocaleString()}</td>
            <td style="padding: 0.5rem; border-bottom: 1px solid rgba(255,255,255,0.1); font-weight:bold; color:var(--accent-blue);">${log.action.toUpperCase()}</td>
            <td style="padding: 0.5rem; border-bottom: 1px solid rgba(255,255,255,0.1); font-family:monospace;">${log.file_name} <br> <span style="font-size:0.7rem; color:#94a3b8;">${log.sha256_hash.substring(0,24)}...</span></td>
            <td style="padding: 0.5rem; border-bottom: 1px solid rgba(255,255,255,0.1);">${log.uploaded_by}</td>
        </tr>`;
    });
}

async function loadAnomalies() {
    if (!currentCaseId) return;
    const res = await fetch(`${API_BASE}/case/${currentCaseId}/anomalies`);
    const anomalies = await res.json();
    
    const container = document.getElementById('anomalies-container');
    container.innerHTML = '';
    
    if (anomalies.length === 0) {
        container.innerHTML = '<p style="color: #94a3b8;">No anomalies detected.</p>';
        return;
    }
    
    anomalies.forEach(a => {
        let badge = '';
        if (a.severity === 'high') badge = '<span class="badge badge-high" style="float:right;">High Severity</span>';
        else if (a.severity === 'medium') badge = '<span class="badge badge-medium" style="float:right;">Medium Severity</span>';
        else badge = '<span class="badge badge-low" style="float:right;">Low Severity</span>';
        
        container.innerHTML += `<div style="background: rgba(0,0,0,0.2); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 1rem;">
            ${badge}
            <h4 style="margin: 0 0 0.5rem 0; color: #fff;">${a.flag_type.replace('_', ' ').toUpperCase()} - Subject: ${a.subject_id}</h4>
            <p style="margin: 0; font-size: 0.9rem; color: #cbd5e1;">${a.description}</p>
            <p style="margin: 0.5rem 0 0 0; font-size: 0.8rem; color: #94a3b8;">Confidence: ${a.confidence ? a.confidence.toUpperCase() : 'N/A'}</p>
        </div>`;
    });
}
