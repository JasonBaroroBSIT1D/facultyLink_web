document.addEventListener('DOMContentLoaded', () => {
  initKraEditToggle();
  initKraWeightTracker();
  initKraSimulator();
});

function initKraEditToggle() {
  document.querySelectorAll('[data-edit-kra]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.kra-config-card');
      if (!card) return;
      card.querySelector('.kra-config-card__view').hidden = true;
      card.querySelector('.kra-config-card__edit').hidden = false;
    });
  });

  document.querySelectorAll('[data-cancel-edit]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const card = btn.closest('.kra-config-card');
      if (!card) return;
      card.querySelector('.kra-config-card__view').hidden = false;
      card.querySelector('.kra-config-card__edit').hidden = true;
    });
  });
}

function initKraWeightTracker() {
  const inputs = document.querySelectorAll('.kra-weight-input');
  const summary = document.getElementById('weight-summary');
  const display = document.getElementById('weight-total-display');
  if (!inputs.length || !display) return;

  const update = () => {
    let total = 0;
    inputs.forEach((inp) => {
      total += parseFloat(inp.value) || 0;
    });
    display.textContent = `${Math.round(total * 10) / 10}%`;
    if (summary) {
      summary.classList.toggle('is-valid', Math.abs(total - 100) < 0.01);
      summary.classList.toggle('is-invalid', Math.abs(total - 100) >= 0.01);
      const hint = summary.querySelector('.kra-weight-summary__hint');
      if (hint) {
        hint.textContent = Math.abs(total - 100) < 0.01 ? 'Weights balanced' : 'Must equal 100%';
      }
    }
  };

  inputs.forEach((inp) => inp.addEventListener('input', update));
  update();
}

function initKraSimulator() {
  const url = window.KRA_COMPUTE_URL;
  const inputs = document.querySelectorAll('.sim-score-input');
  if (!url || !inputs.length) return;

  let timer = null;
  const run = () => {
    const scores = {};
    inputs.forEach((inp) => {
      scores[inp.dataset.kraSlug] = parseFloat(inp.value) || 0;
    });

    fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scores }),
    })
      .then((r) => r.json())
      .then((data) => renderSimulation(data))
      .catch(() => {});
  };

  inputs.forEach((inp) => {
    inp.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(run, 280);
    });
  });
}

function renderSimulation(data) {
  const breakdown = data.breakdown;
  const qual = data.qualification;
  const totalEl = document.getElementById('sim-total');
  const listEl = document.getElementById('sim-breakdown');
  const bannerEl = document.getElementById('qual-banner');

  if (totalEl && breakdown) {
    totalEl.textContent = `${breakdown.total_score}%`;
  }

  if (listEl && breakdown && breakdown.kra_items) {
    listEl.innerHTML = breakdown.kra_items
      .map(
        (item) => `
      <li class="${item.passed ? 'pass' : 'fail'}">
        <span>${escapeHtml(item.kra_name)}</span>
        <span>${item.raw_score}% → ${item.weighted_contribution}% contrib.</span>
        <span class="kra-breakdown-list__status">${
          item.passed ? '✓ Meets min' : `✗ Below ${item.min_score}%`
        }</span>
      </li>`
      )
      .join('');
  }

  if (bannerEl && qual) {
    bannerEl.className = `qualification-banner ${
      qual.simulated_qualified ? 'qualification-banner--pass' : 'qualification-banner--fail'
    }`;
    bannerEl.innerHTML = `
      <strong>${
        qual.simulated_qualified
          ? 'Eligible for reclassification (simulated)'
          : 'Not yet qualified'
      }</strong>
      <ul>${(qual.notes || []).map((n) => `<li>${escapeHtml(n)}</li>`).join('')}</ul>
    `;
  }
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}
