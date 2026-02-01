// Frontend application
// Auto-detect API base URL based on environment
const API_BASE_URL = window.location.origin;

let standingsData = [];
// Multi-column sort: array of {column, direction} objects
// Default: Strength only
let sortOrders = [
    {column: 'strengthScore', direction: 'desc'}
];

// Cache for Solkoff details to avoid repeated API calls
let solkoffDetailsCache = {};
let expandedTeamId = null;
let currentStage = 'LEAGUE';
let playoffAnalysisCache = {};
let currentPairsList = []; // Store current pairs for navigation
let currentPairIndex = -1; // Current pair index in the list

// Stage mapping
const STAGE_MAP = {
    'LEAGUE': 'league',
    'KNOCKOUT_PLAYOFF': 'knockout-playoff',
    'ROUND_OF_16': 'round-of-16',
    'QUARTER_FINAL': 'quarter-final',
    'SEMI_FINAL': 'semi-final',
    'FINAL': 'final'
};

const STAGE_DISPLAY_NAMES = {
    'league': 'League Table',
    'knockout-playoff': 'Knockout Play-off',
    'round-of-16': 'Round of 16',
    'quarter-final': 'Quarter-final',
    'semi-final': 'Semi-final',
    'final': 'Final'
};

// Helper function to format match dates
function formatMatchDate(dateString) {
    if (!dateString) return '';
    
    try {
        const date = new Date(dateString);
        
        // Check if date is valid
        if (isNaN(date.getTime())) {
            return '';
        }
        
        // Check if date is in the future and seems reasonable (not more than 2 years ahead)
        const now = new Date();
        const twoYearsFromNow = new Date();
        twoYearsFromNow.setFullYear(now.getFullYear() + 2);
        
        // If date is more than 2 years in the future, it's likely invalid/placeholder
        if (date > twoYearsFromNow) {
            return ''; // Don't show invalid dates
        }
        
        // Also check if date is in the past (more than 6 months ago for scheduled matches)
        // This catches invalid placeholder dates like July 2025 when we're in 2026
        const sixMonthsAgo = new Date();
        sixMonthsAgo.setMonth(now.getMonth() - 6);
        if (date < sixMonthsAgo) {
            return ''; // Don't show old/invalid dates
        }
        
        // Format as readable date
        return date.toLocaleDateString('en-US', { 
            month: 'short', 
            day: 'numeric', 
            year: 'numeric' 
        });
    } catch (e) {
        console.warn('Error formatting date:', dateString, e);
        return ''; // Return empty if parsing fails
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, initializing...');
    
    // Initialize tabs
    initializeTabs();
    
    // Check for URL hash first (for permalinks)
    const hash = window.location.hash.slice(1); // Remove the '#'
    const validTabIds = ['league', 'knockout-playoff', 'round-of-16', 'quarter-final', 'semi-final', 'final'];
    
    if (hash && validTabIds.includes(hash)) {
        // Hash takes precedence - activate the tab from URL
        console.log(`Activating tab from URL hash: ${hash}`);
        activateTab(hash);
    } else {
        // No valid hash, ensure knockout-playoff tab is active by default (matches HTML)
        // This ensures it loads immediately even if loadCurrentStage is async
        const knockoutTabBtn = document.querySelector('.tab-btn[data-tab="knockout-playoff"]');
        const knockoutTabPane = document.getElementById('knockout-playoff-tab');
        if (knockoutTabBtn && knockoutTabPane && knockoutTabBtn.classList.contains('active')) {
            // Load content for knockout-playoff tab immediately
            loadTabContent('knockout-playoff');
        }
        
        // Load current stage and switch to appropriate tab (defaults to knockout-playoff)
        loadCurrentStage();
    }
    
    // Listen for hash changes (back/forward browser buttons)
    window.addEventListener('hashchange', () => {
        const newHash = window.location.hash.slice(1);
        if (newHash && validTabIds.includes(newHash)) {
            activateTab(newHash);
        }
    });
    
    // Refresh button
    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            refreshData();
        });
    }
    
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

function initializeTabs() {
    const tabButtons = document.querySelectorAll('.tab-btn');
    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.dataset.tab;
            activateTab(tabId);
        });
    });
}

async function loadCurrentStage() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/tournament/current-stage`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        currentStage = data.stage;
        
        // Always default to knockout-playoff tab, regardless of API stage
        // Only switch if we have actual knockout data for a later stage (not LEAGUE)
        let tabId = 'knockout-playoff'; // Default
        
        // Only switch to a different knockout stage if we're past play-off
        if (currentStage && currentStage !== 'LEAGUE' && currentStage !== 'KNOCKOUT_PLAYOFF') {
            const mappedTabId = STAGE_MAP[currentStage];
            if (mappedTabId && mappedTabId !== 'league') {
                tabId = mappedTabId;
            }
        }
        
        // Only activate tab if it's different from current active tab
        const currentActiveTab = document.querySelector('.tab-btn.active')?.getAttribute('data-tab');
        if (currentActiveTab !== tabId) {
            console.log(`Current stage: ${currentStage}, Switching to tab: ${tabId}`);
            activateTab(tabId);
        } else {
            console.log(`Current stage: ${currentStage}, Keeping tab: ${tabId}`);
        }
    } catch (error) {
        console.error('Error loading current stage:', error);
        // Default to knockout-playoff tab
        console.log('Defaulting to knockout-playoff tab');
        activateTab('knockout-playoff');
    }
}

function activateTab(tabId) {
    // Remove active class from all tabs and panes
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
    
    // Activate selected tab
    const tabBtn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
    const tabPane = document.getElementById(`${tabId}-tab`);
    
    if (tabBtn && tabPane) {
        tabBtn.classList.add('active');
        tabPane.classList.add('active');
        
        // Hide loading indicator when switching tabs
        const loadingEl = document.getElementById('loading');
        if (loadingEl) {
            loadingEl.style.display = 'none';
        }
        
        // Update URL hash for permalink
        if (window.location.hash !== `#${tabId}`) {
            window.history.replaceState(null, '', `#${tabId}`);
        }
        
        // Load content for the activated tab
        loadTabContent(tabId);
    }
}

function loadTabContent(tabId) {
    if (tabId === 'league') {
        loadStandings();
    } else {
        const stageKey = Object.keys(STAGE_MAP).find(key => STAGE_MAP[key] === tabId);
        if (stageKey) {
            loadKnockoutPairs(stageKey, tabId);
        }
    }
}

async function loadKnockoutPairs(stage, tabId) {
    const containerId = `${tabId}-pairs`;
    const container = document.getElementById(containerId);
    
    if (!container) {
        console.error(`Container not found: ${containerId}`);
        return;
    }
    
    // Show loading only if this tab is active
    const tabPane = document.getElementById(`${tabId}-tab`);
    if (tabPane && tabPane.classList.contains('active')) {
        container.innerHTML = '<div class="loading">Loading pairs...</div>';
    }
    
    try {
        console.log(`Fetching pairs for stage: ${stage}, tabId: ${tabId}`);
        const response = await fetch(`${API_BASE_URL}/api/knockout-pairs/${stage}`);
        
        if (response.status === 404 || response.status === 400) {
            console.log(`No data available for ${stage}`);
            showDataNotAvailable(container, stage);
            return;
        }
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const pairs = await response.json();
        console.log(`Received ${pairs.length} pairs for ${stage}`);
        
        if (pairs.length === 0) {
            showDataNotAvailable(container, stage);
            return;
        }
        
        // Store pairs globally for navigation
        currentPairsList = pairs;
        renderKnockoutPairs(pairs, container);
    } catch (error) {
        console.error(`Error loading pairs for ${stage}:`, error);
        showDataNotAvailable(container, stage);
    }
}

function showDataNotAvailable(container, stage) {
    const tabId = STAGE_MAP[stage] || stage.toLowerCase().replace('_', '-');
    const displayName = STAGE_DISPLAY_NAMES[tabId] || stage;
    
    // Check if we're in league stage - if so, provide more helpful message
    const isLeagueStage = currentStage === 'LEAGUE';
    const message = isLeagueStage 
        ? `The ${displayName} stage has not started yet. Draw information will be available once the tournament reaches this stage.`
        : `The ${displayName} stage has not started yet or data is not available.`;
    
    container.innerHTML = `
        <div class="data-not-available">
            <div class="data-not-available-icon">📅</div>
            <h3>Data Not Available Yet</h3>
            <p>${message}</p>
            ${isLeagueStage ? '<p class="info-note">The tournament is currently in the league/group stage. Knockout pair information will appear here once the draw is made and matches are scheduled.</p>' : ''}
        </div>
    `;
}

function renderKnockoutPairs(pairs, container) {
    container.innerHTML = '';
    
    if (pairs.length === 0) {
        container.innerHTML = '<div class="data-not-available"><p>No pairs found for this stage.</p></div>';
        return;
    }
    
    const pairsGrid = document.createElement('div');
    pairsGrid.className = 'playoff-pairs-grid';
    
    pairs.forEach((pair) => {
        const pairCard = document.createElement('div');
        pairCard.className = 'playoff-pair-card';
        pairCard.dataset.team1Id = pair.team1.id;
        pairCard.dataset.team2Id = pair.team2.id;
        
        const team1Logo = pair.team1.crest 
            ? `<img src="${pair.team1.crest}" alt="${pair.team1.name}" class="pair-team-logo" onerror="this.style.display='none'">`
            : '<span class="pair-team-logo-placeholder"></span>';
        
        const team2Logo = pair.team2.crest 
            ? `<img src="${pair.team2.crest}" alt="${pair.team2.name}" class="pair-team-logo" onerror="this.style.display='none'">`
            : '<span class="pair-team-logo-placeholder"></span>';
        
        const statusBadge = pair.status === 'FINISHED' 
            ? '<span class="status-badge finished">Finished</span>'
            : pair.status === 'SCHEDULED' || pair.status === 'TIMED'
            ? '<span class="status-badge scheduled">Scheduled</span>'
            : pair.status === 'LIVE' || pair.status === 'IN_PLAY'
            ? '<span class="status-badge live">Live</span>'
            : '';
        
        // Calculate win probability display (excluding draws)
        let winProbabilityHtml = '';
        if (pair.winProbability) {
            const prob = pair.winProbability;
            // Normalize probabilities excluding draws to ensure they sum to 100%
            const winTotal = (prob.team1Win || 0) + (prob.team2Win || 0);
            const normalizedTeam1 = winTotal > 0 ? (prob.team1Win || 0) / winTotal : 0.5;
            const normalizedTeam2 = winTotal > 0 ? (prob.team2Win || 0) / winTotal : 0.5;
            const team1Win = (normalizedTeam1 * 100).toFixed(1);
            const team2Win = (normalizedTeam2 * 100).toFixed(1);
            
            winProbabilityHtml = `
                <div class="pair-probability">
                    <div style="flex: 1;">
                        <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                            <span class="probability-label" style="font-size: 0.8em;">${pair.team1.name}</span>
                            <span class="probability-value">${team1Win}%</span>
                        </div>
                        <div class="probability-bar-mini">
                            <div class="probability-fill-mini team1" style="width: ${team1Win}%"></div>
                        </div>
                    </div>
                    <div style="flex: 1; margin-left: 10px;">
                        <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                            <span class="probability-label" style="font-size: 0.8em;">${pair.team2.name}</span>
                            <span class="probability-value">${team2Win}%</span>
                        </div>
                        <div class="probability-bar-mini">
                            <div class="probability-fill-mini team2" style="width: ${team2Win}%"></div>
                        </div>
                    </div>
                </div>
            `;
        }
        
        pairCard.innerHTML = `
            <div class="pair-header">
                <div class="pair-team">
                    ${team1Logo}
                    <span class="pair-team-name">${pair.team1.name}</span>
                </div>
                <div class="pair-vs">vs</div>
                <div class="pair-team">
                    ${team2Logo}
                    <span class="pair-team-name">${pair.team2.name}</span>
                </div>
            </div>
            <div class="pair-meta">
                ${statusBadge}
                ${pair.date ? `<span class="pair-date">${formatMatchDate(pair.date)}</span>` : ''}
            </div>
            ${winProbabilityHtml}
            <button class="pair-analyze-btn" onclick="showPlayoffAnalysis(${pair.team1.id}, ${pair.team2.id})">
                View Historical Analysis
            </button>
        `;
        
        pairsGrid.appendChild(pairCard);
    });
    
    container.appendChild(pairsGrid);
}

async function loadStandings() {
    const loadingEl = document.getElementById('loading');
    const errorEl = document.getElementById('error');
    const tableBody = document.getElementById('standingsBody');
    
    if (!tableBody) {
        console.error('Table body not found');
        return;
    }
    
    // Show loading if element exists and league tab is active
    if (loadingEl) {
        const leagueTab = document.getElementById('league-tab');
        if (leagueTab && leagueTab.classList.contains('active')) {
            loadingEl.style.display = 'block';
            console.log('Showing loading indicator');
        }
    }
    
    if (errorEl) {
        errorEl.style.display = 'none';
    }
    
    // Clear table body
    tableBody.innerHTML = '';
    
    try {
        console.log('Fetching standings from:', `${API_BASE_URL}/api/standings`);
        const response = await fetch(`${API_BASE_URL}/api/standings`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        standingsData = await response.json();
        console.log('Standings data received:', standingsData ? standingsData.length : 0, 'teams');
        
        if (standingsData && standingsData.length > 0) {
            applySorting(); // Apply current sort orders
            renderTable(standingsData);
            updateSortIndicators();
            updateLastUpdated();
            console.log('Table rendered successfully');
        } else {
            console.warn('No standings data received');
            tableBody.innerHTML = '<tr><td colspan="12" style="text-align: center; padding: 20px;">No standings data available</td></tr>';
        }
        
    } catch (error) {
        console.error('Error loading standings:', error);
        if (errorEl) {
            errorEl.textContent = `Error loading standings: ${error.message}`;
            errorEl.style.display = 'block';
        }
        tableBody.innerHTML = '<tr><td colspan="12" style="text-align: center; padding: 20px; color: #dc3545;">Error: ' + error.message + '</td></tr>';
    } finally {
        // Always hide loading
        if (loadingEl) {
            loadingEl.style.display = 'none';
            console.log('Hiding loading indicator');
        }
    }
}

function renderTable(data) {
    const tableBody = document.getElementById('standingsBody');
    tableBody.innerHTML = '';
    
    data.forEach((team, index) => {
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
            <td class="clickable">${(team.solkoffCoefficient || 0).toFixed(2)}</td>
            <td class="clickable strength-score">${(team.strengthScore || 0).toFixed(2)}</td>
        `;
        
        // Add click handler to team name and Solkoff cell
        const teamCell = row.querySelector('.team-cell');
        const solkoffCell = row.querySelector('td:nth-child(11)'); // Solkoff column (11th)
        
        teamCell.addEventListener('click', () => toggleTeamDetails(team.teamId));
        solkoffCell.addEventListener('click', () => toggleTeamDetails(team.teamId));
        
        tableBody.appendChild(row);
        
        // Add detail row if this team is expanded
        let detailRow = null;
        if (expandedTeamId === team.teamId) {
            detailRow = createDetailRow(team.teamId);
            tableBody.appendChild(detailRow);
        }
        
        // Add qualification line classes for 8th and 24th rows based on visual position
        // This ensures lines stay in place regardless of sorting
        // index is 0-based, so 8th row is index 7, 24th row is index 23
        // If there's a detail row, put the qualification line on it; otherwise on the team row
        const targetRow = detailRow || row;
        if (index === 7) {
            targetRow.classList.add('qualification-round-of-16');
        } else if (index === 23) {
            targetRow.classList.add('qualification-knockout');
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
                        <span class="opponent-points">${opp.pointsPerGame ? opp.pointsPerGame.toFixed(3) : '0.000'} PPG</span>
                        <span class="opponent-matches">${opp.matchesPlayed} match${opp.matchesPlayed !== 1 ? 'es' : ''}</span>
                    </div>
                </div>
            </div>
        `;
    }).join('');
    
    const calculationText = details.opponents.length > 0
        ? details.opponents.map(opp => `${opp.pointsPerGame ? opp.pointsPerGame.toFixed(3) : '0.000'}`).join(' + ') + ` = ${details.averageOpponentPPG ? details.averageOpponentPPG.toFixed(3) : '0.000'} (avg)`
        : 'No opponents faced yet';
    
    // Calculate Strength Score (Points % × Solkoff)
    // We need to get the team's points and played from the standings data
    const teamData = standingsData.find(t => t.teamId === details.teamId);
    const teamPoints = teamData ? teamData.points : 0;
    const teamPlayed = teamData ? teamData.played : 0;
    const solkoffCoeff = details.solkoffCoefficient || 0;
    const pointsPercentage = teamPlayed > 0 ? (teamPoints / (teamPlayed * 3)) * 100 : 0;
    const strengthScore = (pointsPercentage / 100.0) * solkoffCoeff;
    const strengthCalculationText = teamPlayed > 0 && solkoffCoeff > 0
        ? `${pointsPercentage.toFixed(1)}% × ${solkoffCoeff.toFixed(3)} / 100 = ${strengthScore.toFixed(2)}`
        : '0 (no points or Solkoff coefficient)';
    
    detailCell.innerHTML = `
        <div class="detail-panel">
            <div class="detail-header">
                <div class="detail-team-info">
                    ${details.teamCrest ? `<img src="${details.teamCrest}" alt="${details.teamName}" class="detail-team-logo" onerror="this.style.display='none'">` : ''}
                    <div>
                        <h3>${details.teamName}</h3>
                        <p class="detail-subtitle">
                            Solkoff Coefficient: <strong class="solkoff-value">${(details.solkoffCoefficient || 0).toFixed(2)}</strong> • 
                            Strength Score: <strong class="strength-value">${strengthScore.toFixed(2)}</strong>
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
                                Average points per game of all opponents faced
                            </p>
                        </div>
                        <div class="calculation-item">
                            <p class="calculation-label">Strength Score:</p>
                            <p class="calculation-formula">${strengthCalculationText}</p>
                            <p class="calculation-explanation">
                                (Points % × Solkoff Coefficient) / 100 (avg PPG of opponents)
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

// Play-off analysis functionality
// Note: playoffAnalysisCache is already declared at the top of the file

async function showPlayoffAnalysis(team1Id, team2Id) {
    // Find current pair index in the list
    currentPairIndex = currentPairsList.findIndex(p => 
        (p.team1.id == team1Id && p.team2.id == team2Id) || 
        (p.team1.id == team2Id && p.team2.id == team1Id)
    );
    
    const cacheKey = `${team1Id}-${team2Id}`;
    
    // Check cache first
    if (playoffAnalysisCache[cacheKey]) {
        await displayPlayoffAnalysis(playoffAnalysisCache[cacheKey], team1Id, team2Id);
        return;
    }
    
    // Show loading
    const modal = createAnalysisModal();
    modal.querySelector('.analysis-content').innerHTML = '<div class="loading">Loading analysis...</div>';
    
    try {
        const response = await fetch(`${API_BASE_URL}/api/playoff-pairs/${team1Id}/${team2Id}/analysis`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const analysis = await response.json();
        playoffAnalysisCache[cacheKey] = analysis;
        await displayPlayoffAnalysis(analysis, team1Id, team2Id);
    } catch (error) {
        console.error('Error loading play-off analysis:', error);
        const modal = document.getElementById('playoffAnalysisModal');
        if (modal) {
            modal.querySelector('.analysis-content').innerHTML = 
                `<div class="error">Error loading analysis: ${error.message}</div>`;
        }
    }
}

function createAnalysisModal() {
    let modal = document.getElementById('playoffAnalysisModal');
    
    // If modal exists but doesn't have the structure, create it
    if (modal) {
        // Check if modal already has the structure
        if (!modal.querySelector('.analysis-content')) {
            modal.className = 'playoff-modal';
            modal.innerHTML = `
                <div class="modal-overlay" onclick="closePlayoffAnalysis()"></div>
                <div class="modal-content">
                    <div class="modal-header">
                        <h2>Historical Analysis</h2>
                        <button class="modal-close" onclick="closePlayoffAnalysis()">×</button>
                    </div>
                    <div class="analysis-content"></div>
                </div>
            `;
        }
        // Make sure modal is visible
        modal.style.display = 'block';
        
        // Add keyboard navigation if not already added
        if (!modal._keyHandler) {
            const handleKeyPress = (e) => {
                if (modal.style.display === 'none' || !modal.classList.contains('playoff-modal')) {
                    return;
                }
                
                if (e.key === 'ArrowLeft') {
                    e.preventDefault();
                    if (currentPairIndex > 0) {
                        navigateToPair(currentPairIndex - 1);
                    }
                } else if (e.key === 'ArrowRight') {
                    e.preventDefault();
                    if (currentPairIndex >= 0 && currentPairIndex < currentPairsList.length - 1) {
                        navigateToPair(currentPairIndex + 1);
                    }
                } else if (e.key === 'Escape') {
                    e.preventDefault();
                    closePlayoffAnalysis();
                }
            };
            modal._keyHandler = handleKeyPress;
            document.addEventListener('keydown', handleKeyPress);
        }
        
        return modal;
    }
    
    // Create new modal if it doesn't exist
    modal = document.createElement('div');
    modal.id = 'playoffAnalysisModal';
    modal.className = 'playoff-modal';
    modal.innerHTML = `
        <div class="modal-overlay" onclick="closePlayoffAnalysis()"></div>
        <div class="modal-content">
            <div class="modal-header">
                <h2>Historical Analysis</h2>
                <button class="modal-close" onclick="closePlayoffAnalysis()">×</button>
            </div>
            <div class="analysis-content"></div>
        </div>
    `;
    document.body.appendChild(modal);
    
    // Add keyboard navigation (arrow keys)
    const handleKeyPress = (e) => {
        if (modal.style.display === 'none' || !modal.classList.contains('playoff-modal')) {
            return;
        }
        
        if (e.key === 'ArrowLeft') {
            e.preventDefault();
            if (currentPairIndex > 0) {
                navigateToPair(currentPairIndex - 1);
            }
        } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            if (currentPairIndex >= 0 && currentPairIndex < currentPairsList.length - 1) {
                navigateToPair(currentPairIndex + 1);
            }
        } else if (e.key === 'Escape') {
            e.preventDefault();
            closePlayoffAnalysis();
        }
    };
    
    // Remove old listener if exists
    document.removeEventListener('keydown', modal._keyHandler);
    // Add new listener
    modal._keyHandler = handleKeyPress;
    document.addEventListener('keydown', handleKeyPress);
    
    return modal;
}

async function displayPlayoffAnalysis(analysis, team1Id, team2Id) {
    const modal = createAnalysisModal();
    const content = modal.querySelector('.analysis-content');
    
    if (!content) {
        console.error('Analysis content element not found in modal');
        return;
    }
    
    // Update current pair index if not already set
    if (currentPairIndex < 0 && team1Id && team2Id) {
        currentPairIndex = currentPairsList.findIndex(p => 
            (p.team1.id == team1Id && p.team2.id == team2Id) || 
            (p.team1.id == team2Id && p.team2.id == team1Id)
        );
    }
    
    const team1 = analysis.team1;
    const team2 = analysis.team2;
    const leagueTable = analysis.leagueTable;
    const fullLeagueTable = leagueTable.fullLeagueTable || [];
    const winProbability = leagueTable.winProbability || {team1Win: 0.5, team2Win: 0.5, draw: 0.0};
    
    // Find positions of team1 and team2 in the MAIN UCL league table (not the historical mini-table)
    // Get position from standingsData which contains the current season's league table
    // If standingsData is empty, try to load it first
    let team1Position = null;
    let team2Position = null;
    
    if (standingsData && standingsData.length > 0) {
        const team1MainStanding = standingsData.find(t => {
            const tId = t.teamId;
            const team1Id = team1.id;
            // Try multiple comparison methods
            return tId == team1Id || parseInt(tId) === parseInt(team1Id) || String(tId) === String(team1Id);
        });
        const team2MainStanding = standingsData.find(t => {
            const tId = t.teamId;
            const team2Id = team2.id;
            return tId == team2Id || parseInt(tId) === parseInt(team2Id) || String(tId) === String(team2Id);
        });
        
        // Get position from main league table (this year's UCL standings)
        team1Position = team1MainStanding ? (team1MainStanding.position || null) : null;
        team2Position = team2MainStanding ? (team2MainStanding.position || null) : null;
        
        // Debug logging
        if (!team1Position || !team2Position) {
            console.log('Position lookup:', {
                standingsDataLength: standingsData.length,
                team1: { id: team1.id, name: team1.name, found: !!team1MainStanding, position: team1Position },
                team2: { id: team2.id, name: team2.name, found: !!team2MainStanding, position: team2Position },
                sampleStanding: standingsData[0] ? { teamId: standingsData[0].teamId, position: standingsData[0].position } : null
            });
        }
    } else {
        // standingsData not loaded yet, try to load it
        console.log('standingsData not available, attempting to load...');
        try {
            const response = await fetch(`${API_BASE_URL}/api/standings`);
            if (response.ok) {
                const loadedStandings = await response.json();
                const team1MainStanding = loadedStandings.find(t => {
                    const tId = t.teamId;
                    const team1Id = team1.id;
                    return tId == team1Id || parseInt(tId) === parseInt(team1Id) || String(tId) === String(team1Id);
                });
                const team2MainStanding = loadedStandings.find(t => {
                    const tId = t.teamId;
                    const team2Id = team2.id;
                    return tId == team2Id || parseInt(tId) === parseInt(team2Id) || String(tId) === String(team2Id);
                });
                team1Position = team1MainStanding ? (team1MainStanding.position || null) : null;
                team2Position = team2MainStanding ? (team2MainStanding.position || null) : null;
            }
        } catch (error) {
            console.error('Error loading standings for position lookup:', error);
        }
    }
    
    const team1Stats = leagueTable.team1;
    const team2Stats = leagueTable.team2;
    
    // Determine winner based on league position
    let winner = null;
    if (team1Position !== null && team2Position !== null) {
        if (team1Position < team2Position) {
            winner = 1;
        } else if (team2Position < team1Position) {
            winner = 2;
        }
    }
    
    const winnerClass1 = winner === 1 ? 'winner' : '';
    const winnerClass2 = winner === 2 ? 'winner' : '';
    
    // Format win probability - normalize excluding draws to ensure they sum to 100%
    const winTotal = (winProbability.team1Win || 0) + (winProbability.team2Win || 0);
    const normalizedTeam1 = winTotal > 0 ? (winProbability.team1Win || 0) / winTotal : 0.5;
    const normalizedTeam2 = winTotal > 0 ? (winProbability.team2Win || 0) / winTotal : 0.5;
    
    const team1WinPercent = (normalizedTeam1 * 100).toFixed(1);
    const team2WinPercent = (normalizedTeam2 * 100).toFixed(1);
    
    // Extract direct matches between team1 and team2
    const team1Data = fullLeagueTable.find(t => t.teamId === team1.id);
    const team2Data = fullLeagueTable.find(t => t.teamId === team2.id);
    
    const directMatches = [];
    if (team1Data && team1Data.matches) {
        // Find matches where team1 played against team2
        const team1VsTeam2 = team1Data.matches.filter(m => 
            m.opponentId === team2.id || String(m.opponentId) === String(team2.id)
        );
        directMatches.push(...team1VsTeam2.map(m => ({
            ...m,
            teamName: team1.name,
            teamCrest: team1.crest,
            opponentName: team2.name,
            opponentCrest: team2.crest,
            isHome: m.isHome
        })));
    }
    
    // Sort direct matches by date (most recent first)
    directMatches.sort((a, b) => {
        if (!a.date && !b.date) return 0;
        if (!a.date) return 1;
        if (!b.date) return -1;
        return new Date(b.date) - new Date(a.date);
    });
    
    // Navigation buttons
    const hasPrevious = currentPairIndex > 0;
    const hasNext = currentPairIndex >= 0 && currentPairIndex < currentPairsList.length - 1;
    const pairNumber = currentPairIndex >= 0 ? currentPairIndex + 1 : 0;
    const totalPairs = currentPairsList.length;
    
    const navButtons = totalPairs > 1 ? `
        <div class="analysis-navigation">
            <button class="nav-btn nav-prev" ${!hasPrevious ? 'disabled' : ''} onclick="navigateToPair(${currentPairIndex - 1})" title="Previous pair">
                <span>‹</span>
            </button>
            <span class="pair-counter">${pairNumber} / ${totalPairs}</span>
            <button class="nav-btn nav-next" ${!hasNext ? 'disabled' : ''} onclick="navigateToPair(${currentPairIndex + 1})" title="Next pair">
                <span>›</span>
            </button>
        </div>
    ` : '';
    
    // Build direct matches HTML
    const directMatchesHtml = directMatches.length > 0 ? `
        <div class="direct-matches-section">
            <h3>Direct Matches: ${team1.name} vs ${team2.name}</h3>
            <div class="direct-matches-list">
                ${directMatches.map(match => {
                    const outcomeClass = match.outcome === 'win' ? 'match-win' : 
                                        match.outcome === 'loss' ? 'match-loss' : 'match-draw';
                    const outcomeIcon = match.outcome === 'win' ? '✓' : 
                                       match.outcome === 'loss' ? '✗' : '=';
                    const homeTeam = match.isHome ? match.teamName : match.opponentName;
                    const awayTeam = match.isHome ? match.opponentName : match.teamName;
                    const homeScore = match.isHome ? match.teamScore : match.opponentScore;
                    const awayScore = match.isHome ? match.opponentScore : match.teamScore;
                    const homeCrest = match.isHome ? match.teamCrest : match.opponentCrest;
                    const awayCrest = match.isHome ? match.opponentCrest : match.teamCrest;
                    
                    return `
                        <div class="direct-match-item ${outcomeClass}">
                            <div class="direct-match-teams">
                                <div class="direct-match-team">
                                    ${homeCrest ? `<img src="${homeCrest}" alt="${homeTeam}" class="direct-match-logo" onerror="this.style.display='none'">` : ''}
                                    <span class="direct-match-team-name">${homeTeam}</span>
                                </div>
                                <div class="direct-match-score">
                                    <span class="score">${homeScore}-${awayScore}</span>
                                    <span class="match-outcome-icon">${outcomeIcon}</span>
                                </div>
                                <div class="direct-match-team">
                                    ${awayCrest ? `<img src="${awayCrest}" alt="${awayTeam}" class="direct-match-logo" onerror="this.style.display='none'">` : ''}
                                    <span class="direct-match-team-name">${awayTeam}</span>
                                </div>
                            </div>
                            <div class="direct-match-meta">
                                <span class="match-date">${match.date ? new Date(match.date).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' }) : ''}</span>
                                ${match.isHome ? '<span class="match-venue">Home</span>' : '<span class="match-venue">Away</span>'}
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        </div>
    ` : `
        <div class="direct-matches-section">
            <h3>Direct Matches: ${team1.name} vs ${team2.name}</h3>
            <p class="no-direct-matches">No direct matches found between these teams in the historical data.</p>
        </div>
    `;
    
    content.innerHTML = `
        ${navButtons}
        <div class="analysis-header">
            <div class="analysis-team ${winnerClass1}">
                ${team1.crest ? `<img src="${team1.crest}" alt="${team1.name}" class="analysis-team-logo">` : ''}
                <h3>${team1.name}</h3>
                ${team1Position !== null ? `<span class="league-position" title="Position in current UCL league table">#${team1Position}</span>` : ''}
            </div>
            <div class="analysis-vs">vs</div>
            <div class="analysis-team ${winnerClass2}">
                ${team2.crest ? `<img src="${team2.crest}" alt="${team2.name}" class="analysis-team-logo">` : ''}
                <h3>${team2.name}</h3>
                ${team2Position !== null ? `<span class="league-position" title="Position in current UCL league table">#${team2Position}</span>` : ''}
            </div>
        </div>
        
        ${directMatchesHtml}
        
        <div class="win-probability">
            <h3>Win Probability</h3>
            <div class="probability-bars">
                <div class="probability-item">
                    <div class="probability-label">
                        <span>${team1.name}</span>
                        <span class="probability-value">${team1WinPercent}%</span>
                    </div>
                    <div class="probability-bar">
                        <div class="probability-fill team1-fill" style="width: ${team1WinPercent}%"></div>
                    </div>
                </div>
                <div class="probability-item">
                    <div class="probability-label">
                        <span>${team2.name}</span>
                        <span class="probability-value">${team2WinPercent}%</span>
                    </div>
                    <div class="probability-bar">
                        <div class="probability-fill team2-fill" style="width: ${team2WinPercent}%"></div>
                    </div>
                </div>
            </div>
            ${getProbabilityExplanation(winProbability, team1.name, team2.name)}
        </div>
        
        <div class="analysis-info">
            <p><strong>Teams in League:</strong> ${fullLeagueTable.length}</p>
            <p><strong>Common Opponents:</strong> ${analysis.commonOpponentsCount}</p>
            <p><strong>Historical Period:</strong> Last ${analysis.historicalYears} years</p>
            ${analysis.commonOpponentsCount === 0 ? '<p class="warning">No common opponents found in the historical data.</p>' : ''}
        </div>
        
        ${fullLeagueTable.length > 0 ? `
        <div class="league-table-container">
            <h3>Full League Table (All Matches Between Involved Teams)</h3>
            <p class="table-note">Ranked by: Points % → Solkoff → Strength per Game → Goals For</p>
            <table class="playoff-league-table">
                <thead>
                    <tr>
                        <th>Pos</th>
                        <th>Team</th>
                        <th>P</th>
                        <th>W</th>
                        <th>D</th>
                        <th>L</th>
                        <th>GF</th>
                        <th>GA</th>
                        <th>GD</th>
                        <th>Pts</th>
                        <th>Pts%</th>
                        <th>Solkoff</th>
                        <th>Strength/G</th>
                    </tr>
                </thead>
                <tbody>
                    ${fullLeagueTable.map((team, index) => {
                        const isTeam1 = team.teamId === team1.id;
                        const isTeam2 = team.teamId === team2.id;
                        const rowClass = isTeam1 ? winnerClass1 : (isTeam2 ? winnerClass2 : '');
                        const highlightClass = (isTeam1 || isTeam2) ? 'playoff-team' : '';
                        
                        return `
                            <tr class="${rowClass} ${highlightClass}" data-team-id="${team.teamId}">
                                <td>${index + 1}</td>
                                <td class="team-cell">
                                    ${team.teamCrest ? `<img src="${team.teamCrest}" alt="${team.teamName}" class="table-team-logo">` : ''}
                                    ${isTeam1 || isTeam2 ? '<strong>' : ''}${team.teamName}${isTeam1 || isTeam2 ? '</strong>' : ''}
                                </td>
                                <td>${team.played}</td>
                                <td>${team.won}</td>
                                <td>${team.drawn}</td>
                                <td>${team.lost}</td>
                                <td>${team.goalsFor}</td>
                                <td>${team.goalsAgainst}</td>
                                <td>${team.goalDifference > 0 ? '+' : ''}${team.goalDifference}</td>
                                <td><strong>${team.points}</strong></td>
                                <td>${team.pointsPercentage ? team.pointsPercentage.toFixed(1) : '0.0'}%</td>
                                <td>${(team.solkoffCoefficient || 0).toFixed(2)}</td>
                                <td>${team.strengthPerGame ? team.strengthPerGame.toFixed(2) : '0.00'}</td>
                            </tr>
                        `;
                    }).join('')}
                </tbody>
            </table>
        </div>
        
        <div class="matches-section">
            <h3>Match History (All Teams)</h3>
            ${(() => {
                // Separate teams into playoff teams and other teams
                const playoffTeams = [];
                const otherTeams = [];
                
                fullLeagueTable.forEach(team => {
                    const isTeam1 = team.teamId === team1.id;
                    const isTeam2 = team.teamId === team2.id;
                    if (isTeam1 || isTeam2) {
                        playoffTeams.push(team);
                    } else {
                        otherTeams.push(team);
                    }
                });
                
                // Sort playoff teams: team1 first, then team2
                playoffTeams.sort((a, b) => {
                    if (a.teamId === team1.id) return -1;
                    if (b.teamId === team1.id) return 1;
                    if (a.teamId === team2.id) return -1;
                    if (b.teamId === team2.id) return 1;
                    return 0;
                });
                
                // Combine: playoff teams first, then other teams
                const orderedTeams = [...playoffTeams, ...otherTeams];
                
                return orderedTeams.map((team, index) => {
                    const isTeam1 = team.teamId === team1.id;
                    const isTeam2 = team.teamId === team2.id;
                    const isPlayoffTeam = isTeam1 || isTeam2;
                    
                    if (team.matches && team.matches.length > 0) {
                        // Filter out direct matches between team1 and team2 (already shown above)
                        const otherMatches = team.matches.filter(m => 
                            !((m.opponentId === team2.id || String(m.opponentId) === String(team2.id)) && isTeam1) &&
                            !((m.opponentId === team1.id || String(m.opponentId) === String(team1.id)) && isTeam2)
                        );
                        
                        if (otherMatches.length === 0) {
                            return '';
                        }
                        
                        const matchesList = otherMatches.map(match => {
                            const outcomeClass = match.outcome === 'win' ? 'match-win' : 
                                                match.outcome === 'loss' ? 'match-loss' : 'match-draw';
                            const outcomeIcon = match.outcome === 'win' ? '✓' : 
                                               match.outcome === 'loss' ? '✗' : '=';
                            const opponentCrest = match.opponentCrest 
                                ? `<img src="${match.opponentCrest}" alt="${match.opponentName}" class="match-opponent-logo" onerror="this.style.display='none'">`
                                : '<span class="match-opponent-logo-placeholder"></span>';
                            
                            return `
                                <div class="match-item ${outcomeClass}">
                                    ${opponentCrest}
                                    <span class="match-opponent">${match.opponentName}</span>
                                    <span class="match-score">${match.teamScore}-${match.opponentScore}</span>
                                    <span class="match-outcome">${outcomeIcon}</span>
                                    <span class="match-date">${match.date ? new Date(match.date).toLocaleDateString() : ''}</span>
                                </div>
                            `;
                        }).join('');
                        
                        return `
                            <div class="team-matches ${isPlayoffTeam ? 'playoff-team-matches' : ''}">
                                <h4 class="team-matches-header">
                                    ${team.teamCrest ? `<img src="${team.teamCrest}" alt="${team.teamName}" class="team-matches-logo">` : ''}
                                    <strong>${team.teamName}</strong> (${otherMatches.length} match${otherMatches.length !== 1 ? 'es' : ''})
                                </h4>
                                <div class="matches-list">
                                    ${matchesList}
                                </div>
                            </div>
                        `;
                    }
                    return '';
                }).filter(html => html !== '').join('');
            })()}
        </div>
        ` : ''}
    `;
    
    modal.style.display = 'block';
}

function closePlayoffAnalysis() {
    const modal = document.getElementById('playoffAnalysisModal');
    if (modal) {
        modal.style.display = 'none';
        // Remove keyboard listener when modal is closed
        if (modal._keyHandler) {
            document.removeEventListener('keydown', modal._keyHandler);
            modal._keyHandler = null;
        }
    }
}

// Make functions globally accessible
// Helper function to generate probability explanation
function getProbabilityExplanation(winProbability, team1Name = 'Team 1', team2Name = 'Team 2') {
    const method = winProbability.method || 'unknown';
    let explanation = '';
    
    switch(method) {
        case 'head_to_head':
            // Normalize for display (excluding draws)
            const h2hWinTotal = (winProbability.team1Win || 0) + (winProbability.team2Win || 0);
            const h2hTeam1 = h2hWinTotal > 0 ? (winProbability.team1Win || 0) / h2hWinTotal : 0.5;
            const h2hTeam2 = h2hWinTotal > 0 ? (winProbability.team2Win || 0) / h2hWinTotal : 0.5;
            explanation = `
                <div class="probability-explanation">
                    <h4>How this probability is calculated:</h4>
                    <p><strong>Method: Head-to-Head Analysis</strong></p>
                    <p>This probability is based on direct historical matches between these two teams. 
                    We analyzed their past encounters and calculated the win rates from those matches (excluding draws).</p>
                    <ul>
                        <li>${team1Name} win rate: ${(h2hTeam1 * 100).toFixed(1)}%</li>
                        <li>${team2Name} win rate: ${(h2hTeam2 * 100).toFixed(1)}%</li>
                    </ul>
                    <p class="probability-note">Draws are excluded from the calculation. Probabilities are normalized to sum to exactly 100%.</p>
                </div>
            `;
            break;
        case 'points_per_game':
            const team1PPG = winProbability.team1PointsPerGame || 0;
            const team2PPG = winProbability.team2PointsPerGame || 0;
            // Normalize for display (excluding draws)
            const ppgWinTotal = (winProbability.team1Win || 0) + (winProbability.team2Win || 0);
            const ppgTeam1 = ppgWinTotal > 0 ? (winProbability.team1Win || 0) / ppgWinTotal : 0.5;
            const ppgTeam2 = ppgWinTotal > 0 ? (winProbability.team2Win || 0) / ppgWinTotal : 0.5;
            explanation = `
                <div class="probability-explanation">
                    <h4>How this probability is calculated:</h4>
                    <p><strong>Method: Points Per Game Analysis</strong></p>
                    <p>This probability is calculated based on each team's performance against common opponents in historical matches.</p>
                    <ul>
                        <li>${team1Name} average points per game: ${team1PPG.toFixed(2)}</li>
                        <li>${team2Name} average points per game: ${team2PPG.toFixed(2)}</li>
                    </ul>
                    <p>The win probabilities are calculated by comparing the relative strength (points per game) of each team. 
                    The probability is distributed proportionally based on each team's strength relative to the other.</p>
                    <p class="probability-note">Draws are excluded from the calculation. Probabilities are normalized to sum to exactly 100%.</p>
                </div>
            `;
            break;
        case 'combined_strength':
            const team1Main = winProbability.team1MainStrength || 0;
            const team2Main = winProbability.team2MainStrength || 0;
            const team1Hist = winProbability.team1HistoricalStrength || 0;
            const team2Hist = winProbability.team2HistoricalStrength || 0;
            explanation = `
                <div class="probability-explanation">
                    <h4>How this probability is calculated:</h4>
                    <p><strong>Method: Combined Strength Rating</strong></p>
                    <p>This probability is calculated using two equally weighted components:</p>
                    <ul>
                        <li><strong>50% - Current UCL Performance:</strong> Strength rating from this year's league table</li>
                        <li><strong>50% - Historical Performance:</strong> Strength rating from the historical mini-table</li>
                    </ul>
                    <p><strong>${team1Name}:</strong> Main League: ${team1Main.toFixed(2)}, Historical: ${team1Hist.toFixed(2)}, Combined: ${((team1Main * 0.5) + (team1Hist * 0.5)).toFixed(2)}</p>
                    <p><strong>${team2Name}:</strong> Main League: ${team2Main.toFixed(2)}, Historical: ${team2Hist.toFixed(2)}, Combined: ${((team2Main * 0.5) + (team2Hist * 0.5)).toFixed(2)}</p>
                    <p class="probability-note">Draws are excluded from the calculation. Probabilities are normalized to sum to exactly 100%.</p>
                </div>
            `;
            break;
        case 'equal_strength':
        case 'no_data':
        case 'insufficient_data':
            explanation = `
                <div class="probability-explanation">
                    <h4>How this probability is calculated:</h4>
                    <p><strong>Method: Default/Equal Probability</strong></p>
                    <p>Insufficient historical data is available to calculate a meaningful probability. 
                    The probabilities shown are equal (50% each team) as a default.</p>
                    <p class="probability-note">Draws are excluded from the calculation. Probabilities are normalized to sum to exactly 100%.</p>
                </div>
            `;
            break;
        default:
            explanation = `
                <div class="probability-explanation">
                    <h4>How this probability is calculated:</h4>
                    <p><strong>Method: ${method}</strong></p>
                    <p>This probability is calculated based on historical match data and team performance metrics.</p>
                    <p class="probability-note">Draws are excluded from the calculation. Probabilities are normalized to sum to exactly 100%.</p>
                </div>
            `;
    }
    
    return explanation;
}

async function navigateToPair(index) {
    if (index < 0 || index >= currentPairsList.length) {
        return;
    }
    
    const pair = currentPairsList[index];
    if (pair && pair.team1 && pair.team2) {
        await showPlayoffAnalysis(pair.team1.id, pair.team2.id);
    }
}

window.showPlayoffAnalysis = showPlayoffAnalysis;
window.closePlayoffAnalysis = closePlayoffAnalysis;
window.navigateToPair = navigateToPair;

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
            
            // If no sort orders left, add default (Strength only)
            if (sortOrders.length === 0) {
                sortOrders = [
                    {column: 'strengthScore', direction: 'desc'}
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
        
        // If no sort orders left, add default (Strength only)
        if (sortOrders.length === 0) {
            sortOrders = [
                {column: 'strengthScore', direction: 'desc'}
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

