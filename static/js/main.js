document.addEventListener('DOMContentLoaded', () => {
  initPasswordToggle();
  initSidebar();
  initCharts();
  initChartToggle();
  initProfileModal();
  initNotifBell();
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

function initNotifBell() {
  const wrap = document.getElementById('notifBellWrap');
  const btn = document.getElementById('notifBellBtn');
  const dropdown = document.getElementById('notifDropdown');
  const badge = document.getElementById('notifBadge');
  const list = document.getElementById('notifDropdownList');
  const markAllBtn = document.getElementById('notifMarkAll');
  if (!btn || !dropdown) return;

  const CATEGORY_ICONS = { warning: '⚠️', error: '🚨', success: '✅', reminder: '🔔', info: 'ℹ️' };

  async function fetchUnreadCount() {
    try {
      const res = await fetch('/api/notifications/unread-count');
      const data = await res.json();
      if (badge) {
        badge.textContent = data.count > 99 ? '99+' : data.count;
        badge.style.display = data.count > 0 ? 'flex' : 'none';
      }
    } catch (_) {}
  }

  async function loadDropdown() {
    if (!list) return;
    list.innerHTML = '<li class="notif-dropdown__empty">Loading…</li>';
    try {
      const res = await fetch('/api/notifications/recent');
      const data = await res.json();
      const items = data.notifications || [];
      if (!items.length) {
        list.innerHTML = '<li class="notif-dropdown__empty">No notifications yet.</li>';
        return;
      }
      list.innerHTML = items.map(n => {
        const cat = n.category || 'info';
        const icon = CATEGORY_ICONS[cat] || 'ℹ️';
        const unreadClass = n.read ? '' : 'notif-dropdown__item--unread';
        return `<li class="notif-dropdown__item ${unreadClass}" data-id="${n.id}">
          <span class="notif-dd-icon notif-dd-icon--${cat}">${icon}</span>
          <div class="notif-dd-body">
            <span class="notif-dd-title">${n.title}</span>
            <span class="notif-dd-msg">${n.message || ''}</span>
            <span class="notif-dd-time">${n.created_at}</span>
          </div>
          ${!n.read ? `<button class="notif-dd-read-btn" data-id="${n.id}" title="Mark read" aria-label="Mark read">✓</button>` : ''}
        </li>`;
      }).join('');

      list.querySelectorAll('.notif-dd-read-btn').forEach(b => {
        b.addEventListener('click', async (e) => {
          e.stopPropagation();
          const id = b.dataset.id;
          await fetch('/api/notifications/mark-read', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id }),
          });
          const item = list.querySelector(`[data-id="${id}"]`);
          if (item) item.classList.remove('notif-dropdown__item--unread');
          b.remove();
          fetchUnreadCount();
        });
      });
    } catch (_) {
      list.innerHTML = '<li class="notif-dropdown__empty">Could not load notifications.</li>';
    }
  }

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const isOpen = !dropdown.hidden;
    dropdown.hidden = isOpen;
    btn.setAttribute('aria-expanded', String(!isOpen));
    if (!isOpen) loadDropdown();
  });

  document.addEventListener('click', (e) => {
    if (wrap && !wrap.contains(e.target)) {
      dropdown.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
    }
  });

  if (markAllBtn) {
    markAllBtn.addEventListener('click', async () => {
      await fetch('/api/notifications/mark-read', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: 'all' }),
      });
      list.querySelectorAll('.notif-dropdown__item--unread').forEach(i => i.classList.remove('notif-dropdown__item--unread'));
      list.querySelectorAll('.notif-dd-read-btn').forEach(b => b.remove());
      fetchUnreadCount();
    });
  }

  // Initial load + poll every 60s
  fetchUnreadCount();
  setInterval(fetchUnreadCount, 60000);
}
