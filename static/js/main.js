document.addEventListener('DOMContentLoaded', () => {
  initPasswordToggle();
  initSidebar();
  initCharts();
  initChartToggle();
  initProfileModal();
});

function initPasswordToggle() {
  const btn = document.querySelector('.toggle-pwd');
  const input = document.querySelector('#password');
  if (!btn || !input) return;
  btn.addEventListener('click', () => {
    const isPassword = input.type === 'password';
    input.type = isPassword ? 'text' : 'password';
    btn.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
  });
}

function initSidebar() {
  const toggle = document.querySelector('.menu-toggle');
  const sidebar = document.querySelector('.sidebar');
  if (toggle && sidebar) {
    toggle.addEventListener('click', () => sidebar.classList.toggle('open'));
  }
}

function initCharts() {
  const canvas = document.getElementById('submissionChart');
  if (!canvas || typeof Chart === 'undefined') return;

  fetch('/api/chart-data')
    .then(r => r.json())
    .then(data => {
      new Chart(canvas, {
        type: 'bar',
        data: {
          labels: data.labels,
          datasets: [{
            label: 'Submissions',
            data: data.data,
            backgroundColor: data.labels.map((_, i) =>
              i === 3 ? '#002D4F' : '#c5d4e3'
            ),
            borderRadius: 4,
            borderSkipped: false,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            y: { beginAtZero: true, grid: { color: '#f1f5f9' } },
            x: { grid: { display: false } },
          },
        },
      });
    });
}

function initProfileModal() {
  const modal = document.getElementById('profile-modal');
  if (!modal) return;

  const openers = document.querySelectorAll('[data-open-profile]');
  const closers = modal.querySelectorAll('[data-close-modal]');
  const panels = modal.querySelectorAll('.profile-panel');
  const panelTriggers = modal.querySelectorAll('[data-show-panel]');

  const showPanel = (panelId) => {
    panels.forEach((panel) => {
      panel.hidden = panel.id !== panelId;
    });
  };

  const openModal = () => {
    showPanel('profile-view');
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  };

  const closeModal = () => {
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    showPanel('profile-view');
  };

  openers.forEach((el) => el.addEventListener('click', openModal));
  closers.forEach((el) => el.addEventListener('click', closeModal));

  panelTriggers.forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.getAttribute('data-show-panel');
      if (target) showPanel(target);
    });
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal.classList.contains('is-open')) closeModal();
  });
}

function initChartToggle() {
  const buttons = document.querySelectorAll('.chart-toggle button');
  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      buttons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });
}

function initAnalyticsCharts() {
  const statusEl = document.getElementById('statusChart');
  const kraEl = document.getElementById('kraChart');
  if (!statusEl || typeof Chart === 'undefined') return;

  const statusData = JSON.parse(statusEl.dataset.values || '[]');
  const statusLabels = JSON.parse(statusEl.dataset.labels || '[]');

  new Chart(statusEl, {
    type: 'doughnut',
    data: {
      labels: statusLabels,
      datasets: [{
        data: statusData,
        backgroundColor: ['#f59e0b', '#22c55e', '#ef4444', '#3b82f6'],
      }],
    },
    options: { responsive: true, plugins: { legend: { position: 'bottom' } } },
  });

  if (kraEl) {
    const kraLabels = JSON.parse(kraEl.dataset.labels || '[]');
    const kraData = JSON.parse(kraEl.dataset.values || '[]');
    new Chart(kraEl, {
      type: 'bar',
      data: {
        labels: kraLabels,
        datasets: [{ label: 'Submissions', data: kraData, backgroundColor: '#002D4F', borderRadius: 4 }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } },
      },
    });
  }
}

if (document.getElementById('statusChart')) {
  document.addEventListener('DOMContentLoaded', initAnalyticsCharts);
}
