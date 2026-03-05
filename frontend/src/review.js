/**
 * review.js — Review screen: render, toggle, inline-edit, replace.
 */

import { state } from './state.js';
import { apiFetch } from './api.js';

// ── HTML escaper (used here and exported for trips.js) ───────────────────────

export function esc(str) {
    return String(str || '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Review screen ────────────────────────────────────────────────────────────

export function showReviewScreen(result) {
    state.rawData = result;
    result.photos      = Array.isArray(result.photos)      ? result.photos      : [];
    result.restaurants = Array.isArray(result.restaurants)  ? result.restaurants : [];
    result.attractions = Array.isArray(result.attractions)  ? result.attractions : [];
    state.approvalState.photos      = result.photos.map(() => true);
    state.approvalState.restaurants = result.restaurants.map(() => true);
    state.approvalState.attractions = result.attractions.map(() => true);

    document.getElementById('reviewTitle').textContent = `Review — ${result.location}`;
    document.getElementById('reviewSubtitle').textContent =
        `${result.photo_count + result.restaurant_count + result.attraction_count} suggestions · toggle off anything you don't want in the final guide`;

    const body = document.getElementById('reviewBody');
    body.innerHTML = '';
    if (result.photos.length)
        body.appendChild(buildReviewSection('photos',      result.photos,      'Photography', 'Locations & photo spots'));
    if (result.restaurants.length)
        body.appendChild(buildReviewSection('restaurants', result.restaurants, 'Dining',      'Restaurants & cafés'));
    if (result.attractions.length)
        body.appendChild(buildReviewSection('attractions', result.attractions, 'Attractions', 'Things to see & do'));

    if (Array.isArray(result.warnings) && result.warnings.length > 0) {
        const warnHtml = result.warnings.map(msg => `
            <div class="scout-warning">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
                    <line x1="12" y1="9" x2="12" y2="13"/>
                    <line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                ${msg}
            </div>`).join('');
        body.insertAdjacentHTML('afterbegin', warnHtml);
    }

    updateGlobalCount();
    document.getElementById('loading').classList.remove('active');
    document.getElementById('reviewContainer').classList.add('active');
    document.getElementById('reviewContainer').scrollIntoView({ behavior: 'smooth' });
}

export function buildReviewSection(type, items, title, subtitle) {
    const byDay = {};
    items.forEach((item, idx) => {
        const day = item.day || 1;
        if (!byDay[day]) byDay[day] = [];
        byDay[day].push({ item, idx });
    });

    const section = document.createElement('div');
    section.className = 'review-section';
    section.id = `review-section-${type}`;

    const header = document.createElement('div');
    header.className = 'review-section-header';
    header.innerHTML = `
        <div class="review-section-titles">
            <div class="review-section-title">${title}</div>
            <div class="review-section-subtitle">${subtitle}</div>
        </div>
        <div class="review-section-count" id="count-${type}">
            <strong>${items.length}</strong>&nbsp;of&nbsp;<strong>${items.length}</strong>&nbsp;selected
        </div>
        <div class="review-bulk-actions">
            <button class="review-bulk-btn" onclick="bulkSelect('${type}', true)">All</button>
            <span class="review-bulk-sep">/</span>
            <button class="review-bulk-btn" onclick="bulkSelect('${type}', false)">None</button>
        </div>`;
    section.appendChild(header);

    Object.keys(byDay).sort((a, b) => a - b).forEach(day => {
        const group = document.createElement('div');
        group.className = 'review-day-group';
        group.innerHTML = `<div class="review-day-label">Day ${day}</div>`;
        byDay[day].forEach(({ item, idx }) => group.appendChild(buildReviewItem(type, item, idx)));
        section.appendChild(group);
    });

    return section;
}

export function buildReviewItem(type, item, idx) {
    const wrapper = document.createElement('div');
    wrapper.id = `review-wrapper-${type}-${idx}`;

    const row = document.createElement('div');
    row.className = 'review-item';
    row.id = `review-item-${type}-${idx}`;

    const isVerified = item._status === 'OPERATIONAL';
    const dotClass   = isVerified ? 'verified' : 'unverified';

    let tags = '';
    if (type === 'photos') {
        if (item.time)  tags += `<span class="review-item-tag accent">${esc(item.time)}</span>`;
        const addrPart = (item.address || '').split(',')[0].trim();
        if (addrPart)   tags += `<span class="review-item-tag">${esc(addrPart)}</span>`;
    } else if (type === 'restaurants') {
        if (item.meal_type) tags += `<span class="review-item-tag accent">${esc(item.meal_type)}</span>`;
        if (item.cuisine)   tags += `<span class="review-item-tag">${esc(item.cuisine)}</span>`;
        if (item.price)     tags += `<span class="review-item-tag">${esc(item.price)}</span>`;
    } else {
        if (item.time)     tags += `<span class="review-item-tag accent">${esc(item.time)}</span>`;
        if (item.category) tags += `<span class="review-item-tag">${esc(item.category)}</span>`;
        if (item.admission && item.admission !== 'Free')
                           tags += `<span class="review-item-tag">${esc(item.admission)}</span>`;
    }

    row.innerHTML = `
        <button class="review-item-toggle" id="toggle-${type}-${idx}"
                onclick="toggleItem('${type}', ${idx})" title="Toggle this item"></button>
        <span class="review-item-name" id="name-${type}-${idx}">${esc(item.name || 'Unnamed')}</span>
        <div class="review-item-tags">${tags}</div>
        <div class="review-item-actions">
            <button class="review-action-btn" id="edit-btn-${type}-${idx}"
                    onclick="toggleEditPanel('${type}', ${idx})" title="Edit name or notes">Edit</button>
            <button class="review-action-btn" id="replace-btn-${type}-${idx}"
                    onclick="replaceItem('${type}', ${idx})" title="Get an alternative suggestion">Alt</button>
        </div>
        <div class="review-status-dot ${dotClass}" title="${isVerified ? 'Verified open' : 'Unverified'}"></div>`;

    const editPanel = document.createElement('div');
    editPanel.className = 'review-edit-panel';
    editPanel.id = `edit-panel-${type}-${idx}`;
    editPanel.innerHTML = `
        <label>Name</label>
        <input type="text" id="edit-name-${type}-${idx}"
               value="${esc(item.name || '')}"
               style="width:100%;border:1px solid var(--rule);border-bottom:2px solid var(--ink);
                      padding:7px 10px;font-family:Inter,sans-serif;font-size:0.85rem;
                      background:var(--white);margin-bottom:10px;border-radius:0;">
        <label>Notes (visible to consultant only — won't appear in final guide)</label>
        <textarea id="edit-notes-${type}-${idx}" placeholder="Add context, corrections, or reminders…">${esc(item._consultant_notes || '')}</textarea>
        <div class="review-edit-actions">
            <button class="review-edit-cancel" onclick="toggleEditPanel('${type}', ${idx})">Cancel</button>
            <button class="review-edit-save" onclick="saveItemEdit('${type}', ${idx})">Save</button>
        </div>`;

    wrapper.appendChild(row);
    wrapper.appendChild(editPanel);
    return wrapper;
}

// ── Edit panel ───────────────────────────────────────────────────────────────

export function toggleEditPanel(type, idx) {
    const panel  = document.getElementById(`edit-panel-${type}-${idx}`);
    const btn    = document.getElementById(`edit-btn-${type}-${idx}`);
    const isOpen = panel.classList.toggle('open');
    btn.textContent = isOpen ? 'Done' : 'Edit';
    if (isOpen) document.getElementById(`edit-name-${type}-${idx}`).focus();
}

export function saveItemEdit(type, idx) {
    const newName  = document.getElementById(`edit-name-${type}-${idx}`).value.trim();
    const newNotes = document.getElementById(`edit-notes-${type}-${idx}`).value.trim();
    if (!newName) return;
    const arr = type === 'photos' ? state.rawData.photos
              : type === 'restaurants' ? state.rawData.restaurants
              : state.rawData.attractions;
    arr[idx].name = newName;
    arr[idx]._consultant_notes = newNotes;
    document.getElementById(`name-${type}-${idx}`).textContent = newName;
    toggleEditPanel(type, idx);
}

// ── Replace ──────────────────────────────────────────────────────────────────

export async function replaceItem(type, idx) {
    if (!state.rawData) return;

    const btn = document.getElementById(`replace-btn-${type}-${idx}`);
    btn.textContent = '…';
    btn.classList.add('replacing');
    btn.disabled = true;

    const arr = type === 'photos' ? state.rawData.photos
              : type === 'restaurants' ? state.rawData.restaurants
              : state.rawData.attractions;
    const excludeNames  = arr.map(it => it.name).filter(Boolean);
    const currentItem   = arr[idx];

    try {
        const response = await apiFetch('/replace', {
            method: 'POST',
            body: JSON.stringify({
                session_id:    state.rawData.session_id,
                trip_id:       state.rawData.trip_id || null,
                type,
                index:         idx,
                day:           currentItem.day || 1,
                meal_type:     currentItem.meal_type || null,
                exclude_names: excludeNames,
            }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not find an alternative');

        arr[idx] = result.item;
        const wrapper    = document.getElementById(`review-wrapper-${type}-${idx}`);
        const newWrapper = buildReviewItem(type, result.item, idx);
        if (!state.approvalState[type][idx]) {
            const newRow    = newWrapper.querySelector('.review-item');
            const newToggle = newWrapper.querySelector('.review-item-toggle');
            if (newRow)    newRow.classList.add('rejected');
            if (newToggle) newToggle.classList.add('off');
        }
        wrapper.replaceWith(newWrapper);
        const newRow = document.getElementById(`review-item-${type}-${idx}`);
        if (newRow) {
            newRow.classList.add('just-replaced');
            setTimeout(() => newRow.classList.remove('just-replaced'), 1500);
        }
    } catch (err) {
        const b = document.getElementById(`replace-btn-${type}-${idx}`);
        if (b) { b.textContent = 'Alt'; b.classList.remove('replacing'); b.disabled = false; }
        alert(`Could not get an alternative: ${err.message}`);
    }
}

// ── Approval toggles ─────────────────────────────────────────────────────────

export function toggleItem(type, idx) {
    state.approvalState[type][idx] = !state.approvalState[type][idx];
    const row    = document.getElementById(`review-item-${type}-${idx}`);
    const toggle = document.getElementById(`toggle-${type}-${idx}`);
    const approved = state.approvalState[type][idx];
    row.classList.toggle('rejected', !approved);
    toggle.classList.toggle('off', !approved);
    updateSectionCount(type);
    updateGlobalCount();
}

export function bulkSelect(type, approved) {
    state.approvalState[type] = state.approvalState[type].map(() => approved);
    state.approvalState[type].forEach((_, idx) => {
        const row    = document.getElementById(`review-item-${type}-${idx}`);
        const toggle = document.getElementById(`toggle-${type}-${idx}`);
        if (row)    row.classList.toggle('rejected', !approved);
        if (toggle) toggle.classList.toggle('off', !approved);
    });
    updateSectionCount(type);
    updateGlobalCount();
}

export function updateSectionCount(type) {
    const total    = state.approvalState[type].length;
    const selected = state.approvalState[type].filter(Boolean).length;
    const el = document.getElementById(`count-${type}`);
    if (el) el.innerHTML =
        `<strong>${selected}</strong>&nbsp;of&nbsp;<strong>${total}</strong>&nbsp;selected`;
}

export function updateGlobalCount() {
    const total = Object.values(state.approvalState).flat().filter(Boolean).length;
    document.getElementById('finalCountBadge').textContent = `${total} selected`;
    document.getElementById('generateFinalBtn').disabled = (total === 0);
}
