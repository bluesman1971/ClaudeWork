/**
 * clients.js — Client CRM: list, create.
 *             Gear profiles: list, create, edit, delete.
 */

import { state } from './state.js';
import { apiFetch } from './api.js';

// ── Camera type labels (mirrors prompts.py _CAMERA_LABELS) ───────────────────

const CAMERA_LABELS = {
    full_frame_mirrorless: 'Full-frame mirrorless',
    apsc_mirrorless:       'APS-C mirrorless',
    apsc_dslr:             'APS-C DSLR',
    full_frame_dslr:       'Full-frame DSLR',
    smartphone:            'Smartphone',
    film_35mm:             '35mm film',
    film_medium_format:    'Medium-format film',
};

// ── Client CRUD ───────────────────────────────────────────────────────────────

export async function refreshClientList() {
    try {
        const res  = await apiFetch('/clients');
        const data = await res.json();
        state._clients = data.clients || [];
        const sel = document.getElementById('clientSelect');
        const cur = sel.value;
        sel.innerHTML = '<option value="">— No client selected —</option>';
        state._clients.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = `${c.reference_code} — ${c.name}${c.company ? ' (' + c.company + ')' : ''}`;
            sel.appendChild(opt);
        });
        if (cur) sel.value = cur;
    } catch { /* non-fatal */ }
}

export function openClientModal() {
    document.getElementById('clientForm').reset();
    document.getElementById('clientModal').classList.add('active');
    setTimeout(() => document.getElementById('cName').focus(), 50);
}

export function closeClientModal() {
    document.getElementById('clientModal').classList.remove('active');
}

export async function saveNewClient(e) {
    e.preventDefault();
    const btn = document.getElementById('saveClientBtn');
    btn.disabled = true;
    btn.textContent = 'Saving…';
    try {
        const res = await apiFetch('/clients', {
            method: 'POST',
            body: JSON.stringify({
                name:                 document.getElementById('cName').value.trim(),
                email:                document.getElementById('cEmail').value.trim(),
                phone:                document.getElementById('cPhone').value.trim(),
                company:              document.getElementById('cCompany').value.trim(),
                home_city:            document.getElementById('cHomeCity').value.trim(),
                preferred_budget:     document.getElementById('cBudget').value,
                travel_style:         document.getElementById('cTravelStyle').value.trim(),
                dietary_requirements: document.getElementById('cDietary').value.trim(),
                notes:                document.getElementById('cNotes').value.trim(),
                tags:                 document.getElementById('cTags').value.trim(),
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to save client');
        closeClientModal();
        await refreshClientList();
        document.getElementById('clientSelect').value = data.client.id;
    } catch (ex) {
        alert('Error: ' + ex.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save Client';
    }
}

// ── Gear profile: load + populate selector ────────────────────────────────────

export async function refreshGearProfiles() {
    try {
        const res  = await apiFetch('/gear-profiles');
        const data = await res.json();
        state._gearProfiles = data.gear_profiles || [];
        _populateGearSelector();
        _renderGearProfileList();
    } catch { /* non-fatal */ }
}

function _populateGearSelector() {
    const sel = document.getElementById('gearProfileSelect');
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">— No gear profile —</option>';
    state._gearProfiles.forEach(gp => {
        const opt = document.createElement('option');
        opt.value = gp.id;
        opt.textContent = `${gp.name} (${CAMERA_LABELS[gp.camera_type] || gp.camera_type})`;
        sel.appendChild(opt);
    });
    if (cur) sel.value = cur;
}

function _renderGearProfileList() {
    const list = document.getElementById('gearProfileList');
    if (!list) return;
    if (!state._gearProfiles.length) {
        list.innerHTML = '<div class="gear-list-empty">No gear profiles yet. Add one to get tailored shoot advice.</div>';
        return;
    }
    list.innerHTML = '';
    state._gearProfiles.forEach(gp => {
        const row = document.createElement('div');
        row.className = 'gear-profile-row';
        const lenses  = (gp.lenses  || []).join(', ') || '—';
        const filters = (gp.has_filters || []).join(', ') || '—';
        row.innerHTML = `
            <div class="gear-profile-info">
                <div class="gear-profile-name">${_esc(gp.name)}</div>
                <div class="gear-profile-meta">
                    ${CAMERA_LABELS[gp.camera_type] || gp.camera_type}${gp.has_tripod ? ' · Tripod' : ''}${gp.has_gimbal ? ' · Gimbal' : ''}
                </div>
                <div class="gear-profile-lenses">Lenses: ${_esc(lenses)}</div>
                ${gp.has_filters && gp.has_filters.length ? `<div class="gear-profile-lenses">Filters: ${_esc(filters)}</div>` : ''}
            </div>
            <div class="gear-profile-actions">
                <button class="gear-action-btn" onclick="openGearProfileModal(${gp.id})">Edit</button>
                <button class="gear-action-btn gear-action-btn--del" onclick="deleteGearProfile(${gp.id})">Delete</button>
            </div>`;
        list.appendChild(row);
    });
}

// ── Gear profile modal ────────────────────────────────────────────────────────

export function openGearProfileModal(profileId) {
    const modal   = document.getElementById('gearProfileModal');
    const form    = document.getElementById('gearProfileForm');
    const title   = document.getElementById('gearModalTitle');
    const saveBtn = document.getElementById('saveGearProfileBtn');

    form.reset();
    document.getElementById('gearProfileId').value = profileId || '';

    if (profileId) {
        title.textContent = 'Edit Gear Profile';
        saveBtn.textContent = 'Save Changes';
        const gp = state._gearProfiles.find(p => p.id === profileId);
        if (gp) {
            document.getElementById('gpName').value        = gp.name;
            document.getElementById('gpCameraType').value  = gp.camera_type;
            document.getElementById('gpLenses').value      = (gp.lenses || []).join('\n');
            document.getElementById('gpHasTripod').checked = gp.has_tripod;
            document.getElementById('gpHasGimbal').checked = gp.has_gimbal;
            document.getElementById('gpFilters').value     = (gp.has_filters || []).join('\n');
            document.getElementById('gpNotes').value       = gp.notes || '';
        }
    } else {
        title.textContent = 'New Gear Profile';
        saveBtn.textContent = 'Save Profile';
    }

    modal.classList.add('active');
    setTimeout(() => document.getElementById('gpName').focus(), 50);
}

export function closeGearProfileModal() {
    document.getElementById('gearProfileModal').classList.remove('active');
}

export async function saveGearProfile(e) {
    e.preventDefault();
    const btn       = document.getElementById('saveGearProfileBtn');
    const profileId = parseInt(document.getElementById('gearProfileId').value, 10) || null;
    btn.disabled = true;
    btn.textContent = 'Saving…';

    // Parse lenses / filters: one per line, trim empties
    const parseLines = id =>
        document.getElementById(id).value
            .split('\n').map(s => s.trim()).filter(Boolean);

    const body = {
        name:        document.getElementById('gpName').value.trim(),
        camera_type: document.getElementById('gpCameraType').value,
        lenses:      parseLines('gpLenses'),
        has_tripod:  document.getElementById('gpHasTripod').checked,
        has_gimbal:  document.getElementById('gpHasGimbal').checked,
        has_filters: parseLines('gpFilters'),
        notes:       document.getElementById('gpNotes').value.trim() || null,
    };

    try {
        const url    = profileId ? `/gear-profiles/${profileId}` : '/gear-profiles';
        const method = profileId ? 'PUT' : 'POST';
        const res    = await apiFetch(url, { method, body: JSON.stringify(body) });
        const data   = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || 'Failed to save gear profile');
        closeGearProfileModal();
        await refreshGearProfiles();
    } catch (ex) {
        alert('Error: ' + ex.message);
    } finally {
        btn.disabled = false;
        btn.textContent = profileId ? 'Save Changes' : 'Save Profile';
    }
}

export async function deleteGearProfile(profileId) {
    if (!confirm('Delete this gear profile? This cannot be undone.')) return;
    try {
        const res  = await apiFetch(`/gear-profiles/${profileId}`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || 'Failed to delete gear profile');
        await refreshGearProfiles();
    } catch (ex) {
        alert('Error: ' + ex.message);
    }
}

// ── Gear profile management panel toggle ──────────────────────────────────────

export function openGearPanel() {
    document.getElementById('gearPanel').classList.add('active');
    refreshGearProfiles();
}

export function closeGearPanel() {
    document.getElementById('gearPanel').classList.remove('active');
}

// ── Private helpers ───────────────────────────────────────────────────────────

function _esc(str) {
    return String(str || '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
