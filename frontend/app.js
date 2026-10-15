// Frontend application
// Auto-detect API base URL based on environment
const API_BASE_URL = window.location.origin;

// Season selection: the current season is resolved from /api/seasons and every
// season-dependent GET carries the selected start year. `seasonGen` drops
// responses that belong to a season the user already switched away from.
let seasons = [];
let selectedSeason = null; // season start year; null until resolved (backend defaults to current)
let currentSeason = null;  // resolved current season, for historical-date display
let seasonGen = 0;

let standingsData = [];
// Multi-column sort: array of {column, direction} objects
// Default: Strength only
let sortOrders = [
    {column: 'strengthScore', direction: 'desc'}
];
// League toolbar state: case-insensitive search + advanced-column visibility
let searchQuery = '';
let advancedColumns = true;
// #leagueSort preset value -> standings field; 'custom' means header-built orders
const SORT_PRESETS = { strength: 'strengthScore', solkoff: 'solkoffCoefficient', points: 'points' };

// Cache for Solkoff details to avoid repeated API calls
let solkoffDetailsCache = {};
let expandedTeamId = null;
let currentStage = 'LEAGUE';
let playoffAnalysisCache = {};
let currentPairsList = []; // Store current pairs for navigation
let currentPairIndex = -1; // Current pair index in the list
// Analysis sidebar state: generation counter drops late analysis responses,
// the recorded selection drives card highlighting and focus return.
let analysisRequestGen = 0;
let activeAnalysisSelection = null; // {team1Id, team2Id} of the open analysis
let lastAnalysisCard = null;        // card that initiated the open analysis

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

// Helper function to format match dates. Archived seasons keep their real
// dates: the 6-month freshness window only guards the live feed.
function formatMatchDate(dateString, allowHistorical = false) {
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
        // This catches invalid placeholder dates like July 2025 when we're in 2026.
        // Archived seasons legitimately have older dates, so skip this window then.
        if (!allowHistorical) {
            const sixMonthsAgo = new Date();
            sixMonthsAgo.setMonth(now.getMonth() - 6);
            if (date < sixMonthsAgo) {
                return ''; // Don't show old/invalid dates
            }
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

function isHistoricalSeason() {
    return selectedSeason != null && currentSeason != null && selectedSeason < currentSeason;
}

// Tooltip for the league-position badge: names the selected season's table so an
// archived view never claims to show the current UCL standings.
function leaguePositionTitle() {
    if (isHistoricalSeason()) {
        const season = seasons.find(s => s.year === selectedSeason);
        return `Position in the ${season ? season.label : selectedSeason} league table`;
    }
    return 'Position in current UCL league table';
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', async () => {
    console.log('DOM loaded, initializing...');

    // Resolve selectable seasons first so every season-dependent GET below and
    // later carries the selected year. Falls back to backend default when the
    // endpoint is unavailable.
    await fetchSeasons();

    // Initialize primary view navigation
    initializePrimaryNav();

    // Initialize stage tabs
    initializeTabs();

    // Knockout analysis sidebar: close button + scoped arrow/Escape keys
    initializeKnockoutSidebar();

    // Overview first paint: explicit loading states until data arrives
    renderOverview();

    // Resolve the initial route from the URL hash
    const route = resolveRoute(window.location.hash);
    if (route.stage) {
        // Explicit stage permalink: knockout view + that stage
        explicitStageChosen = true;
        currentRoute.stage = route.stage; // skip provisional default, load once
        activateView('knockout', { updateHash: false });
        activateTab(route.stage, { updateHash: false });
    } else {
        activateView(route.view, { updateHash: false });
    }
    // Raw standings feed Overview metrics/rankings and are reused by League.
    ensureStandingsLoaded();
    // Resolve the initial knockout stage for generic Knockout visits; once known,
    // Overview previews that stage's pairs (never overriding user route/choice).
    loadCurrentStage().then(() => loadOverviewKnockout());

    // Browser back/forward (popstate + hashchange both fire; syncFromLocation dedupes)
    window.addEventListener('popstate', syncFromLocation);
    window.addEventListener('hashchange', syncFromLocation);
    
    // Refresh button
    const refreshBtn = document.getElementById('refreshBtn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            refreshData();
        });
    }

    // Season switcher
    const seasonSelect = document.getElementById('seasonSelect');
    if (seasonSelect) {
        seasonSelect.addEventListener('change', () => {
            const year = parseInt(seasonSelect.value, 10);
            if (Number.isInteger(year)) switchSeason(year);
        });
    }
    
    // Make table headers sortable (click + keyboard)
    const headers = document.querySelectorAll('.standings-table th');
    headers.forEach((header, index) => {
        if (header.textContent.trim() !== 'Team') {
            header.addEventListener('click', (e) => {
                if (e.shiftKey) {
                    removeSortOrder(index);
                } else {
                    addSortOrder(index);
                }
            });
            // Keyboard: Enter sorts, Shift+Enter removes sort
            header.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    if (e.shiftKey) {
                        removeSortOrder(index);
                    } else {
                        addSortOrder(index);
                    }
                }
            });
        }
    });

    // League toolbar: search, sort preset, advanced columns
    const teamSearch = document.getElementById('teamSearch');
    if (teamSearch) {
        teamSearch.addEventListener('input', () => {
            searchQuery = teamSearch.value;
            renderLeague();
        });
    }

    const leagueSort = document.getElementById('leagueSort');
    if (leagueSort) {
        leagueSort.addEventListener('change', () => {
            const column = SORT_PRESETS[leagueSort.value];
            if (!column) return; // 'custom' is a display state, not a preset
            sortOrders = [{ column: column, direction: 'desc' }];
            renderLeague();
        });
    }

    const advancedToggle = document.getElementById('advancedToggle');
    if (advancedToggle) {
        advancedToggle.addEventListener('change', () => {
            advancedColumns = advancedToggle.checked;
            renderLeague();
        });
    }

    // Intro banner
    showIntroIfFirstVisit();
    const dismissBtn = document.getElementById('introDismissBtn');
    if (dismissBtn) {
        dismissBtn.addEventListener('click', dismissIntro);
    }
});

// ---------------------------------------------------------------------------
// Primary view routing (UCL Strength Lab)
// ---------------------------------------------------------------------------
const PRIMARY_VIEWS = ['overview', 'league', 'knockout'];
const STAGE_TAB_IDS = ['knockout-playoff', 'round-of-16', 'quarter-final', 'semi-final', 'final'];
const DEFAULT_KNOCKOUT_STAGE = 'round-of-16';
// In-page anchors that are not routes: keep current view on hashchange
const IGNORED_ANCHOR_HASHES = ['main-content'];

let currentRoute = { view: 'overview', stage: null };
let explicitStageChosen = false;   // true after a stage hash or a user stage click
let standingsRequested = false;    // avoid duplicate league loads from routing
let initialKnockoutStage = DEFAULT_KNOCKOUT_STAGE; // resolved from the stage API

function resolveRoute(hash) {
    const h = (hash || '').replace(/^#/, '');
    if (h === 'overview') return { view: 'overview', stage: null };
    if (h === 'league') return { view: 'league', stage: null };
    if (h === 'knockout') return { view: 'knockout', stage: null };
    if (STAGE_TAB_IDS.includes(h)) return { view: 'knockout', stage: h };
    return { view: 'overview', stage: null };
}

function routeMatches(a, b) {
    return a.view === b.view && a.stage === b.stage;
}

function syncFromLocation() {
    const raw = (window.location.hash || '').replace(/^#/, '');
    if (IGNORED_ANCHOR_HASHES.includes(raw)) return; // skip-link and other in-page anchors
    const route = resolveRoute(window.location.hash);
    if (routeMatches(route, currentRoute)) return; // dedupe popstate+hashchange pairs
    if (route.stage) {
        explicitStageChosen = true;
        if (currentRoute.view !== 'knockout') {
            currentRoute.stage = route.stage; // skip provisional default, load once
            activateView('knockout', { updateHash: false });
        }
        activateTab(route.stage, { updateHash: false });
    } else {
        if (route.view === 'knockout') currentRoute.stage = null; // drop stale stage
        activateView(route.view, { updateHash: false });
    }
}

function activateView(view, { updateHash = true } = {}) {
    if (!PRIMARY_VIEWS.includes(view)) view = 'overview';
    if (view === 'knockout') {
        currentRoute.view = 'knockout';
    } else {
        currentRoute = { view, stage: null };
    }

    // Primary nav selected state + roving tabindex
    document.querySelectorAll('[data-view]').forEach(btn => {
        const on = btn.dataset.view === view;
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
        btn.tabIndex = on ? 0 : -1;
    });

    // Panels: only the active one is visible
    PRIMARY_VIEWS.forEach(id => {
        const panel = document.getElementById(id);
        if (panel) panel.hidden = id !== view;
    });

    if (view === 'knockout' && !currentRoute.stage) {
        // Generic Knockout: use the API-resolved initial stage (or fallback)
        activateTab(initialKnockoutStage, { updateHash: false });
    }

    if (view === 'league') {
        ensureStandingsLoaded();
    }

    if (updateHash) {
        const target = view === 'knockout' ? `#${currentRoute.stage}` : `#${view}`;
        if (window.location.hash !== target) {
            window.history.pushState(null, '', target);
        }
    }
}

function initializePrimaryNav() {
    const navButtons = Array.from(document.querySelectorAll('.primary-nav [data-view]'));
    navButtons.forEach(btn => {
        btn.addEventListener('click', () => activateView(btn.dataset.view));
        btn.addEventListener('keydown', (e) => {
            const idx = navButtons.indexOf(e.currentTarget);
            let target = null;
            if (e.key === 'ArrowRight') {
                target = navButtons[(idx + 1) % navButtons.length];
            } else if (e.key === 'ArrowLeft') {
                target = navButtons[(idx - 1 + navButtons.length) % navButtons.length];
            } else if (e.key === 'Home') {
                target = navButtons[0];
            } else if (e.key === 'End') {
                target = navButtons[navButtons.length - 1];
            }
            if (target) {
                e.preventDefault();
                target.focus();
                target.click();
            }
        });
    });
}

function initializeTabs() {
    const tabButtons = document.querySelectorAll('.tab-btn');
    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.dataset.tab;
            activateTab(tabId, { userChose: true });
        });

        // WAI-ARIA roving tabindex: arrow key navigation within tablist
        btn.addEventListener('keydown', (e) => {
            const tabs = Array.from(document.querySelectorAll('.tab-btn'));
            const idx = tabs.indexOf(e.currentTarget);
            let target = null;
            if (e.key === 'ArrowRight') {
                target = tabs[(idx + 1) % tabs.length];
            } else if (e.key === 'ArrowLeft') {
                target = tabs[(idx - 1 + tabs.length) % tabs.length];
            } else if (e.key === 'Home') {
                target = tabs[0];
            } else if (e.key === 'End') {
                target = tabs[tabs.length - 1];
            }
            if (target) {
                e.preventDefault();
                target.focus();
                target.click();
            }
        });
    });
}

// ---------------------------------------------------------------------------
// Season selection
// ---------------------------------------------------------------------------
function seasonQuery() {
    return selectedSeason != null ? `?season=${selectedSeason}` : '';
}

async function fetchSeasons(attempt = 1) {
    try {
        const response = await fetch(`${API_BASE_URL}/api/seasons`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        const list = data && Array.isArray(data.seasons) ? data.seasons : [];
        const current = data && Number.isInteger(data.currentSeason) ? data.currentSeason : null;
        if (list.length > 0) {
            seasons = list;
            currentSeason = current != null ? current : list[0].year;
        } else if (current != null) {
            // Degenerate/older response: still offer the current season alone
            // rather than leaving the switcher dead.
            const next = String((current + 1) % 100).padStart(2, '0');
            seasons = [{ year: current, label: `${current}/${next}` }];
            currentSeason = current;
        } else {
            return; // nothing usable: keep the default (no season param) behaviour
        }
        selectedSeason = currentSeason;
        renderSeasonOptions();
    } catch (error) {
        console.error('Error loading seasons:', error);
        if (attempt < 2) {
            await new Promise(resolve => setTimeout(resolve, 400));
            return fetchSeasons(attempt + 1);
        }
    }
}

function renderSeasonOptions() {
    const select = document.getElementById('seasonSelect');
    if (!select) return;
    select.innerHTML = seasons.map(season =>
        `<option value="${season.year}">${escapeHtml(season.label || String(season.year))}</option>`
    ).join('');
    if (selectedSeason != null) select.value = String(selectedSeason);
    // A single season needs no switcher; disable to keep the control honest.
    select.disabled = seasons.length <= 1;
}

// Switching seasons clears every payload tied to the previous season and
// reloads all views. Generation guards (module counters + seasonGen) ensure a
// late response for the old season can never paint over the new selection.
async function switchSeason(year) {
    if (year === selectedSeason) return;
    selectedSeason = year;
    const sgen = ++seasonGen;

    solkoffDetailsCache = {};
    playoffAnalysisCache = {};
    pairsByStage = {};
    currentPairsList = [];
    currentPairIndex = -1;
    expandedTeamId = null;
    standingsData = [];
    standingsState = 'loading';
    overviewPairs = null;
    pairsState = 'loading';
    standingsRequested = true; // the reload below *is* the league load
    closeActiveAnalysis({ invalidate: true, focusCard: false });

    const tableBody = document.getElementById('standingsBody');
    if (tableBody) tableBody.innerHTML = renderSkeletonRows(10);
    renderOverview();

    // Re-resolve the selected season's stage before reloading the views.
    const stageBefore = currentRoute.stage;
    await loadCurrentStage();
    if (sgen !== seasonGen) return; // another switch happened mid-flight

    // Generic Knockout may have been re-pointed at the season's stage already;
    // a retained explicit stage still needs its pairs reloaded for the season.
    if (currentRoute.view === 'knockout' && currentRoute.stage === stageBefore) {
        loadTabContent(currentRoute.stage);
    }
    loadStandings();
    loadOverviewKnockout();
}

async function loadCurrentStage() {
    const sgen = seasonGen;
    let stage = null;
    try {
        const response = await fetch(`${API_BASE_URL}/api/tournament/current-stage${seasonQuery()}`);
        if (sgen !== seasonGen) return; // season switched mid-flight
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        if (sgen !== seasonGen) return;
        stage = data.stage;
    } catch (error) {
        if (sgen !== seasonGen) return;
        console.error('Error loading current stage:', error);
    }
    if (sgen !== seasonGen) return;
    currentStage = stage || 'LEAGUE';

    let tabId = DEFAULT_KNOCKOUT_STAGE; // LEAGUE and failures fall back to Round of 16
    const mappedTabId = STAGE_MAP[currentStage];
    if (mappedTabId && mappedTabId !== 'league') {
        tabId = mappedTabId;
    }
    // Remember the resolved choice for later generic Knockout visits
    initialKnockoutStage = tabId;

    // Initial knockout stage only: never override the primary view, an explicit
    // stage hash, or a stage the user already clicked into.
    if (explicitStageChosen) return;
    if (currentRoute.view !== 'knockout') return;

    if (tabId !== currentRoute.stage) {
        console.log(`Current stage: ${currentStage}, initial knockout tab: ${tabId}`);
        // Auto-switching our own provisional default: keep the URL in sync with
        // the visible selection. Explicit user choices return before this point,
        // so a user-picked stage or permalink is never overridden here.
        activateTab(tabId, { updateHash: true });
    }
}

function activateTab(tabId, { updateHash = true, userChose = false } = {}) {
    const tabBtn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
    const tabPane = document.getElementById(`${tabId}-tab`);
    if (!tabBtn || !tabPane) return;

    // Stage tab selected state + roving tabindex
    document.querySelectorAll('.tab-btn').forEach(btn => {
        const on = btn.dataset.tab === tabId;
        btn.classList.toggle('active', on);
        btn.setAttribute('aria-selected', on ? 'true' : 'false');
        btn.tabIndex = on ? 0 : -1;
    });

    // Panes live inside the knockout panel
    document.querySelectorAll('#knockout .tab-pane').forEach(pane => pane.classList.remove('active'));
    tabPane.classList.add('active');

    if (userChose) explicitStageChosen = true;
    currentRoute = { view: 'knockout', stage: tabId };

    // Hide loading indicator when switching tabs
    const loadingEl = document.getElementById('loading');
    if (loadingEl) {
        loadingEl.style.display = 'none';
    }

    // Update URL hash for permalink / back-forward support
    if (updateHash) {
        const target = `#${tabId}`;
        if (window.location.hash !== target) {
            window.history.pushState(null, '', target);
        }
    }

    // Load content for the activated tab
    loadTabContent(tabId);
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

// Per-stage pair cache (stage key -> Promise<pairs[]>), shared between the
// Overview preview and the Knockout tab so a stage is fetched at most once.
let pairsByStage = {};
// Generation counter for tab loads: hidden panes may keep their own containers
// and cache, but only the newest load may overwrite the shared selection list.
let knockoutLoadGen = 0;

function fetchKnockoutPairsOnce(stage) {
    if (!pairsByStage[stage]) {
        const promise = fetch(`${API_BASE_URL}/api/knockout-pairs/${stage}${seasonQuery()}`)
            .then(response => {
                if (response.status === 404 || response.status === 400) {
                    const err = new Error(`No data available for ${stage}`);
                    err.noData = true;
                    throw err;
                }
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(pairs => {
                if (!Array.isArray(pairs)) {
                    throw new Error(`Invalid pairs payload for ${stage}`);
                }
                return pairs;
            });
        pairsByStage[stage] = promise;
        // A failed fetch must not poison the cache for later visits.
        promise.catch(() => {
            if (pairsByStage[stage] === promise) delete pairsByStage[stage];
        });
    }
    return pairsByStage[stage];
}

async function loadKnockoutPairs(stage, tabId) {
    const gen = ++knockoutLoadGen;
    const containerId = `${tabId}-pairs`;
    const container = document.getElementById(containerId);
    
    if (!container) {
        console.error(`Container not found: ${containerId}`);
        return;
    }
    
    // Stage change invalidates the open analysis: the sidebar, the card
    // selection and any in-flight analysis fetch belong to the old stage.
    closeActiveAnalysis({ invalidate: true, focusCard: false });
    
    // Only the newest stage's cards live in the DOM: empty the other panes so
    // stale pairs can never leak into #knockout queries or a11y text.
    document.querySelectorAll('#knockout .playoff-pairs').forEach(other => {
        if (other !== container) other.innerHTML = '';
    });
    
    // Show loading only if this tab is active
    const tabPane = document.getElementById(`${tabId}-tab`);
    if (tabPane && tabPane.classList.contains('active')) {
        container.innerHTML = '<div class="loading">Loading pairs...</div>';
    }
    
    try {
        console.log(`Loading pairs for stage: ${stage}, tabId: ${tabId}`);
        const pairs = await fetchKnockoutPairsOnce(stage);
        console.log(`Received ${pairs.length} pairs for ${stage}`);
        
        // Late response for a stage the user already left: never touch the
        // selected list or render into any pane.
        if (gen !== knockoutLoadGen) return;
        
        if (pairs.length === 0) {
            showDataNotAvailable(container, stage, 'empty');
            return;
        }
        
        currentPairsList = pairs; // newest load only (guarded above)
        renderKnockoutPairs(pairs, container);
    } catch (error) {
        if (gen !== knockoutLoadGen) return; // stale error must not clobber the list
        console.error(`Error loading pairs for ${stage}:`, error);
        showDataNotAvailable(container, stage, error.noData ? 'empty' : 'error');
    }
}

function showDataNotAvailable(container, stage, kind = 'empty') {
    const tabId = STAGE_MAP[stage] || stage.toLowerCase().replace('_', '-');
    const displayName = STAGE_DISPLAY_NAMES[tabId] || stage;
    
    // Distinct error copy so a failed fetch is never shown as "stage not
    // started", and vice versa (empty stage must not read as an error).
    if (kind === 'error') {
        container.innerHTML = `
            <div class="data-error" role="alert">
                <p>Could not load ${displayName} pairs — please try again later.</p>
            </div>
        `;
        return;
    }
    
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

// A real stored score is a non-negative finite integer. Strings, booleans,
// null and fractions are rejected so no malformed value can be coerced to 0 or
// injected into markup (values are interpolated, not escaped).
function isScoreValue(value) {
    return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

// Both scores must be real score values; a missing/malformed side is never
// rendered as an invented 0-0.
function hasBothScores(leg) {
    return !!leg && isScoreValue(leg.homeScore) && isScoreValue(leg.awayScore);
}

// Only a well-formed two-entry aggregate of real score values is used; a
// partial, null, string or otherwise malformed aggregate is unavailable.
function normalizeAggregate(aggregate) {
    if (!aggregate) return null;
    if (!isScoreValue(aggregate.team1) || !isScoreValue(aggregate.team2)) return null;
    return { team1: aggregate.team1, team2: aggregate.team2 };
}

// Orient a single fixture's goals to the pair's stable team1/team2 identity, so
// a preview never flips the score when the payload's home side is team2.
function legTeamScores(pair, leg) {
    const team1Id = Number((pair.team1 || {}).id);
    return Number(leg.homeTeamId) === team1Id
        ? { team1: leg.homeScore, team2: leg.awayScore }
        : { team1: leg.awayScore, team2: leg.homeScore };
}

// Resolve a leg's team id to the pair's stable team name, so orientation never
// depends on the latest leg's home/away order.
function pairTeamName(pair, teamId) {
    const t1 = pair.team1 || {};
    const t2 = pair.team2 || {};
    if (Number(t1.id) === Number(teamId)) return t1.name || '';
    if (Number(t2.id) === Number(teamId)) return t2.name || '';
    return '';
}

// Full-card results: every scored leg with home/away labels plus an explicitly
// labelled aggregate. Unscored/scheduled fixtures contribute nothing, so no
// score is ever fabricated. A drawn aggregate is labelled a draw, never a
// winner (stored goals cannot encode a shootout).
function pairResultsHtml(pair) {
    const legs = Array.isArray(pair.legs) ? pair.legs.filter(hasBothScores) : [];
    const aggregate = normalizeAggregate(pair.aggregateScore);
    if (legs.length === 0 && !aggregate) return '';

    const legHtml = legs.map(leg =>
        `<div class="pair-leg">` +
        `<span class="leg-side"><span class="leg-tag">Home</span> ${escapeHtml(pairTeamName(pair, leg.homeTeamId))}</span>` +
        `<span class="leg-score">${leg.homeScore}-${leg.awayScore}</span>` +
        `<span class="leg-side"><span class="leg-tag">Away</span> ${escapeHtml(pairTeamName(pair, leg.awayTeamId))}</span>` +
        `</div>`
    ).join('');

    const aggregateHtml = aggregate
        ? `<div class="pair-aggregate"><span class="aggregate-label">Aggregate</span> ` +
          `<span class="aggregate-score">${aggregate.team1}-${aggregate.team2}</span></div>`
        : '';

    return `<div class="pair-results">${legHtml}${aggregateHtml}</div>`;
}

// Compact preview result: the aggregate when available, otherwise the score of
// a genuine single-leg fixture. A partial two-leg tie never masquerades as a
// single match, and scheduled/unscored ties render nothing.
function miniResultsHtml(pair) {
    const aggregate = normalizeAggregate(pair.aggregateScore);
    if (aggregate) {
        return `<div class="mini-result"><span class="aggregate-label">Aggregate</span> ` +
            `<span class="aggregate-score">${aggregate.team1}-${aggregate.team2}</span></div>`;
    }
    const legs = Array.isArray(pair.legs) ? pair.legs : [];
    if (legs.length === 1 && hasBothScores(legs[0])) {
        const score = legTeamScores(pair, legs[0]);
        return `<div class="mini-result">${score.team1}-${score.team2}</div>`;
    }
    return '';
}

function renderKnockoutPairs(pairs, container) {
    container.innerHTML = '';
    
    if (pairs.length === 0) {
        container.innerHTML = '<div class="data-not-available"><p>No pairs available for this stage yet.</p></div>';
        return;
    }
    
    const pairsGrid = document.createElement('div');
    pairsGrid.className = 'playoff-pairs-grid';
    // Selectable-card list: cards are options, Enter/Space opens the sidebar.
    pairsGrid.setAttribute('role', 'listbox');
    const gridTabId = container.id.replace(/-pairs$/, '');
    pairsGrid.setAttribute('aria-label',
        `${STAGE_DISPLAY_NAMES[gridTabId] || 'Knockout'} pair list`);
    
    pairs.forEach((pair) => {
        const pairCard = document.createElement('div');
        pairCard.className = 'playoff-pair-card';
        pairCard.setAttribute('role', 'option');
        pairCard.setAttribute('aria-selected', 'false');
        pairCard.tabIndex = 0;
        pairCard.dataset.team1Id = pair.team1.id;
        pairCard.dataset.team2Id = pair.team2.id;
        
        const statusBadge = pair.status === 'FINISHED' 
            ? '<span class="status-badge finished">Finished</span>'
            : pair.status === 'SCHEDULED' || pair.status === 'TIMED'
            ? '<span class="status-badge scheduled">Scheduled</span>'
            : pair.status === 'LIVE' || pair.status === 'IN_PLAY'
            ? '<span class="status-badge live">Live</span>'
            : '';
        
        // Calculate win probability display (excluding draws). Default or
        // no-signal methods and zero totals render as unavailable — never a
        // fabricated 50/50.
        const probNorm = normalizeWinProbability(pair.winProbability);
        let winProbabilityHtml = `
                <div class="pair-probability">
                    <div class="pair-probability-unavailable">Win probability unavailable</div>
                </div>`;
        if (probNorm) {
            const team1Win = (probNorm.team1 * 100).toFixed(1);
            const team2Win = (probNorm.team2 * 100).toFixed(1);
            winProbabilityHtml = `
                <div class="pair-probability">
                    <div style="flex: 1;">
                        <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                            <span class="probability-label" style="font-size: 0.8em;">${escapeHtml(pair.team1.name)}</span>
                            <span class="probability-value">${team1Win}%</span>
                        </div>
                        <div class="probability-bar-mini">
                            <div class="probability-fill-mini team1" style="width: ${team1Win}%"></div>
                        </div>
                    </div>
                    <div style="flex: 1; margin-left: 10px;">
                        <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                            <span class="probability-label" style="font-size: 0.8em;">${escapeHtml(pair.team2.name)}</span>
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
                    ${crestHtml(pair.team1.crest, pair.team1.name)}
                    <span class="pair-team-name">${escapeHtml(pair.team1.name)}</span>
                </div>
                <div class="pair-vs">vs</div>
                <div class="pair-team">
                    ${crestHtml(pair.team2.crest, pair.team2.name)}
                    <span class="pair-team-name">${escapeHtml(pair.team2.name)}</span>
                </div>
            </div>
            <div class="pair-meta">
                ${statusBadge}
                ${pair.date ? `<span class="pair-date">${escapeHtml(formatMatchDate(pair.date, isHistoricalSeason()))}</span>` : ''}
            </div>
            ${pairResultsHtml(pair)}
            ${winProbabilityHtml}
            <span class="pair-analyze-btn">View Historical Analysis</span>
        `;
        
        pairsGrid.appendChild(pairCard);
    });
    
    container.appendChild(pairsGrid);
    wireCrestFallbacks(container);
    
    // Listeners (never inline handlers): clicking or Enter/Space on a card
    // selects it and opens the persistent analysis sidebar.
    container.querySelectorAll('.playoff-pair-card').forEach(card => {
        const open = () => showPlayoffAnalysis(
            Number(card.dataset.team1Id), Number(card.dataset.team2Id), card);
        card.addEventListener('click', () => {
            card.focus();
            open();
        });
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
                e.preventDefault();
                open();
            }
        });
    });
    
    // A selection that survived this re-render (same stage) keeps its marks.
    markAnalysisSelection();
}

async function loadStandings() {
    const gen = ++standingsGen; // generation guard: only the newest load may write
    const loadingEl = document.getElementById('loading');
    const errorEl = document.getElementById('error');
    const tableBody = document.getElementById('standingsBody');
    
    if (!tableBody) {
        console.error('Table body not found');
        return;
    }
    
    // Show loading if element exists and league view panel is active
    if (loadingEl) {
        const leaguePanel = document.getElementById('league');
        if (leaguePanel && !leaguePanel.hidden) {
            loadingEl.style.display = 'block';
            console.log('Showing loading indicator');
        }
    }
    
    if (errorEl) {
        errorEl.style.display = 'none';
    }
    // A new load replaces whatever failure status is showing; honest state
    // only ever reflects the newest request.
    clearAppStatus();

    // Show skeleton rows while loading
    tableBody.innerHTML = renderSkeletonRows(10);

    try {
        console.log('Fetching standings from:', `${API_BASE_URL}/api/standings${seasonQuery()}`);
        const response = await fetch(`${API_BASE_URL}/api/standings${seasonQuery()}`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        if (gen !== standingsGen) return; // stale: a newer standings load superseded this
        standingsData = data;
        console.log('Standings data received:', standingsData ? standingsData.length : 0, 'teams');
        
        if (standingsData && standingsData.length > 0) {
            standingsState = 'ready';
            renderLeague(); // filter/sort a copy; raw standings stay immutable
            updateLastUpdated();
            console.log('Table rendered successfully');
        } else {
            standingsState = 'empty';
            console.warn('No standings data received');
            tableBody.innerHTML = `<tr><td colspan="${visibleColumnCount()}" style="text-align: center; padding: 20px;">No standings data available</td></tr>`;
        }
        renderOverviewStandings();
        
    } catch (error) {
        if (gen !== standingsGen) return; // stale error must not clobber a newer result
        console.error('Error loading standings:', error);
        standingsState = 'error';
        if (errorEl) {
            errorEl.textContent = `Error loading standings: ${error.message}`;
            errorEl.style.display = 'block';
        }
        // Mirror to the global status when the League panel is hidden, so a
        // standings failure is never swallowed on Overview/Knockout.
        const leaguePanel = document.getElementById('league');
        if (leaguePanel && leaguePanel.hidden) {
            setAppStatus(`Standings unavailable — could not reload saved data (${error.message}). Use Reload data to try again.`);
        }
        tableBody.innerHTML = `<tr><td colspan="${visibleColumnCount()}" style="text-align: center; padding: 20px; color: #dc3545;">Error: ` + escapeHtml(error.message) + `</td></tr>`;
        renderOverviewStandings();
    } finally {
        // Always hide loading — but never let a superseded load (newer request
        // or a season switch) clear the indicator the current load owns.
        if (loadingEl && gen === standingsGen) {
            loadingEl.style.display = 'none';
            console.log('Hiding loading indicator');
        }
    }
}

function renderTable(rows) {
    const tableBody = document.getElementById('standingsBody');
    if (!tableBody) return;
    const table = document.getElementById('standingsTable');
    if (table) table.classList.toggle('advanced-off', !advancedColumns);
    tableBody.innerHTML = '';

    // Color scale normalizes against the COMPLETE raw dataset, never the
    // filtered/sorted subset, so hues stay stable across search and sort.
    const source = standingsState === 'ready' ? standingsData : rows;
    const strengthValues = source.map(t => t.strengthScore || 0);
    const ptsValues = source.map(t => t.points || 0);
    const maxStrength = Math.max(...strengthValues);
    const minStrength = Math.min(...strengthValues);
    const maxPts = Math.max(...ptsValues);
    const minPts = Math.min(...ptsValues);
    const strengthRange = (maxStrength - minStrength) || 1;
    const ptsRange = (maxPts - minPts) || 1;

    rows.forEach((team) => {
        // Main team row
        const row = document.createElement('tr');
        row.className = 'team-row';
        row.dataset.teamId = team.teamId;
        
        const logoHtml = crestHtml(team.teamCrest, team.teamName, team.teamCode);
        const zone = qualificationForPosition(team.position);
        
        row.innerHTML = `
            <td>
                ${team.position || '-'}
                <span class="zone-badge ${zone.cls}">${zone.label}</span>
            </td>
            <td class="team-cell clickable">
                ${logoHtml}
                <span class="team-name">${escapeHtml(team.teamName)}</span>
            </td>
            <td>${team.played}</td>
            <td class="col-adv">${team.won}</td>
            <td class="col-adv">${team.drawn}</td>
            <td class="col-adv">${team.lost}</td>
            <td class="col-adv">${team.gf}</td>
            <td class="col-adv">${team.ga}</td>
            <td class="col-adv">${team.gd > 0 ? '+' : ''}${team.gd}</td>
            <td class="pts-cell">${team.points}</td>
            <td class="clickable">${(team.solkoffCoefficient || 0).toFixed(2)}</td>
            <td class="clickable strength-score">${(team.strengthScore || 0).toFixed(2)}</td>
        `;

        // Apply color-scale CSS custom properties
        const ptsPct = ((team.points || 0) - minPts) / ptsRange;
        const strengthPct = ((team.strengthScore || 0) - minStrength) / strengthRange;
        row.querySelector('.pts-cell').style.setProperty('--pts-pct', ptsPct);
        row.querySelector('.strength-score').style.setProperty('--strength-pct', strengthPct);
        
        // Expand/collapse on the team name, the Solkoff value, or anywhere on
        // the row; the cells keep their clickable affordance.
        row.addEventListener('click', () => toggleTeamDetails(team.teamId));
        
        tableBody.appendChild(row);
        
        // Add detail row if this team is expanded
        if (expandedTeamId === team.teamId) {
            tableBody.appendChild(createDetailRow(team.teamId));
        }
    });
    wireCrestFallbacks(tableBody);
}

// Case-insensitive filter + multicolumn sort on a COPY of the raw standings;
// the raw data is never mutated. Orders array contains {column, direction}
// objects; when none survive, default Strength descending.
function getVisibleStandings(data, query, orders) {
    const term = (query || '').trim().toLowerCase();
    let rows = Array.isArray(data) ? data.slice() : [];
    if (term) {
        rows = rows.filter(team => String(team.teamName || '').toLowerCase().includes(term));
    }
    const effective = (Array.isArray(orders) && orders.length > 0)
        ? orders
        : [{ column: 'strengthScore', direction: 'desc' }];
    rows.sort((a, b) => {
        for (const order of effective) {
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
        return 0;
    });
    return rows;
}

// Qualification zone comes from the OFFICIAL position, never from the current
// sort order, so badges stay truthful while the ranking changes. No
// zone-divider rows are inserted — they would mislead under alternate sorts.
function qualificationForPosition(position) {
    if (position >= 1 && position <= 8) return { cls: 'zone-direct', label: 'Direct' };
    if (position >= 9 && position <= 24) return { cls: 'zone-playoff', label: 'Play-off' };
    return { cls: 'zone-out', label: 'Eliminated' };
}

function visibleColumnCount() {
    return advancedColumns ? 12 : 6;
}

// Single league rerender: filter + sort a copy, paint rows, sync header
// indicators and the preset selector.
function renderLeague() {
    renderTable(getVisibleStandings(standingsData, searchQuery, sortOrders));
    updateSortIndicators();
    syncSortSelector();
}

// Header customization beyond a single preset descending shows 'custom'.
function syncSortSelector() {
    const select = document.getElementById('leagueSort');
    if (!select) return;
    let value = 'custom';
    if (sortOrders.length === 1 && sortOrders[0].direction === 'desc') {
        const preset = Object.keys(SORT_PRESETS)
            .find(key => SORT_PRESETS[key] === sortOrders[0].column);
        if (preset) value = preset;
    }
    select.value = value;
}

function createDetailRow(teamId) {
    const detailRow = document.createElement('tr');
    detailRow.className = 'detail-row';
    detailRow.id = `detail-${teamId}`;
    
    const detailCell = document.createElement('td');
    detailCell.colSpan = visibleColumnCount(); // adjusts with hidden advanced columns
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
    const sgen = seasonGen;
    try {
        const response = await fetch(`${API_BASE_URL}/api/teams/${teamId}/solkoff-details${seasonQuery()}`);
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const details = await response.json();
        // A collapse or refresh re-render detaches this cell; a late response
        // must not render into dead DOM nor poison the post-refresh cache. A
        // season switch must not repopulate the previous season's cache either.
        if (sgen !== seasonGen) return;
        if (!detailCell.isConnected) return;
        solkoffDetailsCache[teamId] = details;
        renderDetailContent(detailCell, details);
    } catch (error) {
        if (sgen !== seasonGen) return;
        console.error('Error loading Solkoff details:', error);
        if (!detailCell.isConnected) return;
        detailCell.innerHTML = `<div class="detail-error">Error loading details: ${escapeHtml(error.message)}</div>`;
    }
}

function renderDetailContent(detailCell, details) {
    const opponentsList = details.opponents.map(opp => {
        const logoHtml = crestHtml(opp.teamCrest, opp.teamName, opp.teamCode);
        
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
                return `<span class="match-result ${outcomeClass}" title="${escapeHtml(match.date || '')}">${scoreDisplay} ${outcomeIcon}</span>`;
            }).join(' ')
            : '';
        
        return `
            <div class="opponent-item">
                ${logoHtml}
                <div class="opponent-main-info">
                    <span class="opponent-name">${escapeHtml(opp.teamName)}</span>
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
                    ${crestHtml(details.teamCrest, details.teamName, details.teamCode)}
                    <div>
                        <h3>${escapeHtml(details.teamName)}</h3>
                        <p class="detail-subtitle">
                            Solkoff Coefficient: <strong class="solkoff-value">${(details.solkoffCoefficient || 0).toFixed(2)}</strong> • 
                            Strength Score: <strong class="strength-value">${strengthScore.toFixed(2)}</strong>
                        </p>
                    </div>
                </div>
                <button type="button" class="detail-close" aria-label="Close details">×</button>
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
    wireCrestFallbacks(detailCell);
    // Close is an attached listener (never an inline handler); the closure
    // keeps this team's toggle so collapsing works from any expanded row.
    const closeBtn = detailCell.querySelector('.detail-close');
    if (closeBtn) closeBtn.addEventListener('click', () => toggleTeamDetails(details.teamId));
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
    renderLeague();
}

// Play-off analysis functionality
// Note: playoffAnalysisCache is already declared at the top of the file

// ── Knockout analysis sidebar ────────────────────────────────────────────
// Detailed pair analysis renders into the persistent #analysisPanel beside
// the stage cards (no modal overlay, no body scroll lock). analysisRequestGen
// invalidates late analysis responses when the stage changes, the selection
// changes, or the panel is closed.

function getAnalysisPanel() {
    return document.getElementById('analysisPanel');
}

function findPairCard(team1Id, team2Id) {
    const scope = document.querySelector('#knockout .tab-pane.active') || document;
    return scope.querySelector(
        `.playoff-pair-card[data-team1-id="${team1Id}"][data-team2-id="${team2Id}"]`) ||
        scope.querySelector(
            `.playoff-pair-card[data-team1-id="${team2Id}"][data-team2-id="${team1Id}"]`);
}

function markAnalysisSelection() {
    document.querySelectorAll('#knockout .playoff-pair-card').forEach(card => {
        const s = activeAnalysisSelection;
        const on = !!s && (
            (card.dataset.team1Id == s.team1Id && card.dataset.team2Id == s.team2Id) ||
            (card.dataset.team1Id == s.team2Id && card.dataset.team2Id == s.team1Id));
        card.classList.toggle('selected', on);
        card.setAttribute('aria-selected', on ? 'true' : 'false');
    });
}

function updateAnalysisPairTitle(team1Name, team2Name) {
    const panel = getAnalysisPanel();
    if (!panel) return;
    const title = panel.querySelector('.analysis-pair-title');
    if (title) {
        title.innerHTML = `${escapeHtml(team1Name)} <span class="analysis-pair-vs">vs</span> ${escapeHtml(team2Name)}`;
    }
}

// Clear the sidebar + selection. `invalidate` drops in-flight analysis
// fetches; `focusCard` returns focus into the active Knockout view (used by
// close/Escape, not by stage changes). Focus fallback chain: originating card
// → the matching currently-rendered card (an Overview preview can open the
// analysis before the cached pair promise has rendered any card) → the active
// stage tab. Never a hidden Overview control, never <body>.
function closeActiveAnalysis({ invalidate = true, focusCard = false } = {}) {
    if (invalidate) analysisRequestGen++;
    const selection = activeAnalysisSelection;
    activeAnalysisSelection = null;
    currentPairIndex = -1;
    const panel = getAnalysisPanel();
    if (panel) {
        panel.hidden = true;
        const content = panel.querySelector('.analysis-content');
        if (content) content.innerHTML = '';
        const title = panel.querySelector('.analysis-pair-title');
        if (title) title.textContent = '';
    }
    document.querySelectorAll('#knockout .playoff-pair-card.selected').forEach(card => {
        card.classList.remove('selected');
        card.setAttribute('aria-selected', 'false');
    });
    if (focusCard) {
        let target = (lastAnalysisCard && lastAnalysisCard.isConnected) ? lastAnalysisCard : null;
        if (!target && selection) {
            target = findPairCard(selection.team1Id, selection.team2Id);
        }
        if (!target) {
            target = document.querySelector('#knockout .tab-btn.active');
        }
        if (target) target.focus();
    }
    lastAnalysisCard = null;
}

function closePlayoffAnalysis() {
    closeActiveAnalysis({ invalidate: true, focusCard: true });
}

async function showPlayoffAnalysis(team1Id, team2Id, sourceCard) {
    const gen = ++analysisRequestGen;
    const panel = getAnalysisPanel();
    if (!panel) return;

    currentPairIndex = currentPairsList.findIndex(p => 
        (p.team1.id == team1Id && p.team2.id == team2Id) || 
        (p.team1.id == team2Id && p.team2.id == team1Id)
    );
    
    activeAnalysisSelection = { team1Id, team2Id };
    lastAnalysisCard = sourceCard || findPairCard(team1Id, team2Id);
    markAnalysisSelection();
    
    const pair = currentPairIndex >= 0 ? currentPairsList[currentPairIndex] : null;
    updateAnalysisPairTitle(
        pair ? pair.team1.name : 'Pair analysis',
        pair ? pair.team2.name : '');
    panel.hidden = false;
    
    const cacheKey = `${team1Id}-${team2Id}`;
    const content = panel.querySelector('.analysis-content');
    
    // Check cache first — cached payload still passes the generation guard
    // because a later selection/close can invalidate while rendering.
    if (playoffAnalysisCache[cacheKey]) {
        await displayPlayoffAnalysis(playoffAnalysisCache[cacheKey], team1Id, team2Id, gen);
        return;
    }
    
    // Show loading
    content.innerHTML = '<div class="loading">Loading analysis...</div>';
    
    try {
        const response = await fetch(`${API_BASE_URL}/api/playoff-pairs/${team1Id}/${team2Id}/analysis${seasonQuery()}`);
        if (gen !== analysisRequestGen) return; // selection/stage changed: drop late response
        
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const analysis = await response.json();
        if (gen !== analysisRequestGen) return;
        playoffAnalysisCache[cacheKey] = analysis;
        await displayPlayoffAnalysis(analysis, team1Id, team2Id, gen);
    } catch (error) {
        if (gen !== analysisRequestGen) return;
        console.error('Error loading play-off analysis:', error);
        const livePanel = getAnalysisPanel();
        if (livePanel && !livePanel.hidden) {
            const liveContent = livePanel.querySelector('.analysis-content');
            // Failure is never cached; the next open retries.
            liveContent.innerHTML = `
                <div class="analysis-error" role="alert">
                    <p>Analysis unavailable — could not load this match.</p>
                    <p class="analysis-error-detail">${escapeHtml(error.message)}</p>
                </div>`;
        }
    }
}

// Sidebar wiring: labelled close button + arrow/Escape keys scoped to the
// knockout section. Tab controls and form fields keep their own keys —
// closest('[role="tab"]') bails out before anything is prevented.
function initializeKnockoutSidebar() {
    const panel = getAnalysisPanel();
    if (panel) {
        const closeBtn = panel.querySelector('.analysis-close');
        if (closeBtn) closeBtn.addEventListener('click', closePlayoffAnalysis);
    }
    
    const knockout = document.getElementById('knockout');
    if (!knockout) return;
    knockout.addEventListener('keydown', (e) => {
        const target = e.target;
        if (target && target.closest && target.closest('[role="tab"], input, select, textarea')) {
            return; // stage tabs and form controls own their arrow keys
        }
        if (!activeAnalysisSelection) return;
        if (e.key === 'ArrowRight') {
            if (currentPairIndex >= 0 && currentPairIndex < currentPairsList.length - 1) {
                e.preventDefault();
                navigateToPair(currentPairIndex + 1);
            }
        } else if (e.key === 'ArrowLeft') {
            if (currentPairIndex > 0) {
                e.preventDefault();
                navigateToPair(currentPairIndex - 1);
            }
        } else if (e.key === 'Escape') {
            e.preventDefault();
            closePlayoffAnalysis();
        }
    });
}

async function displayPlayoffAnalysis(analysis, team1Id, team2Id, gen) {
    const panel = getAnalysisPanel();
    if (!panel) return;
    const content = panel.querySelector('.analysis-content');
    
    if (!content) {
        console.error('Analysis content element not found in panel');
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
    updateAnalysisPairTitle(team1.name, team2.name);
    const leagueTable = analysis.leagueTable;
    const fullLeagueTable = leagueTable.fullLeagueTable || [];
    // Missing probability stays null (rendered as unavailable), never a fake 50/50.
    const winProbability = leagueTable.winProbability || null;
    const isEloMethod = !!winProbability && winProbability.method === 'elo';
    
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
            const response = await fetch(`${API_BASE_URL}/api/standings${seasonQuery()}`);
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
    
    // Format win probability - normalize excluding draws so they sum to 100%.
    // Default/no-signal methods and zero totals yield null → "unavailable".
    const probNorm = normalizeWinProbability(winProbability);
    const methodSlug = winProbability && winProbability.method
        ? ` (method: ${escapeHtml(String(winProbability.method).replace(/_/g, ' '))})`
        : '';
    // Default/no-signal methods never yield displayed probabilities; their
    // honest "why nothing is shown" explanation belongs on the unavailable
    // branch, not next to bars that only genuine methods reach.
    const isDefaultProbMethod = !!winProbability &&
        EXCLUDED_PROB_METHODS.some(m => String(winProbability.method || '').toLowerCase().includes(m));
    
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
    
    // Navigation buttons: accessible labels, disabled at bounds, wired after
    // render (never inline handlers inside Knockout).
    const hasPrevious = currentPairIndex > 0;
    const hasNext = currentPairIndex >= 0 && currentPairIndex < currentPairsList.length - 1;
    const pairNumber = currentPairIndex >= 0 ? currentPairIndex + 1 : 0;
    const totalPairs = currentPairsList.length;
    
    const navButtons = totalPairs > 1 ? `
        <div class="analysis-navigation">
            <button type="button" class="nav-btn nav-prev" data-index="${currentPairIndex - 1}"
                    ${!hasPrevious ? 'disabled' : ''} aria-label="Previous pair" title="Previous pair">
                <span aria-hidden="true">‹</span>
            </button>
            <span class="pair-counter">${pairNumber} / ${totalPairs}</span>
            <button type="button" class="nav-btn nav-next" data-index="${currentPairIndex + 1}"
                    ${!hasNext ? 'disabled' : ''} aria-label="Next pair" title="Next pair">
                <span aria-hidden="true">›</span>
            </button>
        </div>
    ` : '';
    
    // Build direct matches HTML
    const directMatchesHtml = directMatches.length > 0 ? `
        <div class="direct-matches-section">
            <h3>Direct Matches: ${escapeHtml(team1.name)} vs ${escapeHtml(team2.name)}</h3>
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
                                    ${crestHtml(homeCrest, homeTeam)}
                                    <span class="direct-match-team-name">${escapeHtml(homeTeam)}</span>
                                </div>
                                <div class="direct-match-score">
                                    <span class="score">${homeScore}-${awayScore}</span>
                                    <span class="match-outcome-icon">${outcomeIcon}</span>
                                </div>
                                <div class="direct-match-team">
                                    ${crestHtml(awayCrest, awayTeam)}
                                    <span class="direct-match-team-name">${escapeHtml(awayTeam)}</span>
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
            <h3>Direct Matches: ${escapeHtml(team1.name)} vs ${escapeHtml(team2.name)}</h3>
            <p class="no-direct-matches">No direct matches found between these teams in the historical data.</p>
        </div>
    `;
    
    // Build Elo breakdown section (shown when method is 'elo')
    let eloSectionHtml = '';
    if (isEloMethod) {
        const r1 = winProbability.team1EloRating || 1500;
        const r2 = winProbability.team2EloRating || 1500;
        const m1 = winProbability.team1MatchesPlayed || 0;
        const m2 = winProbability.team2MatchesPlayed || 0;
        const delta = r2 - r1;
        const exponent = delta / 400;
        const power = Math.pow(10, exponent);
        const p1 = 1 / (1 + power);
        const p2 = 1 - p1;
        const deltaLabel = delta >= 0 ? `+${delta.toFixed(0)}` : delta.toFixed(0);

        const s1 = winProbability.team1Stats || null;
        const s2 = winProbability.team2Stats || null;

        function eloStatsRow(s) {
            if (!s) return '';
            const gd = s.goalDifference >= 0 ? `+${s.goalDifference}` : `${s.goalDifference}`;
            return `
                <div class="elo-record">
                    <span class="elo-record-item won">${s.won}W</span>
                    <span class="elo-record-sep">·</span>
                    <span class="elo-record-item drawn">${s.drawn}D</span>
                    <span class="elo-record-sep">·</span>
                    <span class="elo-record-item lost">${s.lost}L</span>
                    <span class="elo-record-sep">·</span>
                    <span class="elo-record-item gd">GD ${gd}</span>
                </div>`;
        }

        function notableWinsTable(s, name) {
            if (!s || !s.notableWins || s.notableWins.length === 0) return '';
            const rows = s.notableWins.map(w => `
                <tr>
                    <td>${escapeHtml(w.date || '—')}</td>
                    <td>${escapeHtml(w.opponent)}</td>
                    <td class="score-cell">${escapeHtml(w.score)}</td>
                </tr>`).join('');
            return `
                <div class="notable-wins">
                    <h5>${escapeHtml(name)} — Biggest wins</h5>
                    <table class="notable-wins-table">
                        <thead><tr><th>Date</th><th>Opponent</th><th>Score</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>`;
        }

        eloSectionHtml = `
            <div class="elo-analysis-section">
                <h3>Elo Rating Breakdown</h3>
                <div class="elo-rating-comparison">
                    <div class="elo-team-card">
                        ${crestHtml(team1.crest, team1.name)}
                        <div class="elo-team-name">${escapeHtml(team1.name)}</div>
                        <div class="elo-rating-value">${r1.toFixed(0)}</div>
                        <div class="elo-matches-played">${m1} matches</div>
                        ${eloStatsRow(s1)}
                        <div class="elo-win-pct team1-color">${(p1 * 100).toFixed(1)}% win</div>
                    </div>
                    <div class="elo-divider">
                        <div class="elo-delta ${delta > 0 ? 'delta-negative' : 'delta-positive'}">${deltaLabel}</div>
                        <div class="elo-divider-label">Elo gap</div>
                    </div>
                    <div class="elo-team-card">
                        ${crestHtml(team2.crest, team2.name)}
                        <div class="elo-team-name">${escapeHtml(team2.name)}</div>
                        <div class="elo-rating-value">${r2.toFixed(0)}</div>
                        <div class="elo-matches-played">${m2} matches</div>
                        ${eloStatsRow(s2)}
                        <div class="elo-win-pct team2-color">${(p2 * 100).toFixed(1)}% win</div>
                    </div>
                </div>

                <div class="elo-notable-wins-row">
                    ${notableWinsTable(s1, team1.name)}
                    ${notableWinsTable(s2, team2.name)}
                </div>

                <div class="elo-formula-breakdown">
                    <h4>Step-by-step calculation (neutral ground, ${escapeHtml(team1.name)} perspective)</h4>
                    <div class="formula-steps">
                        <div class="formula-step">ΔElo = R₂ − R₁ = ${r2.toFixed(0)} − ${r1.toFixed(0)} = ${delta.toFixed(0)}</div>
                        <div class="formula-step">P(${escapeHtml(team1.name)}) = 1 / (1 + 10^(ΔElo / 400))</div>
                        <div class="formula-step">= 1 / (1 + 10^(${delta.toFixed(0)} / 400))</div>
                        <div class="formula-step">= 1 / (1 + 10^${exponent.toFixed(4)})</div>
                        <div class="formula-step">= 1 / (1 + ${power.toFixed(4)})</div>
                        <div class="formula-step result">= <strong>${(p1 * 100).toFixed(1)}%</strong> &nbsp;·&nbsp; P(${escapeHtml(team2.name)}) = 1 − ${(p1 * 100).toFixed(1)}% = <strong>${(p2 * 100).toFixed(1)}%</strong></div>
                    </div>
                </div>

                <p class="elo-methodology-note">
                    Ratings built by replaying 10 years of UEFA Champions League history chronologically.
                    Each result updates both teams' ratings: K-factor = 30, goal multiplier = 1 + ln(|goal diff| + 1),
                    home advantage = +75 Elo during training. Win probability is calculated on a neutral ground.
                    Default starting rating: 1500. Minimum 5 matches required for confidence.
                </p>
            </div>
        `;
    }

    // Late guard: a newer selection, stage change or close may have landed
    // while positions/standings were still resolving — never overwrite then.
    if (gen !== undefined && gen !== analysisRequestGen) return;
    panel.hidden = false;
    
    content.innerHTML = `
        ${navButtons}
        <div class="analysis-header">
            <div class="analysis-team ${winnerClass1}">
                ${crestHtml(team1.crest, team1.name)}
                <h3>${escapeHtml(team1.name)}</h3>
                ${team1Position !== null ? `<span class="league-position" title="${escapeHtml(leaguePositionTitle())}">#${team1Position}</span>` : ''}
            </div>
            <div class="analysis-vs">vs</div>
            <div class="analysis-team ${winnerClass2}">
                ${crestHtml(team2.crest, team2.name)}
                <h3>${escapeHtml(team2.name)}</h3>
                ${team2Position !== null ? `<span class="league-position" title="${escapeHtml(leaguePositionTitle())}">#${team2Position}</span>` : ''}
            </div>
        </div>
        
        ${directMatchesHtml}
        
        <div class="win-probability">
            <h3>Win Probability <span class="win-prob-scope">— excluding draws</span></h3>
            ${probNorm ? `
            <div class="probability-bars">
                <div class="probability-item">
                    <div class="probability-label">
                        <span>${escapeHtml(team1.name)}</span>
                        <span class="probability-value">${(probNorm.team1 * 100).toFixed(1)}%</span>
                    </div>
                    <div class="probability-bar">
                        <div class="probability-fill team1-fill" style="width: ${(probNorm.team1 * 100).toFixed(1)}%"></div>
                    </div>
                </div>
                <div class="probability-item">
                    <div class="probability-label">
                        <span>${escapeHtml(team2.name)}</span>
                        <span class="probability-value">${(probNorm.team2 * 100).toFixed(1)}%</span>
                    </div>
                    <div class="probability-bar">
                        <div class="probability-fill team2-fill" style="width: ${(probNorm.team2 * 100).toFixed(1)}%"></div>
                    </div>
                </div>
            </div>
            ${getProbabilityExplanation(winProbability, team1.name, team2.name)}` : `
            <p class="probability-unavailable">No meaningful win probability available${methodSlug}.</p>
            ${isDefaultProbMethod ? getProbabilityExplanation(winProbability, team1.name, team2.name) : ''}`}
        </div>
        
        ${eloSectionHtml}

        ${!isEloMethod ? `
        <div class="analysis-info">
            <p><strong>Teams in League:</strong> ${fullLeagueTable.length}</p>
            <p><strong>Common Opponents:</strong> ${analysis.commonOpponentsCount}</p>
            <p><strong>Historical Period:</strong> Last ${analysis.historicalYears} years</p>
            ${analysis.commonOpponentsCount === 0 ? '<p class="warning">No common opponents found in the historical data.</p>' : ''}
        </div>` : ''}

        ${fullLeagueTable.length > 0 && !isEloMethod ? `
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
                                    ${crestHtml(team.teamCrest, team.teamName)}
                                    ${isTeam1 || isTeam2 ? '<strong>' : ''}${escapeHtml(team.teamName)}${isTeam1 || isTeam2 ? '</strong>' : ''}
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
                            
                            return `
                                <div class="match-item ${outcomeClass}">
                                    ${crestHtml(match.opponentCrest, match.opponentName)}
                                    <span class="match-opponent">${escapeHtml(match.opponentName)}</span>
                                    <span class="match-score">${match.teamScore}-${match.opponentScore}</span>
                                    <span class="match-outcome">${outcomeIcon}</span>
                                    <span class="match-date">${match.date ? new Date(match.date).toLocaleDateString() : ''}</span>
                                </div>
                            `;
                        }).join('');
                        
                        return `
                            <div class="team-matches ${isPlayoffTeam ? 'playoff-team-matches' : ''}">
                                <h4 class="team-matches-header">
                                    ${crestHtml(team.teamCrest, team.teamName)}
                                    <strong>${escapeHtml(team.teamName)}</strong> (${otherMatches.length} match${otherMatches.length !== 1 ? 'es' : ''})
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
    
    wireCrestFallbacks(content);
    // Nav buttons carry data-index only; listeners attached after every render.
    content.querySelectorAll('.nav-prev, .nav-next').forEach(btn => {
        btn.addEventListener('click', () => navigateToPair(Number(btn.dataset.index)));
    });
}

// Make functions globally accessible
// Helper function to generate probability explanation
function getProbabilityExplanation(winProbability, team1Name = 'Team 1', team2Name = 'Team 2') {
    // API-derived names are inert text; escape once up front (no value changes).
    team1Name = escapeHtml(team1Name);
    team2Name = escapeHtml(team2Name);
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
        case 'elo': {
            const r1 = (winProbability.team1EloRating || 1500).toFixed(0);
            const r2 = (winProbability.team2EloRating || 1500).toFixed(0);
            const m1 = winProbability.team1MatchesPlayed || 0;
            const m2 = winProbability.team2MatchesPlayed || 0;
            const rDiff = Math.abs((winProbability.team1EloRating || 1500) - (winProbability.team2EloRating || 1500)).toFixed(0);
            explanation = `
                <div class="probability-explanation">
                    <h4>How this probability is calculated:</h4>
                    <p><strong>Method: Goal-Adjusted Elo Rating</strong></p>
                    <p>Elo ratings are built by replaying 10 years of UEFA Champions League match history.
                    Each match updates both teams' ratings based on the result and goal margin
                    (larger wins = bigger rating swing). A home advantage of +75 Elo points is applied during training.</p>
                    <ul>
                        <li><strong>${team1Name}:</strong> Elo ${r1} (${m1} matches)</li>
                        <li><strong>${team2Name}:</strong> Elo ${r2} (${m2} matches)</li>
                        <li>Rating difference: ${rDiff} points</li>
                    </ul>
                    <p>Win probability is calculated on a neutral ground:
                    <code>P(win) = 1 / (1 + 10^(ΔElo / 400))</code></p>
                    <p class="probability-note">Draw probability is not modelled — probabilities sum to 100%.</p>
                </div>
            `;
            break;
        }
        case 'historical_no_data':
            explanation = `
                <div class="probability-explanation">
                    <h4>Why no win probability is shown:</h4>
                    <p><strong>Method: historical archive</strong></p>
                    <p>Win probability is unavailable for archived seasons. Elo and
                    live strength ratings are built from present-day data, so using
                    them here would leak results that happened after this season into
                    its prediction. No estimate is shown rather than a misleading one.</p>
                </div>
            `;
            break;
        case 'equal_strength':
        case 'default':
        case 'no_data':
        case 'insufficient_data':
            // Nothing is rendered as a prediction for these methods, so the
            // explanation says so honestly — no claim of shown equal values.
            explanation = `
                <div class="probability-explanation">
                    <h4>Why no win probability is shown:</h4>
                    <p><strong>Method: ${escapeHtml(method)}</strong></p>
                    <p>Insufficient historical data is available to calculate a meaningful
                    probability. No prediction is displayed: inventing an equal split would
                    be fake, so this match's win probability is reported as unavailable.</p>
                </div>
            `;
            break;
        case 'no_common_opponents':
            explanation = `
                <div class="probability-explanation">
                    <h4>Why no win probability is shown:</h4>
                    <p><strong>Method: ${escapeHtml(method)}</strong></p>
                    <p>No common opponents were found in the historical data, so there is
                    no basis for a probability. It is reported as unavailable rather than
                    guessed.</p>
                </div>
            `;
            break;
        case 'error':
            explanation = `
                <div class="probability-explanation">
                    <h4>Why no win probability is shown:</h4>
                    <p><strong>Method: ${escapeHtml(method)}</strong></p>
                    <p>The probability calculation failed, so no prediction is shown for
                    this match. It is reported as unavailable.</p>
                </div>
            `;
            break;
        default:
            explanation = `
                <div class="probability-explanation">
                    <h4>How this probability is calculated:</h4>
                    <p><strong>Method: ${escapeHtml(method)}</strong></p>
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
        // Move the selection (and focus for chained keyboard navigation).
        const card = findPairCard(pair.team1.id, pair.team2.id);
        if (card) {
            lastAnalysisCard = card;
            card.focus();
        }
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
    
    renderLeague();
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
        
        renderLeague();
    }
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

// App-level inline failure/status (role="alert" region outside the view
// panels). The legacy #error lives inside the League panel and is invisible
// while Overview/Knockout is active, so failures must also land here.
function setAppStatus(message) {
    const el = document.getElementById('appStatus');
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
}

function clearAppStatus() {
    const el = document.getElementById('appStatus');
    if (!el) return;
    el.textContent = '';
    el.hidden = true;
}

// Only one reload cycle at a time: clicks during the reload window are
// ignored (the disabled button plus this flag guard against concurrency).
let refreshInFlight = false;

// Public "Reload data": re-reads already-stored standings/pairs over GET only.
// It never issues a POST and never calls /api/refresh, so the browser cannot
// trigger server-side data collection; scheduled updates run separately.
async function refreshData() {
    if (refreshInFlight) return;
    const btn = document.getElementById('refreshBtn');
    if (!btn) return;
    refreshInFlight = true;
    btn.disabled = true;
    btn.textContent = 'Reloading...';

    try {
        // Drop every cached payload so post-reload views can never replay
        // pre-reload data. closeActiveAnalysis bumps analysisRequestGen, so
        // in-flight analysis responses are discarded and no stale
        // selection/panel content survives as if current. The existing raw
        // data is kept on screen until the re-read replaces it; a failure below
        // never claims freshness.
        solkoffDetailsCache = {};
        playoffAnalysisCache = {};
        pairsByStage = {};
        closeActiveAnalysis({ invalidate: true, focusCard: false });

        // Re-read once each via the shared GET loaders. Generation counters
        // inside the loaders supersede pre-reload pending responses; the shared
        // pairs cache means the Overview preview and the selected Knockout stage
        // never fetch the same stage twice. Search/sort/advanced/expanded state
        // live in module variables, so the re-render preserves them. The
        // "Standings loaded at" timestamp is written only by a successful
        // standings fetch.
        await loadStandings();
        await loadOverviewKnockout();
        if (currentRoute.view === 'knockout' && currentRoute.stage) {
            await loadKnockoutPairs(stageKeyForTab(currentRoute.stage), currentRoute.stage);
        }

    } catch (error) {
        // Inline failure only — never alert(), never wipe valid data. League
        // view keeps its legacy #error surface; every other view gets the
        // global status region so a failure is never hidden away in a panel
        // the user cannot see. The copy refers to reloading stored data, not a
        // forced upstream update.
        console.error('Error reloading data:', error);
        const message = 'Reload failed — stored data was not updated. Please try again.';
        const leaguePanel = document.getElementById('league');
        const errorEl = document.getElementById('error');
        if (leaguePanel && !leaguePanel.hidden && errorEl) {
            errorEl.textContent = message;
            errorEl.style.display = 'block';
        } else {
            setAppStatus(message);
        }
    } finally {
        refreshInFlight = false;
        btn.disabled = false;
        btn.textContent = 'Reload data';
    }
}

function updateLastUpdated() {
    const lastUpdatedEl = document.getElementById('lastUpdated');
    const now = new Date();
    lastUpdatedEl.textContent = `Standings loaded at ${now.toLocaleTimeString()}`;
}

// ── Intro banner ──────────────────────────────────────────────────────────────
function showIntroIfFirstVisit() {
    if (!localStorage.getItem('solkoff-intro-seen')) {
        const banner = document.getElementById('intro-banner');
        if (banner) banner.removeAttribute('hidden');
    }
}

function dismissIntro() {
    localStorage.setItem('solkoff-intro-seen', '1');
    const banner = document.getElementById('intro-banner');
    if (banner) banner.setAttribute('hidden', '');
}

// ── Skeleton loading rows ─────────────────────────────────────────────────────
function renderSkeletonRows(count) {
    const widths = ['20px', '140px', '24px', '24px', '24px', '24px', '28px', '28px', '32px', '32px', '48px', '48px'];
    return Array.from({length: count}, () =>
        `<tr class="skeleton-row" aria-hidden="true">` +
        widths.map((w, i) => {
            const adv = (i >= 3 && i <= 8) ? ' class="col-adv"' : '';
            return `<td${adv}><span class="skeleton-cell" style="width:${w}"></span></td>`;
        }).join('') +
        `</tr>`
    ).join('');
}

// ---------------------------------------------------------------------------
// Overview (UCL Strength Lab) — shared helpers + data-only rendering
// ---------------------------------------------------------------------------

// All API-derived text is inserted as escaped, inert content.
function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}

// Crest images are only allowed over HTTP(S); unsafe schemes fall back to initials.
function safeCrestUrl(url) {
    return typeof url === 'string' && /^https?:\/\//i.test(url.trim()) ? url.trim() : null;
}

function crestFallbackText(name, code) {
    if (code) return String(code).trim().slice(0, 3).toUpperCase();
    const words = String(name || '').trim().split(/\s+/).filter(Boolean);
    const initials = words.slice(0, 3).map(w => w[0]).join('');
    return (initials || String(name || '?').trim().slice(0, 3)).toUpperCase();
}

// Real crest when a safe URL exists, initials otherwise. Broken images are
// swapped to initials by wireCrestFallbacks (no inline handlers added).
function crestHtml(crest, name, code) {
    const fallback = escapeHtml(crestFallbackText(name, code));
    const url = safeCrestUrl(crest);
    if (!url) {
        return `<span class="crest-initials crest-placeholder" aria-hidden="true">${fallback}</span>`;
    }
    return `<img class="crest" src="${escapeHtml(url)}" alt="" data-fallback="${fallback}">`;
}

function wireCrestFallbacks(root) {
    root.querySelectorAll('img.crest[data-fallback]').forEach(img => {
        img.addEventListener('error', () => {
            const span = document.createElement('span');
            span.className = 'crest-initials crest-placeholder';
            span.setAttribute('aria-hidden', 'true');
            span.textContent = img.dataset.fallback;
            img.replaceWith(span);
        }, { once: true });
    });
}

// Normalized two-team win fractions excluding draws. null when the probability
// is missing, comes from a default/no-signal method, or the win total is zero —
// callers must show "unavailable" and never invent or present a fake 50/50.
function normalizeWinProbability(probability) {
    if (!probability) return null;
    const method = String(probability.method || '').toLowerCase();
    if (EXCLUDED_PROB_METHODS.some(m => method.includes(m))) return null;
    const t1 = Number(probability.team1Win) || 0;
    const t2 = Number(probability.team2Win) || 0;
    const total = t1 + t2;
    if (!(total > 0)) return null;
    return { team1: t1 / total, team2: t2 / total };
}

// Backend default/no-signal methods (see backend/playoff_analyzer.py): these
// emit flat 0.5/0.5 placeholders and must never be treated as predictions —
// neither in balanced-tie selection nor in any displayed probability.
const EXCLUDED_PROB_METHODS = ['no_data', 'no_common_opponents', 'default',
                               'equal', 'insufficient', 'error'];

function isMeaningfulProbability(probability) {
    return normalizeWinProbability(probability) !== null;
}

function findMostBalancedTie(pairs) {
    let best = null;
    let bestGap = Infinity;
    (pairs || []).forEach(pair => {
        if (!isMeaningfulProbability(pair.winProbability)) return;
        const norm = normalizeWinProbability(pair.winProbability);
        const gap = Math.abs(norm.team1 - norm.team2);
        if (gap < bestGap) { bestGap = gap; best = pair; }
    });
    return best;
}

function buildOverviewSummary(standings, pairs) {
    let strongest = null;
    let toughest = null;
    (Array.isArray(standings) ? standings : []).forEach(team => {
        if (!strongest || Number(team.strengthScore) > Number(strongest.strengthScore)) strongest = team;
        if (!toughest || Number(team.solkoffCoefficient) > Number(toughest.solkoffCoefficient)) toughest = team;
    });
    return { strongest, toughest, balancedTie: findMostBalancedTie(pairs) };
}

// Standings and pair previews load independently; each keeps its own explicit
// loading/empty/error state. Generation counters drop stale async writes.
let standingsState = 'loading'; // loading|ready|empty|error
let standingsGen = 0;
let overviewPairs = null;
let overviewPairsStage = 'ROUND_OF_16'; // API stage key the preview set was fetched for
let pairsState = 'loading'; // loading|ready|empty|error
let pairsGen = 0;

function ensureStandingsLoaded() {
    if (standingsRequested) return;
    standingsRequested = true;
    loadStandings();
}

function stageKeyForTab(tabId) {
    return Object.keys(STAGE_MAP).find(k => STAGE_MAP[k] === tabId) || 'ROUND_OF_16';
}

function standingsMessageForState() {
    if (standingsState === 'loading') return 'Loading standings...';
    if (standingsState === 'empty') return 'No standings data available';
    if (standingsState === 'error') return 'Standings unavailable';
    return '';
}

function pairsMessageForState() {
    if (pairsState === 'loading') return 'Loading knockout ties...';
    if (pairsState === 'empty') return 'No knockout ties available';
    if (pairsState === 'error') return 'Knockout ties unavailable';
    return '';
}

function renderOverview() {
    renderOverviewStandings();
    renderOverviewKnockout();
}

function renderOverviewStandings() {
    renderOverviewMetrics();
    renderRanking('strengthRanking', 'strengthScore', v => v.toFixed(1));
    renderRanking('scheduleRanking', 'solkoffCoefficient', v => v.toFixed(2));
}

function renderRanking(containerId, valueKey, format) {
    const el = document.getElementById(containerId);
    if (!el) return;
    if (standingsState !== 'ready') {
        el.innerHTML = `<div class="overview-state">${standingsMessageForState()}</div>`;
        return;
    }
    const ranked = [...standingsData].sort(
        (a, b) => (Number(b[valueKey]) || 0) - (Number(a[valueKey]) || 0));
    // Bars normalize against the maximum of the COMPLETE dataset, not the
    // visible top-10 slice.
    const max = ranked.length ? Number(ranked[0][valueKey]) || 0 : 0;
    el.innerHTML = ranked.slice(0, 10).map((team, i) => {
        const value = Number(team[valueKey]) || 0;
        const width = max > 0 ? Math.min(100, (value / max) * 100) : 0;
        return `<div class="rank-row">
            <span class="rank-pos">${i + 1}</span>
            <span class="rank-team">${crestHtml(team.teamCrest, team.teamName, team.teamCode)}<span class="rank-name">${escapeHtml(team.teamName)}</span></span>
            <div class="bar-track"><div class="bar-fill" style="width:${width.toFixed(2)}%"></div></div>
            <span class="rank-value">${format(value)}</span>
        </div>`;
    }).join('');
    wireCrestFallbacks(el);
}

function renderOverviewMetrics() {
    const el = document.getElementById('overviewMetrics');
    if (!el) return;
    const summary = buildOverviewSummary(
        standingsState === 'ready' ? standingsData : [], overviewPairs || []);
    el.innerHTML = [
        metricTeamCard('Strongest Team', summary.strongest,
            t => `Strength ${Number(t.strengthScore).toFixed(1)}`, standingsMessageForState()),
        metricTeamCard('Toughest Schedule', summary.toughest,
            t => `Solkoff ${Number(t.solkoffCoefficient).toFixed(2)}`, standingsMessageForState()),
        metricTieCard(summary.balancedTie),
    ].join('');
    wireCrestFallbacks(el);
}

function metricTeamCard(label, team, valueLine, fallbackMsg) {
    const body = team
        ? `<div class="metric-value">${crestHtml(team.teamCrest, team.teamName, team.teamCode)}` +
          `<span>${escapeHtml(team.teamName)}</span></div>` +
          `<div class="metric-sub">${valueLine(team)}</div>`
        : `<div class="metric-sub">${fallbackMsg || 'No data available'}</div>`;
    return `<div class="metric-card"><div class="metric-label">${label}</div>${body}</div>`;
}

function metricTieCard(tie) {
    let body;
    if (tie) {
        const norm = normalizeWinProbability(tie.winProbability);
        const split = `${(norm.team1 * 100).toFixed(1)}% – ${(norm.team2 * 100).toFixed(1)}%`;
        body = `<div class="metric-value"><span>${escapeHtml(tie.team1.name)} vs ${escapeHtml(tie.team2.name)}</span></div>` +
               `<div class="metric-sub">${split}</div>`;
    } else {
        const msg = pairsMessageForState() || 'No meaningful probabilities available';
        body = `<div class="metric-sub">${msg}</div>`;
    }
    return `<div class="metric-card"><div class="metric-label">Most Balanced Tie</div>${body}</div>`;
}

function renderOverviewKnockout() {
    const el = document.getElementById('overviewKnockoutCards');
    if (!el) return;
    if (pairsState !== 'ready' || (overviewPairs || []).length === 0) {
        el.innerHTML = `<div class="overview-state">${pairsMessageForState() || 'No knockout ties available'}</div>`;
        renderOverviewMetrics();
        return;
    }
    // Up to three previews, in API order — existing ties, not invented dates.
    el.innerHTML = overviewPairs.slice(0, 3).map(miniMatchHtml).join('');
    wireCrestFallbacks(el);
    el.querySelectorAll('.mini-match').forEach(btn => {
        // Open the stage actually fetched for this preview set, not the raw DB
        // token on the pair (which may not exist in STAGE_MAP).
        btn.addEventListener('click', () => openKnockoutPairFromOverview(
            overviewPairsStage, Number(btn.dataset.team1), Number(btn.dataset.team2)));
    });
    renderOverviewMetrics();
}

function miniMatchHtml(pair) {
    const t1 = pair.team1 || {};
    const t2 = pair.team2 || {};
    const id1 = Number(t1.id) || 0;
    const id2 = Number(t2.id) || 0;
    const norm = isMeaningfulProbability(pair.winProbability)
        ? normalizeWinProbability(pair.winProbability) : null;
    const prob = norm
        ? `<div class="mini-prob">${(norm.team1 * 100).toFixed(1)}% – ${(norm.team2 * 100).toFixed(1)}%</div>`
        : '<div class="mini-prob">Win probability unavailable</div>';
    return `<button type="button" class="mini-match" data-team1="${id1}" data-team2="${id2}"` +
        ` aria-label="Open analysis for ${escapeHtml(t1.name)} vs ${escapeHtml(t2.name)}">` +
        `<div class="mini-row">${crestHtml(t1.crest, t1.name)}<span>${escapeHtml(t1.name)}</span></div>` +
        `<div class="mini-row">${crestHtml(t2.crest, t2.name)}<span>${escapeHtml(t2.name)}</span></div>` +
        miniResultsHtml(pair) + prob + '</button>';
}

function openKnockoutPairFromOverview(stageKey, team1Id, team2Id) {
    // Reveal the Knockout view, select the pair's exact stage (pairs load from
    // the shared cache), then open the existing analysis view for the tie.
    activateView('knockout', { updateHash: false });
    const tabId = Object.prototype.hasOwnProperty.call(STAGE_MAP, stageKey)
        ? STAGE_MAP[stageKey] : null;
    if (tabId) activateTab(tabId);
    if (team1Id && team2Id) showPlayoffAnalysis(team1Id, team2Id);
}

// Preview pairs are chosen by the async current-stage API but only fill the
// Overview panel; the user's route/stage selection is never overridden.
async function loadOverviewKnockout() {
    const stage = stageKeyForTab(initialKnockoutStage);
    const gen = ++pairsGen;
    overviewPairsStage = stage;
    pairsState = 'loading';
    overviewPairs = null;
    renderOverviewKnockout();
    try {
        const pairs = await fetchKnockoutPairsOnce(stage);
        if (gen !== pairsGen) return; // stale preview response
        overviewPairs = pairs;
        pairsState = pairs.length > 0 ? 'ready' : 'empty';
    } catch (error) {
        if (gen !== pairsGen) return;
        console.error('Error loading overview knockout preview:', error);
        pairsState = 'error';
    }
    renderOverviewKnockout();
}
