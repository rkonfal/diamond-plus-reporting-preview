const state = {
  theme: localStorage.getItem('dp-theme') || 'light',
};

document.documentElement.setAttribute('data-theme', state.theme);

function setTheme(next) {
  state.theme = next;
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('dp-theme', next);
  const button = document.querySelector('[data-theme-toggle]');
  if (button) button.textContent = next === 'dark' ? 'Světlý režim' : 'Tmavý režim';
}

function initThemeToggle() {
  const button = document.querySelector('[data-theme-toggle]');
  if (!button) return;
  button.textContent = state.theme === 'dark' ? 'Světlý režim' : 'Tmavý režim';
  button.addEventListener('click', () => setTheme(state.theme === 'dark' ? 'light' : 'dark'));
}

async function loadJson(path) {
  const res = await fetch(path, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to load ${path}`);
  return res.json();
}

function fmtNumber(value) {
  return new Intl.NumberFormat('cs-CZ').format(Number(value || 0));
}

function fmtDateTime(value) {
  if (!value) return '–';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat('cs-CZ', { dateStyle: 'medium', timeStyle: 'short' }).format(d);
}

function fmtPercent(value) {
  return `${Number(value || 0).toFixed(1)} %`;
}

function sum(list, key) {
  return (list || []).reduce((acc, item) => acc + Number(item?.[key] || 0), 0);
}

function statusPill(text, kind = 'info') {
  return `<span class="pill ${kind}">${text}</span>`;
}

window.DP = { initThemeToggle, loadJson, fmtNumber, fmtDateTime, fmtPercent, sum, statusPill };
