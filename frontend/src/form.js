/**
 * form.js — Form controls, section toggles, progress animation, and shared UI utils.
 *
 * Also contains resetForm() and showError() which are used across modules.
 */

import { state } from './state.js';

// ── Section toggles ──────────────────────────────────────────────────────────

export function toggleSection(key) {
    state.sectionEnabled[key] = !state.sectionEnabled[key];
    const section = document.getElementById(`section-${key}`);
    const toggle  = document.getElementById(`toggle-${key}`);
    const label   = document.getElementById(`toggle-${key}-label`);
    if (state.sectionEnabled[key]) {
        section.classList.remove('disabled');
        toggle.classList.add('on');
        label.textContent = 'On';
    } else {
        section.classList.add('disabled');
        toggle.classList.remove('on');
        label.textContent = 'Off';
    }
}

// ── Count steppers ───────────────────────────────────────────────────────────

export function adjustCount(id, delta) {
    const cfg = state.countConfig[id];
    cfg.value = Math.min(cfg.max, Math.max(cfg.min, cfg.value + delta));
    document.getElementById(id + '_display').textContent = cfg.value;
    document.getElementById(id).value = cfg.value;
}

// ── "Other" checkbox reveal ──────────────────────────────────────────────────

export function toggleOther(checkbox, textId) {
    const wrapper = document.getElementById(textId + '_wrapper');
    const input   = document.getElementById(textId);
    if (checkbox.checked) { wrapper.classList.add('visible'); input.focus(); }
    else { wrapper.classList.remove('visible'); input.value = ''; }
}

// ── Read checkbox group values ───────────────────────────────────────────────

export function getGroupValues(name, otherTextId) {
    return Array.from(document.querySelectorAll(`input[name="${name}"]:checked`))
        .map(cb => cb.value === 'other'
            ? (document.getElementById(otherTextId).value.trim() || null)
            : cb.value)
        .filter(Boolean);
}

// ── Progress animation ───────────────────────────────────────────────────────

export function buildProgressStepIds() {
    const steps = [];
    if (state.sectionEnabled.photos)      steps.push('step-photos');
    if (state.sectionEnabled.dining)      steps.push('step-restaurants');
    if (state.sectionEnabled.attractions) steps.push('step-attractions');
    steps.push('step-building');
    return steps;
}

export function startProgressAnimation() {
    const steps = buildProgressStepIds();
    ['step-photos', 'step-restaurants', 'step-attractions', 'step-building']
        .forEach(id => {
            const el = document.getElementById(id);
            el.className = 'progress-step';
            el.style.display = steps.includes(id) ? '' : 'none';
        });
    let current = 0;
    state.progressInterval = setInterval(() => {
        if (current < steps.length) {
            if (current > 0)
                document.getElementById(steps[current - 1]).className = 'progress-step done';
            document.getElementById(steps[current]).className = 'progress-step active';
            current++;
        }
    }, 8000);
}

export function stopProgressAnimation(success) {
    clearInterval(state.progressInterval);
    state.progressInterval = null;
    ['step-photos', 'step-restaurants', 'step-attractions', 'step-building'].forEach(id => {
        const el = document.getElementById(id);
        if (el.style.display !== 'none')
            el.className = success ? 'progress-step done' : 'progress-step';
    });
}

// ── Shared UI utilities ──────────────────────────────────────────────────────

export function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

export function showError(message) {
    const el = document.getElementById('errorMessage');
    el.textContent = message;
    el.classList.add('active');
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

// ── Reset entire form + app state ────────────────────────────────────────────

export function resetForm() {
    document.getElementById('tripForm').reset();
    document.getElementById('accommodation').value = '';
    document.getElementById('prePlanned').value = '';
    // Reset other text inputs
    ['photo_other_text', 'cuisine_other_text', 'attr_other_text'].forEach(id => {
        document.getElementById(id).value = '';
        document.getElementById(id + '_wrapper').classList.remove('visible');
    });
    // Reset section toggles to On
    ['photos', 'dining', 'attractions'].forEach(key => {
        if (!state.sectionEnabled[key]) toggleSection(key);
    });
    // Reset counts to defaults
    [['photos_per_day', 3], ['restaurants_per_day', 3], ['attractions_per_day', 4]]
        .forEach(([id, def]) => {
            state.countConfig[id].value = def;
            document.getElementById(id + '_display').textContent = def;
            document.getElementById(id).value = def;
        });
    // Clear review state
    state.rawData = null;
    state.approvalState = { photos: [], restaurants: [], attractions: [] };
    document.getElementById('reviewBody').innerHTML = '';
    document.getElementById('reviewContainer').classList.remove('active');
    document.getElementById('finalizing').classList.remove('active');
    document.getElementById('resultContainer').classList.remove('active');
    document.getElementById('errorMessage').classList.remove('active');
    document.getElementById('loading').classList.remove('active');
    window.scrollTo({ top: 0, behavior: 'smooth' });
    document.getElementById('location').focus();
}
