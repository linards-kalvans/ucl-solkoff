const teams = [
  { rank:1, name:'Man City', code:'MC', pld:8,w:7,d:1,l:0,gf:20,ga:8,gd:12,pts:22,solkoff:1.51,strength:93.4,color:'blue' },
  { rank:2, name:'Real Madrid', code:'RM', pld:8,w:6,d:1,l:1,gf:18,ga:7,gd:11,pts:19,solkoff:1.42,strength:91.7,color:'gold' },
  { rank:3, name:'Arsenal', code:'ARS', pld:8,w:6,d:0,l:2,gf:16,ga:9,gd:7,pts:18,solkoff:1.38,strength:88.1,color:'red' },
  { rank:4, name:'Inter', code:'IN', pld:8,w:5,d:2,l:1,gf:13,ga:7,gd:6,pts:17,solkoff:1.38,strength:87.3,color:'blackblue' },
  { rank:5, name:'Bayern Munich', code:'BAY', pld:8,w:5,d:1,l:2,gf:17,ga:11,gd:6,pts:16,solkoff:1.33,strength:86.9,color:'red' },
  { rank:6, name:'Liverpool', code:'LIV', pld:8,w:5,d:1,l:2,gf:15,ga:9,gd:6,pts:16,solkoff:1.27,strength:85.6,color:'red' },
  { rank:7, name:'Barcelona', code:'BAR', pld:8,w:5,d:0,l:3,gf:20,ga:12,gd:8,pts:15,solkoff:1.29,strength:84.8,color:'red' },
  { rank:8, name:'Atlético', code:'ATM', pld:8,w:5,d:0,l:3,gf:14,ga:10,gd:4,pts:15,solkoff:1.22,strength:82.1,color:'red' },
  { rank:9, name:'Dortmund', code:'BVB', pld:8,w:4,d:2,l:2,gf:16,ga:13,gd:3,pts:14,solkoff:1.24,strength:80.4,color:'yellow' },
  { rank:10, name:'PSG', code:'PSG', pld:8,w:4,d:1,l:3,gf:14,ga:11,gd:3,pts:13,solkoff:1.27,strength:78.9,color:'blue' },
  { rank:25, name:'Shakhtar', code:'SHA', pld:8,w:1,d:2,l:5,gf:8,ga:18,gd:-10,pts:5,solkoff:.76,strength:58.2,color:'gold' },
  { rank:26, name:'Young Boys', code:'YB', pld:8,w:0,d:1,l:7,gf:7,ga:24,gd:-17,pts:1,solkoff:.64,strength:52.1,color:'yellow' }
];

const ties = [
  { date:'Mar 5, 2025 · 21:00 UTC', a:'Arsenal',ac:'ARS',b:'Inter',bc:'IN', pa:54,pb:46,eloA:88.1,eloB:87.3,diff:'+24 (ARS)' },
  { date:'Mar 6, 2025 · 21:00 UTC', a:'Real Madrid',ac:'RM',b:'Leverkusen',bc:'B04', pa:62,pb:38,eloA:91.7,eloB:78.3,diff:'+134 (RM)' },
  { date:'Mar 6, 2025 · 21:00 UTC', a:'PSG',ac:'PSG',b:'Liverpool',bc:'LIV', pa:49,pb:51,eloA:78.9,eloB:85.6,diff:'-12 (LIV)' },
  { date:'Mar 11, 2025 · 21:00 UTC', a:'Barcelona',ac:'BAR',b:'Dortmund',bc:'BVB', pa:56,pb:44,eloA:84.8,eloB:80.4,diff:'+40 (BAR)' },
  { date:'Mar 11, 2025 · 21:00 UTC', a:'Atlético',ac:'ATM',b:'Juventus',bc:'JUV', pa:52,pb:48,eloA:82.1,eloB:80.3,diff:'+18 (ATM)' },
  { date:'Mar 12, 2025 · 21:00 UTC', a:'Benfica',ac:'BEN',b:'Lille',bc:'LIL', pa:60,pb:40,eloA:79.8,eloB:75.0,diff:'+48 (BEN)' }
];

const crestClass = code => {
  if (['RM','SHA'].includes(code)) return 'crest-gold';
  if (['BVB','YB'].includes(code)) return 'crest-yellow';
  if (['IN'].includes(code)) return 'crest-blackblue';
  if (['MC','PSG'].includes(code)) return 'crest-blue';
  return 'crest-red';
};

function rankRow(team, metric='strength') {
  const value = metric === 'strength' ? team.strength : team.solkoff;
  const max = metric === 'strength' ? 100 : 1.6;
  return `
    <div class="rank-row">
      <span>${team.rank}</span>
      <div class="rank-team"><div class="crest ${crestClass(team.code)}">${team.code}</div><span>${team.name}</span></div>
      <strong>${value.toFixed(metric === 'strength' ? 1 : 2)}</strong>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, value/max*100)}%"></div></div>
    </div>`;
}

function renderOverview() {
  document.querySelector('#strengthRanking').innerHTML = teams.slice(0,10).map(t => rankRow(t,'strength')).join('');
  document.querySelector('#scheduleRanking').innerHTML = [...teams].sort((a,b)=>b.solkoff-a.solkoff).slice(0,10).map(t => rankRow(t,'solkoff')).join('');
  document.querySelector('#overviewKnockoutCards').innerHTML = ties.slice(0,3).map(miniMatch).join('');
}

function miniMatch(t) {
  return `
    <article class="mini-match">
      <div class="match-meta">${t.date} · Round of 16</div>
      <div class="match-teams">
        <div class="match-team"><div class="crest ${crestClass(t.ac)}">${t.ac}</div>${t.a}</div>
        <span>VS</span>
        <div class="match-team right">${t.b}<div class="crest ${crestClass(t.bc)}">${t.bc}</div></div>
      </div>
      <div class="probability-bar">
        <div class="prob-a" style="width:${t.pa}%">${t.pa}%</div>
        <div class="prob-b" style="width:${t.pb}%">${t.pb}%</div>
      </div>
      <div class="match-footer"><span>Elo difference: ${t.diff}</span><span>View analysis →</span></div>
    </article>`;
}

function renderLeague() {
  const search = document.querySelector('#teamSearch').value.toLowerCase();
  const sort = document.querySelector('#leagueSort').value;
  const list = teams
    .filter(t => t.name.toLowerCase().includes(search))
    .sort((a,b) => sort === 'points' ? b.pts-a.pts : sort === 'solkoff' ? b.solkoff-a.solkoff : b.strength-a.strength);

  let html = '';
  let directAdded = false, playoffAdded = false, outAdded = false;

  for (const t of list) {
    if (t.rank <= 8 && !directAdded) {
      html += `<tr class="zone-row zone-direct"><td colspan="12">Top 8 — Direct qualification to Round of 16</td></tr>`;
      directAdded = true;
    } else if (t.rank >= 9 && t.rank <= 24 && !playoffAdded) {
      html += `<tr class="zone-row zone-playoff"><td colspan="12">9–24 — Knockout play-off</td></tr>`;
      playoffAdded = true;
    } else if (t.rank >= 25 && !outAdded) {
      html += `<tr class="zone-row zone-out"><td colspan="12">Eliminated</td></tr>`;
      outAdded = true;
    }
    html += `
      <tr>
        <td>${t.rank}</td>
        <td><div class="team-inline"><div class="crest ${crestClass(t.code)}">${t.code}</div>${t.name}</div></td>
        <td>${t.pld}</td>
        <td class="advanced">${t.w}</td><td class="advanced">${t.d}</td><td class="advanced">${t.l}</td>
        <td class="advanced">${t.gf}</td><td class="advanced">${t.ga}</td><td class="advanced">${t.gd>0?'+':''}${t.gd}</td>
        <td><strong>${t.pts}</strong></td>
        <td>${t.solkoff.toFixed(2)}</td>
        <td class="strength-cell"><div class="strength-wrap"><div class="bar-track"><div class="bar-fill" style="width:${t.strength}%"></div></div><strong>${t.strength.toFixed(1)}</strong></div></td>
      </tr>`;
  }
  document.querySelector('#leagueBody').innerHTML = html;
  const on = document.querySelector('#advancedToggle').checked;
  document.querySelectorAll('.advanced').forEach(el => el.style.display = on ? '' : 'none');
}

function matchCard(t, i) {
  return `
    <article class="match-card" data-index="${i}">
      <div class="match-meta">${t.date}</div>
      <div class="match-teams">
        <div class="match-team"><div class="crest ${crestClass(t.ac)}">${t.ac}</div>${t.a}</div>
        <span>VS</span>
        <div class="match-team right">${t.b}<div class="crest ${crestClass(t.bc)}">${t.bc}</div></div>
      </div>
      <div class="probability-bar">
        <div class="prob-a" style="width:${t.pa}%">${t.pa}%</div>
        <div class="prob-b" style="width:${t.pb}%">${t.pb}%</div>
      </div>
      <div class="match-footer"><span>Elo ${t.eloA}</span><span>${t.diff}</span><span>Elo ${t.eloB}</span></div>
    </article>`;
}

function renderKnockout() {
  document.querySelector('#knockoutGrid').innerHTML = ties.map(matchCard).join('');
  document.querySelectorAll('.match-card').forEach(card => {
    card.addEventListener('click', () => {
      document.querySelectorAll('.match-card').forEach(c=>c.classList.remove('selected'));
      card.classList.add('selected');
      showAnalysis(ties[Number(card.dataset.index)]);
    });
  });
}

function showAnalysis(t) {
  document.querySelector('#analysisPanel').innerHTML = `
    <div class="analysis-header">
      <div>
        <span class="eyebrow">Match Analysis</span>
        <h3>${t.a} vs ${t.b}</h3>
      </div>
      <span class="match-meta">${t.date}</span>
    </div>
    <div class="analysis-block">
      <span class="eyebrow">Our prediction</span>
      <div class="probability-bar" style="margin-top:10px">
        <div class="prob-a" style="width:${t.pa}%">${t.pa}%</div>
        <div class="prob-b" style="width:${t.pb}%">${t.pb}%</div>
      </div>
      <div class="analysis-stat-grid">
        <div class="analysis-stat"><strong>${t.eloA}</strong><span>${t.a} Elo</span></div>
        <div class="analysis-stat"><strong class="positive">${t.diff}</strong><span>Elo difference</span></div>
        <div class="analysis-stat"><strong>${t.eloB}</strong><span>${t.b} Elo</span></div>
      </div>
    </div>
    <div class="analysis-block">
      <span class="eyebrow">Interpretation</span>
      <p style="color:var(--muted);line-height:1.6">
        ${t.pa > t.pb ? t.a : t.b} is the model favourite. The probability split combines team strength and Elo difference,
        and should be treated as a comparative signal rather than a certainty.
      </p>
    </div>
    <div class="analysis-block">
      <span class="eyebrow">Key takeaways</span>
      <ul style="color:var(--muted);line-height:1.8;padding-left:18px">
        <li>Model edge: ${Math.abs(t.pa-t.pb)} percentage points.</li>
        <li>Elo spread: ${t.diff}.</li>
        <li>This prototype can be wired to your existing playoff-analysis API.</li>
      </ul>
    </div>`;
}

function switchView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active-view'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.querySelector(`#${id}`).classList.add('active-view');
  document.querySelector(`.nav-btn[data-view="${id}"]`)?.classList.add('active');
}

document.querySelectorAll('[data-view]').forEach(btn => btn.addEventListener('click', () => switchView(btn.dataset.view)));
document.querySelector('#leagueSort').addEventListener('change', renderLeague);
document.querySelector('#teamSearch').addEventListener('input', renderLeague);
document.querySelector('#advancedToggle').addEventListener('change', renderLeague);

renderOverview();
renderLeague();
renderKnockout();
