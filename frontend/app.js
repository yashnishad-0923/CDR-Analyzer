document.addEventListener("DOMContentLoaded", () => {
    const fileInput = document.getElementById("file-input");
    const fileNameDisplay = document.getElementById("file-name");
    const uploadBtn = document.getElementById("upload-btn");
    const statusMsg = document.getElementById("upload-status");
    
    let currentDataType = 'cdr'; // Global state to track 'cdr' or 'ipdr'

    let cy = cytoscape({
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
        layout: {
            name: 'cose'
        }
    });

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
        const file = fileInput.files[0];
        if (!file) return;

        const formData = new FormData();
        formData.append("file", file);

        uploadBtn.disabled = true;
        uploadBtn.textContent = "Uploading...";
        statusMsg.textContent = "";
        statusMsg.className = "status-message";

        try {
            const uploadUrl = currentDataType === 'cdr' ? "/api/v1/upload/cdr" : "/api/v1/upload/ipdr";
            const response = await fetch(uploadUrl, {
                method: "POST",
                body: formData
            });

            if (!response.ok) throw new Error("Upload failed");

            const result = await response.json();
            statusMsg.textContent = `Successfully ingested ${result.ingested_count} records.`;
            statusMsg.classList.add("success");
            
            // Fetch updated data
            fetchDataAndRender();
        } catch (error) {
            statusMsg.textContent = error.message;
            statusMsg.classList.add("error");
        } finally {
            uploadBtn.textContent = "Upload & Normalize";
            uploadBtn.disabled = false;
        }
    });

    async function fetchDataAndRender() {
        try {
            const startDate = document.getElementById("start-date")?.value;
            const endDate = document.getElementById("end-date")?.value;
            let url = currentDataType === 'cdr' ? "/api/v1/cdrs" : "/api/v1/ipdrs";
            
            const params = new URLSearchParams();
            if (startDate) params.append("start_date", startDate);
            if (endDate) params.append("end_date", endDate);
            
            if (params.toString()) {
                url += `?${params.toString()}`;
            }

            const response = await fetch(url);
            const data = await response.json();
            const records = currentDataType === 'cdr' ? data.cdrs : data.ipdrs;
            
            document.getElementById("stat-cdrs").textContent = data.count;
            
            // Build graph elements and aggregation
            const elements = [];
            const nodes = new Set();
            const entityStats = {};
            
            records.forEach(record => {
                let source, target, timeStr, dur;
                if (currentDataType === 'cdr') {
                    source = record.caller;
                    target = record.callee;
                    timeStr = record.start_time;
                    dur = record.duration;
                } else {
                    source = record.source_ip;
                    target = record.dest_ip;
                    timeStr = record.session_start;
                    dur = (record.session_end && record.session_start) ? (new Date(record.session_end) - new Date(record.session_start))/1000 : 0;
                }
                
                // For graph
                if (source) nodes.add(source);
                if (target) nodes.add(target);
                
                if (source && target) {
                    elements.push({
                        data: {
                            id: `${source}-${target}-${timeStr}`,
                            source: source,
                            target: target
                        }
                    });
                }
                
                // For aggregation
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
            
            nodes.forEach(node => {
                elements.push({ data: { id: node } });
            });
            
            document.getElementById("stat-nodes").textContent = nodes.size;
            
            cy.elements().remove();
            cy.add(elements);
            cy.layout({ name: 'cose' }).run();

            // Build aggregation table
            const tbody = document.getElementById("aggregation-tbody");
            if (tbody) {
                tbody.innerHTML = '';
                const sortedEntities = Object.keys(entityStats).sort((a, b) => entityStats[b].count - entityStats[a].count);
                
                if (sortedEntities.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="2" style="padding: 1rem; text-align: center; color: #94a3b8;">No data available</td></tr>';
                    const detailsPanel = document.getElementById("details-panel");
                    if (detailsPanel) {
                        detailsPanel.innerHTML = '<h4 style="margin-top: 0;">Details</h4><p style="color: #94a3b8;">Select an entity to view details.</p>';
                    }
                } else {
                    sortedEntities.forEach(entity => {
                        const tr = document.createElement("tr");
                        tr.style.cursor = "pointer";
                        tr.style.borderBottom = "1px solid rgba(255,255,255,0.1)";
                        
                        tr.onmouseover = () => tr.style.background = "rgba(255,255,255,0.05)";
                        tr.onmouseout = () => tr.style.background = "transparent";
                        
                        tr.innerHTML = `
                            <td style="padding: 0.5rem;">${entity}</td>
                            <td style="padding: 0.5rem;">${entityStats[entity].count}</td>
                        `;
                        
                        tr.addEventListener("click", () => {
                            const detailsPanel = document.getElementById("details-panel");
                            if (!detailsPanel) return;
                            const calls = entityStats[entity].calls;
                            let totalDuration = 0;
                            
                            let html = `<h4 style="margin-top: 0; color: #fff;">Details for ${entity}</h4>`;
                            html += `<div style="max-height: 250px; overflow-y: auto; margin-top: 1rem;">`;
                            html += `<table style="width: 100%; border-collapse: collapse; font-size: 0.85rem;">`;
                            html += `<thead><tr><th style="text-align: left; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 4px;">Time</th><th style="text-align: left; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 4px;">Type</th><th style="text-align: left; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 4px;">Duration</th></tr></thead><tbody>`;
                            
                            calls.forEach(call => {
                                let type, timeStr, dur;
                                if (currentDataType === 'cdr') {
                                    type = call.caller === entity ? "Outgoing" : "Incoming";
                                    timeStr = call.start_time ? new Date(call.start_time).toLocaleString() : 'N/A';
                                    dur = call.duration || 0;
                                } else {
                                    type = call.source_ip === entity ? "Source" : "Dest";
                                    timeStr = call.session_start ? new Date(call.session_start).toLocaleString() : 'N/A';
                                    dur = (call.session_end && call.session_start) ? (new Date(call.session_end) - new Date(call.session_start))/1000 : 0;
                                }
                                totalDuration += dur;
                                html += `<tr><td style="padding: 0.25rem 0; border-bottom: 1px solid rgba(255,255,255,0.05);">${timeStr}</td><td style="border-bottom: 1px solid rgba(255,255,255,0.05);">${type}</td><td style="border-bottom: 1px solid rgba(255,255,255,0.05);">${parseInt(dur)}s</td></tr>`;
                            });
                            
                            html += `</tbody></table></div>`;
                            
                            const statsHtml = `
                                <p style="margin: 0.25rem 0;"><strong>Total Calls/Sessions:</strong> ${entityStats[entity].count}</p>
                                <p style="margin: 0.25rem 0;"><strong>Total Duration:</strong> ${parseInt(totalDuration)}s</p>
                            `;
                            html = html.replace(`<div style="max-height: 250px`, statsHtml + `<div style="max-height: 250px`);
                            
                            detailsPanel.innerHTML = html;
                        });
                        
                        tbody.appendChild(tr);
                    });
                }
            }
            
        } catch (error) {
            console.error("Error fetching data:", error);
        }
    }
    
    // Dropdown toggle logic
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

    const applyFiltersBtn = document.getElementById("apply-filters-btn");
    if (applyFiltersBtn) {
        applyFiltersBtn.addEventListener("click", () => {
            fetchDataAndRender();
        });
    }

    // Tab Navigation Logic
    const navLinks = document.querySelectorAll('.nav-links a[data-target]');
    const cards = {
        'dashboard': ['card-stats', 'card-graph'],
        'ingestion': ['card-filters', 'card-ingestion', 'card-analysis'],
        'graph': ['card-graph'],
        'timeline': ['card-timeline'],
        'reports': ['card-reports']
    };

    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            
            // Remove active from all list items
            document.querySelectorAll('.nav-links li').forEach(li => li.classList.remove('active'));
            // Add active to parent li (if it's in a dropdown menu, it activates the parent li, if not, activates its own li)
            link.parentElement.classList.add('active');
            
            // If the link is inside a dropdown menu, keep the dropdown's parent li active as well
            const parentDropdown = link.closest('.dropdown');
            if (parentDropdown) {
                parentDropdown.classList.add('active');
            }

            const type = link.getAttribute('data-type');
            if (type) {
                currentDataType = type;
                // Update header label dynamically
                const headerEntity = document.getElementById('analysis-header-entity');
                if (headerEntity) {
                    headerEntity.textContent = type === 'cdr' ? 'Phone Number' : 'IP Address';
                }
                // Refetch data for new type
                fetchDataAndRender();
            }

            // Hide all cards
            document.querySelectorAll('.dashboard-grid .card').forEach(card => {
                card.style.display = 'none';
            });

            // Show target cards
            const target = link.getAttribute('data-target');
            if (cards[target]) {
                cards[target].forEach(cardId => {
                    const el = document.getElementById(cardId);
                    if (el) el.style.display = ''; 
                });
                
                if (target === 'dashboard' || target === 'graph') {
                    setTimeout(() => cy.resize(), 50);
                }
            }
        });
    });

    // Initialize layout
    const activeTab = document.querySelector('.nav-links li.active a');
    if (activeTab) {
        activeTab.click();
    } else {
        fetchDataAndRender();
    }

    // Report Generation Logic
    const generateReportBtn = document.getElementById('generate-report-btn');
    if (generateReportBtn) {
        generateReportBtn.addEventListener('click', () => {
            const startDate = document.getElementById("start-date")?.value;
            const endDate = document.getElementById("end-date")?.value;
            let url = currentDataType === 'cdr' ? '/api/v1/report/pdf' : '/api/v1/report/ipdr/pdf';
            const params = new URLSearchParams();
            if (startDate) params.append("start_date", startDate);
            if (endDate) params.append("end_date", endDate);
            
            if (params.toString()) {
                url += `?${params.toString()}`;
            }
            window.open(url, '_blank');
        });
    }
});
