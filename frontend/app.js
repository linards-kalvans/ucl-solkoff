// Frontend application
// Auto-detect API base URL based on environment
const API_BASE_URL = window.location.origin;

let standingsData = [];
// Multi-column sort: array of {column, direction} objects
// Default: Points (primary), Goal Difference (secondary), Goals For (tertiary)
// This matches UEFA Champions League regulations
let sortOrders = [
    {column: 'points', direction: 'desc'},
    {column: 'gd', direction: 'desc'},
    {column: 'gf', direction: 'desc'}
];

// Cache for Solkoff details to avoid repeated API calls
let solkoffDetailsCache = {};
let expandedTeamId = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    loadStandings();
    
    // Refresh button
    document.getElementById('refreshBtn').addEventListener('click', () => {
        refreshData();
    });
    
    // Make table headers sortable
    const headers = document.querySelectorAll('.standings-table th');
    headers.forEach((header, index) => {
        if (header.textContent.trim() !== 'Team') {
            header.addEventListener('click', (e) => {
                // Check if Shift key is held to remove from sort
                if (e.shiftKey) {
                    removeSortOrder(index);
                } else {
                    addSortOrder(index);
                }
            });
        }
    });
});

async function loadStandings() {
    const loadingEl = document.getElementById('loading');
    const errorEl = document.getElementById('error');
    const tableBody = document.getElementById('standingsBody');
    
    loadingEl.style.display = 'block';
    errorEl.style.display = 'none';
    tableBody.innerHTML = '';
    
    try {
        const response = await fetch(`${API_BASE_URL}/api/standings`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        standingsData = await response.json();
        applySorting(); // Apply current sort orders
        renderTable(standingsData);
        updateSortIndicators();
        updateLastUpdated();
        
    } catch (error) {
        console.error('Error loading standings:', error);
        errorEl.textContent = `Error loading standings: ${error.message}`;
        errorEl.style.display = 'block';
    } finally {
        loadingEl.style.display = 'none';
    }
}

function renderTable(data) {
    const tableBody = document.getElementById('standingsBody');
    tableBody.innerHTML = '';
    
    data.forEach(team => {
        // Main team row
        const row = document.createElement('tr');
        row.className = 'team-row';
        row.dataset.teamId = team.teamId;
        
        const logoHtml = team.teamCrest 
            ? `<img src="${team.teamCrest}" alt="${team.teamName}" class="team-logo" onerror="this.style.display='none'">`
            : '<span class="team-logo-placeholder"></span>';
        
        row.innerHTML = `
            <td>${team.position || '-'}</td>
            <td class="team-cell clickable">
                ${logoHtml}
                <span class="team-name">${team.teamName}</span>
            </td>
            <td>${team.played}</td>
            <td>${team.won}</td>
            <td>${team.drawn}</td>
            <td>${team.lost}</td>
            <td>${team.gf}</td>
            <td>${team.ga}</td>
            <td>${team.gd > 0 ? '+' : ''}${team.gd}</td>
            <td><strong>${team.points}</strong></td>
            <td class="clickable">${team.solkoffCoefficient || 0}</td>
            <td class="clickable strength-score">${team.strengthScore || 0}</td>
        `;
        
        // Add click handler to team name and Solkoff cell
        const teamCell = row.querySelector('.team-cell');
        const solkoffCell = row.querySelector('td:nth-child(11)'); // Solkoff column (11th)
        
        teamCell.addEventListener('click', () => toggleTeamDetails(team.teamId));
        solkoffCell.addEventListener('click', () => toggleTeamDetails(team.teamId));
        
        tableBody.appendChild(row);
        
        // Add detail row if this team is expanded
        if (expandedTeamId === team.teamId) {
            const detailRow = createDetailRow(team.teamId);
            tableBody.appendChild(detailRow);
        }
    });
}

function createDetailRow(teamId) {
    const detailRow = document.createElement('tr');
    detailRow.className = 'detail-row';
    detailRow.id = `detail-${teamId}`;
    
    const detailCell = document.createElement('td');
    detailCell.colSpan = 12; // Updated to include new Strength column
    detailCell.className = 'detail-cell';
    detailCell.innerHTML = '<div class="detail-loading">Loading details...</div>';
    
    detailRow.appendChild(detailCell);
    
    // Load details if not cached
    if (solkoffDetailsCache[teamId]) {
        renderDetailContent(detailCell, solkoffDetailsCache[teamId]);
    } else {
        loadSolkoffDetails(teamId, detailCell);
    }
    
    return detailRow;
}

async function loadSolkoffDetails(teamId, detailCell) {
    try {
        const response = await fetch(`${API_BASE_URL}/api/teams/${teamId}/solkoff-details`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const details = await response.json();
        solkoffDetailsCache[teamId] = details;
        renderDetailContent(detailCell, details);
    } catch (error) {
        console.error('Error loading Solkoff details:', error);
        detailCell.innerHTML = `<div class="detail-error">Error loading details: ${error.message}</div>`;
    }
}

function renderDetailContent(detailCell, details) {
    const opponentsList = details.opponents.map(opp => {
        const logoHtml = opp.teamCrest 
            ? `<img src="${opp.teamCrest}" alt="${opp.teamName}" class="opponent-logo" onerror="this.style.display='none'">`
            : '<span class="opponent-logo-placeholder"></span>';
        
        // Calculate match outcomes summary
        const wins = opp.matches ? opp.matches.filter(m => m.outcome === 'win').length : 0;
        const draws = opp.matches ? opp.matches.filter(m => m.outcome === 'draw').length : 0;
        const losses = opp.matches ? opp.matches.filter(m => m.outcome === 'loss').length : 0;
        
        // Format match results
        const matchResults = opp.matches && opp.matches.length > 0
            ? opp.matches.map(match => {
                const scoreDisplay = match.teamScore !== null && match.opponentScore !== null
                    ? `${match.teamScore}-${match.opponentScore}`
                    : 'N/A';
                const outcomeClass = match.outcome === 'win' ? 'match-win' : 
                                    match.outcome === 'loss' ? 'match-loss' : 'match-draw';
                const outcomeIcon = match.outcome === 'win' ? '✓' : 
                                   match.outcome === 'loss' ? '✗' : '=';
                return `<span class="match-result ${outcomeClass}" title="${match.date || ''}">${scoreDisplay} ${outcomeIcon}</span>`;
            }).join(' ')
            : '';
        
        return `
            <div class="opponent-item">
                ${logoHtml}
                <div class="opponent-main-info">
                    <span class="opponent-name">${opp.teamName}</span>
                    <span class="opponent-stats">${wins}W ${draws}D ${losses}L</span>
                </div>
                <div class="opponent-matches-info">
                    <div class="match-results">${matchResults}</div>
                    <div class="opponent-meta">
                        <span class="opponent-points">${opp.points} pts</span>
                        <span class="opponent-matches">${opp.matchesPlayed} match${opp.matchesPlayed !== 1 ? 'es' : ''}</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
    
    const calculationText = details.opponents.length > 0
        ? details.opponents.map(opp => opp.points).join(' + ') + ` = ${details.totalOpponentPoints}`
        : 'No opponents faced yet';
    
    // Calculate Strength Score (Points × Solkoff)
    // We need to get the team's points from the standings data
    const teamData = standingsData.find(t => t.teamId === details.teamId);
    const teamPoints = teamData ? teamData.points : 0;
    const strengthScore = teamPoints * details.solkoffCoefficient;
    const strengthCalculationText = teamPoints > 0 && details.solkoffCoefficient > 0
        ? `${teamPoints} × ${details.solkoffCoefficient} = ${strengthScore}`
        : '0 (no points or Solkoff coefficient)';
    
    detailCell.innerHTML = `
        <div class="detail-panel">
            <div class="detail-header">
                <div class="detail-team-info">
                    ${details.teamCrest ? `<img src="${details.teamCrest}" alt="${details.teamName}" class="detail-team-logo" onerror="this.style.display='none'">` : ''}
                    <div>
                        <h3>${details.teamName}</h3>
                        <p class="detail-subtitle">
                            Solkoff Coefficient: <strong class="solkoff-value">${details.solkoffCoefficient}</strong> • 
                            Strength Score: <strong class="strength-value">${strengthScore}</strong>
                        </p>
                    </div>
                </div>
                <button class="detail-close" onclick="window.toggleTeamDetails(${details.teamId})" aria-label="Close details">×</button>
            </div>
            <div class="detail-content">
                <div class="detail-section">
                    <h4>Opponents Faced (${details.opponentsCount})</h4>
                    <div class="opponents-list">
                        ${details.opponentsCount > 0 ? opponentsList : '<p class="no-opponents">No matches played yet</p>'}
                    </div>
                </div>
                <div class="detail-section">
                    <h4>Calculations</h4>
                    <div class="calculation">
                        <div class="calculation-item">
                            <p class="calculation-label">Solkoff Coefficient:</p>
                            <p class="calculation-formula">${calculationText}</p>
                            <p class="calculation-explanation">
                                Sum of points earned by all opponents faced
                            </p>
                        </div>
                        <div class="calculation-item">
                            <p class="calculation-label">Strength Score:</p>
                            <p class="calculation-formula">${strengthCalculationText}</p>
                            <p class="calculation-explanation">
                                Points × Solkoff Coefficient
                            </p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
}

function toggleTeamDetails(teamId) {
    if (expandedTeamId === teamId) {
        // Collapse
        expandedTeamId = null;
    } else {
        // Expand (collapse any other expanded team)
        expandedTeamId = teamId;
    }
    
    // Re-render table to show/hide detail row
    applySorting();
    renderTable(standingsData);
    updateSortIndicators();
}

// Make function globally accessible for onclick handlers
window.toggleTeamDetails = toggleTeamDetails;

function getColumnName(columnIndex) {
    const columnMap = {
        0: 'position',
        2: 'played',
        3: 'won',
        4: 'drawn',
        5: 'lost',
        6: 'gf',
        7: 'ga',
        8: 'gd',
        9: 'points',
        10: 'solkoffCoefficient',
        11: 'strengthScore'
    };
    return columnMap[columnIndex];
}

function addSortOrder(columnIndex) {
    const column = getColumnName(columnIndex);
    if (!column) return;
    
    // Find if this column is already in sort orders
    const existingIndex = sortOrders.findIndex(order => order.column === column);
    
    if (existingIndex !== -1) {
        // Column already exists in sort orders
        if (sortOrders[existingIndex].direction === 'desc') {
            // Second click: toggle to ascending
            sortOrders[existingIndex].direction = 'asc';
        } else {
            // Third click: remove the sort
            sortOrders.splice(existingIndex, 1);
            
            // If no sort orders left, add default (UEFA regulations order)
            if (sortOrders.length === 0) {
                sortOrders = [
                    {column: 'points', direction: 'desc'},
                    {column: 'gd', direction: 'desc'},
                    {column: 'gf', direction: 'desc'}
                ];
            }
        }
    } else {
        // New column - add as next priority (secondary, tertiary, etc.)
        // If no sorts exist, it becomes primary; otherwise it's appended
        if (sortOrders.length === 0) {
            sortOrders.push({column: column, direction: 'desc'});
        } else {
            // Add as the next priority (after existing sorts)
            sortOrders.push({column: column, direction: 'desc'});
        }
        
        // Limit to 5 sort orders max
        if (sortOrders.length > 5) {
            sortOrders = sortOrders.slice(0, 5);
        }
    }
    
    applySorting();
    renderTable(standingsData);
    updateSortIndicators();
}

function removeSortOrder(columnIndex) {
    const column = getColumnName(columnIndex);
    if (!column) return;
    
    const existingIndex = sortOrders.findIndex(order => order.column === column);
    if (existingIndex !== -1) {
        sortOrders.splice(existingIndex, 1);
        
        // If no sort orders left, add default (UEFA regulations order)
        if (sortOrders.length === 0) {
            sortOrders = [
                {column: 'points', direction: 'desc'},
                {column: 'gd', direction: 'desc'},
                {column: 'gf', direction: 'desc'}
            ];
        }
        
        applySorting();
        renderTable(standingsData);
        updateSortIndicators();
    }
}

function applySorting() {
    // Sort data using all sort orders
    standingsData.sort((a, b) => {
        for (const order of sortOrders) {
            const aVal = a[order.column] || 0;
            const bVal = b[order.column] || 0;
            
            let comparison = 0;
            if (typeof aVal === 'string' && typeof bVal === 'string') {
                comparison = aVal.localeCompare(bVal);
            } else {
                comparison = aVal - bVal;
            }
            
            if (comparison !== 0) {
                return order.direction === 'asc' ? comparison : -comparison;
            }
        }
        return 0; // All sort criteria are equal
    });
}

function updateSortIndicators() {
    const headers = document.querySelectorAll('.standings-table th');
    headers.forEach(header => {
        header.classList.remove('sorted', 'sorted-1', 'sorted-2', 'sorted-3', 'sorted-4', 'sorted-5');
        header.removeAttribute('data-sort-indicator');
    });
    
    const columnMap = {
        'position': 0,
        'played': 2,
        'won': 3,
        'drawn': 4,
        'lost': 5,
        'gf': 6,
        'ga': 7,
        'gd': 8,
        'points': 9,
        'solkoffCoefficient': 10,
        'strengthScore': 11
    };
    
    // Add indicators for each sort order
    sortOrders.forEach((order, index) => {
        const headerIndex = columnMap[order.column];
        if (headerIndex !== undefined) {
            const header = headers[headerIndex];
            header.classList.add('sorted');
            header.classList.add(`sorted-${index + 1}`);
            
            // Update the indicator text
            const priority = index + 1;
            const direction = order.direction === 'asc' ? '▲' : '▼';
            header.setAttribute('data-sort-indicator', `${priority}${direction}`);
        }
    });
}

async function refreshData() {
    const btn = document.getElementById('refreshBtn');
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
    
    try {
        const response = await fetch(`${API_BASE_URL}/api/refresh`, {
            method: 'POST'
        });
        
        if (!response.ok) {
            throw new Error('Refresh failed');
        }
        
        // Wait a moment for data to update, then reload
        setTimeout(() => {
            loadStandings();
            btn.disabled = false;
            btn.textContent = 'Refresh Data';
        }, 1000);
        
    } catch (error) {
        console.error('Error refreshing data:', error);
        btn.disabled = false;
        btn.textContent = 'Refresh Data';
        alert('Error refreshing data. Please try again.');
    }
}

function updateLastUpdated() {
    const lastUpdatedEl = document.getElementById('lastUpdated');
    const now = new Date();
    lastUpdatedEl.textContent = `Last updated: ${now.toLocaleTimeString()}`;
}

